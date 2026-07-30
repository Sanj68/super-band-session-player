#include "PluginProcessor.h"
#include "PluginEditor.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <limits>
#include <numeric>

namespace session_player
{
namespace
{
constexpr auto listenerVersion = "0.1.0";
constexpr double defaultTempo = 120.0;
constexpr double beatsPerBar = 4.0;
constexpr double referencePitch = 440.0;
constexpr double maxPlayingCallbackGapMs = 500.0;
constexpr double backwardPpqJitterBeats = 0.25;
constexpr double minimumForwardSeekBeats = 2.0;

constexpr bool isPreRollPpq(double ppqPosition)
{
    return ppqPosition < 0.0;
}

constexpr bool isMeaningfulPpqDiscontinuity(
    double previousPpq,
    double observedPpq,
    double blockBeats)
{
    const auto positiveBlockBeats = blockBeats > 0.0 ? blockBeats : 0.0;
    const auto blockScaledForwardLimit = positiveBlockBeats * 2.0 + backwardPpqJitterBeats;
    const auto forwardLimit = blockScaledForwardLimit > minimumForwardSeekBeats
        ? blockScaledForwardLimit
        : minimumForwardSeekBeats;
    const auto observedDelta = observedPpq - previousPpq;
    return (
        observedDelta < -backwardPpqJitterBeats
        || observedDelta > forwardLimit
    );
}

struct BarTransition
{
    int activeBar;
    int completedBar;
};

constexpr BarTransition advanceBar(int activeBar, int observedBar)
{
    if (activeBar < 0)
        return { observedBar, -1 };
    // A meaningful backwards jump resets the capture before this tracker is
    // called. Any remaining lower bar is host jitter around a boundary.
    if (observedBar <= activeBar)
        return { activeBar, -1 };
    return { observedBar, activeBar };
}

static_assert(isPreRollPpq(-0.5));
static_assert(! isPreRollPpq(0.0));
static_assert(! isPreRollPpq(0.5));
static_assert(! isMeaningfulPpqDiscontinuity(8.0, 8.0, 0.025));
static_assert(! isMeaningfulPpqDiscontinuity(8.0, 7.99, 0.025));
static_assert(! isMeaningfulPpqDiscontinuity(8.0, 8.5, 0.025));
static_assert(isMeaningfulPpqDiscontinuity(8.0, 7.5, 0.025));
static_assert(isMeaningfulPpqDiscontinuity(8.0, 10.5, 0.025));
static_assert(! isMeaningfulPpqDiscontinuity(8.0, 10.4, 1.2));
static_assert(advanceBar(-1, 0).activeBar == 0);
static_assert(advanceBar(-1, 0).completedBar == -1);
static_assert(advanceBar(0, 0).completedBar == -1);
static_assert(advanceBar(1, 0).activeBar == 1);
static_assert(advanceBar(1, 0).completedBar == -1);

// A two-chord C -> G boundary closes and reports the C bar; the new G bar
// becomes active only after that completed-bar decision has been made.
constexpr std::array<int, 2> twoChordPitchClasses { 0, 7 };
constexpr auto twoChordBoundary = advanceBar(0, 1);
static_assert(twoChordBoundary.completedBar == 0);
static_assert(twoChordPitchClasses[static_cast<size_t>(twoChordBoundary.completedBar)] == 0);
static_assert(twoChordPitchClasses[static_cast<size_t>(twoChordBoundary.activeBar)] == 7);

constexpr std::array<const char*, 12> noteNames {
    "C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"
};

constexpr std::array<double, 12> majorProfile {
    6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88
};

constexpr std::array<double, 12> minorProfile {
    6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17
};

int barIndexFromPpq(double ppqPosition)
{
    return juce::jmax(0, static_cast<int>(std::floor(ppqPosition / beatsPerBar)));
}

double profileCorrelation(const std::array<float, 12>& chroma, const std::array<double, 12>& profile, int tonic)
{
    double chromaMean = 0.0;
    double profileMean = 0.0;

    for (int i = 0; i < 12; ++i)
    {
        chromaMean += chroma[static_cast<size_t>(i)];
        profileMean += profile[static_cast<size_t>(i)];
    }

    chromaMean /= 12.0;
    profileMean /= 12.0;

    double numerator = 0.0;
    double chromaEnergy = 0.0;
    double profileEnergy = 0.0;

    for (int i = 0; i < 12; ++i)
    {
        const auto chromaDelta = static_cast<double>(chroma[static_cast<size_t>(i)]) - chromaMean;
        const auto profileDelta = profile[static_cast<size_t>((i + 12 - tonic) % 12)] - profileMean;
        numerator += chromaDelta * profileDelta;
        chromaEnergy += chromaDelta * chromaDelta;
        profileEnergy += profileDelta * profileDelta;
    }

    const auto denom = std::sqrt(chromaEnergy * profileEnergy);
    return denom > std::numeric_limits<double>::epsilon() ? numerator / denom : 0.0;
}

int strongestPitchClass(const std::array<float, 12>& chroma)
{
    int strongest = 0;
    for (int i = 1; i < 12; ++i)
        if (chroma[static_cast<size_t>(i)] > chroma[static_cast<size_t>(strongest)])
            strongest = i;

    return strongest;
}
} // namespace

HarmonicBridgeClient::HarmonicBridgeClient()
    : Thread("Session Player Harmonic Client"),
      pluginInstanceId("logic-harmonic-au-" + juce::Uuid().toString()),
      apiBaseUrl(getApiBaseUrl())
{
}

HarmonicBridgeClient::~HarmonicBridgeClient()
{
    stop();
}

void HarmonicBridgeClient::start()
{
    running.store(true);
    startThread();
}

void HarmonicBridgeClient::stop()
{
    running.store(false);
    transportRunning.store(false);
    signalThreadShouldExit();
    stopThread(2000);
}

void HarmonicBridgeClient::setTransportRunning(bool isRunning)
{
    transportRunning.store(isRunning);
}

bool HarmonicBridgeClient::pushFrame(const HarmonicFrame& frame)
{
    if (! transportRunning.load())
        return false;

    const auto scope = fifo.write(1);
    if (scope.blockSize1 <= 0)
        return false;

    frames[static_cast<size_t>(scope.startIndex1)] = frame;
    // ScopedWrite commits the slot when it leaves scope.
    return true;
}

bool HarmonicBridgeClient::isConnected() const
{
    return connected.load();
}

void HarmonicBridgeClient::run()
{
    while (! threadShouldExit() && running.load())
    {
        std::vector<HarmonicFrame> batch;
        batch.reserve(8);

        {
            const auto available = juce::jmin(8, fifo.getNumReady());
            const auto scope = fifo.read(available);

            for (int i = 0; i < scope.blockSize1; ++i)
                batch.push_back(frames[static_cast<size_t>(scope.startIndex1 + i)]);
            for (int i = 0; i < scope.blockSize2; ++i)
                batch.push_back(frames[static_cast<size_t>(scope.startIndex2 + i)]);
            // ScopedRead consumes exactly these slots when it leaves scope.
        }

        // Stopping transport invalidates every queued analysis frame. The
        // bridge thread remains the FIFO's sole consumer and drains them here,
        // so a large Logic project cannot keep POSTing stale bars after stop.
        if (! transportRunning.load())
        {
            connected.store(false);
        }
        else if (! batch.empty())
        {
            const auto configuredSessionId = getSessionId();
            if (configuredSessionId.isNotEmpty())
                boundSessionId = configuredSessionId;
            if (boundSessionId.isEmpty())
                boundSessionId = resolveLatestSessionId(pluginInstanceId);

            juce::String body("[");
            size_t readyCount = 0;
            for (auto& frame : batch)
            {
                if (frame.sessionId.isEmpty())
                    frame.sessionId = boundSessionId;

                if (frame.sessionId.isEmpty())
                    continue;

                if (readyCount != 0)
                    body += ",";
                body += frameToJson(frame, pluginInstanceId);
                ++readyCount;
            }
            body += "]";

            connected.store(
                transportRunning.load()
                && readyCount > 0
                && postJson(apiBaseUrl + "/harmonic", body));
        }

        wait(50);
    }
}

juce::String HarmonicBridgeClient::getEnvironment(const char* name)
{
    if (const char* raw = std::getenv(name))
        return juce::String(raw).trim();

    return {};
}

juce::String HarmonicBridgeClient::getSessionId()
{
    if (const auto envSession = getEnvironment("SESSION_PLAYER_SESSION_ID"); envSession.isNotEmpty())
        return envSession;

    const auto configFile = juce::File::getSpecialLocation(juce::File::userApplicationDataDirectory)
        .getChildFile("Application Support")
        .getChildFile("Session Player Bridge")
        .getChildFile("config.json");

    if (configFile.existsAsFile())
        if (const auto parsed = juce::JSON::parse(configFile); parsed.isObject())
            return parsed.getDynamicObject()->getProperty("session_id").toString().trim();

    return {};
}

juce::String HarmonicBridgeClient::getApiBaseUrl()
{
    auto baseUrl = juce::String();
    const auto configFile = juce::File::getSpecialLocation(juce::File::userApplicationDataDirectory)
        .getChildFile("Application Support")
        .getChildFile("Session Player Bridge")
        .getChildFile("config.json");

    if (configFile.existsAsFile())
        if (const auto parsed = juce::JSON::parse(configFile); parsed.isObject())
            baseUrl = parsed.getDynamicObject()->getProperty("api_base_url").toString().trim();

    if (const auto envUrl = getEnvironment("SESSION_PLAYER_BRIDGE_URL"); envUrl.isNotEmpty())
        baseUrl = envUrl;
    if (baseUrl.isEmpty())
        baseUrl = "http://127.0.0.1:8000/api/bridge";
    return baseUrl.trim().trimCharactersAtEnd("/");
}

juce::String HarmonicBridgeClient::resolveLatestSessionId(const juce::String& pluginId) const
{
    const auto body = juce::String("{\"plugin_instance_id\":\"") + jsonEscape(pluginId)
        + "\",\"plugin_version\":\"" + listenerVersion
        + "\",\"source_id\":\"session-player-listener\"}";
    juce::String responseBody;
    if (! postJson(apiBaseUrl + "/heartbeat", body, &responseBody))
        return {};

    const auto response = juce::JSON::parse(responseBody);
    if (! response.isObject())
        return {};
    return response.getProperty("session_id", "").toString().trim();
}

juce::String HarmonicBridgeClient::jsonEscape(const juce::String& text)
{
    const auto escaped = juce::JSON::escapeString(text);
    if (escaped.startsWithChar('"') && escaped.endsWithChar('"'))
        return escaped.substring(1, escaped.length() - 1);

    return escaped;
}

juce::String HarmonicBridgeClient::frameToJson(
    const HarmonicFrame& frame,
    const juce::String& pluginId)
{
    juce::String chroma("[");
    for (size_t i = 0; i < frame.chroma.size(); ++i)
    {
        if (i != 0)
            chroma += ",";
        chroma += juce::String(frame.chroma[i], 6);
    }
    chroma += "]";

    auto json = juce::String("{")
        + "\"plugin_instance_id\":\"" + jsonEscape(pluginId) + "\","
        + "\"session_id\":\"" + jsonEscape(frame.sessionId) + "\","
        + "\"source_id\":\"session-player-listener\","
        + "\"capture_epoch\":" + juce::String(static_cast<juce::int64>(frame.captureEpoch)) + ","
        + "\"sample_rate\":" + juce::String(frame.sampleRate, 1) + ","
        + "\"host_tempo\":" + juce::String(frame.tempo, 3) + ","
        + "\"tempo_bpm\":" + juce::String(frame.tempo, 3) + ","
        + "\"tempo_confidence\":" + juce::String(frame.tempoConfidence, 4) + ","
        + "\"playing\":" + (frame.playing ? "true" : "false") + ","
        + "\"bar_index\":" + juce::String(frame.barIndex) + ","
        + "\"frame_start_seconds\":" + juce::String(frame.frameStartSeconds, 6) + ","
        + "\"duration_seconds\":" + juce::String(frame.durationSeconds, 6) + ","
        + "\"chroma\":" + chroma + ","
        + "\"key_pc\":" + juce::String(frame.keyPc) + ","
        + "\"key\":\"" + jsonEscape(frame.key) + "\","
        + "\"scale\":\"" + jsonEscape(frame.scale) + "\","
        + "\"key_confidence\":" + juce::String(frame.keyConfidence, 4) + ","
        + "\"scale_confidence\":" + juce::String(frame.scaleConfidence, 4) + ","
        + "\"cadence\":\"" + jsonEscape(frame.cadence) + "\","
        + "\"cadence_confidence\":" + juce::String(frame.cadenceConfidence, 4);

    if (frame.ppqPosition >= 0.0)
        json += ",\"ppq_position\":" + juce::String(frame.ppqPosition, 6);

    return json + "}";
}

bool HarmonicBridgeClient::postJson(
    const juce::String& url,
    const juce::String& body,
    juce::String* responseBody)
{
    int statusCode = 0;
    juce::URL target(url);
    target = target.withPOSTData(body);

    auto options = juce::URL::InputStreamOptions(juce::URL::ParameterHandling::inPostData)
        .withHttpRequestCmd("POST")
        .withExtraHeaders("Content-Type: application/json\r\n")
        .withConnectionTimeoutMs(500)
        .withNumRedirectsToFollow(0)
        .withStatusCode(&statusCode);

    auto stream = target.createInputStream(options);
    if (stream == nullptr || statusCode < 200 || statusCode >= 300)
        return false;
    if (responseBody != nullptr)
        *responseBody = stream->readEntireStreamAsString();
    return true;
}

SessionPlayerListenerAudioProcessor::SessionPlayerListenerAudioProcessor(
    bool enableBridgeNetworking)
    : AudioProcessor(BusesProperties()
          .withInput("Input", juce::AudioChannelSet::stereo(), true)
          .withOutput("Output", juce::AudioChannelSet::stereo(), true)),
      Thread("Session Player Listener Analysis"),
      bridgeNetworkingEnabled(enableBridgeNetworking)
{
    juce::dsp::WindowingFunction<float>::fillWindowingTables(
        fftWindow.data(),
        static_cast<size_t>(fftSize),
        juce::dsp::WindowingFunction<float>::hann,
        false);

    if (bridgeNetworkingEnabled)
        bridgeClient.start();
    startThread();
}

SessionPlayerListenerAudioProcessor::~SessionPlayerListenerAudioProcessor()
{
    analysisTransportRunning.store(false);
    signalThreadShouldExit();
    stopThread(2000);
    if (bridgeNetworkingEnabled)
        bridgeClient.stop();
}

void SessionPlayerListenerAudioProcessor::prepareToPlay(double sampleRate, int)
{
    resetAnalysisState(sampleRate);
}

void SessionPlayerListenerAudioProcessor::releaseResources()
{
}

bool SessionPlayerListenerAudioProcessor::isBusesLayoutSupported(const BusesLayout& layouts) const
{
    const auto& input = layouts.getMainInputChannelSet();
    const auto& output = layouts.getMainOutputChannelSet();
    return input == output && (input == juce::AudioChannelSet::mono() || input == juce::AudioChannelSet::stereo());
}

void SessionPlayerListenerAudioProcessor::processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midiMessages)
{
    juce::ignoreUnused(midiMessages);
    juce::ScopedNoDenormals noDenormals;

    const auto totalInputChannels = getTotalNumInputChannels();
    const auto totalOutputChannels = getTotalNumOutputChannels();
    const auto numSamples = buffer.getNumSamples();
    const auto analysisChannels = juce::jmax(1, juce::jmin(totalInputChannels, buffer.getNumChannels()));

    for (int ch = totalInputChannels; ch < totalOutputChannels; ++ch)
        buffer.clear(ch, 0, numSamples);

    juce::AudioPlayHead::PositionInfo position;
    if (auto* playHead = getPlayHead())
        if (auto pos = playHead->getPosition())
            position = *pos;

    const auto transportRunning = position.getIsPlaying() || position.getIsRecording();
    if (! transportRunning)
    {
        analysisTransportRunning.store(false);
        bridgeClient.setTransportRunning(false);
        if (wasTransportRunning)
            resetCaptureWindow();

        wasTransportRunning = false;
        lastPpqPosition = -1.0;
        lastPlayingCallbackMs = 0.0;
        return;
    }

    const auto hostPpq = position.getPpqPosition();
    if (hostPpq.hasValue() && isPreRollPpq(*hostPpq))
    {
        // Treat host count-in/pre-roll as silence for analysis. PPQ zero
        // reopens the client and establishes a fresh capture epoch.
        analysisTransportRunning.store(false);
        bridgeClient.setTransportRunning(false);
        resetCaptureWindow();
        wasTransportRunning = false;
        lastPpqPosition = -1.0;
        lastPlayingCallbackMs = 0.0;
        return;
    }

    auto tempo = defaultTempo;
    if (auto bpm = position.getBpm(); bpm.hasValue())
        tempo = juce::jlimit(20.0, 400.0, *bpm);
    const auto blockBeats = (tempo / 60.0)
        * (static_cast<double>(numSamples) / currentSampleRate);
    const auto nowMs = juce::Time::getMillisecondCounterHiRes();
    bool startsNewCapture = ! wasTransportRunning;
    if (
        wasTransportRunning
        && lastPlayingCallbackMs > 0.0
        && nowMs - lastPlayingCallbackMs > maxPlayingCallbackGapMs
    )
        startsNewCapture = true;
    if (wasTransportRunning && hostPpq.hasValue() && lastPpqPosition >= 0.0)
    {
        // Logic may repeat a coarse PPQ value across many callbacks, then
        // advance it in a larger step. Judge only the observed musical delta:
        // tolerate repeats/jitter/coarse updates and reset on an actual seek.
        if (isMeaningfulPpqDiscontinuity(
                lastPpqPosition, *hostPpq, blockBeats))
            startsNewCapture = true;
    }
    if (startsNewCapture)
    {
        ++captureEpoch;
        activeAnalysisEpoch.store(captureEpoch);
        resetCaptureWindow();
    }
    wasTransportRunning = true;
    analysisTransportRunning.store(true);
    bridgeClient.setTransportRunning(true);

    // PPQ identifies the bar at the start of this block. Close the previous
    // bar before any samples from the newly observed bar enter its window.
    if (hostPpq.hasValue())
        maybeEmitBarFrame(position);

    for (int sample = 0; sample < numSamples; ++sample)
    {
        float mono = 0.0f;
        for (int ch = 0; ch < analysisChannels; ++ch)
            mono += buffer.getReadPointer(ch)[sample];

        pushAnalysisSample(mono / static_cast<float>(analysisChannels));
        ++processedSamples;
        ++samplesSinceLastFallbackBar;
    }

    // Without PPQ, sample count is the only boundary signal and is available
    // only after this block has been accumulated.
    if (! hostPpq.hasValue())
        maybeEmitBarFrame(position);
    lastPpqPosition = hostPpq.hasValue() ? *hostPpq : -1.0;
    lastPlayingCallbackMs = nowMs;
}

