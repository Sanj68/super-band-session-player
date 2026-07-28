#include "PluginProcessor.h"
#include "PluginEditor.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <limits>

namespace
{
constexpr int  kPollMs = 2000;
constexpr int  kMaxPartNotes = 4096;
constexpr int  kMaxAutomationEvents = 16384;
constexpr int  kMidiPitchCount = 128;
constexpr int  kPitchBendRangeSemitones = 12;
constexpr int  kRegenerateOrdinary = 1;
constexpr int  kRegenerateNewBeat = 2;
constexpr double kMinimumReportedHostTempo = 40.0;
constexpr double kMaximumReportedHostTempo = 240.0;

constexpr bool shouldPollEngine (bool refreshRequested,
                                 bool handledAction,
                                 bool transportRunning)
{
    return refreshRequested || handledAction || transportRunning;
}

constexpr bool shouldRefreshTakeReceipt (bool refreshRequested,
                                         bool handledAction)
{
    // The musical summary describes the generated take. Live transport polls
    // may refresh beat-frame/connection status, but must not silently rewrite
    // that receipt as the analyser accumulates new frames.
    return refreshRequested || handledAction;
}

bool isUsableReportedHostTempo (double bpm)
{
    return (
        std::isfinite (bpm)
        && bpm >= kMinimumReportedHostTempo
        && bpm <= kMaximumReportedHostTempo
    );
}

constexpr double wrapBeatToLoop (double beat, double loopLength)
{
    if (loopLength <= 0.0)
        return 0.0;
    while (beat >= loopLength)
        beat -= loopLength;
    while (beat < 0.0)
        beat += loopLength;
    return beat;
}

constexpr bool activeOffPrecedesOrTiesOn (int offSample, int onSample)
{
    return offSample <= onSample;
}

constexpr int pitchBendPositionFromSigned (int value)
{
    return value <= -8192 ? 0
                          : (value >= 8191 ? 16383 : value + 8192);
}

constexpr bool shouldResetForPartRevision (
    std::uint64_t activeRevision,
    std::uint64_t incomingRevision)
{
    return incomingRevision != 0 && incomingRevision != activeRevision;
}

constexpr bool shouldAcceptFetchedPart (
    bool bindingEpochUnchanged,
    bool bindingIdUnchanged,
    bool responseMatchesRequestedSession)
{
    return (
        bindingEpochUnchanged
        && bindingIdUnchanged
        && responseMatchesRequestedSession
    );
}

void mixFingerprint (std::uint64_t& hash, std::uint64_t value)
{
    hash ^= value;
    hash *= 1099511628211ULL;
}

std::uint64_t doubleFingerprintBits (double value)
{
    if (std::fpclassify (value) == FP_ZERO)
        value = 0.0;
    std::uint64_t bits = 0;
    static_assert (sizeof (bits) == sizeof (value));
    std::memcpy (&bits, &value, sizeof (bits));
    return bits;
}

std::uint64_t playbackFingerprintFor (const BassPart& part)
{
    std::uint64_t hash = 1469598103934665603ULL;
    mixFingerprint (hash, static_cast<std::uint64_t> (part.sessionId.hashCode64()));
    mixFingerprint (hash, static_cast<std::uint64_t> (part.barCount));
    mixFingerprint (hash, static_cast<std::uint64_t> (part.beatsPerBar));
    mixFingerprint (hash, static_cast<std::uint64_t> (part.version));
    mixFingerprint (hash, doubleFingerprintBits (part.phaseOffsetBeats));
    mixFingerprint (hash, static_cast<std::uint64_t> (part.notes.size()));
    for (const auto& note : part.notes)
    {
        mixFingerprint (hash, static_cast<std::uint64_t> (note.pitch));
        mixFingerprint (hash, static_cast<std::uint64_t> (note.velocity));
        mixFingerprint (hash, doubleFingerprintBits (note.startBeats));
        mixFingerprint (hash, doubleFingerprintBits (note.durBeats));
    }
    mixFingerprint (hash, static_cast<std::uint64_t> (part.automation.size()));
    for (const auto& event : part.automation)
    {
        mixFingerprint (
            hash,
            static_cast<std::uint64_t> (event.type));
        mixFingerprint (hash, static_cast<std::uint64_t> (event.channel));
        mixFingerprint (hash, static_cast<std::uint64_t> (event.controller));
        mixFingerprint (
            hash,
            static_cast<std::uint64_t> (
                static_cast<std::int64_t> (event.value)));
        mixFingerprint (hash, doubleFingerprintBits (event.beat));
    }
    return hash;
}

bool isPitchRangeConfigurationController (int controller)
{
    return (
        controller == 101
        || controller == 100
        || controller == 6
        || controller == 38
    );
}

void restoreExpressionStateAt (
    const BassPart& part,
    double outputBeat,
    juce::MidiBuffer& midi,
    int samplePos)
{
    if (part.automation.empty())
        return;

    const auto loopLength = part.loopBeats();
    std::array<double, 128> controllerDistance;
    std::array<int, 128> controllerValue {};
    controllerDistance.fill (std::numeric_limits<double>::infinity());
    auto pitchDistance = std::numeric_limits<double>::infinity();
    auto pitchValue = 0;
    auto hasPitch = false;

    for (const auto& event : part.automation)
    {
        const auto shiftedBeat = wrapBeatToLoop (
            event.beat + part.phaseOffsetBeats,
            loopLength);
        auto distance = outputBeat - shiftedBeat;
        // Exact-boundary events are emitted by the normal scheduler next; the
        // restored value must represent the end of the previous loop.
        if (distance <= 1.0e-9)
            distance += loopLength;

        if (event.type == BassPartAutomationEvent::Type::pitchBend)
        {
            if (distance < pitchDistance)
            {
                pitchDistance = distance;
                pitchValue = event.value;
                hasPitch = true;
            }
            continue;
        }

        if (isPitchRangeConfigurationController (event.controller))
            continue;
        const auto controller = juce::jlimit (0, 127, event.controller);
        if (distance < controllerDistance[static_cast<size_t> (controller)])
        {
            controllerDistance[static_cast<size_t> (controller)] = distance;
            controllerValue[static_cast<size_t> (controller)] = event.value;
        }
    }

    for (size_t controller = 0; controller < controllerDistance.size(); ++controller)
    {
        if (std::isfinite (controllerDistance[controller]))
        {
            midi.addEvent (
                juce::MidiMessage::controllerEvent (
                    1,
                    static_cast<int> (controller),
                    controllerValue[controller]),
                samplePos);
        }
    }
    if (hasPitch)
    {
        midi.addEvent (
            juce::MidiMessage::pitchWheel (
                1,
                pitchBendPositionFromSigned (pitchValue)),
            samplePos);
    }
}

// Keep the stopped-state contract compile-time checked: hydrate once and honour
// direct UI actions, but never run the recurring poll merely because Logic has
// instantiated the plug-in.
static_assert (shouldPollEngine (true, false, false));
static_assert (shouldPollEngine (false, true, false));
static_assert (shouldPollEngine (false, false, true));
static_assert (! shouldPollEngine (false, false, false));
static_assert (shouldRefreshTakeReceipt (true, false));
static_assert (shouldRefreshTakeReceipt (false, true));
static_assert (! shouldRefreshTakeReceipt (false, false));
static_assert (wrapBeatToLoop (0.0 + 0.5, 16.0) == 0.5);
static_assert (wrapBeatToLoop (15.75 + 0.5, 16.0) == 0.25);
static_assert (wrapBeatToLoop (15.5 + 0.5, 16.0) == 0.0);
static_assert (wrapBeatToLoop (0.0 + 4.0, 16.0) == 4.0);
static_assert (wrapBeatToLoop (0.0 + 4.0, 4.0) == 0.0);
static_assert (wrapBeatToLoop (3.75 + 4.0, 4.0) == 3.75);
static_assert (wrapBeatToLoop (-0.5, 16.0) == 15.5);
static_assert (! activeOffPrecedesOrTiesOn (300, 100));
static_assert (activeOffPrecedesOrTiesOn (100, 300));
static_assert (activeOffPrecedesOrTiesOn (100, 100));
static_assert (pitchBendPositionFromSigned (-8192) == 0);
static_assert (pitchBendPositionFromSigned (0) == 8192);
static_assert (pitchBendPositionFromSigned (8191) == 16383);
static_assert (! shouldResetForPartRevision (7, 7));
static_assert (shouldResetForPartRevision (7, 8));
static_assert (! shouldResetForPartRevision (0, 0));
static_assert (shouldAcceptFetchedPart (true, true, true));
static_assert (! shouldAcceptFetchedPart (false, true, true));
static_assert (! shouldAcceptFetchedPart (true, false, true));
static_assert (! shouldAcceptFetchedPart (true, true, false));

juce::String loadPluginApiBaseUrl()
{
    auto baseUrl = juce::String();
    const auto configFile = juce::File::getSpecialLocation (juce::File::userApplicationDataDirectory)
                                .getChildFile ("Application Support")
                                .getChildFile ("Session Player Bridge")
                                .getChildFile ("config.json");
    if (configFile.existsAsFile())
    {
        if (const auto parsed = juce::JSON::parse (configFile); parsed.isObject())
            baseUrl = parsed.getProperty ("plugin_api_base_url", "").toString().trim();
    }

    if (const auto envUrl = juce::SystemStats::getEnvironmentVariable (
            "SESSION_PLAYER_PLUGIN_URL", {}); envUrl.isNotEmpty())
        baseUrl = envUrl;
    if (baseUrl.isEmpty())
        baseUrl = "http://127.0.0.1:8000/api/plugin";
    return baseUrl.trim().trimCharactersAtEnd ("/");
}
}

