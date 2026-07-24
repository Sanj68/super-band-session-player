#include "PluginProcessor.h"
#include "PluginEditor.h"

namespace
{
constexpr auto kBassPartUrl   = "http://127.0.0.1:8000/api/plugin/bass-part";
constexpr auto kRegenerateUrl = "http://127.0.0.1:8000/api/plugin/regenerate";
constexpr auto kCommandUrl    = "http://127.0.0.1:8000/api/plugin/command";
constexpr auto kAdviceUrl     = "http://127.0.0.1:8000/api/plugin/advice";
constexpr auto kKeepUrl       = "http://127.0.0.1:8000/api/plugin/keep";
constexpr auto kHistoryUrl    = "http://127.0.0.1:8000/api/plugin/history";
constexpr auto kNavigateUrl   = "http://127.0.0.1:8000/api/plugin/history/navigate";
constexpr int  kPollMs = 2000;
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
        juce::NormalisableRange<float> (0.0f, 1.0f, 0.01f), 0.5f));
    layout.add (std::make_unique<juce::AudioParameterChoice> (
        juce::ParameterID { "instrument", 1 }, "Bass Family",
        SessionPlayerMidiFXProcessor::instrumentChoices, 0));
    return layout;
}

SessionPlayerMidiFXProcessor::SessionPlayerMidiFXProcessor()
    : juce::AudioProcessor (BusesProperties()), // MIDI effect: no audio buses
      juce::Thread ("sp-midifx-poll"),
      apvts (*this, nullptr, "PARAMS", makeLayout())
{
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
}

// ---- engine polling --------------------------------------------------------

void SessionPlayerMidiFXProcessor::run()
{
    while (! threadShouldExit())
    {
        if (keepRequested_.exchange (false))
        {
            setStatus ("keeping idea...");
            juce::DynamicObject::Ptr body = new juce::DynamicObject();
            const auto sessionId = boundSessionId();
            if (sessionId.isNotEmpty())
                body->setProperty ("session_id", sessionId);
            const auto json = juce::JSON::toString (juce::var (body.get()), true);
            juce::URL url { kKeepUrl };
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
            const auto goingEarlier = historyStep < 0;
            setStatus (goingEarlier ? "recalling earlier idea..." : "recalling later idea...");
            juce::DynamicObject::Ptr body = new juce::DynamicObject();
            const auto sessionId = boundSessionId();
            if (sessionId.isNotEmpty())
                body->setProperty ("session_id", sessionId);
            body->setProperty ("direction", goingEarlier ? "previous" : "next");
            const auto json = juce::JSON::toString (juce::var (body.get()), true);
            juce::URL url { kNavigateUrl };
            auto options = juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inPostData)
                               .withExtraHeaders ("Content-Type: application/json")
                               .withConnectionTimeoutMs (4000);
            if (auto stream = url.withPOSTData (json).createInputStream (options))
            {
                const auto parsed = juce::JSON::parse (stream->readEntireStreamAsString());
                setStatus (parsed.getProperty ("message", "Idea recalled.").toString());
                parametersHydratedFromPart_ = false;
                fetchPart (false);
            }
            else
                setStatus (goingEarlier ? "no earlier idea available" : "no later idea available");
            fetchHistory();
        }

        if (regenerateRequested_.exchange (false))
        {
            setStatus ("regenerating...");
            auto* styleParam = dynamic_cast<juce::AudioParameterChoice*> (apvts.getParameter ("style"));
            auto* playerParam = dynamic_cast<juce::AudioParameterChoice*> (apvts.getParameter ("player"));
            auto* instrumentParam = dynamic_cast<juce::AudioParameterChoice*> (apvts.getParameter ("instrument"));
            auto lockValue = apvts.getRawParameterValue ("lock")->load();
            auto expressionValue = apvts.getRawParameterValue ("expression")->load();
            juce::DynamicObject::Ptr body = new juce::DynamicObject();
            const auto sessionId = boundSessionId();
            if (sessionId.isNotEmpty())
                body->setProperty ("session_id", sessionId);
            body->setProperty ("bass_style", styleChoices[styleParam ? styleParam->getIndex() : 0]);
            body->setProperty ("bass_player", playerEngineIds[playerParam ? playerParam->getIndex() : 0]);
            body->setProperty ("bass_instrument",
                               instrumentEngineIds[instrumentParam ? instrumentParam->getIndex() : 0]);
            body->setProperty ("lock_to_groove", (double) lockValue);
            body->setProperty ("bass_expression", (double) expressionValue);
            const auto json = juce::JSON::toString (juce::var (body.get()), true);

            juce::URL url { kRegenerateUrl };
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
            setStatus ("\"" + command + "\" ...");
            juce::DynamicObject::Ptr body = new juce::DynamicObject();
            const auto sessionId = boundSessionId();
            if (sessionId.isNotEmpty())
                body->setProperty ("session_id", sessionId);
            body->setProperty ("text", command);
            const auto json = juce::JSON::toString (juce::var (body.get()), true);
            juce::URL url { kCommandUrl };
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

        fetchPart();
        fetchAdvice();
        fetchHistory();

        for (int i = 0; i < kPollMs / 100 && ! threadShouldExit() && ! regenerateRequested_.load(); ++i)
            wait (100);
    }
}

