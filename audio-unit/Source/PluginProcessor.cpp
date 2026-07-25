#include "PluginProcessor.h"

#include <cstdlib>
#include <cmath>
#include <utility>

namespace session_player
{
namespace
{
constexpr auto pluginVersion = "0.1.0";
constexpr double frameDurationSeconds = 0.125;
constexpr double defaultTempo = 120.0;
constexpr double fourFourBeatsPerBar = 4.0;

double coefficientForCutoff(double cutoffHz, double sampleRate)
{
    const auto clampedCutoff = juce::jlimit(20.0, sampleRate * 0.45, cutoffHz);
    return std::exp(-2.0 * juce::MathConstants<double>::pi * clampedCutoff / sampleRate);
}

float unitFromEnergy(double meanSquare)
{
    const auto rms = std::sqrt(juce::jmax(0.0, meanSquare));
    return static_cast<float>(juce::jlimit(0.0, 1.0, rms));
}

int barIndexFromPpq(double ppqPosition)
{
    return juce::jmax(0, static_cast<int>(std::floor(ppqPosition / fourFourBeatsPerBar)));
}

juce::String getEnvironment(const char* name)
{
    if (const char* raw = std::getenv(name))
        return juce::String(raw).trim();

    return {};
}
} // namespace

BridgeClient::BridgeClient()
    : Thread("Session Player Bridge Client"),
      pluginInstanceId(makePluginInstanceId())
{
}

BridgeClient::~BridgeClient()
{
    stop();
}

void BridgeClient::start()
{
    running.store(true);
    startThread();
}

void BridgeClient::stop()
{
    running.store(false);
    signalThreadShouldExit();
    stopThread(2000);
}

bool BridgeClient::pushFrame(const FeatureFrame& frame)
{
    const auto scope = fifo.write(1);
    if (scope.blockSize1 <= 0)
        return false;

    frames[static_cast<size_t>(scope.startIndex1)] = frame;
    fifo.finishedWrite(1);
    return true;
}

void BridgeClient::run()
{
    auto config = loadConfig();
    auto lastConfigReload = juce::Time::getMillisecondCounterHiRes();
    auto lastHeartbeat = 0.0;

    while (! threadShouldExit() && running.load())
    {
        const auto now = juce::Time::getMillisecondCounterHiRes();
        if (now - lastConfigReload > 3000.0)
        {
            auto refreshed = loadConfig();
            if (refreshed.sessionId.isEmpty())
                refreshed.sessionId = config.sessionId;
            config = std::move(refreshed);
            lastConfigReload = now;
        }

        if (now - lastHeartbeat > 2000.0)
        {
            postHeartbeat(config);
            lastHeartbeat = now;
        }

        std::vector<FeatureFrame> batch;
        batch.reserve(32);
        const auto available = juce::jmin(32, fifo.getNumReady());
        const auto scope = fifo.read(available);

        for (int i = 0; i < scope.blockSize1; ++i)
            batch.push_back(frames[static_cast<size_t>(scope.startIndex1 + i)]);
        for (int i = 0; i < scope.blockSize2; ++i)
            batch.push_back(frames[static_cast<size_t>(scope.startIndex2 + i)]);

        fifo.finishedRead(static_cast<int>(batch.size()));

        if (! batch.empty())
            postFrames(config, batch);

        wait(50);
    }
}

BridgeConfig BridgeClient::loadConfig() const
{
    BridgeConfig config;

    const auto configFile = juce::File::getSpecialLocation(juce::File::userApplicationDataDirectory)
        .getChildFile("Application Support")
        .getChildFile("Session Player Bridge")
        .getChildFile("config.json");

    if (configFile.existsAsFile())
    {
        if (const auto parsed = juce::JSON::parse(configFile); parsed.isObject())
        {
            const auto* obj = parsed.getDynamicObject();
            config.apiBaseUrl = obj->getProperty("api_base_url").toString().trim();
            config.sessionId = obj->getProperty("session_id").toString().trim();
            config.sourceId = obj->getProperty("source_id").toString().trim();
        }
    }

    if (config.apiBaseUrl.isEmpty())
        config.apiBaseUrl = "http://127.0.0.1:8000/api/bridge";
    if (config.sourceId.isEmpty())
        config.sourceId = "logic-live";

    if (const auto envUrl = getEnvironment("SESSION_PLAYER_BRIDGE_URL"); envUrl.isNotEmpty())
        config.apiBaseUrl = envUrl;
    if (const auto envSession = getEnvironment("SESSION_PLAYER_SESSION_ID"); envSession.isNotEmpty())
        config.sessionId = envSession;
    if (const auto envSource = getEnvironment("SESSION_PLAYER_SOURCE_ID"); envSource.isNotEmpty())
        config.sourceId = envSource;

    config.apiBaseUrl = config.apiBaseUrl.trim().trimCharactersAtEnd("/");
    return config;
}

void BridgeClient::postHeartbeat(BridgeConfig& config)
{
    auto body = juce::String("{\"plugin_instance_id\":\"") + jsonEscape(pluginInstanceId)
        + "\",\"plugin_version\":\"" + pluginVersion + "\"";

    if (config.sessionId.isNotEmpty())
        body += ",\"session_id\":\"" + jsonEscape(config.sessionId) + "\"";
    if (config.sourceId.isNotEmpty())
        body += ",\"source_id\":\"" + jsonEscape(config.sourceId) + "\"";

    body += "}";
    juce::String responseBody;
    if (! postJson(config.apiBaseUrl + "/heartbeat", body, &responseBody))
        return;

    const auto response = juce::JSON::parse(responseBody);
    if (! response.isObject())
        return;

    const auto resolvedSessionId = response.getProperty("session_id", "").toString().trim();
    if (config.sessionId.isEmpty() && resolvedSessionId.isNotEmpty())
        config.sessionId = resolvedSessionId;
}

void BridgeClient::postFrames(const BridgeConfig& config, const std::vector<FeatureFrame>& batch)
{
    if (config.sessionId.isEmpty())
        return;

    juce::String body("[");
    for (size_t i = 0; i < batch.size(); ++i)
    {
        if (i != 0)
            body += ",";
        body += frameToJson(config, pluginInstanceId, batch[i]);
    }
    body += "]";

    postJson(config.apiBaseUrl + "/sessions/" + juce::URL::addEscapeChars(config.sessionId, true) + "/source-frames", body);
}

juce::String BridgeClient::makePluginInstanceId()
{
    return "logic-au-" + juce::Uuid().toString();
}

juce::String BridgeClient::jsonEscape(const juce::String& text)
{
    const auto escaped = juce::JSON::escapeString(text);
    if (escaped.startsWithChar('"') && escaped.endsWithChar('"'))
        return escaped.substring(1, escaped.length() - 1);

    return escaped;
}

juce::String BridgeClient::frameToJson(const BridgeConfig& config, const juce::String& pluginId, const FeatureFrame& frame)
{
    const auto barPosition = (frame.ppqPosition / fourFourBeatsPerBar) - static_cast<double>(frame.barIndex);
    return juce::String("{")
        + "\"plugin_instance_id\":\"" + jsonEscape(pluginId) + "\","
        + "\"session_id\":\"" + jsonEscape(config.sessionId) + "\","
        + "\"source_id\":\"" + jsonEscape(config.sourceId) + "\","
        + "\"sample_rate\":" + juce::String(frame.sampleRate, 1) + ","
        + "\"host_tempo\":" + juce::String(frame.hostTempo, 3) + ","
        + "\"tempo\":" + juce::String(frame.hostTempo, 3) + ","
        + "\"playing\":" + (frame.playing ? "true" : "false") + ","
        + "\"ppq_position\":" + juce::String(frame.ppqPosition, 6) + ","
        + "\"bar_position\":" + juce::String(barPosition, 6) + ","
        + "\"bar_index\":" + juce::String(frame.barIndex) + ","
        + "\"frame_start_seconds\":" + juce::String(frame.frameStartSeconds, 6) + ","
        + "\"duration_seconds\":" + juce::String(frame.durationSeconds, 6) + ","
        + "\"rms\":" + juce::String(frame.rms, 6) + ","
        + "\"band_energy\":{\"low\":" + juce::String(frame.lowBandEnergy, 6)
        + ",\"mid\":" + juce::String(frame.midBandEnergy, 6)
        + ",\"high\":" + juce::String(frame.highBandEnergy, 6) + "},"
        + "\"low_band_energy\":" + juce::String(frame.lowBandEnergy, 6) + ","
        + "\"mid_band_energy\":" + juce::String(frame.midBandEnergy, 6) + ","
        + "\"high_band_energy\":" + juce::String(frame.highBandEnergy, 6) + ","
        + "\"onset_strength\":" + juce::String(frame.onsetStrength, 6)
        + "}";
}

bool BridgeClient::postJson(
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

SessionPlayerBridgeAudioProcessor::SessionPlayerBridgeAudioProcessor()
    : AudioProcessor(BusesProperties()
          .withInput("Input", juce::AudioChannelSet::stereo(), true)
          .withOutput("Output", juce::AudioChannelSet::stereo(), true))
{
    bridgeClient.start();
}

SessionPlayerBridgeAudioProcessor::~SessionPlayerBridgeAudioProcessor()
{
    bridgeClient.stop();
}

void SessionPlayerBridgeAudioProcessor::prepareToPlay(double sampleRate, int)
{
    resetAnalysisState(sampleRate);
}

void SessionPlayerBridgeAudioProcessor::releaseResources()
{
}

bool SessionPlayerBridgeAudioProcessor::isBusesLayoutSupported(const BusesLayout& layouts) const
{
    const auto& input = layouts.getMainInputChannelSet();
    const auto& output = layouts.getMainOutputChannelSet();
    return input == output && (input == juce::AudioChannelSet::mono() || input == juce::AudioChannelSet::stereo());
}

void SessionPlayerBridgeAudioProcessor::processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midiMessages)
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

