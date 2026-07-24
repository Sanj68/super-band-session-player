#include "PluginEditor.h"

SessionPlayerMidiFXEditor::SessionPlayerMidiFXEditor (SessionPlayerMidiFXProcessor& p)
    : juce::AudioProcessorEditor (p), processor_ (p)
{
    styleBox_.addItemList (SessionPlayerMidiFXProcessor::styleChoices, 1);
    addAndMakeVisible (styleBox_);
    styleAttach_ = std::make_unique<juce::AudioProcessorValueTreeState::ComboBoxAttachment> (
        processor_.apvts, "style", styleBox_);

    lockSlider_.setSliderStyle (juce::Slider::RotaryHorizontalVerticalDrag);
    lockSlider_.setTextBoxStyle (juce::Slider::TextBoxBelow, false, 64, 18);
    addAndMakeVisible (lockSlider_);
    lockAttach_ = std::make_unique<juce::AudioProcessorValueTreeState::SliderAttachment> (
        processor_.apvts, "lock", lockSlider_);

    lockLabel_.setText ("LOCK TO GROOVE", juce::dontSendNotification);
    lockLabel_.setJustificationType (juce::Justification::centred);
    lockLabel_.setFont (juce::Font (juce::FontOptions (12.0f, juce::Font::bold)));
    addAndMakeVisible (lockLabel_);

    expressionSlider_.setSliderStyle (juce::Slider::RotaryHorizontalVerticalDrag);
    expressionSlider_.setTextBoxStyle (juce::Slider::TextBoxBelow, false, 64, 18);
    addAndMakeVisible (expressionSlider_);
    expressionAttach_ = std::make_unique<juce::AudioProcessorValueTreeState::SliderAttachment> (
        processor_.apvts, "expression", expressionSlider_);

    expressionLabel_.setText ("RESTRAINED  ·  BOLD", juce::dontSendNotification);
    expressionLabel_.setJustificationType (juce::Justification::centred);
    expressionLabel_.setFont (juce::Font (juce::FontOptions (12.0f, juce::Font::bold)));
    addAndMakeVisible (expressionLabel_);

    regenerateButton_.onClick = [this] { processor_.requestRegenerate(); };
    addAndMakeVisible (regenerateButton_);

    advancedButton_.setClickingTogglesState (true);
    advancedButton_.onClick = [this] { setAdvancedVisible (advancedButton_.getToggleState()); };
    addAndMakeVisible (advancedButton_);

    commandBox_.setTextToShowWhenEmpty ("describe a change... (more space · redo bar 2)",
                                        juce::Colours::grey);
    commandBox_.setFont (juce::Font (juce::FontOptions (13.0f)));
    commandBox_.onReturnKey = [this]
    {
        processor_.requestCommand (commandBox_.getText());
        commandBox_.clear();
    };
    addAndMakeVisible (commandBox_);
    sendButton_.onClick = [this]
    {
        processor_.requestCommand (commandBox_.getText());
        commandBox_.clear();
    };
    addAndMakeVisible (sendButton_);

    statusLabel_.setJustificationType (juce::Justification::centredLeft);
    statusLabel_.setFont (juce::Font (juce::FontOptions (12.0f)));
    statusLabel_.setColour (juce::Label::textColourId, juce::Colours::lightgrey);
    addAndMakeVisible (statusLabel_);

    setAdvancedVisible (false);
    startTimerHz (4);
}

void SessionPlayerMidiFXEditor::setAdvancedVisible (bool shouldShow)
{
    commandBox_.setVisible (shouldShow);
    sendButton_.setVisible (shouldShow);
    setSize (460, shouldShow ? 270 : 228);
    resized();
}

void SessionPlayerMidiFXEditor::timerCallback()
{
    statusLabel_.setText (processor_.statusText(), juce::dontSendNotification);
}

void SessionPlayerMidiFXEditor::paint (juce::Graphics& g)
{
    g.fillAll (juce::Colour (0xff14141c));
    g.setColour (juce::Colours::white);
    g.setFont (juce::Font (juce::FontOptions (17.0f, juce::Font::bold)));
    g.drawText ("SESSION PLAYER  ·  BASS", 16, 12, getWidth() - 32, 22,
                juce::Justification::centredLeft);
    g.setColour (juce::Colour (0xff6366f1));
    g.fillRect (16, 36, getWidth() - 32, 2);
}

void SessionPlayerMidiFXEditor::resized()
{
    auto area = getLocalBounds().reduced (16);
    area.removeFromTop (32); // title zone

    auto controlRow = area.removeFromTop (112);
    auto left = controlRow.removeFromLeft (controlRow.getWidth() / 2);
    styleBox_.setBounds (left.removeFromTop (30).reduced (4, 1));
    regenerateButton_.setBounds (left.removeFromTop (36).reduced (4, 3));
    advancedButton_.setBounds (left.removeFromTop (32).reduced (4, 3));

    auto expressionArea = controlRow.removeFromLeft (controlRow.getWidth() / 2);
    expressionLabel_.setBounds (expressionArea.removeFromTop (18));
    expressionSlider_.setBounds (expressionArea.reduced (6, 0));
    lockLabel_.setBounds (controlRow.removeFromTop (18));
    lockSlider_.setBounds (controlRow.reduced (6, 0));

    statusLabel_.setBounds (area.removeFromBottom (22));
    if (advancedButton_.getToggleState())
    {
        auto commandRow = area.removeFromBottom (34);
        sendButton_.setBounds (commandRow.removeFromRight (64).reduced (2));
        commandBox_.setBounds (commandRow.reduced (2));
    }
}
