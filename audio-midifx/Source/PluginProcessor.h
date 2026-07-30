#pragma once

#include <juce_audio_utils/juce_audio_utils.h>

#include <atomic>
#include <cstdint>
#include <memory>
#include <vector>

// Session Player Bass — Logic MIDI FX (BUILD_NOTES §6b, v0.6 chassis).
//
// Sits in the MIDI FX slot of an instrument channel. Polls the local
// Session Player engine for the current bass part and plays it in sync
// with the host transport (tempo-following: note times are stored in
// beats, the host clock decides what a beat is). Style / lock-to-groove
// live here as parameters; Regenerate posts them to the engine.

struct BassPartNote
{
    int pitch = 36;
    int velocity = 90;
    double startBeats = 0.0;
    double durBeats = 0.5;
};

struct BassPartAutomationEvent
{
    enum class Type
    {
        controlChange,
        pitchBend,
    };

    Type type = Type::controlChange;
    int channel = 1;
    int controller = 0;
    int value = 0;
    double beat = 0.0;
};

struct BassPart
{
    int version = 1;
    juce::String sessionId;
    juce::String key;
    juce::String scale;
    juce::String bassStyle { "supportive" };
    juce::String bassPlayer { "none" };
    int barCount = 0;
    int beatsPerBar = 4;
    juce::String preview;
    juce::String bassInstrument { "finger_bass" };
    juce::String bassArticulationFocus { "natural" };
    juce::String bassArticulationEffective { "natural" };
    juce::String bassArticulationNotice;
    float bassDensityBias = 0.0f;
    float lockToGroove = 0.5f;
    float bassExpression = 0.5f;
    float ghostAmount = 0.0f;
    float muteAmount = 0.0f;
    float slideAmount = 0.0f;
    float legatoAmount = 0.0f;
    float timingHumanize = 0.5f;
    float velocityHumanize = 0.5f;
    juce::String bassPerformanceControlsNotice;
    bool grooveSourceStatusKnown = false;
    bool grooveSourceReady = false;
    int grooveSourceFrameCount = 0;
    juce::String grooveSourceNotice;
    double phaseOffsetBeats = 0.0;
    int outputTransposeSemitones = 0;
    std::vector<BassPartNote> notes;
    std::vector<BassPartAutomationEvent> automation;
    std::uint64_t playbackFingerprint = 0;
    std::uint64_t revision = 0;

    double loopBeats() const { return juce::jmax(1, barCount) * (double) beatsPerBar; }
};

class SessionPlayerMidiFXProcessor : public juce::AudioProcessor,
                                     private juce::Thread
{
public:
    SessionPlayerMidiFXProcessor();
    ~SessionPlayerMidiFXProcessor() override;

    // AudioProcessor
    void prepareToPlay (double sampleRate, int samplesPerBlock) override;
    void releaseResources() override {}
    void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }

    const juce::String getName() const override { return "Session Player Bass"; }
    bool acceptsMidi() const override { return true; }
    bool producesMidi() const override { return true; }
    bool isMidiEffect() const override { return true; }
    double getTailLengthSeconds() const override { return 0.0; }

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram (int) override {}
    const juce::String getProgramName (int) override { return {}; }
    void changeProgramName (int, const juce::String&) override {}

    void getStateInformation (juce::MemoryBlock& destData) override;
    void setStateInformation (const void* data, int sizeInBytes) override;

    // engine I/O (editor calls these)
    void requestRegenerate();
    void requestNewBeatReset();
    void requestCommand (const juce::String& text);
    void requestKeep();
    void requestHistoryStep (int direction);
    juce::String statusText() const;
    juce::String adviceText() const;
    juce::String historyText() const;
    bool canRecallEarlier() const { return canRecallEarlier_.load(); }
    bool canRecallLater() const { return canRecallLater_.load(); }

    juce::AudioProcessorValueTreeState apvts;

    static const juce::StringArray styleChoices;
    static const juce::StringArray playerChoices;
    static const juce::StringArray playerEngineIds;
    static const juce::StringArray instrumentChoices;
    static const juce::StringArray instrumentEngineIds;
    static const juce::StringArray touchChoices;
    static const juce::StringArray touchEngineIds;

private:
    // polling thread
    void run() override;
    void fetchPart (bool updateStatus = true);
    void fetchAdvice();
    void fetchHistory();
    void hydrateParametersFromPart (const BassPart& part);
    std::shared_ptr<const BassPart> currentPart() const;
    void retirePart (std::shared_ptr<const BassPart> part);
    void collectRetiredParts();
    juce::String boundSessionId() const;

    std::shared_ptr<const BassPart> part_;
    mutable juce::SpinLock partLock_;
    juce::CriticalSection retiredPartsLock_;
    std::vector<std::shared_ptr<const BassPart>> retiredParts_;
    std::atomic<bool> parametersHydratedFromPart_ { false };
    std::atomic<std::uint64_t> partRevisionCounter_ { 0 };

    // 0 = none, 1 = ordinary regenerate, 2 = force a genuinely new phrase.
    // A single atomic preserves last-click semantics without letting the
    // force flag from one request leak into another request.
    std::atomic<int> regenerateRequestMode_ { 0 };
    std::atomic<bool> keepRequested_ { false };
    std::atomic<int> historyStepRequested_ { 0 };
    std::atomic<bool> transportRunning_ { false };
    std::atomic<bool> refreshRequested_ { true };
    std::atomic<bool> canRecallEarlier_ { false };
    std::atomic<bool> canRecallLater_ { false };
    juce::String pendingCommand_;
    juce::CriticalSection commandLock_;
    juce::CriticalSection statusLock_;
    juce::CriticalSection adviceLock_;
    juce::CriticalSection historyLock_;
    mutable juce::CriticalSection sessionLock_;
    juce::String status_ { "connecting to engine..." };
    juce::String advice_ { "Listening to the current source..." };
    juce::String articulationNotice_;
    juce::String performanceControlsNotice_;
    juce::String grooveSourceNotice_;
    juce::String history_ { "No saved ideas yet" };
    juce::String boundSessionId_;
    std::uint64_t sessionBindingEpoch_ = 0;
    juce::String apiBaseUrl_;
    std::atomic<double> latestValidHostTempo_ { 0.0 };

    // playback state
    double sampleRate_ = 44100.0;
    double lastPpq_ = -1.0;
    double lastBlockBeats_ = 0.0;
    std::uint64_t activePartRevision_ = 0;
    bool wasPlaying_ = false;
    struct ActiveNote { int pitch; int samplesLeft; };
    std::vector<ActiveNote> active_;
    struct DueNoteOn
    {
        int noteIndex;
        double offsetBeats;
        int samplePos;
    };
    std::vector<DueNoteOn> dueNoteOns_;
    struct DueAutomation
    {
        int eventIndex;
        double offsetBeats;
        int samplePos;
    };
    std::vector<DueAutomation> dueAutomation_;

    void allNotesOff (juce::MidiBuffer& midi, int samplePos);
    void resetExpression (juce::MidiBuffer& midi, int samplePos);
    void resetPlaybackState (juce::MidiBuffer& midi, int samplePos);
    void setStatus (const juce::String& s);

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SessionPlayerMidiFXProcessor)
};
