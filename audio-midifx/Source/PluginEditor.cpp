#include "PluginEditor.h"

SessionPlayerMidiFXEditor::SessionPlayerMidiFXEditor (SessionPlayerMidiFXProcessor& p)
    : juce::AudioProcessorEditor (p), processor_ (p)
{
    styleBox_.addItemList (SessionPlayerMidiFXProcessor::styleChoices, 1);
    addAndMakeVisible (styleBox_);
    styleAttach_ = std::make_unique<juce::AudioProcessorValueTreeState::ComboBoxAttachment> (
        processor_.apvts, "style", styleBox_);

    touchBox_.addItemList (SessionPlayerMidiFXProcessor::touchChoices, 1);
    addAndMakeVisible (touchBox_);
    touchAttach_ = std::make_unique<juce::AudioProcessorValueTreeState::ComboBoxAttachment> (
        processor_.apvts, "touch", touchBox_);
    touchLabel_.setText ("TOUCH", juce::dontSendNotification);
    touchLabel_.setFont (juce::Font (juce::FontOptions (11.0f, juce::Font::bold)));
    addAndMakeVisible (touchLabel_);

    instrumentBox_.addItemList (SessionPlayerMidiFXProcessor::instrumentChoices, 1);
    addAndMakeVisible (instrumentBox_);
    instrumentAttach_ = std::make_unique<juce::AudioProcessorValueTreeState::ComboBoxAttachment> (
        processor_.apvts, "instrument", instrumentBox_);

    instrumentLabel_.setText ("BASS FAMILY", juce::dontSendNotification);
    instrumentLabel_.setFont (juce::Font (juce::FontOptions (11.0f, juce::Font::bold)));
    addAndMakeVisible (instrumentLabel_);

    activitySlider_.setSliderStyle (juce::Slider::LinearHorizontal);
    activitySlider_.setTextBoxStyle (juce::Slider::TextBoxRight, false, 58, 18);
    addAndMakeVisible (activitySlider_);
    activityAttach_ = std::make_unique<juce::AudioProcessorValueTreeState::SliderAttachment> (
        processor_.apvts, "activity", activitySlider_);
    activityLabel_.setText ("ACTIVITY", juce::dontSendNotification);
    activityLabel_.setFont (juce::Font (juce::FontOptions (11.0f, juce::Font::bold)));
    addAndMakeVisible (activityLabel_);

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

    expressionLabel_.setText ("CHARACTER", juce::dontSendNotification);
    expressionLabel_.setJustificationType (juce::Justification::centred);
    expressionLabel_.setFont (juce::Font (juce::FontOptions (12.0f, juce::Font::bold)));
    addAndMakeVisible (expressionLabel_);

    const auto configurePerformanceSlider = [this] (
        juce::Slider& slider,
        juce::Label& label,
        const juce::String& labelText,
        const juce::String& parameterId,
        auto& attachment)
    {
        slider.setSliderStyle (juce::Slider::LinearHorizontal);
        slider.setTextBoxStyle (juce::Slider::TextBoxRight, false, 42, 18);
        addAndMakeVisible (slider);
        label.setText (labelText, juce::dontSendNotification);
        label.setFont (juce::Font (juce::FontOptions (10.0f, juce::Font::bold)));
        addAndMakeVisible (label);
        attachment = std::make_unique<
            juce::AudioProcessorValueTreeState::SliderAttachment> (
                processor_.apvts,
                parameterId,
                slider);
    };
    configurePerformanceSlider (
        ghostSlider_, ghostLabel_, "GHOSTS", "ghost", ghostAttach_);
    configurePerformanceSlider (
        muteSlider_, muteLabel_, "MUTES", "mute", muteAttach_);
    configurePerformanceSlider (
        slideSlider_, slideLabel_, "SLIDES", "slide", slideAttach_);
    configurePerformanceSlider (
        legatoSlider_, legatoLabel_, "LEGATO", "legato", legatoAttach_);
    configurePerformanceSlider (
        timingSlider_, timingLabel_, "TIMING", "timing_humanize", timingAttach_);
    configurePerformanceSlider (
        dynamicsSlider_, dynamicsLabel_, "DYNAMICS", "velocity_humanize", dynamicsAttach_);

    expressiveFusionButton_.setColour (
        juce::TextButton::buttonColourId,
        juce::Colour (0xff4f46a5));
    expressiveFusionButton_.onClick = [this] { applyExpressiveFusionPreset(); };
    addAndMakeVisible (expressiveFusionButton_);

    regenerateButton_.onClick = [this] { processor_.requestRegenerate(); };
    addAndMakeVisible (regenerateButton_);

    newBeatResetButton_.setColour (
        juce::TextButton::buttonColourId,
        juce::Colour (0xff9a3412));
    newBeatResetButton_.setTooltip (
        "Force a genuinely new core phrase for the current beat. "
        "Beat analysis requires Session Player Bridge on the beat track.");
    newBeatResetButton_.onClick = [this] { processor_.requestNewBeatReset(); };
    addAndMakeVisible (newBeatResetButton_);

    earlierButton_.onClick = [this] { processor_.requestHistoryStep (-1); };
    addAndMakeVisible (earlierButton_);
    keepButton_.onClick = [this] { processor_.requestKeep(); };
    keepButton_.setColour (juce::TextButton::buttonColourId, juce::Colour (0xff31405d));
    addAndMakeVisible (keepButton_);
    laterButton_.onClick = [this] { processor_.requestHistoryStep (1); };
    addAndMakeVisible (laterButton_);

    advancedButton_.setClickingTogglesState (true);
    advancedButton_.onClick = [this] { setAdvancedVisible (advancedButton_.getToggleState()); };
    addAndMakeVisible (advancedButton_);

    commandBox_.setTextToShowWhenEmpty ("describe a change... (more space - redo bar 2)",
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

    adviceLabel_.setJustificationType (juce::Justification::centredLeft);
    adviceLabel_.setFont (juce::Font (juce::FontOptions (12.0f)));
    adviceLabel_.setColour (juce::Label::textColourId, juce::Colour (0xffb8c4df));
    addAndMakeVisible (adviceLabel_);

    historyLabel_.setJustificationType (juce::Justification::centredRight);
    historyLabel_.setFont (juce::Font (juce::FontOptions (11.0f)));
    historyLabel_.setColour (juce::Label::textColourId, juce::Colour (0xff9aa7bd));
    addAndMakeVisible (historyLabel_);

    instrumentBox_.onChange = [this] { updateTouchAvailability(); };
    styleBox_.onChange = [this]
    {
        if (touchBox_.getSelectedId() == 1)
            applyTouchMacro();
    };
    touchBox_.onChange = [this]
    {
        updateTouchAvailability();
        applyTouchMacro();
    };
    updateTouchAvailability();
    setAdvancedVisible (false);
    startTimerHz (4);
}

void SessionPlayerMidiFXEditor::setAdvancedVisible (bool shouldShow)
{
    commandBox_.setVisible (shouldShow);
    sendButton_.setVisible (shouldShow);
    instrumentBox_.setVisible (shouldShow);
    instrumentLabel_.setVisible (shouldShow);
    activitySlider_.setVisible (shouldShow);
    activityLabel_.setVisible (shouldShow);
    ghostSlider_.setVisible (shouldShow);
    ghostLabel_.setVisible (shouldShow);
    muteSlider_.setVisible (shouldShow);
    muteLabel_.setVisible (shouldShow);
    slideSlider_.setVisible (shouldShow);
    slideLabel_.setVisible (shouldShow);
    legatoSlider_.setVisible (shouldShow);
    legatoLabel_.setVisible (shouldShow);
    timingSlider_.setVisible (shouldShow);
    timingLabel_.setVisible (shouldShow);
    dynamicsSlider_.setVisible (shouldShow);
    dynamicsLabel_.setVisible (shouldShow);
    expressiveFusionButton_.setVisible (shouldShow);
    setSize (460, shouldShow ? 646 : 392);
    resized();
}

void SessionPlayerMidiFXEditor::updateTouchAvailability()
{
    const auto family = instrumentBox_.getSelectedItemIndex();
    const auto selectedTouch = touchBox_.getSelectedId();
    const auto allow = [this, selectedTouch] (int itemId, bool supported)
    {
        // Keep an already-requested fallback visible. Once the user chooses
        // another touch, the unsupported choice becomes unavailable.
        touchBox_.setItemEnabled (itemId, supported || itemId == selectedTouch);
    };

    allow (1, true);                       // Natural
    allow (2, true);                       // Clean
    allow (3, family != 1 && family != 3); // Ghosted: fingered or upright
    allow (4, family == 0);                // Muted: fingered only
    allow (5, family != 3);                // Connected: not sub / synth
}

void SessionPlayerMidiFXEditor::applyTouchMacro()
{
    const auto amount = juce::jlimit (0.0, 1.0, expressionSlider_.getValue());
    auto ghost = 0.0;
    auto mute = 0.0;
    auto slide = 0.0;
    auto legato = 0.0;
    auto timing = juce::jlimit (0.0, 1.0, 0.18 + 0.52 * amount);
    auto dynamics = juce::jlimit (0.0, 1.0, 0.22 + 0.58 * amount);

    switch (touchBox_.getSelectedId())
    {
        case 2: // Clean
            timing = juce::jlimit (0.0, 1.0, 0.12 + 0.18 * amount);
            dynamics = juce::jlimit (0.0, 1.0, 0.16 + 0.22 * amount);
            break;
        case 3: // Ghosted
            ghost = juce::jlimit (0.0, 1.0, 0.35 + 0.60 * amount);
            break;
        case 4: // Muted
            mute = juce::jlimit (0.0, 1.0, 0.30 + 0.62 * amount);
            break;
        case 5: // Connected
            slide = juce::jlimit (0.0, 1.0, 0.25 + 0.62 * amount);
            legato = juce::jlimit (0.0, 1.0, 0.30 + 0.66 * amount);
            break;
        default: // Natural: restrained, style-aware blend.
        {
            static constexpr double styleMix[][4] {
                { 0.08, 0.02, 0.10, 0.12 }, // supportive
                { 0.08, 0.00, 0.34, 0.38 }, // melodic
                { 0.30, 0.22, 0.08, 0.12 }, // rhythmic
                { 0.48, 0.42, 0.14, 0.20 }, // slap
                { 0.34, 0.16, 0.58, 0.48 }, // fusion
            };
            const auto styleIndex = juce::jlimit (
                0,
                4,
                styleBox_.getSelectedItemIndex());
            ghost = styleMix[styleIndex][0] * amount;
            mute = styleMix[styleIndex][1] * amount;
            slide = styleMix[styleIndex][2] * amount;
            legato = styleMix[styleIndex][3] * amount;
            break;
        }
    }

    ghostSlider_.setValue (ghost, juce::sendNotificationSync);
    muteSlider_.setValue (mute, juce::sendNotificationSync);
    slideSlider_.setValue (slide, juce::sendNotificationSync);
    legatoSlider_.setValue (legato, juce::sendNotificationSync);
    timingSlider_.setValue (timing, juce::sendNotificationSync);
    dynamicsSlider_.setValue (dynamics, juce::sendNotificationSync);
}

void SessionPlayerMidiFXEditor::applyExpressiveFusionPreset()
{
    // Keep the exact producer preset in sync with the backend and web app.
    touchBox_.setSelectedId (1, juce::sendNotificationSync);
    styleBox_.setSelectedItemIndex (4, juce::sendNotificationSync);
    ghostSlider_.setValue (0.62, juce::sendNotificationSync);
    muteSlider_.setValue (0.24, juce::sendNotificationSync);
    slideSlider_.setValue (0.80, juce::sendNotificationSync);
    legatoSlider_.setValue (0.68, juce::sendNotificationSync);
    timingSlider_.setValue (0.58, juce::sendNotificationSync);
    dynamicsSlider_.setValue (0.72, juce::sendNotificationSync);
}

void SessionPlayerMidiFXEditor::timerCallback()
{
    updateTouchAvailability();
    statusLabel_.setText (processor_.statusText(), juce::dontSendNotification);
    adviceLabel_.setText (processor_.adviceText(), juce::dontSendNotification);
    historyLabel_.setText (processor_.historyText(), juce::dontSendNotification);
    earlierButton_.setEnabled (processor_.canRecallEarlier());
    laterButton_.setEnabled (processor_.canRecallLater());
}

void SessionPlayerMidiFXEditor::paint (juce::Graphics& g)
{
    g.fillAll (juce::Colour (0xff14141c));
    g.setColour (juce::Colours::white);
    g.setFont (juce::Font (juce::FontOptions (17.0f, juce::Font::bold)));
    g.drawText ("SESSION PLAYER - BASS", 16, 12, getWidth() - 32, 22,
                juce::Justification::centredLeft);
    g.setColour (juce::Colour (0xff6366f1));
    g.fillRect (16, 36, getWidth() - 32, 2);
}

void SessionPlayerMidiFXEditor::resized()
{
    auto area = getLocalBounds().reduced (16);
    area.removeFromTop (32); // title zone
    adviceLabel_.setBounds (area.removeFromTop (66).reduced (4, 3));

    auto historyRow = area.removeFromTop (34);
    earlierButton_.setBounds (historyRow.removeFromLeft (72).reduced (2, 2));
    keepButton_.setBounds (historyRow.removeFromLeft (64).reduced (2, 2));
    laterButton_.setBounds (historyRow.removeFromLeft (64).reduced (2, 2));
    historyLabel_.setBounds (historyRow.reduced (4, 1));

    auto controlRow = area.removeFromTop (178);
    auto left = controlRow.removeFromLeft (controlRow.getWidth() / 2);
    styleBox_.setBounds (left.removeFromTop (30).reduced (4, 1));
    auto touchRow = left.removeFromTop (34);
    touchLabel_.setBounds (touchRow.removeFromLeft (58).reduced (4, 1));
    touchBox_.setBounds (touchRow.reduced (2, 1));
    regenerateButton_.setBounds (left.removeFromTop (36).reduced (4, 3));
    newBeatResetButton_.setBounds (left.removeFromTop (36).reduced (4, 3));
    advancedButton_.setBounds (left.removeFromTop (32).reduced (4, 3));

    auto expressionArea = controlRow.removeFromLeft (controlRow.getWidth() / 2);
    expressionLabel_.setBounds (expressionArea.removeFromTop (18));
    expressionSlider_.setBounds (expressionArea.reduced (6, 0));
    lockLabel_.setBounds (controlRow.removeFromTop (18));
    lockSlider_.setBounds (controlRow.reduced (6, 0));

    statusLabel_.setBounds (area.removeFromBottom (22));
    if (advancedButton_.getToggleState())
    {
        auto instrumentRow = area.removeFromTop (30);
        instrumentLabel_.setBounds (instrumentRow.removeFromLeft (110).reduced (4, 1));
        instrumentBox_.setBounds (instrumentRow.reduced (2, 1));
        auto activityRow = area.removeFromTop (38);
        activityLabel_.setBounds (activityRow.removeFromLeft (110).reduced (4, 1));
        activitySlider_.setBounds (activityRow.reduced (2, 1));
        expressiveFusionButton_.setBounds (
            area.removeFromTop (30).reduced (4, 2));
        const auto layoutPerformanceRow = [] (
            juce::Rectangle<int> row,
            juce::Label& leftLabel,
            juce::Slider& leftSlider,
            juce::Label& rightLabel,
            juce::Slider& rightSlider)
        {
            auto leftControl = row.removeFromLeft (row.getWidth() / 2);
            leftLabel.setBounds (leftControl.removeFromLeft (62).reduced (2, 1));
            leftSlider.setBounds (leftControl.reduced (1, 1));
            rightLabel.setBounds (row.removeFromLeft (62).reduced (2, 1));
            rightSlider.setBounds (row.reduced (1, 1));
        };
        layoutPerformanceRow (
            area.removeFromTop (36),
            ghostLabel_, ghostSlider_, muteLabel_, muteSlider_);
        layoutPerformanceRow (
            area.removeFromTop (36),
            slideLabel_, slideSlider_, legatoLabel_, legatoSlider_);
        layoutPerformanceRow (
            area.removeFromTop (36),
            timingLabel_, timingSlider_, dynamicsLabel_, dynamicsSlider_);
        auto commandRow = area.removeFromBottom (34);
        sendButton_.setBounds (commandRow.removeFromRight (64).reduced (2));
        commandBox_.setBounds (commandRow.reduced (2));
    }
}
