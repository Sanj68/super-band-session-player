#pragma once

#include <array>
#include <atomic>
#include <vector>

#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_dsp/juce_dsp.h>

namespace session_player
{

struct HarmonicFrame
{
    double sampleRate = 44100.0;
    double tempo = 120.0;
    double tempoConfidence = 0.0;
    double ppqPosition = -1.0;
    double frameStartSeconds = 0.0;
    double durationSeconds = 0.125;
    juce::String key = "C";
    int keyPc = 0;
    float keyConfidence = 0.0f;
    juce::String scale = "major";
    float scaleConfidence = 0.0f;
    juce::String cadence = "unknown";
    float cadenceConfidence = 0.0f;
    std::array<float, 12> chroma {};
    int barIndex = 0;
    bool playing = false;
    juce::String sessionId;
};

class HarmonicBridgeClient final : private juce::Thread
{
public:
    HarmonicBridgeClient();
    ~HarmonicBridgeClient() override;

    void start();
    void stop();
    bool pushFrame(const HarmonicFrame& frame);
    bool isConnected() const;

private:
    void run() override;

    static juce::String getEnvironment(const char* name);
    static juce::String getSessionId();
    static juce::String jsonEscape(const juce::String& text);
    static juce::String frameToJson(
        const HarmonicFrame& frame,
        const juce::String& pluginInstanceId);
    static bool postJson(const juce::String& url, const juce::String& body);

    juce::AbstractFifo fifo { 64 };
    std::array<HarmonicFrame, 64> frames {};
    juce::String pluginInstanceId;
    std::atomic<bool> running { false };
    std::atomic<bool> connected { false };
};

class SessionPlayerListenerAudioProcessor final : public juce::AudioProcessor
{
public:
    SessionPlayerListenerAudioProcessor();
    ~SessionPlayerListenerAudioProcessor() override;

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

    bool isConnected() const;
    juce::String getCurrentKeyText() const;

private:
    static constexpr int fftOrder = 13;
    static constexpr int fftSize = 1 << fftOrder;

    void resetAnalysisState(double sampleRate);
    void pushAnalysisSample(float sample);
    void maybeEmitBarFrame(const juce::AudioPlayHead::PositionInfo& position);
    HarmonicFrame analyseCurrentWindow(double tempo, int barIndex);
    std::array<float, 12> extractChroma();
    static juce::String cadenceFromChroma(const std::array<float, 12>& chroma, int tonic);
    static double onsetStrengthForWindow(const std::array<float, fftSize>& samples);

    HarmonicBridgeClient bridgeClient;
    juce::dsp::FFT fft { fftOrder };
    std::array<float, fftSize> window {};
    std::array<float, fftSize> analysisBuffer {};
    std::array<float, fftSize * 2> fftBuffer {};
    std::array<float, fftSize> fftWindow {};

    double currentSampleRate = 44100.0;
    double processedSamples = 0.0;
    double samplesSinceLastFallbackBar = 0.0;
    int writeIndex = 0;
    int validSamples = 0;
    int lastEmittedBar = -1;
    int fallbackBarIndex = 0;

    mutable juce::CriticalSection keyLock;
    juce::String currentKeyText = "C major";

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(SessionPlayerListenerAudioProcessor)
};

} // namespace session_player
