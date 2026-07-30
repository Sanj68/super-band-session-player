#pragma once

#include <array>
#include <atomic>
#include <cstdint>
#include <type_traits>
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
    std::uint64_t captureEpoch = 0;
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
    void setTransportRunning(bool isRunning);
    bool pushFrame(const HarmonicFrame& frame);
    bool isConnected() const;

private:
    void run() override;

    static juce::String getEnvironment(const char* name);
    static juce::String getSessionId();
    static juce::String getApiBaseUrl();
    juce::String resolveLatestSessionId(const juce::String& pluginInstanceId) const;
    static juce::String jsonEscape(const juce::String& text);
    static juce::String frameToJson(
        const HarmonicFrame& frame,
        const juce::String& pluginInstanceId);
    static bool postJson(
        const juce::String& url,
        const juce::String& body,
        juce::String* responseBody = nullptr);

    juce::AbstractFifo fifo { 64 };
    std::array<HarmonicFrame, 64> frames {};
    juce::String pluginInstanceId;
    juce::String apiBaseUrl;
    juce::String boundSessionId;
    std::atomic<bool> running { false };
    std::atomic<bool> transportRunning { false };
    std::atomic<bool> connected { false };
};

class SessionPlayerListenerAudioProcessor final : public juce::AudioProcessor
                                                , private juce::Thread
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
    float getCurrentKeyConfidence() const;

private:
    static constexpr int fftOrder = 13;
    static constexpr int fftSize = 1 << fftOrder;
    static constexpr int analysisQueueCapacity = 4;

    struct AnalysisJob
    {
        std::array<float, fftSize> samples {};
        double sampleRate = 44100.0;
        double tempo = 120.0;
        double tempoConfidence = 0.0;
        double ppqPosition = -1.0;
        double frameStartSeconds = 0.0;
        double durationSeconds = 0.125;
        int barIndex = 0;
        std::uint64_t captureEpoch = 0;
        bool playing = false;
    };

    static_assert(
        std::is_trivially_copyable<AnalysisJob>::value,
        "The audio callback may hand off only fixed-size POD analysis jobs.");
    static_assert(
        std::atomic<std::uint64_t>::is_always_lock_free,
        "Capture-epoch signalling must remain lock-free on the audio callback.");

    void resetAnalysisState(double sampleRate);
    void resetCaptureWindow();
    void clearBarAnalysisWindow();
    void pushAnalysisSample(float sample);
    void maybeEmitBarFrame(const juce::AudioPlayHead::PositionInfo& position);
    bool queueCurrentWindow(
        double tempo,
        double tempoConfidence,
        double ppqPosition,
        int barIndex,
        bool playing);
    void run() override;
    void processAnalysisJob(const AnalysisJob& job);
    HarmonicFrame analyseWindow(const AnalysisJob& job);
    std::array<float, 12> extractChroma(const AnalysisJob& job);
    static juce::String cadenceFromChroma(const std::array<float, 12>& chroma, int tonic);
    static double onsetStrengthForWindow(const std::array<float, fftSize>& samples);

    HarmonicBridgeClient bridgeClient;
    juce::AbstractFifo analysisFifo { analysisQueueCapacity };
    std::array<AnalysisJob, analysisQueueCapacity> analysisJobs {};
    std::atomic<bool> analysisTransportRunning { false };
    std::atomic<std::uint64_t> activeAnalysisEpoch { 0 };
    juce::dsp::FFT fft { fftOrder };
    std::array<float, fftSize> window {};
    std::array<float, fftSize * 2> fftBuffer {};
    std::array<float, fftSize> fftWindow {};

    double currentSampleRate = 44100.0;
    double processedSamples = 0.0;
    double samplesSinceLastFallbackBar = 0.0;
    int writeIndex = 0;
    int validSamples = 0;
    int lastEmittedBar = -1;
    int fallbackBarIndex = 0;
    int activeBarIndex = -1;
    std::uint64_t captureEpoch = 0;
    bool wasTransportRunning = false;
    double lastPpqPosition = -1.0;
    double lastPlayingCallbackMs = 0.0;

    mutable juce::CriticalSection keyLock;
    juce::String currentKeyText = "No estimate yet";
    std::atomic<float> currentKeyConfidence { 0.0f };

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(SessionPlayerListenerAudioProcessor)
};

} // namespace session_player