juce::AudioProcessorEditor* SessionPlayerListenerAudioProcessor::createEditor()
{
    return new SessionPlayerListenerAudioProcessorEditor(*this);
}

bool SessionPlayerListenerAudioProcessor::hasEditor() const
{
    return true;
}

const juce::String SessionPlayerListenerAudioProcessor::getName() const
{
    return JucePlugin_Name;
}

bool SessionPlayerListenerAudioProcessor::acceptsMidi() const
{
    return false;
}

bool SessionPlayerListenerAudioProcessor::producesMidi() const
{
    return false;
}

bool SessionPlayerListenerAudioProcessor::isMidiEffect() const
{
    return false;
}

double SessionPlayerListenerAudioProcessor::getTailLengthSeconds() const
{
    return 0.0;
}

int SessionPlayerListenerAudioProcessor::getNumPrograms()
{
    return 1;
}

int SessionPlayerListenerAudioProcessor::getCurrentProgram()
{
    return 0;
}

void SessionPlayerListenerAudioProcessor::setCurrentProgram(int)
{
}

const juce::String SessionPlayerListenerAudioProcessor::getProgramName(int)
{
    return {};
}

void SessionPlayerListenerAudioProcessor::changeProgramName(int, const juce::String&)
{
}

void SessionPlayerListenerAudioProcessor::getStateInformation(juce::MemoryBlock&)
{
}