const juce::StringArray SessionPlayerMidiFXProcessor::styleChoices {
    "supportive", "melodic", "rhythmic", "slap", "fusion"
};

// Public automation vocabulary stays neutral. The parallel internal id list
// preserves the legacy engine routing until the trait-profile layer replaces it.
const juce::StringArray SessionPlayerMidiFXProcessor::playerChoices {
    "neutral", "deep_soul_anchor", "laid_back_sustain", "elastic_funk",
    "percussive_funk", "melodic_fusion", "acoustic_walking"
};

const juce::StringArray SessionPlayerMidiFXProcessor::playerEngineIds {
    "none", "james_jamerson", "pino", "bootsy", "marcus", "jaco_pastorius", "paul_chambers"
};

const juce::StringArray SessionPlayerMidiFXProcessor::instrumentChoices {
    "Fingered", "Fretless", "Upright", "Sub / Synth"
};

const juce::StringArray SessionPlayerMidiFXProcessor::instrumentEngineIds {
    "finger_bass", "fretless_bass", "upright_bass", "sub_bass"
};

const juce::StringArray SessionPlayerMidiFXProcessor::touchChoices {
    "Natural", "Clean", "Ghosted", "Muted", "Connected"
};

const juce::StringArray SessionPlayerMidiFXProcessor::touchEngineIds {
    "natural", "clean", "ghosted", "muted", "connected"
};

static juce::AudioProcessorValueTreeState::ParameterLayout makeLayout()
{
    juce::AudioProcessorValueTreeState::ParameterLayout layout;
    layout.add (std::make_unique<juce::AudioParameterChoice> (
        juce::ParameterID { "style", 1 }, "Style",
        SessionPlayerMidiFXProcessor::styleChoices, 0));
    layout.add (std::make_unique<juce::AudioParameterChoice> (
        juce::ParameterID { "player", 1 }, "Player",
        SessionPlayerMidiFXProcessor::playerChoices, 0)); // legacy compatibility; hidden from public UI
    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "lock", 1 }, "Lock to Groove",
        juce::NormalisableRange<float> (0.0f, 1.0f, 0.01f), 0.5f));
    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "expression", 1 }, "Character",
        juce::NormalisableRange<float> (0.0f, 1.0f, 0.25f), 0.5f));
    layout.add (std::make_unique<juce::AudioParameterChoice> (
        juce::ParameterID { "instrument", 1 }, "Bass Family",
        SessionPlayerMidiFXProcessor::instrumentChoices, 0));
    layout.add (std::make_unique<juce::AudioParameterChoice> (
        juce::ParameterID { "touch", 1 }, "Touch",
        SessionPlayerMidiFXProcessor::touchChoices, 0));
    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "activity", 1 }, "Activity",
        juce::NormalisableRange<float> (-1.0f, 1.0f, 0.5f), 0.0f));
    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "ghost", 1 }, "Ghosts",
        juce::NormalisableRange<float> (0.0f, 1.0f, 0.01f), 0.2f));
    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "mute", 1 }, "Mutes",
        juce::NormalisableRange<float> (0.0f, 1.0f, 0.01f), 0.05f));
    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "slide", 1 }, "Slides",
        juce::NormalisableRange<float> (0.0f, 1.0f, 0.01f), 0.25f));
    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "legato", 1 }, "Legato",
        juce::NormalisableRange<float> (0.0f, 1.0f, 0.01f), 0.25f));
    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "timing_humanize", 1 }, "Timing Feel",
        juce::NormalisableRange<float> (0.0f, 1.0f, 0.01f), 0.5f));
    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "velocity_humanize", 1 }, "Dynamics",
        juce::NormalisableRange<float> (0.0f, 1.0f, 0.01f), 0.5f));
    return layout;
}

SessionPlayerMidiFXProcessor::SessionPlayerMidiFXProcessor()
    : juce::AudioProcessor (BusesProperties()), // MIDI effect: no audio buses
      juce::Thread ("sp-midifx-poll"),
      apvts (*this, nullptr, "PARAMS", makeLayout()),
      apiBaseUrl_ (loadPluginApiBaseUrl())
{
    active_.reserve (kMidiPitchCount);
    dueNoteOns_.reserve (kMaxPartNotes);
    dueAutomation_.reserve (kMaxAutomationEvents);
    startThread();
}

