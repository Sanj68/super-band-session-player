#pragma once

#include <juce_audio_utils/juce_audio_utils.h>

#include <atomic>
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

struct BassPart
{
    juce::String sessionId;
    juce::String key;
    juce::String scale;
    juce::String bassStyle { "supportive" };
    int barCount = 0;
    int beatsPerBar = 4;
    juce::String preview;
    juce::String bassInstrument { "finger_bass" };
    float lockToGroove = 0.5f;
    float bassExpression = 0.5f;
    std::vector<BassPartNote> notes;

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

private:
    // polling thread
    void run() override;
    void fetchPart (bool updateStatus = true);
    void fetchAdvice();
    void fetchHistory();
    void hydrateParametersFromPart (const BassPart& part);
    std::shared_ptr<const BassPart> currentPart() const;
    juce::String boundSessionId() const;
    void bindSession (const juce::String& sessionId, bool replaceExisting = false);

    std::shared_ptr<const BassPart> part_;
    mutable juce::SpinLock partLock_;
    std::atomic<bool> parametersHydratedFromPart_ { false };

    std::atomic<bool> regenerateRequested_ { false };
    std::atomic<bool> keepRequested_ { false };
    std::atomic<int> historyStepRequested_ { 0 };
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
    juce::String history_ { "No saved ideas yet" };
    juce::String boundSessionId_;
    juce::String apiBaseUrl_;

    // playback state
    double sampleRate_ = 44100.0;
    double lastPpq_ = -1.0;
    double lastBlockBeats_ = 0.0;
    bool wasPlaying_ = false;
    struct ActiveNote { int pitch; int samplesLeft; };
    std::vector<ActiveNote> active_;

    void allNotesOff (juce::MidiBuffer& midi, int samplePos);
    void setStatus (const juce::String& s);

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SessionPlayerMidiFXProcessor)
};