void SessionPlayerListenerAudioProcessor::setStateInformation(const void*, int)
{
}

bool SessionPlayerListenerAudioProcessor::isConnected() const
{
    return bridgeClient.isConnected();
}

juce::String SessionPlayerListenerAudioProcessor::getCurrentKeyText() const
{
    const juce::ScopedLock lock(keyLock);
    return currentKeyText;
}

float SessionPlayerListenerAudioProcessor::getCurrentKeyConfidence() const
{
    return currentKeyConfidence.load();
}

void SessionPlayerListenerAudioProcessor::resetAnalysisState(double sampleRate)
{
    analysisTransportRunning.store(false);
    currentSampleRate = sampleRate > 0.0 ? sampleRate : 44100.0;
    wasTransportRunning = false;
    lastPpqPosition = -1.0;
    lastPlayingCallbackMs = 0.0;
    resetCaptureWindow();
}

void SessionPlayerListenerAudioProcessor::resetCaptureWindow()
{
    processedSamples = 0.0;
    lastEmittedBar = -1;
    fallbackBarIndex = 0;
    activeBarIndex = -1;
    clearBarAnalysisWindow();
}

void SessionPlayerListenerAudioProcessor::clearBarAnalysisWindow()
{
    samplesSinceLastFallbackBar = 0.0;
    writeIndex = 0;
    validSamples = 0;
}