SessionPlayerMidiFXProcessor::~SessionPlayerMidiFXProcessor()
{
    stopThread (4000);
}

void SessionPlayerMidiFXProcessor::prepareToPlay (double sampleRate, int)
{
    sampleRate_ = sampleRate;
    active_.clear();
    lastPpq_ = -1.0;
    lastBlockBeats_ = 0.0;
    activePartRevision_ = 0;
}

// ---- engine polling --------------------------------------------------------

void SessionPlayerMidiFXProcessor::run()
{
    while (! threadShouldExit())
    {
        bool handledAction = false;

        if (keepRequested_.exchange (false))
        {
            handledAction = true;
            setStatus ("keeping idea...");
            juce::DynamicObject::Ptr body = new juce::DynamicObject();
            const auto sessionId = boundSessionId();
            if (sessionId.isNotEmpty())
                body->setProperty ("session_id", sessionId);
            const auto json = juce::JSON::toString (juce::var (body.get()), true);
            juce::URL url { apiBaseUrl_ + "/keep" };
            auto options = juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inPostData)
                               .withExtraHeaders ("Content-Type: application/json")
                               .withConnectionTimeoutMs (4000);
            if (auto stream = url.withPOSTData (json).createInputStream (options))
            {
                const auto parsed = juce::JSON::parse (stream->readEntireStreamAsString());
                setStatus (parsed.getProperty ("message", "Idea kept.").toString());
            }
            else
                setStatus ("engine offline (keep failed)");
            fetchHistory();
        }

        const auto historyStep = historyStepRequested_.exchange (0);
        if (historyStep != 0)
        {
            handledAction = true;
            const auto goingEarlier = historyStep < 0;
            setStatus (goingEarlier ? "recalling earlier idea..." : "recalling later idea...");
            juce::DynamicObject::Ptr body = new juce::DynamicObject();
            const auto sessionId = boundSessionId();
            if (sessionId.isNotEmpty())
                body->setProperty ("session_id", sessionId);
            body->setProperty ("direction", goingEarlier ? "previous" : "next");
            const auto json = juce::JSON::toString (juce::var (body.get()), true);
            juce::URL url { apiBaseUrl_ + "/history/navigate" };
            int statusCode = 0;
            auto options = juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inPostData)
                               .withExtraHeaders ("Content-Type: application/json")
                               .withConnectionTimeoutMs (4000)
                               .withStatusCode (&statusCode);
            if (auto stream = url.withPOSTData (json).createInputStream (options))
            {
                const auto parsed = juce::JSON::parse (stream->readEntireStreamAsString());
                if (statusCode >= 200 && statusCode < 300)
                {
                    setStatus (parsed.getProperty ("message", "Idea recalled.").toString());
                    parametersHydratedFromPart_ = false;
                    fetchPart (false);
                }
                else
                {
                    const auto fallback = goingEarlier
                        ? "no earlier idea available"
                        : "no later idea available";
                    const auto detail = parsed.getProperty ("detail", juce::var());
                    const auto message = detail.getProperty ("message", fallback).toString();
                    setStatus (message.isNotEmpty() ? message : fallback);
                }
            }
            else
                setStatus (goingEarlier ? "no earlier idea available" : "no later idea available");
            fetchHistory();
        }

        const auto regenerateMode = regenerateRequestMode_.exchange (
            0, std::memory_order_acquire);
        if (regenerateMode != 0)
        {
            handledAction = true;
            const auto forceNewPhrase = regenerateMode == kRegenerateNewBeat;
            const auto hostTempo = latestValidHostTempo_.load (
                std::memory_order_relaxed);
            if (forceNewPhrase && isUsableReportedHostTempo (hostTempo))
                setStatus (
                    "resetting for new beat at "
                    + juce::String (hostTempo, 1)
                    + " BPM...");
            else
                setStatus (
                    forceNewPhrase
                        ? "resetting for new beat..."
                        : "regenerating...");
            auto* styleParam = dynamic_cast<juce::AudioParameterChoice*> (apvts.getParameter ("style"));
            auto* playerParam = dynamic_cast<juce::AudioParameterChoice*> (apvts.getParameter ("player"));
            auto* instrumentParam = dynamic_cast<juce::AudioParameterChoice*> (apvts.getParameter ("instrument"));
            auto* touchParam = dynamic_cast<juce::AudioParameterChoice*> (apvts.getParameter ("touch"));
            auto lockValue = apvts.getRawParameterValue ("lock")->load();
            auto expressionValue = apvts.getRawParameterValue ("expression")->load();
            auto activityValue = apvts.getRawParameterValue ("activity")->load();
            auto ghostValue = apvts.getRawParameterValue ("ghost")->load();
            auto muteValue = apvts.getRawParameterValue ("mute")->load();
            auto slideValue = apvts.getRawParameterValue ("slide")->load();
            auto legatoValue = apvts.getRawParameterValue ("legato")->load();
            auto timingValue = apvts.getRawParameterValue ("timing_humanize")->load();
            auto dynamicsValue = apvts.getRawParameterValue ("velocity_humanize")->load();
            juce::DynamicObject::Ptr body = new juce::DynamicObject();
            const auto sessionId = boundSessionId();
            if (sessionId.isNotEmpty())
                body->setProperty ("session_id", sessionId);
            body->setProperty ("bass_style", styleChoices[styleParam ? styleParam->getIndex() : 0]);
            body->setProperty ("bass_player", playerEngineIds[playerParam ? playerParam->getIndex() : 0]);
            body->setProperty ("bass_instrument",
                               instrumentEngineIds[instrumentParam ? instrumentParam->getIndex() : 0]);
            body->setProperty ("bass_articulation_focus",
                               touchEngineIds[touchParam ? touchParam->getIndex() : 0]);
            body->setProperty ("bass_density_bias", (double) activityValue);
            body->setProperty ("lock_to_groove", (double) lockValue);
            body->setProperty ("bass_expression", (double) expressionValue);
            juce::DynamicObject::Ptr performance = new juce::DynamicObject();
            performance->setProperty ("ghost", (double) ghostValue);
            performance->setProperty ("mute", (double) muteValue);
            performance->setProperty ("slide", (double) slideValue);
            performance->setProperty ("legato", (double) legatoValue);
            performance->setProperty ("timing_humanize", (double) timingValue);
            performance->setProperty ("velocity_humanize", (double) dynamicsValue);
            body->setProperty (
                "bass_performance_controls",
                juce::var (performance.get()));
            body->setProperty ("force_new_phrase", forceNewPhrase);
            if (forceNewPhrase && isUsableReportedHostTempo (hostTempo))
                body->setProperty ("host_tempo", hostTempo);
            const auto json = juce::JSON::toString (juce::var (body.get()), true);

            juce::URL url { apiBaseUrl_ + "/regenerate" };
            auto options = juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inPostData)
                               .withExtraHeaders ("Content-Type: application/json")
                               .withConnectionTimeoutMs (4000);
            if (auto stream = url.withPOSTData (json).createInputStream (options))
                stream->readEntireStreamAsString(); // response == fresh part; next fetch picks it up
            else
                setStatus ("engine offline (regenerate failed)");
        }

        juce::String command;
        {
            const juce::ScopedLock l (commandLock_);
            command.swapWith (pendingCommand_);
        }
        if (command.isNotEmpty())
        {
            handledAction = true;
            setStatus ("\"" + command + "\" ...");
            juce::DynamicObject::Ptr body = new juce::DynamicObject();
            const auto sessionId = boundSessionId();
            if (sessionId.isNotEmpty())
                body->setProperty ("session_id", sessionId);
            body->setProperty ("text", command);
            const auto json = juce::JSON::toString (juce::var (body.get()), true);
            juce::URL url { apiBaseUrl_ + "/command" };
            auto options = juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inPostData)
                               .withExtraHeaders ("Content-Type: application/json")
                               .withConnectionTimeoutMs (6000);
            if (auto stream = url.withPOSTData (json).createInputStream (options))
            {
                const auto parsed = juce::JSON::parse (stream->readEntireStreamAsString());
                const auto msg = parsed.getProperty ("message", "").toString();
                setStatus (msg.isNotEmpty() ? msg : "command sent");
            }
            else
                setStatus ("engine offline (command failed)");
            parametersHydratedFromPart_ = false;
            fetchPart (false); // refresh the part; keep the reply on screen
            for (int i = 0; i < 30 && ! threadShouldExit(); ++i)
                wait (100);     // let the reply read for ~3s
        }

        const auto refreshRequested = refreshRequested_.exchange (false);
        if (shouldPollEngine (refreshRequested, handledAction, transportRunning_.load()))
        {
            fetchPart();
            // Advice is a receipt for the current generated take, not a live
            // analyser readout. Keep polling the part while transport runs so
            // frame/connection status stays current, but refresh the receipt
            // only at an explicit binding refresh or producer action.
            if (shouldRefreshTakeReceipt (refreshRequested, handledAction))
            {
                fetchAdvice();
                fetchHistory();
            }
        }

        collectRetiredParts();

        // UI commands call notify(). Transport state is signalled lock-free
        // from processBlock and checked here in short sleeps so the real-time
        // audio thread never takes a condition-variable lock.
        for (int i = 0;
             i < kPollMs / 100
                 && ! threadShouldExit()
                 && ! refreshRequested_.load();
             ++i)
            wait (100);
    }
}

