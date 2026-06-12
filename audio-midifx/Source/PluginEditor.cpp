#include "PluginEditor.h"

SessionPlayerMidiFXEditor::SessionPlayerMidiFXEditor (SessionPlayerMidiFXProcessor& p)
    : juce::AudioProcessorEditor (p), processor_ (p)
{
    styleBox_.addItemList (SessionPlayerMidiFXProcessor::styleChoices, 1);
    addAndMakeVisible (styleBox_);
    styleAttach_ = std::make_unique<juce::AudioProcessorValueTreeState::ComboBoxAttachment> (
        processor_.apvts, "style", styleBox_);

    playerBox_.addItemList (SessionPlayerMidiFXProcessor::playerChoices, 1);
    addAndMakeVisible (playerBox_);
    playerAttach_ = std::make_unique<juce::AudioProcessorValueTreeState::ComboBoxAttachment> (
        processor_.apvts, "player", playerBox_);

    lockSlider_.setSliderStyle (juce::Slider::RotaryHorizontalVerticalDrag);
    lockSlider_.setTextBoxStyle (juce::Slider::TextBoxBelow, false, 64, 18);
    addAndMakeVisible (lockSlider_);
    lockAttach_ = std::make_unique<juce::AudioProcessorValueTreeState::SliderAttachment> (
        processor_.apvts, "lock", lockSlider_);

    lockLabel_.setText ("LOCK TO GROOVE", juce::dontSendNotification);
    lockLabel_.setJustificationType (juce::Justification::centred);
    lockLabel_.setFont (juce::Font (juce::FontOptions (12.0f, juce::Font::bold)));
    addAndMakeVisible (lockLabel_);

    regenerateButton_.onClick = [this] { processor_.requestRegenerate(); };
    addAndMakeVisible (regenerateButton_);

    // v0.7 prompting layer: "busier" · "like jamerson" · "turnaround on bar 4"
    commandBox_.setTextToShowWhenEmpty ("tell the bassist... (busier · like jamerson · redo bar 2)",
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

    setSize (420, 240);
    startTimerHz (4);
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

    auto row = area.removeFromTop (96);
    auto left = row.removeFromLeft (row.getWidth() / 2);
    styleBox_.setBounds (left.removeFromTop (28).reduced (4, 0));
    playerBox_.setBounds (left.removeFromTop (28).reduced (4, 1));
    regenerateButton_.setBounds (left.removeFromTop (34).reduced (4, 3));

    lockLabel_.setBounds (row.removeFromTop (16));
    lockSlider_.setBounds (row.reduced (8, 0));

    statusLabel_.setBounds (area.removeFromBottom (22));
    auto commandRow = area.removeFromBottom (30);
    sendButton_.setBounds (commandRow.removeFromRight (64).reduced (2));
    commandBox_.setBounds (commandRow.reduced (2));
}