void SessionPlayerListenerAudioProcessor::pushAnalysisSample(float sample)
{
    window[static_cast<size_t>(writeIndex)] = sample;
    writeIndex = (writeIndex + 1) % fftSize;
    validSamples = juce::jmin(fftSize, validSamples + 1);
}

void SessionPlayerListenerAudioProcessor::maybeEmitBarFrame(const juce::AudioPlayHead::PositionInfo& position)
{
    auto tempo = defaultTempo;
    if (auto bpm = position.getBpm(); bpm.hasValue())
        tempo = juce::jlimit(20.0, 400.0, *bpm);

    const auto fallbackBarSamples = currentSampleRate * 60.0 / tempo * beatsPerBar;

    auto barIndex = fallbackBarIndex;
    auto shouldEmit = samplesSinceLastFallbackBar >= fallbackBarSamples;
    auto framePpq = -1.0;

    if (auto ppq = position.getPpqPosition(); ppq.hasValue())
    {
        const auto transition = advanceBar(activeBarIndex, barIndexFromPpq(*ppq));
        activeBarIndex = transition.activeBar;
        barIndex = transition.completedBar;
        shouldEmit = barIndex >= 0;
        if (shouldEmit)
            framePpq = static_cast<double>(barIndex) * beatsPerBar;
    }

    if (! shouldEmit)
        return;

    if (validSamples >= fftSize / 2)
    {
        if (queueCurrentWindow(
                tempo,
                position.getBpm().hasValue() ? 1.0 : 0.25,
                framePpq,
                barIndex,
                position.getIsPlaying() || position.getIsRecording()))
            lastEmittedBar = barIndex;
    }

    if (! position.getPpqPosition().hasValue())
        ++fallbackBarIndex;
    clearBarAnalysisWindow();
}

