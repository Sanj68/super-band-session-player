#pragma once

#include "PluginProcessor.h"

#include <juce_gui_extra/juce_gui_extra.h>

namespace session_player
{

class SessionPlayerListenerAudioProcessorEditor final
    : public juce::AudioProcessorEditor,
      private juce::Timer
{
public:
    explicit SessionPlayerListenerAudioProcessorEditor(SessionPlayerListenerAudioProcessor& processor);
    ~SessionPlayerListenerAudioProcessorEditor() override;

    void paint(juce::Graphics& g) override;
    void resized() override;

private:
    void timerCallback() override;

    SessionPlayerListenerAudioProcessor& audioProcessor;
    juce::Label titleLabel;
    juce::Label keyLabel;
    bool connected = false;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(SessionPlayerListenerAudioProcessorEditor)
};

} // namespace session_player