void SessionPlayerMidiFXProcessor::fetchPart (bool updateStatus)
{
    juce::URL url { apiBaseUrl_ + "/bass-part" };
    juce::String requestedSessionId;
    std::uint64_t requestedBindingEpoch = 0;
    {
        const juce::ScopedLock l (sessionLock_);
        requestedSessionId = boundSessionId_;
        requestedBindingEpoch = sessionBindingEpoch_;
    }
    if (requestedSessionId.isNotEmpty())
        url = url.withParameter ("session_id", requestedSessionId);
    auto options = juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inAddress)
                       .withConnectionTimeoutMs (3000);
    auto stream = url.createInputStream (options);
    if (stream == nullptr)
    {
        if (updateStatus)
            setStatus ("engine offline — start the Session Player backend");
        return;
    }
    const auto parsed = juce::JSON::parse (stream->readEntireStreamAsString());
    if (! parsed.isObject())
    {
        if (updateStatus)
            setStatus ("no bass part yet — generate one in the app");
        return;
    }

    auto fresh = std::make_shared<BassPart>();
    fresh->version     = juce::jmax (1, (int) parsed.getProperty ("version", 1));
    fresh->sessionId   = parsed.getProperty ("session_id", "").toString();
    fresh->key         = parsed.getProperty ("key", "").toString();
    fresh->scale       = parsed.getProperty ("scale", "").toString();
    fresh->bassStyle   = parsed.getProperty ("bass_style", "supportive").toString();
    fresh->bassPlayer  = parsed.getProperty ("bass_player", "none").toString();
    if (fresh->bassPlayer.isEmpty())
        fresh->bassPlayer = "none";
    fresh->barCount    = (int) parsed.getProperty ("bar_count", 0);
    fresh->beatsPerBar = (int) parsed.getProperty ("beats_per_bar", 4);
    fresh->preview     = parsed.getProperty ("preview", "").toString();
    fresh->bassInstrument = parsed.getProperty ("bass_instrument", "finger_bass").toString();
    fresh->bassArticulationFocus =
        parsed.getProperty ("bass_articulation_focus", "natural").toString();
    fresh->bassArticulationEffective =
        parsed.getProperty (
            "bass_articulation_effective",
            fresh->bassArticulationFocus).toString();
    fresh->bassArticulationNotice =
        parsed.getProperty ("bass_articulation_notice", "").toString().trim();
    const auto rawDensityBias = (double) parsed.getProperty ("bass_density_bias", 0.0);
    fresh->bassDensityBias = std::isfinite (rawDensityBias)
        ? juce::jlimit (-1.0f, 1.0f, (float) rawDensityBias)
        : 0.0f;
    const auto lockValue = parsed.getProperty ("lock_to_groove", 0.5);
    fresh->lockToGroove = lockValue.isVoid() ? 0.5f : (float) lockValue;
    fresh->bassExpression = (float) parsed.getProperty ("bass_expression", 0.5);
    const auto requestedPerformance = parsed.getProperty (
        "bass_performance_controls",
        juce::var());
    const auto readPerformanceAmount = [&requestedPerformance] (
        const juce::Identifier& name,
        float fallback)
    {
        if (! requestedPerformance.isObject())
            return fallback;
        const auto raw = (double) requestedPerformance.getProperty (name, fallback);
        return std::isfinite (raw)
            ? juce::jlimit (0.0f, 1.0f, (float) raw)
            : fallback;
    };
    fresh->ghostAmount = readPerformanceAmount ("ghost", 0.0f);
    fresh->muteAmount = readPerformanceAmount ("mute", 0.0f);
    fresh->slideAmount = readPerformanceAmount ("slide", 0.0f);
    fresh->legatoAmount = readPerformanceAmount ("legato", 0.0f);
    fresh->timingHumanize = readPerformanceAmount ("timing_humanize", 0.5f);
    fresh->velocityHumanize = readPerformanceAmount ("velocity_humanize", 0.5f);
    fresh->bassPerformanceControlsNotice = parsed.getProperty (
        "bass_performance_controls_notice",
        "").toString().trim();
    if (auto* object = parsed.getDynamicObject())
    {
        fresh->grooveSourceStatusKnown = (
            object->hasProperty ("groove_source_ready")
            || object->hasProperty ("groove_source_frame_count")
            || object->hasProperty ("groove_source_notice")
        );
    }
    fresh->grooveSourceReady = (bool) parsed.getProperty (
        "groove_source_ready",
        false);
    fresh->grooveSourceFrameCount = juce::jmax (
        0,
        (int) parsed.getProperty ("groove_source_frame_count", 0));
    fresh->grooveSourceNotice = parsed.getProperty (
        "groove_source_notice",
        "").toString().trim();
    const auto rawPhaseOffset = (double) parsed.getProperty ("phase_offset_beats", 0.0);
    fresh->phaseOffsetBeats = std::isfinite (rawPhaseOffset)
        ? juce::jlimit (0.0, 4.0, rawPhaseOffset)
        : 0.0;
    if (auto* arr = parsed.getProperty ("notes", juce::var()).getArray())
    {
        fresh->notes.reserve (
            static_cast<size_t> (juce::jmin (arr->size(), kMaxPartNotes)));
        for (const auto& v : *arr)
        {
            if (fresh->notes.size() >= static_cast<size_t> (kMaxPartNotes))
                break;
            BassPartNote n;
            n.pitch      = juce::jlimit (0, 127, (int) v.getProperty ("pitch", 36));
            n.velocity   = juce::jlimit (1, 127, (int) v.getProperty ("velocity", 90));
            const auto rawStart = (double) v.getProperty ("start_beats", 0.0);
            const auto rawDuration = (double) v.getProperty ("dur_beats", 0.5);
            n.startBeats = std::isfinite (rawStart) ? rawStart : 0.0;
            n.durBeats = std::isfinite (rawDuration)
                ? juce::jmax (0.05, rawDuration)
                : 0.5;
            fresh->notes.push_back (n);
        }
    }
    if (auto* arr = parsed.getProperty ("automation", juce::var()).getArray())
    {
        fresh->automation.reserve (
            static_cast<size_t> (
                juce::jmin (arr->size(), kMaxAutomationEvents)));
        for (const auto& v : *arr)
        {
            if (
                fresh->automation.size()
                >= static_cast<size_t> (kMaxAutomationEvents)
            )
                break;
            if (! v.isObject())
                continue;

            const auto rawBeat = (double) v.getProperty ("beat", -1.0);
            const auto channel = (int) v.getProperty ("channel", 1);
            if (! std::isfinite (rawBeat) || rawBeat < 0.0 || channel != 1)
                continue;

            BassPartAutomationEvent event;
            event.channel = 1;
            event.beat = rawBeat;
            const auto type = v.getProperty ("type", "").toString();
            if (type == "control_change")
            {
                event.type = BassPartAutomationEvent::Type::controlChange;
                event.controller = juce::jlimit (
                    0, 127, (int) v.getProperty ("controller", 0));
                event.value = juce::jlimit (
                    0, 127, (int) v.getProperty ("value", 0));
            }
            else if (type == "pitch_bend")
            {
                event.type = BassPartAutomationEvent::Type::pitchBend;
                event.value = juce::jlimit (
                    -8192, 8191, (int) v.getProperty ("value", 0));
            }
            else
                continue;
            fresh->automation.push_back (event);
        }
    }
    fresh->playbackFingerprint = playbackFingerprintFor (*fresh);
    bool staleBinding = false;
    std::shared_ptr<const BassPart> retiredPart;
    {
        // Binding validation, initial parameter hydration, and publication
        // form one transaction. Project-state restore takes the same locks in
        // this order, so an old response cannot cross the restore boundary.
        const juce::ScopedLock sessionGuard (sessionLock_);
        staleBinding = ! shouldAcceptFetchedPart (
            sessionBindingEpoch_ == requestedBindingEpoch,
            boundSessionId_ == requestedSessionId,
            (
                requestedSessionId.isEmpty()
                || fresh->sessionId == requestedSessionId
            )
        );
        if (! staleBinding)
        {
            if (boundSessionId_.isEmpty() && fresh->sessionId.isNotEmpty())
            {
                boundSessionId_ = fresh->sessionId;
                ++sessionBindingEpoch_;
            }

            if (! parametersHydratedFromPart_.exchange (true))
                hydrateParametersFromPart (*fresh);

            // The digest was calculated before taking either lock, so this
            // nested section is constant-time regardless of part size.
            const juce::SpinLock::ScopedLockType partGuard (partLock_);
            const auto playbackChanged = (
                part_ == nullptr
                || part_->playbackFingerprint != fresh->playbackFingerprint
            );
            fresh->revision = playbackChanged
                ? partRevisionCounter_.fetch_add (1, std::memory_order_relaxed) + 1
                : part_->revision;
            if (playbackChanged)
            {
                retiredPart = std::move (part_);
                part_ = fresh;
            }
        }
    }
    retirePart (std::move (retiredPart));
    if (staleBinding)
    {
        refreshRequested_ = true;
        notify();
        return;
    }

    {
        const juce::ScopedLock l (adviceLock_);
        articulationNotice_ = fresh->bassArticulationNotice;
        performanceControlsNotice_ = fresh->bassPerformanceControlsNotice;
        grooveSourceNotice_ = fresh->grooveSourceNotice;
    }

    if (! updateStatus)
        return;
    {
        auto scaleLabel = fresh->scale.replaceCharacter ('_', ' ');
        auto status = fresh->key + " " + scaleLabel + " (confirmed) | "
                      + juce::String (fresh->notes.size()) + " notes | "
                      + juce::String (fresh->automation.size()) + " expressive events | "
                      + juce::String (fresh->barCount) + " bars | ";
        if (fresh->grooveSourceStatusKnown)
        {
            status += fresh->grooveSourceReady
                ? "beat source connected ("
                    + juce::String (fresh->grooveSourceFrameCount)
                    + " frames)"
                : "BEAT SOURCE NOT CONNECTED";
        }
        else
        {
            status += fresh->preview.contains ("locked to the reference groove")
                ? "reference-locked"
                : (fresh->preview.contains ("too thin")
                       ? "no reference lock (thin evidence)"
                       : "standard pocket");
        }
        if (fresh->bassArticulationNotice.isNotEmpty())
            status += " | " + fresh->bassArticulationNotice;
        if (fresh->bassPerformanceControlsNotice.isNotEmpty())
            status += " | " + fresh->bassPerformanceControlsNotice;
        setStatus (status);
    }
}