bool SessionPlayerListenerAudioProcessor::queueCurrentWindow(
    double tempo,
    double tempoConfidence,
    double ppqPosition,
    int barIndex,
    bool playing)
{
    const auto scope = analysisFifo.write(1);
    if (scope.blockSize1 <= 0)
        return false;

    auto& job = analysisJobs[static_cast<size_t>(scope.startIndex1)];
    const auto sampleCount = juce::jlimit(0, fftSize, validSamples);
    const auto leadingZeros = fftSize - sampleCount;
    std::fill_n(job.samples.begin(), leadingZeros, 0.0f);

    if (sampleCount > 0)
    {
        const auto start = (writeIndex + fftSize - sampleCount) % fftSize;
        const auto firstCount = juce::jmin(sampleCount, fftSize - start);
        std::copy_n(
            window.begin() + start,
            firstCount,
            job.samples.begin() + leadingZeros);

        const auto secondCount = sampleCount - firstCount;
        if (secondCount > 0)
            std::copy_n(
                window.begin(),
                secondCount,
                job.samples.begin() + leadingZeros + firstCount);
    }

    job.sampleRate = currentSampleRate;
    job.tempo = tempo;
    job.tempoConfidence = tempoConfidence;
    job.ppqPosition = ppqPosition;
    job.frameStartSeconds = juce::jmax(
        0.0,
        (processedSamples - static_cast<double>(sampleCount)) / currentSampleRate);
    job.durationSeconds = juce::jmax(
        0.001,
        static_cast<double>(sampleCount) / currentSampleRate);
    job.barIndex = barIndex;
    job.captureEpoch = captureEpoch;
    job.playing = playing;

    // ScopedWrite publishes the fully populated POD slot on destruction.
    return true;
}

