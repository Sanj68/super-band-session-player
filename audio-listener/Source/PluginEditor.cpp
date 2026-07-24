#include "PluginEditor.h"

namespace session_player
{

SessionPlayerListenerAudioProcessorEditor::SessionPlayerListenerAudioProcessorEditor(
    SessionPlayerListenerAudioProcessor& p)
    : AudioProcessorEditor(&p),
      audioProcessor(p)
{
    titleLabel.setText("Session Player Listener", juce::dontSendNotification);
    titleLabel.setJustificationType(juce::Justification::centredLeft);
    titleLabel.setColour(juce::Label::textColourId, juce::Colours::white);
    titleLabel.setFont(juce::FontOptions(20.0f, juce::Font::bold));
    addAndMakeVisible(titleLabel);

    keyLabel.setText(audioProcessor.getCurrentKeyText(), juce::dontSendNotification);
    keyLabel.setJustificationType(juce::Justification::centredLeft);
    keyLabel.setColour(juce::Label::textColourId, juce::Colour(0xffd8e4f0));
    keyLabel.setFont(juce::FontOptions(28.0f, juce::Font::bold));
    addAndMakeVisible(keyLabel);

    setSize(420, 180);
    startTimerHz(12);
}

SessionPlayerListenerAudioProcessorEditor::~SessionPlayerListenerAudioProcessorEditor()
{
    stopTimer();
}

void SessionPlayerListenerAudioProcessorEditor::paint(juce::Graphics& g)
{
    g.fillAll(juce::Colour(0xff12161c));

    auto bounds = getLocalBounds().toFloat().reduced(18.0f);
    g.setColour(juce::Colour(0xff202833));
    g.fillRoundedRectangle(bounds, 8.0f);

    const auto ledBounds = juce::Rectangle<float>(bounds.getRight() - 36.0f, bounds.getY() + 20.0f, 14.0f, 14.0f);
    g.setColour(connected ? juce::Colour(0xff38d06f) : juce::Colour(0xffd74e4e));
    g.fillEllipse(ledBounds);
    g.setColour(juce::Colour(0x66000000));
    g.drawEllipse(ledBounds, 1.0f);

    g.setColour(juce::Colour(0xff7f8b98));
    g.setFont(juce::FontOptions(13.0f));
    const auto confidence = juce::roundToInt(audioProcessor.getCurrentKeyConfidence() * 100.0f);
    g.drawText(
        "Tentative live estimate - " + juce::String(confidence) + "% confidence",
        34,
        76,
        getWidth() - 68,
        20,
        juce::Justification::centredLeft);
}

void SessionPlayerListenerAudioProcessorEditor::resized()
{
    auto bounds = getLocalBounds().reduced(30);
    titleLabel.setBounds(bounds.removeFromTop(36).reduced(0, 2));
    bounds.removeFromTop(34);
    keyLabel.setBounds(bounds.removeFromTop(44));
}

void SessionPlayerListenerAudioProcessorEditor::timerCallback()
{
    const auto nextConnected = audioProcessor.isConnected();
    keyLabel.setText(audioProcessor.getCurrentKeyText(), juce::dontSendNotification);

    if (connected != nextConnected)
    {
        connected = nextConnected;
        repaint();
    }
}

} // namespace session_player