void SessionPlayerMidiFXProcessor::hydrateParametersFromPart (const BassPart& part)
{
    const auto setParameter = [this] (const juce::String& parameterId, float value)
    {
        if (auto* parameter = apvts.getParameter (parameterId))
            parameter->setValueNotifyingHost (parameter->convertTo0to1 (value));
    };

    const auto styleIndex = juce::jmax (0, styleChoices.indexOf (part.bassStyle));
    const auto playerIndex = juce::jmax (
        0, playerEngineIds.indexOf (part.bassPlayer));
    auto instrumentId = part.bassInstrument;
    // Session state still accepts these legacy instruments, while this
    // control intentionally exposes neutral families. Hydrate to the matching
    // family so Touch capabilities remain honest across app/plugin handoff.
    if (instrumentId == "slap_bass")
        instrumentId = "finger_bass";
    else if (instrumentId == "synth_bass")
        instrumentId = "sub_bass";
    const auto instrumentIndex = juce::jmax (
        0, instrumentEngineIds.indexOf (instrumentId));
    const auto touchIndex = juce::jmax (
        0, touchEngineIds.indexOf (part.bassArticulationFocus));
    setParameter ("style", (float) styleIndex);
    setParameter ("player", (float) playerIndex);
    setParameter ("instrument", (float) instrumentIndex);
    setParameter ("touch", (float) touchIndex);
    setParameter ("activity", juce::jlimit (-1.0f, 1.0f, part.bassDensityBias));
    setParameter ("lock", juce::jlimit (0.0f, 1.0f, part.lockToGroove));
    setParameter ("expression", juce::jlimit (0.0f, 1.0f, part.bassExpression));
    setParameter ("ghost", juce::jlimit (0.0f, 1.0f, part.ghostAmount));
    setParameter ("mute", juce::jlimit (0.0f, 1.0f, part.muteAmount));
    setParameter ("slide", juce::jlimit (0.0f, 1.0f, part.slideAmount));
    setParameter ("legato", juce::jlimit (0.0f, 1.0f, part.legatoAmount));
    setParameter (
        "timing_humanize",
        juce::jlimit (0.0f, 1.0f, part.timingHumanize));
    setParameter (
        "velocity_humanize",
        juce::jlimit (0.0f, 1.0f, part.velocityHumanize));
}