void SessionPlayerListenerAudioProcessor::run()
{
    while (! threadShouldExit())
    {
        const auto available = juce::jmin(2, analysisFifo.getNumReady());
        if (available <= 0)
        {
            wait(10);
            continue;
        }

        const auto scope = analysisFifo.read(available);
        for (int i = 0; i < scope.blockSize1; ++i)
            processAnalysisJob(
                analysisJobs[static_cast<size_t>(scope.startIndex1 + i)]);
        for (int i = 0; i < scope.blockSize2; ++i)
            processAnalysisJob(
                analysisJobs[static_cast<size_t>(scope.startIndex2 + i)]);
        // ScopedRead releases processed slots when it leaves scope.
    }
}

void SessionPlayerListenerAudioProcessor::processAnalysisJob(
    const AnalysisJob& job)
{
    if (
        ! analysisTransportRunning.load()
        || job.captureEpoch != activeAnalysisEpoch.load()
    )
        return;

    auto frame = analyseWindow(job);

    if (
        ! analysisTransportRunning.load()
        || job.captureEpoch != activeAnalysisEpoch.load()
    )
        return;

    bridgeClient.pushFrame(frame);

    const juce::ScopedLock lock(keyLock);
    currentKeyText = frame.key + " " + frame.scale;
    currentKeyConfidence.store(frame.keyConfidence);
}

