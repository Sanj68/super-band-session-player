#pragma once

#include <array>
#include <atomic>
#include <vector>

#include <juce_audio_processors/juce_audio_processors.h>

namespace session_player
{

struct BridgeConfig
{
    juce::String apiBaseUrl = "http://127.0.0.1:8000/api/bridge";
    juce::String sessionId;
    juce::String sourceId = "logic-live";
};

struct FeatureFrame
{
    double sampleRate = 44100.0;
    double hostTempo = 120.0;
    double ppqPosition = 0.0;
    double frameStartSeconds = 0.0;
    double durationSeconds = 0.125;
    float rms = 0.0f;
    float lowBandEnergy = 0.0f;
    float midBandEnergy = 0.0f;
    float highBandEnergy = 0.0f;
    float onsetStrength = 0.0f;
    int barIndex = 0;
    bool playing = false;
};

class BridgeClient final : private juce::Thread
{
public:
    BridgeClient();
    ~BridgeClient() override;

    void start();
    void stop();
    bool pushFrame(const FeatureFrame& frame);

private:
    void run() override;

    BridgeConfig loadConfig() const;
    void postHeartbeat(const BridgeConfig& config);
    void postFrames(const BridgeConfig& config, const std::vector<FeatureFrame>& frames);

    static juce::String makePluginInstanceId();
    static juce::String jsonEscape(const juce::String& text);
    static juce::String frameToJson(const BridgeConfig& config, const juce::String& pluginId, const FeatureFrame& frame);
    static bool postJson(const juce::String& url, const juce::String& body);

    juce::AbstractFifo fifo { 512 };
    std::array<FeatureFrame, 512> frames {};
    juce::String pluginInstanceId;
    std::atomic<bool> running { false };
};

class SessionPlayerBridgeAudioProcessor final : public juce::AudioProcessor
{
public:
    SessionPlayerBridgeAudioProcessor();
    ~SessionPlayerBridgeAudioProcessor() override;

    void prepareToPlay(double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;
    bool isBusesLayoutSupported(const BusesLayout& layouts) const override;
    void processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midiMessages) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override;

    const juce::String getName() const override;
    bool acceptsMidi() const override;
    bool producesMidi() const override;
    bool isMidiEffect() const override;
    double getTailLengthSeconds() const override;

    int getNumPrograms() override;
    int getCurrentProgram() override;
    void setCurrentProgram(int index) override;
    const juce::String getProgramName(int index) override;
    void changeProgramName(int index, const juce::String& newName) override;

    void getStateInformation(juce::MemoryBlock& destData) override;
    void setStateInformation(const void* data, int sizeInBytes) override;

private:
    void resetAnalysisState(double sampleRate);
    void accumulateSample(float x);
    void emitFrameFromAccumulator(int numSamples, const juce::AudioPlayHead::PositionInfo& position);

    BridgeClient bridgeClient;
    double currentSampleRate = 44100.0;
    int frameHopSamples = 5512;
    int accumulatedSamples = 0;
    double processedSamples = 0.0;

    double lowState = 0.0;
    double highLowpassState = 0.0;
    double lowCoeff = 0.0;
    double highCoeff = 0.0;
    double fullEnergy = 0.0;
    double lowEnergy = 0.0;
    double midEnergy = 0.0;
    double highEnergy = 0.0;
    double previousRms = 0.0;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(SessionPlayerBridgeAudioProcessor)
};

} // namespace session_player