void SessionPlayerMidiFXProcessor::fetchAdvice()
{
    const auto sessionId = boundSessionId();
    if (sessionId.isEmpty())
        return;
    auto* instrumentParam = dynamic_cast<juce::AudioParameterChoice*> (apvts.getParameter ("instrument"));
    const auto instrumentIndex = instrumentParam ? instrumentParam->getIndex() : 0;
    juce::URL url { apiBaseUrl_ + "/advice" };
    url = url.withParameter ("session_id", sessionId)
             .withParameter ("bass_instrument", instrumentEngineIds[instrumentIndex]);
    auto options = juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inAddress)
                       .withConnectionTimeoutMs (3000);
    if (auto stream = url.createInputStream (options))
    {
        const auto parsed = juce::JSON::parse (stream->readEntireStreamAsString());
        if (parsed.isObject())
        {
            auto text = parsed.getProperty ("summary", "").toString();
            juce::StringArray pathLabels;
            if (auto* paths = parsed.getProperty ("paths", juce::var()).getArray())
                for (const auto& path : *paths)
                    pathLabels.add (path.getProperty ("label", "").toString());
            if (! pathLabels.isEmpty())
                text += "\nSuggested paths: " + pathLabels.joinIntoString (" | ");
            const juce::ScopedLock l (adviceLock_);
            advice_ = text;
        }
    }
}

void SessionPlayerMidiFXProcessor::fetchHistory()
{
    const auto sessionId = boundSessionId();
    if (sessionId.isEmpty())
        return;
    juce::URL url { apiBaseUrl_ + "/history" };
    url = url.withParameter ("session_id", sessionId);
    auto options = juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inAddress)
                       .withConnectionTimeoutMs (3000);
    if (auto stream = url.createInputStream (options))
    {
        const auto parsed = juce::JSON::parse (stream->readEntireStreamAsString());
        if (! parsed.isObject())
            return;
        const auto count = (int) parsed.getProperty ("count", 0);
        const auto keptCount = (int) parsed.getProperty ("kept_count", 0);
        const auto current = parsed.getProperty ("current_index", juce::var());
        const auto currentIsKept = (bool) parsed.getProperty ("current_is_kept", false);
        canRecallEarlier_ = (bool) parsed.getProperty ("can_previous", false);
        canRecallLater_ = (bool) parsed.getProperty ("can_next", false);

        juce::String text;
        if (count == 0)
            text = "No saved ideas yet";
        else if (current.isVoid())
            text = juce::String (count) + " earlier | " + juce::String (keptCount) + " kept";
        else
            text = "Idea " + juce::String ((int) current) + "/" + juce::String (count)
                   + " | " + juce::String (keptCount) + " kept"
                   + (currentIsKept ? " | KEPT" : "");
        const juce::ScopedLock l (historyLock_);
        history_ = text;
    }
}

std::shared_ptr<const BassPart> SessionPlayerMidiFXProcessor::currentPart() const
{
    const juce::SpinLock::ScopedLockType l (partLock_);
    return part_;
}

void SessionPlayerMidiFXProcessor::retirePart (
    std::shared_ptr<const BassPart> part)
{
    const juce::ScopedLock l (retiredPartsLock_);
    if (part != nullptr)
        retiredParts_.push_back (std::move (part));

    // Only non-audio callers enter this queue. Once the queue owns the sole
    // remaining reference, erasing here guarantees that the part's note
    // vector is destroyed away from processBlock.
    retiredParts_.erase (
        std::remove_if (
            retiredParts_.begin(), retiredParts_.end(),
            [] (const std::shared_ptr<const BassPart>& retired)
            {
                return retired.use_count() == 1;
            }),
        retiredParts_.end());
}

void SessionPlayerMidiFXProcessor::collectRetiredParts()
{
    retirePart ({});
}

juce::String SessionPlayerMidiFXProcessor::boundSessionId() const
{
    const juce::ScopedLock l (sessionLock_);
    return boundSessionId_;
}

void SessionPlayerMidiFXProcessor::requestRegenerate()
{
    regenerateRequestMode_.store (
        kRegenerateOrdinary,
        std::memory_order_release);
    notify();
}

void SessionPlayerMidiFXProcessor::requestNewBeatReset()
{
    regenerateRequestMode_.store (
        kRegenerateNewBeat,
        std::memory_order_release);
    notify();
}

void SessionPlayerMidiFXProcessor::requestCommand (const juce::String& text)
{
    {
        const juce::ScopedLock l (commandLock_);
        pendingCommand_ = text.trim();
    }
    notify();
}

void SessionPlayerMidiFXProcessor::requestKeep()
{
    keepRequested_ = true;
    notify();
}

void SessionPlayerMidiFXProcessor::requestHistoryStep (int direction)
{
    historyStepRequested_ = direction < 0 ? -1 : 1;
    notify();
}

void SessionPlayerMidiFXProcessor::setStatus (const juce::String& s)
{
    const juce::ScopedLock l (statusLock_);
    status_ = s;
}

juce::String SessionPlayerMidiFXProcessor::statusText() const
{
    const juce::ScopedLock l (statusLock_);
    return status_;
}

juce::String SessionPlayerMidiFXProcessor::adviceText() const
{
    const juce::ScopedLock l (adviceLock_);
    auto text = advice_;
    if (articulationNotice_.isNotEmpty())
        text += "\n" + articulationNotice_;
    if (performanceControlsNotice_.isNotEmpty())
        text += "\n" + performanceControlsNotice_;
    if (grooveSourceNotice_.isNotEmpty())
        text += "\n" + grooveSourceNotice_;
    return text;
}

juce::String SessionPlayerMidiFXProcessor::historyText() const
{
    const juce::ScopedLock l (historyLock_);
    return history_;
}