HarmonicFrame SessionPlayerListenerAudioProcessor::analyseWindow(
    const AnalysisJob& job)
{
    const auto chroma = extractChroma(job);
    const auto onsetStrength = onsetStrengthForWindow(job.samples);

    auto bestScore = -std::numeric_limits<double>::infinity();
    auto bestTonic = 0;
    auto bestScale = juce::String("major");

    for (int tonic = 0; tonic < 12; ++tonic)
    {
        const auto majorScore = profileCorrelation(chroma, majorProfile, tonic);
        if (majorScore > bestScore)
        {
            bestScore = majorScore;
            bestTonic = tonic;
            bestScale = "major";
        }

        const auto minorScore = profileCorrelation(chroma, minorProfile, tonic);
        if (minorScore > bestScore)
        {
            bestScore = minorScore;
            bestTonic = tonic;
            bestScale = "minor";
        }
    }

    HarmonicFrame frame;
    frame.key = noteNames[static_cast<size_t>(bestTonic)];
    frame.keyPc = bestTonic;
    frame.keyConfidence = static_cast<float>(juce::jlimit(0.0, 1.0, bestScore));
    frame.scale = bestScale;
    frame.scaleConfidence = frame.keyConfidence;
    frame.cadence = onsetStrength < 0.015 ? "sustained" : cadenceFromChroma(chroma, bestTonic);
    frame.cadenceConfidence = static_cast<float>(
        juce::jlimit(0.0, 1.0, onsetStrength < 0.015 ? 0.25 : onsetStrength * 4.0));
    frame.chroma = chroma;
    frame.sampleRate = job.sampleRate;
    frame.tempo = job.tempo;
    frame.tempoConfidence = job.tempoConfidence;
    frame.ppqPosition = job.ppqPosition;
    frame.frameStartSeconds = job.frameStartSeconds;
    frame.durationSeconds = job.durationSeconds;
    frame.barIndex = job.barIndex;
    frame.captureEpoch = job.captureEpoch;
    frame.playing = job.playing;
    return frame;
}