    for (int sample = 0; sample < numSamples; ++sample)
    {
        float mono = 0.0f;
        for (int ch = 0; ch < analysisChannels; ++ch)
            mono += buffer.getReadPointer(ch)[sample];
        mono /= static_cast<float>(analysisChannels);

        accumulateSample(mono);
        ++accumulatedSamples;
        ++processedSamples;

        if (accumulatedSamples >= frameHopSamples)
            emitFrameFromAccumulator(accumulatedSamples, position);
    }
}

juce::AudioProcessorEditor* SessionPlayerBridgeAudioProcessor::createEditor()
{
    return nullptr;
}

bool SessionPlayerBridgeAudioProcessor::hasEditor() const
{
    return false;
}

const juce::String SessionPlayerBridgeAudioProcessor::getName() const
{
    return JucePlugin_Name;
}

bool SessionPlayerBridgeAudioProcessor::acceptsMidi() const
{
    return false;
}

bool SessionPlayerBridgeAudioProcessor::producesMidi() const
{
    return false;
}

bool SessionPlayerBridgeAudioProcessor::isMidiEffect() const
{
    return false;
}

double SessionPlayerBridgeAudioProcessor::getTailLengthSeconds() const
{
    return 0.0;
}

int SessionPlayerBridgeAudioProcessor::getNumPrograms()
{
    return 1;
}

