#include "PluginProcessor.h"
#include "PluginEditor.h"

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
    fifo.finishedWrite(1);
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

        const auto available = juce::jmin(8, fifo.getNumReady());
        const auto scope = fifo.read(available);

        for (int i = 0; i < scope.blockSize1; ++i)
            batch.push_back(frames[static_cast<size_t>(scope.startIndex1 + i)]);
        for (int i = 0; i < scope.blockSize2; ++i)
            batch.push_back(frames[static_cast<size_t>(scope.startIndex2 + i)]);

        fifo.finishedRead(static_cast<int>(batch.size()));

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

SessionPlayerListenerAudioProcessor::SessionPlayerListenerAudioProcessor()
    : AudioProcessor(BusesProperties()
          .withInput("Input", juce::AudioChannelSet::stereo(), true)
          .withOutput("Output", juce::AudioChannelSet::stereo(), true))
{
    bridgeClient.start();
}

SessionPlayerListenerAudioProcessor::~SessionPlayerListenerAudioProcessor()
{
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
        bridgeClient.setTransportRunning(false);
        if (wasTransportRunning)
            resetCaptureWindow();

        wasTransportRunning = false;
        return;
    }

    if (! wasTransportRunning)
        resetCaptureWindow();
    wasTransportRunning = true;
    bridgeClient.setTransportRunning(true);

    for (int sample = 0; sample < numSamples; ++sample)
    {
        float mono = 0.0f;
        for (int ch = 0; ch < analysisChannels; ++ch)
            mono += buffer.getReadPointer(ch)[sample];

        pushAnalysisSample(mono / static_cast<float>(analysisChannels));
        ++processedSamples;
        ++samplesSinceLastFallbackBar;
    }

    maybeEmitBarFrame(position);
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
    currentSampleRate = sampleRate > 0.0 ? sampleRate : 44100.0;
    wasTransportRunning = false;
    resetCaptureWindow();

    juce::dsp::WindowingFunction<float>::fillWindowingTables(
        fftWindow.data(),
        static_cast<size_t>(fftSize),
        juce::dsp::WindowingFunction<float>::hann,
        false);
}

void SessionPlayerListenerAudioProcessor::resetCaptureWindow()
{
    processedSamples = 0.0;
    samplesSinceLastFallbackBar = 0.0;
    writeIndex = 0;
    validSamples = 0;
    lastEmittedBar = -1;
    fallbackBarIndex = 0;
    window.fill(0.0f);
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

    if (auto ppq = position.getPpqPosition(); ppq.hasValue())
    {
        barIndex = barIndexFromPpq(*ppq);
        shouldEmit = barIndex != lastEmittedBar;
    }

    if (! shouldEmit || validSamples < fftSize / 2)
        return;

    auto frame = analyseCurrentWindow(tempo, barIndex);
    frame.sampleRate = currentSampleRate;
    frame.tempoConfidence = position.getBpm().hasValue() ? 1.0 : 0.25;
    frame.playing = position.getIsPlaying();
    frame.frameStartSeconds = juce::jmax(
        0.0,
        (processedSamples - static_cast<double>(validSamples)) / currentSampleRate);
    frame.durationSeconds = juce::jmax(
        0.001,
        static_cast<double>(validSamples) / currentSampleRate);
    if (auto ppq = position.getPpqPosition(); ppq.hasValue())
        frame.ppqPosition = *ppq;
    bridgeClient.pushFrame(frame);

    {
        const juce::ScopedLock lock(keyLock);
        currentKeyText = frame.key + " " + frame.scale;
        currentKeyConfidence = frame.keyConfidence;
    }

    lastEmittedBar = barIndex;
    if (! position.getPpqPosition().hasValue())
    {
        ++fallbackBarIndex;
        samplesSinceLastFallbackBar = 0.0;
    }
}

HarmonicFrame SessionPlayerListenerAudioProcessor::analyseCurrentWindow(double tempo, int barIndex)
{
    const auto chroma = extractChroma();
    const auto onsetStrength = onsetStrengthForWindow(analysisBuffer);

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
    frame.tempo = tempo;
    frame.barIndex = barIndex;
    return frame;
}

std::array<float, 12> SessionPlayerListenerAudioProcessor::extractChroma()
{
    analysisBuffer.fill(0.0f);
    fftBuffer.fill(0.0f);

    const auto start = (writeIndex + fftSize - validSamples) % fftSize;
    const auto leadingZeros = fftSize - validSamples;

    for (int i = 0; i < validSamples; ++i)
    {
        const auto sourceIndex = (start + i) % fftSize;
        const auto destIndex = leadingZeros + i;
        analysisBuffer[static_cast<size_t>(destIndex)] = window[static_cast<size_t>(sourceIndex)];
    }

    for (int i = 0; i < fftSize; ++i)
        fftBuffer[static_cast<size_t>(i)] = analysisBuffer[static_cast<size_t>(i)] * fftWindow[static_cast<size_t>(i)];

    fft.performFrequencyOnlyForwardTransform(fftBuffer.data(), true);

    std::array<float, 12> chroma {};
    const auto nyquist = currentSampleRate * 0.5;

    for (int bin = 1; bin < fftSize / 2; ++bin)
    {
        const auto frequency = static_cast<double>(bin) * currentSampleRate / static_cast<double>(fftSize);
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