std::array<float, 12> SessionPlayerListenerAudioProcessor::extractChroma(
    const AnalysisJob& job)
{
    fftBuffer.fill(0.0f);

    for (int i = 0; i < fftSize; ++i)
        fftBuffer[static_cast<size_t>(i)] = (
            job.samples[static_cast<size_t>(i)]
            * fftWindow[static_cast<size_t>(i)]
        );

    fft.performFrequencyOnlyForwardTransform(fftBuffer.data(), true);

    std::array<float, 12> chroma {};
    const auto nyquist = job.sampleRate * 0.5;

    for (int bin = 1; bin < fftSize / 2; ++bin)
    {
        const auto frequency = (
            static_cast<double>(bin)
            * job.sampleRate
            / static_cast<double>(fftSize)
        );
        if (frequency < 40.0 || frequency > juce::jmin(5000.0, nyquist))
            continue;

        const auto midi = 69.0 + 12.0 * std::log2(frequency / referencePitch);
        const auto pitchClass = (static_cast<int>(std::llround(midi)) % 12 + 12) % 12;
        const auto magnitude = fftBuffer[static_cast<size_t>(bin)];
        chroma[static_cast<size_t>(pitchClass)] += magnitude * magnitude;
    }

    const auto total = std::accumulate(chroma.begin(), chroma.end(), 0.0f);
    if (total > std::numeric_limits<float>::epsilon())
        for (auto& value : chroma)
            value /= total;

    return chroma;
}

juce::String SessionPlayerListenerAudioProcessor::cadenceFromChroma(const std::array<float, 12>& chroma, int tonic)
{
    const auto strongest = strongestPitchClass(chroma);
    const auto interval = (strongest + 12 - tonic) % 12;

    if (interval == 0)
        return "tonic";
    if (interval == 7)
        return "dominant";
    if (interval == 5)
        return "subdominant";

    return "passing";
}

double SessionPlayerListenerAudioProcessor::onsetStrengthForWindow(const std::array<float, fftSize>& samples)
{
    constexpr int frameSize = 512;
    constexpr int hopSize = 256;

    auto previousEnergy = 0.0;
    auto positiveFlux = 0.0;
    auto frameCount = 0;

    for (int start = 0; start + frameSize <= fftSize; start += hopSize)
    {
        auto energy = 0.0;
        for (int i = 0; i < frameSize; ++i)
        {
            const auto sample = static_cast<double>(samples[static_cast<size_t>(start + i)]);
            energy += sample * sample;
        }

        energy = std::sqrt(energy / static_cast<double>(frameSize));
        if (frameCount > 0)
            positiveFlux += juce::jmax(0.0, energy - previousEnergy);

        previousEnergy = energy;
        ++frameCount;
    }

    return juce::jlimit(0.0, 1.0, positiveFlux * 4.0);
}

} // namespace session_player

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new session_player::SessionPlayerListenerAudioProcessor();
}