int SessionPlayerBridgeAudioProcessor::getCurrentProgram()
{
    return 0;
}

void SessionPlayerBridgeAudioProcessor::setCurrentProgram(int)
{
}

const juce::String SessionPlayerBridgeAudioProcessor::getProgramName(int)
{
    return {};
}

void SessionPlayerBridgeAudioProcessor::changeProgramName(int, const juce::String&)
{
}

void SessionPlayerBridgeAudioProcessor::getStateInformation(juce::MemoryBlock&)
{
}

void SessionPlayerBridgeAudioProcessor::setStateInformation(const void*, int)
{
}

void SessionPlayerBridgeAudioProcessor::resetAnalysisState(double sampleRate)
{
    currentSampleRate = sampleRate > 0.0 ? sampleRate : 44100.0;
    frameHopSamples = juce::jmax(1, static_cast<int>(std::round(currentSampleRate * frameDurationSeconds)));
    accumulatedSamples = 0;
    processedSamples = 0.0;
    lowState = 0.0;
    highLowpassState = 0.0;
    lowCoeff = coefficientForCutoff(200.0, currentSampleRate);
    highCoeff = coefficientForCutoff(2000.0, currentSampleRate);
    fullEnergy = 0.0;
    lowEnergy = 0.0;
    midEnergy = 0.0;
    highEnergy = 0.0;
    previousRms = 0.0;
}

void SessionPlayerBridgeAudioProcessor::accumulateSample(float x)
{
    const auto sample = static_cast<double>(x);
    lowState = (lowCoeff * lowState) + ((1.0 - lowCoeff) * sample);
    highLowpassState = (highCoeff * highLowpassState) + ((1.0 - highCoeff) * sample);

    const auto low = lowState;
    const auto high = sample - highLowpassState;
    const auto mid = sample - low - high;

    fullEnergy += sample * sample;
    lowEnergy += low * low;
    midEnergy += mid * mid;
    highEnergy += high * high;
}

void SessionPlayerBridgeAudioProcessor::emitFrameFromAccumulator(
    int numSamples,
    const juce::AudioPlayHead::PositionInfo& position)
{
    const auto denom = static_cast<double>(juce::jmax(1, numSamples));
    const auto rms = unitFromEnergy(fullEnergy / denom);
    const auto onset = static_cast<float>(juce::jlimit(0.0, 1.0, (static_cast<double>(rms) - previousRms) * 8.0));
    previousRms = rms;

    auto tempo = defaultTempo;
    if (auto bpm = position.getBpm(); bpm.hasValue())
        tempo = juce::jlimit(20.0, 400.0, *bpm);

    auto ppq = (processedSamples - numSamples) / currentSampleRate * tempo / 60.0;
    if (auto hostPpq = position.getPpqPosition(); hostPpq.hasValue())
        ppq = *hostPpq;

    FeatureFrame frame;
    frame.sampleRate = currentSampleRate;
    frame.hostTempo = tempo;
    frame.ppqPosition = juce::jmax(0.0, ppq);
    frame.frameStartSeconds = (processedSamples - numSamples) / currentSampleRate;
    frame.durationSeconds = static_cast<double>(numSamples) / currentSampleRate;
    frame.rms = rms;
    frame.lowBandEnergy = unitFromEnergy(lowEnergy / denom);
    frame.midBandEnergy = unitFromEnergy(midEnergy / denom);
    frame.highBandEnergy = unitFromEnergy(highEnergy / denom);
    frame.onsetStrength = onset;
    frame.barIndex = barIndexFromPpq(frame.ppqPosition);
    frame.playing = position.getIsPlaying();

    bridgeClient.pushFrame(frame);

    accumulatedSamples = 0;
    fullEnergy = 0.0;
    lowEnergy = 0.0;
    midEnergy = 0.0;
    highEnergy = 0.0;
}

} // namespace session_player

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new session_player::SessionPlayerBridgeAudioProcessor();
}
