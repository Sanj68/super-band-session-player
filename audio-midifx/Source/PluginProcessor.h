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
    int barCount = 0;
    int beatsPerBar = 4;
    juce::String preview;
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
    juce::String statusText() const;

    juce::AudioProcessorValueTreeState apvts;

    static const juce::StringArray styleChoices;
    static const juce::StringArray playerChoices;

private:
    // polling thread
    void run() override;
    void fetchPart();
    std::shared_ptr<const BassPart> currentPart() const;

    std::shared_ptr<const BassPart> part_;
    mutable juce::SpinLock partLock_;

    std::atomic<bool> regenerateRequested_ { false };
    juce::CriticalSection statusLock_;
    juce::String status_ { "connecting to engine..." };

    // playback state
    double sampleRate_ = 44100.0;
    double lastPpq_ = -1.0;
    bool wasPlaying_ = false;
    struct ActiveNote { int pitch; int samplesLeft; };
    std::vector<ActiveNote> active_;

    void allNotesOff (juce::MidiBuffer& midi, int samplePos);
    void setStatus (const juce::String& s);

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SessionPlayerMidiFXProcessor)
};