void SessionPlayerMidiFXProcessor::fetchPart (bool updateStatus)
{
    juce::URL url { kBassPartUrl };
    const auto sessionId = boundSessionId();
    if (sessionId.isNotEmpty())
        url = url.withParameter ("session_id", sessionId);
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
    fresh->sessionId   = parsed.getProperty ("session_id", "").toString();
    fresh->key         = parsed.getProperty ("key", "").toString();
    fresh->scale       = parsed.getProperty ("scale", "").toString();
    fresh->bassStyle   = parsed.getProperty ("bass_style", "supportive").toString();
    fresh->barCount    = (int) parsed.getProperty ("bar_count", 0);
    fresh->beatsPerBar = (int) parsed.getProperty ("beats_per_bar", 4);
    fresh->preview     = parsed.getProperty ("preview", "").toString();
    fresh->bassInstrument = parsed.getProperty ("bass_instrument", "finger_bass").toString();
    const auto lockValue = parsed.getProperty ("lock_to_groove", 0.5);
    fresh->lockToGroove = lockValue.isVoid() ? 0.5f : (float) lockValue;
    fresh->bassExpression = (float) parsed.getProperty ("bass_expression", 0.5);
    if (auto* arr = parsed.getProperty ("notes", juce::var()).getArray())
    {
        fresh->notes.reserve ((size_t) arr->size());
        for (const auto& v : *arr)
        {
            BassPartNote n;
            n.pitch      = (int) v.getProperty ("pitch", 36);
            n.velocity   = juce::jlimit (1, 127, (int) v.getProperty ("velocity", 90));
            n.startBeats = (double) v.getProperty ("start_beats", 0.0);
            n.durBeats   = juce::jmax (0.05, (double) v.getProperty ("dur_beats", 0.5));
            fresh->notes.push_back (n);
        }
    }
    bindSession (fresh->sessionId);
    if (! parametersHydratedFromPart_.exchange (true))
        hydrateParametersFromPart (*fresh);

    {
        const juce::SpinLock::ScopedLockType l (partLock_);
        part_ = std::move (fresh);
    }
    if (! updateStatus)
        return;
    {
        const juce::SpinLock::ScopedLockType l (partLock_);
        auto scaleLabel = part_->scale.replaceCharacter ('_', ' ');
        setStatus (part_->key + " " + scaleLabel + " (confirmed) | "
                   + juce::String (part_->notes.size()) + " notes | "
                   + juce::String (part_->barCount) + " bars | "
                   + (part_->preview.contains ("locked to the reference groove")
                          ? "reference-locked"
                          : (part_->preview.contains ("too thin") ? "no reference lock (thin evidence)"
                                                                  : "standard pocket")));
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
    const auto instrumentIndex = juce::jmax (
        0, instrumentEngineIds.indexOf (part.bassInstrument));
    setParameter ("style", (float) styleIndex);
    setParameter ("instrument", (float) instrumentIndex);
    setParameter ("lock", juce::jlimit (0.0f, 1.0f, part.lockToGroove));
    setParameter ("expression", juce::jlimit (0.0f, 1.0f, part.bassExpression));
}

void SessionPlayerMidiFXProcessor::fetchAdvice()
{
    const auto sessionId = boundSessionId();
    if (sessionId.isEmpty())
        return;
    auto* instrumentParam = dynamic_cast<juce::AudioParameterChoice*> (apvts.getParameter ("instrument"));
    const auto instrumentIndex = instrumentParam ? instrumentParam->getIndex() : 0;
    juce::URL url { kAdviceUrl };
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
    juce::URL url { kHistoryUrl };
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

juce::String SessionPlayerMidiFXProcessor::boundSessionId() const
{
    const juce::ScopedLock l (sessionLock_);
    return boundSessionId_;
}

void SessionPlayerMidiFXProcessor::bindSession (const juce::String& sessionId,
                                                bool replaceExisting)
{
    if (sessionId.isEmpty())
        return;
    const juce::ScopedLock l (sessionLock_);
    if (replaceExisting || boundSessionId_.isEmpty())
    {
        if (replaceExisting || boundSessionId_ != sessionId)
            parametersHydratedFromPart_ = false;
        boundSessionId_ = sessionId;
    }
}

void SessionPlayerMidiFXProcessor::requestRegenerate()
{
    regenerateRequested_ = true;
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
    return advice_;
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
    active_.clear();
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
    auto* playhead = getPlayHead();
    const auto pos = playhead != nullptr ? playhead->getPosition() : juce::nullopt;
    const bool playing = pos.hasValue() && pos->getIsPlaying();

    if (! playing || part == nullptr || part->notes.empty())
    {
        if (wasPlaying_)
            allNotesOff (midi, 0);
        wasPlaying_ = false;
        lastPpq_ = -1.0;
        lastBlockBeats_ = 0.0;
        return;
    }
    wasPlaying_ = true;

    const double bpm = pos->getBpm().orFallback (120.0);
    const double ppq = pos->getPpqPosition().orFallback (0.0);
    const double beatsPerSample = (bpm / 60.0) / sampleRate_;
    const double blockBeats = beatsPerSample * numSamples;
    const double loopLen = part->loopBeats();

    // A seek, cycle jump, or host discontinuity invalidates every outstanding
    // note-off from the previous transport position.
    if (lastPpq_ >= 0.0)
    {
        const double expectedPpq = lastPpq_ + lastBlockBeats_;
        const double tolerance = juce::jmax (1.0e-4, blockBeats * 0.25);
        if (std::abs (ppq - expectedPpq) > tolerance)
            allNotesOff (midi, 0);
    }

    // note-offs scheduled in samples
    for (auto it = active_.begin(); it != active_.end();)
    {
        if (it->samplesLeft <= numSamples)
        {
            midi.addEvent (juce::MidiMessage::noteOff (1, it->pitch),
                           juce::jlimit (0, numSamples - 1, it->samplesLeft));
            it = active_.erase (it);
        }
        else
        {
            it->samplesLeft -= numSamples;
            ++it;
        }
    }

    // loop-relative window [loopPos, loopPos + blockBeats)
    const double loopPos = std::fmod (juce::jmax (0.0, ppq), loopLen);
    for (const auto& n : part->notes)
    {
        double offsetBeats = n.startBeats - loopPos;
        if (offsetBeats < 0.0)
            offsetBeats += loopLen; // wraps into this block only if close enough
        if (offsetBeats < blockBeats)
        {
            const int samplePos = juce::jlimit (0, numSamples - 1,
                                                (int) (offsetBeats / beatsPerSample));
            // re-trigger guard: kill an already-sounding copy of this pitch
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
            if (noteEndSample < numSamples)
                midi.addEvent (juce::MidiMessage::noteOff (1, n.pitch), noteEndSample);
            else
                active_.push_back ({ n.pitch, noteEndSample - numSamples });
        }
    }

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
            apvts.replaceState (state);
            bindSession (restoredSessionId, true);
            // The polling thread starts with the processor and may have fetched
            // "latest" before Logic restored project state. Never audition that
            // transient part under the restored binding.
            const juce::SpinLock::ScopedLockType l (partLock_);
            part_.reset();
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
