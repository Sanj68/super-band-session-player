#pragma once

#include "PluginProcessor.h"

// Style options, one knob, one button, an honest status line — the whole
// product surface (BUILD_NOTES §6b: "all I want to see on the plug in -
// style options").
class SessionPlayerMidiFXEditor : public juce::AudioProcessorEditor,
                                  private juce::Timer
{
public:
    explicit SessionPlayerMidiFXEditor (SessionPlayerMidiFXProcessor&);
    ~SessionPlayerMidiFXEditor() override = default;

    void paint (juce::Graphics&) override;
    void resized() override;

private:
    void timerCallback() override;

    SessionPlayerMidiFXProcessor& processor_;

    juce::ComboBox styleBox_;
    juce::ComboBox playerBox_;
    juce::Slider lockSlider_;
    juce::Label lockLabel_;
    juce::TextButton regenerateButton_ { "Regenerate" };
    juce::Label statusLabel_;

    std::unique_ptr<juce::AudioProcessorValueTreeState::ComboBoxAttachment> styleAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::ComboBoxAttachment> playerAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> lockAttach_;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SessionPlayerMidiFXEditor)
};