// ---- transport-synced playback ---------------------------------------------

void SessionPlayerMidiFXProcessor::allNotesOff (juce::MidiBuffer& midi, int samplePos)
{
    for (const auto& a : active_)
        midi.addEvent (juce::MidiMessage::noteOff (1, a.pitch), samplePos);
    midi.addEvent (juce::MidiMessage::allNotesOff (1), samplePos);
    active_.clear();
}

void SessionPlayerMidiFXProcessor::resetExpression (
    juce::MidiBuffer& midi,
    int samplePos)
{
    midi.addEvent (juce::MidiMessage::allControllersOff (1), samplePos);
    // Pitch-bend sensitivity is part configuration, not loop-relative
    // performance automation. Re-negotiate it after every reset so a
    // phase-rotated slide can never arrive before its RPN setup.
    midi.addEvent (juce::MidiMessage::controllerEvent (1, 101, 0), samplePos);
    midi.addEvent (juce::MidiMessage::controllerEvent (1, 100, 0), samplePos);
    midi.addEvent (
        juce::MidiMessage::controllerEvent (
            1,
            6,
            kPitchBendRangeSemitones),
        samplePos);
    midi.addEvent (juce::MidiMessage::controllerEvent (1, 38, 0), samplePos);
    midi.addEvent (juce::MidiMessage::controllerEvent (1, 101, 127), samplePos);
    midi.addEvent (juce::MidiMessage::controllerEvent (1, 100, 127), samplePos);
    midi.addEvent (juce::MidiMessage::pitchWheel (1, 8192), samplePos);
}

void SessionPlayerMidiFXProcessor::resetPlaybackState (
    juce::MidiBuffer& midi,
    int samplePos)
{
    allNotesOff (midi, samplePos);
    resetExpression (midi, samplePos);
}

void SessionPlayerMidiFXProcessor::processBlock (juce::AudioBuffer<float>& buffer,
                                                 juce::MidiBuffer& midi)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();
    midi.clear(); // we own this lane's MIDI; incoming notes would double-trigger

    const int numSamples = buffer.getNumSamples() > 0 ? buffer.getNumSamples()
                                                      : (int) (sampleRate_ / 100.0);

    auto part = currentPart();
    bool resetAtBlockStart = false;
    if (
        part != nullptr
        && shouldResetForPartRevision (activePartRevision_, part->revision)
    )
    {
        resetPlaybackState (midi, 0);
        resetAtBlockStart = true;
        activePartRevision_ = part->revision;
        wasPlaying_ = false;
        lastPpq_ = -1.0;
        lastBlockBeats_ = 0.0;
    }
    auto* playhead = getPlayHead();
    const auto pos = playhead != nullptr ? playhead->getPosition() : juce::nullopt;
    if (pos.hasValue())
    {
        const auto reportedBpm = pos->getBpm().orFallback (0.0);
        if (isUsableReportedHostTempo (reportedBpm))
            latestValidHostTempo_.store (
                reportedBpm,
                std::memory_order_relaxed);
    }
    const bool playing = pos.hasValue() && pos->getIsPlaying();
    const bool transportWasRunning = transportRunning_.exchange (playing);
    if (playing != transportWasRunning)
    {
        if (playing)
            refreshRequested_ = true;
    }

    if (! playing || part == nullptr || part->notes.empty())
    {
        if (wasPlaying_ && ! resetAtBlockStart)
            resetPlaybackState (midi, 0);
        wasPlaying_ = false;
        lastPpq_ = -1.0;
        lastBlockBeats_ = 0.0;
        return;
    }
    const bool startingPlayback = ! wasPlaying_;
    wasPlaying_ = true;

    const double bpm = pos->getBpm().orFallback (120.0);
    const double ppq = pos->getPpqPosition().orFallback (0.0);
    const double beatsPerSample = (bpm / 60.0) / sampleRate_;
    const double blockBeats = beatsPerSample * numSamples;
    const double loopLen = part->loopBeats();

    // A seek, cycle jump, or host discontinuity invalidates every outstanding
    // note-off from the previous transport position.
    const bool crossedTimelineZero = (
        ! startingPlayback && lastPpq_ < 0.0 && ppq >= 0.0
    );
    bool transportDiscontinuity = startingPlayback || crossedTimelineZero;
    if (transportDiscontinuity && ! resetAtBlockStart)
    {
        resetPlaybackState (midi, 0);
        resetAtBlockStart = true;
    }
    if (lastPpq_ >= 0.0)
    {
        const double expectedPpq = lastPpq_ + lastBlockBeats_;
        const double tolerance = juce::jmax (1.0e-4, blockBeats * 0.25);
        if (std::abs (ppq - expectedPpq) > tolerance)
        {
            if (! resetAtBlockStart)
            {
                resetPlaybackState (midi, 0);
                resetAtBlockStart = true;
            }
            transportDiscontinuity = true;
        }
    }

    // Logic primes MIDI FX with a negative-PPQ block before bar 1. Clamping
    // that position to zero made the downbeat note sound during the preceding
    // beat. Stay silent through pre-roll; the zero-crossing above lets the
    // next block catch the bar-one note even if it begins a few samples late.
    if (ppq < 0.0)
    {
        if (! active_.empty() && ! resetAtBlockStart)
            resetPlaybackState (midi, 0);
        lastPpq_ = ppq;
        lastBlockBeats_ = blockBeats;
        return;
    }

    // loop-relative window [loopPos, loopPos + blockBeats)
    const double loopPos = std::fmod (juce::jmax (0.0, ppq), loopLen);
    if (resetAtBlockStart)
        restoreExpressionStateAt (*part, loopPos, midi, 0);
    const double catchUpWindow = juce::jmax (1.0e-4, blockBeats * 2.0);
    const auto offsetForPartBeat = [&] (double partBeat)
    {
        const double shiftedBeat = wrapBeatToLoop (
            partBeat + part->phaseOffsetBeats, loopLen);
        double offsetBeats = shiftedBeat - loopPos;
        if (offsetBeats < 0.0)
        {
            if (transportDiscontinuity && -offsetBeats <= catchUpWindow)
                offsetBeats = 0.0;
            else
                offsetBeats += loopLen;
        }
        return offsetBeats;
    };

    dueNoteOns_.clear();
    for (size_t noteIndex = 0; noteIndex < part->notes.size(); ++noteIndex)
    {
        const auto& n = part->notes[noteIndex];
        // Logic can present the first render block a few samples after the
        // requested locator. offsetForPartBeat catches up an event exactly on
        // that locator, while genuinely future wrapped events stay future.
        const double offsetBeats = offsetForPartBeat (n.startBeats);
        if (offsetBeats < blockBeats)
        {
            const int samplePos = juce::jlimit (0, numSamples - 1,
                                                (int) (offsetBeats / beatsPerSample));
            dueNoteOns_.push_back ({
                static_cast<int> (noteIndex),
                offsetBeats,
                samplePos,
            });
        }
    }
    std::sort (
        dueNoteOns_.begin(), dueNoteOns_.end(),
        [] (const DueNoteOn& left, const DueNoteOn& right)
        {
            if (left.samplePos != right.samplePos)
                return left.samplePos < right.samplePos;
            if (left.offsetBeats < right.offsetBeats)
                return true;
            if (right.offsetBeats < left.offsetBeats)
                return false;
            return left.noteIndex < right.noteIndex;
        });

    // Reset expressive state at every part-loop boundary. The half-open
    // block window means a boundary exactly at the next block starts at
    // sample zero there, rather than one sample early here.
    const double boundaryIndex = std::ceil ((ppq - 1.0e-10) / loopLen);
    const double boundaryOffsetBeats = boundaryIndex * loopLen - ppq;
    if (
        boundaryOffsetBeats >= -1.0e-10
        && boundaryOffsetBeats < blockBeats
    )
    {
        const int resetSample = juce::jlimit (
            0,
            numSamples - 1,
            (int) (
                juce::jmax (0.0, boundaryOffsetBeats) / beatsPerSample));
        if (resetSample != 0 || ! resetAtBlockStart)
        {
            resetExpression (midi, resetSample);
            restoreExpressionStateAt (*part, 0.0, midi, resetSample);
        }
    }

    dueAutomation_.clear();
    for (
        size_t eventIndex = 0;
        eventIndex < part->automation.size();
        ++eventIndex
    )
    {
        const auto& event = part->automation[eventIndex];
        const double offsetBeats = offsetForPartBeat (event.beat);
        if (offsetBeats < blockBeats)
        {
            const int samplePos = juce::jlimit (
                0,
                numSamples - 1,
                (int) (offsetBeats / beatsPerSample));
            dueAutomation_.push_back ({
                static_cast<int> (eventIndex),
                offsetBeats,
                samplePos,
            });
        }
    }
    std::sort (
        dueAutomation_.begin(), dueAutomation_.end(),
        [] (const DueAutomation& left, const DueAutomation& right)
        {
            if (left.samplePos != right.samplePos)
                return left.samplePos < right.samplePos;
            if (left.offsetBeats < right.offsetBeats)
                return true;
            if (right.offsetBeats < left.offsetBeats)
                return false;
            return left.eventIndex < right.eventIndex;
        });

    // Automation is inserted before any note-ons below. MidiBuffer preserves
    // insertion order for equal sample positions, so RPN/CC and pitch bend
    // are visible to the instrument before an attack at that exact sample.
    for (const auto& due : dueAutomation_)
    {
        const auto& event = part->automation[
            static_cast<size_t> (due.eventIndex)];
        if (event.type == BassPartAutomationEvent::Type::controlChange)
        {
            midi.addEvent (
                juce::MidiMessage::controllerEvent (
                    event.channel,
                    event.controller,
                    event.value),
                due.samplePos);
        }
        else
        {
            midi.addEvent (
                juce::MidiMessage::pitchWheel (
                    event.channel,
                    pitchBendPositionFromSigned (event.value)),
                due.samplePos);
        }
    }

    const auto emitActiveOffsThrough = [&] (int samplePos)
    {
        while (true)
        {
            auto earliest = active_.end();
            for (auto it = active_.begin(); it != active_.end(); ++it)
            {
                if (
                    activeOffPrecedesOrTiesOn (it->samplesLeft, samplePos)
                    && (
                        earliest == active_.end()
                        || it->samplesLeft < earliest->samplesLeft
                    )
                )
                    earliest = it;
            }
            if (earliest == active_.end())
                return;
            midi.addEvent (
                juce::MidiMessage::noteOff (1, earliest->pitch),
                juce::jlimit (0, numSamples - 1, earliest->samplesLeft));
            active_.erase (earliest);
        }
    };

    for (const auto& due : dueNoteOns_)
    {
        const auto& n = part->notes[static_cast<size_t> (due.noteIndex)];
        const int samplePos = due.samplePos;
        // Natural expiries win exact-time ties. A later expiry remains active
        // so the retrigger guard below can replace it at this earlier onset.
        emitActiveOffsThrough (samplePos);
        // Re-trigger state must be updated in audible sample order. A phase
        // rotation can wrap late source notes ahead of early source notes.
        for (auto it = active_.begin(); it != active_.end();)
        {
            if (it->pitch == n.pitch)
            {
                midi.addEvent (juce::MidiMessage::noteOff (1, it->pitch), samplePos);
                it = active_.erase (it);
            }
            else
                ++it;
        }
        midi.addEvent (juce::MidiMessage::noteOn (1, n.pitch, (juce::uint8) n.velocity),
                       samplePos);
        const int durationSamples = juce::jmax (
            1, (int) std::round (n.durBeats / beatsPerSample));
        const int noteEndSample = samplePos + durationSamples;
        active_.push_back ({ n.pitch, noteEndSample });
    }

    // Finish all expiries that land inside this block. A note ending exactly
    // at the block boundary is carried to sample zero of the next block.
    emitActiveOffsThrough (numSamples - 1);
    for (auto& active : active_)
        active.samplesLeft -= numSamples;

    lastPpq_ = ppq;
    lastBlockBeats_ = blockBeats;
}

