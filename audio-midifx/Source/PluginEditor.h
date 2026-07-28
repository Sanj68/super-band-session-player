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
    void updateTouchAvailability();
    void applyTouchMacro();
    void applyExpressiveFusionPreset();

    SessionPlayerMidiFXProcessor& processor_;

    juce::ComboBox styleBox_;
    juce::ComboBox touchBox_;
    juce::Label touchLabel_;
    juce::ComboBox instrumentBox_;
    juce::Label instrumentLabel_;
    juce::Slider activitySlider_;
    juce::Label activityLabel_;
    juce::Slider lockSlider_;
    juce::Label lockLabel_;
    juce::Slider expressionSlider_;
    juce::Label expressionLabel_;
    juce::Slider ghostSlider_;
    juce::Label ghostLabel_;
    juce::Slider muteSlider_;
    juce::Label muteLabel_;
    juce::Slider slideSlider_;
    juce::Label slideLabel_;
    juce::Slider legatoSlider_;
    juce::Label legatoLabel_;
    juce::Slider timingSlider_;
    juce::Label timingLabel_;
    juce::Slider dynamicsSlider_;
    juce::Label dynamicsLabel_;
    juce::TextButton expressiveFusionButton_ { "EXPRESSIVE FUSION" };
    juce::TextButton regenerateButton_ { "Generate idea" };
    juce::TextButton newBeatResetButton_ { "NEW BEAT / RESET" };
    juce::TextButton earlierButton_ { "Earlier" };
    juce::TextButton keepButton_ { "KEEP" };
    juce::TextButton laterButton_ { "Later" };
    juce::TextButton advancedButton_ { "Advanced" };
    juce::TextEditor commandBox_;
    juce::TextButton sendButton_ { "Say it" };
    juce::Label adviceLabel_;
    juce::Label historyLabel_;
    juce::Label statusLabel_;

    std::unique_ptr<juce::AudioProcessorValueTreeState::ComboBoxAttachment> styleAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::ComboBoxAttachment> touchAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::ComboBoxAttachment> instrumentAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> activityAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> lockAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> expressionAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> ghostAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> muteAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> slideAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> legatoAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> timingAttach_;
    std::unique_ptr<juce::AudioProcessorValueTreeState::SliderAttachment> dynamicsAttach_;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (SessionPlayerMidiFXEditor)
};
