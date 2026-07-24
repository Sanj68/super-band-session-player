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
    void setAdvancedVisible (bool shouldShow);

    SessionPlayerMidiFXProcessor& processor_;

    juce::ComboBox styleBox_;
    juce::ComboBox instrumentBox_;
    juce::Label instrumentLabel_;
    juce::Slider lockSlider_;
    juce::Label lockLabel_;
    juce::Slider expressionSlider_;
    juce::Label expressionLabel_;
    juce::TextButton regenerateButton_ { "Generate idea" };
    juce::TextButton advancedButton_ { "Advanced" };
    juce::TextEditor commandBox_;
    juce::TextButton sendButton_ { "Say it" };
    juce::Label adviceLabel_;
    juce::Label statusLabel_;

    std::unique_ptr<juce::AudioProcessorValueTreeState::ComboBoxAttachment> styleAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::ComboBoxAttachment> instrumentAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> lockAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> expressionAttach_;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SessionPlayerMidiFXEditor)
};