// ---- state (the Meter Core v0.7.3 lesson: never ship empty stubs) ----------

void SessionPlayerMidiFXProcessor::getStateInformation (juce::MemoryBlock& destData)
{
    auto state = apvts.copyState();
    state.setProperty ("session_id", boundSessionId(), nullptr);
    if (auto xml = state.createXml())
        copyXmlToBinary (*xml, destData);
}

void SessionPlayerMidiFXProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    if (auto xml = getXmlFromBinary (data, sizeInBytes))
        if (xml->hasTagName (apvts.state.getType()))
        {
            auto state = juce::ValueTree::fromXml (*xml);
            const auto restoredSessionId = state.getProperty ("session_id").toString();
            state.removeProperty ("session_id", nullptr);
            std::shared_ptr<const BassPart> retiredPart;
            {
                // Serialize restore against fetchPart's validate/hydrate/publish
                // transaction. Both paths take sessionLock_ before partLock_.
                const juce::ScopedLock sessionGuard (sessionLock_);
                apvts.replaceState (state);
                if (restoredSessionId.isNotEmpty())
                {
                    parametersHydratedFromPart_ = false;
                    boundSessionId_ = restoredSessionId;
                }
                // Every restore is a binding-generation boundary, including a
                // legacy state blob with no saved session id.
                ++sessionBindingEpoch_;

                // The polling thread starts with the processor and may have
                // fetched "latest" before Logic restored project state. Never
                // audition that transient part under the restored binding.
                const juce::SpinLock::ScopedLockType partGuard (partLock_);
                retiredPart = std::move (part_);
            }
            retirePart (std::move (retiredPart));
            refreshRequested_ = true;
            notify();
        }
}

juce::AudioProcessorEditor* SessionPlayerMidiFXProcessor::createEditor()
{
    return new SessionPlayerMidiFXEditor (*this);
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new SessionPlayerMidiFXProcessor();
}
