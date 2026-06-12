#include "PluginProcessor.h"
#include "PluginEditor.h"

namespace
{
constexpr auto kBassPartUrl   = "http://127.0.0.1:8000/api/plugin/bass-part";
constexpr auto kRegenerateUrl = "http://127.0.0.1:8000/api/plugin/regenerate";
constexpr int  kPollMs = 2000;
}

const juce::StringArray SessionPlayerMidiFXProcessor::styleChoices {
    "supportive", "melodic", "rhythmic", "slap", "fusion"
};

// The musical depth lives in the personas — expose them as first-class
// (the v0.6 chassis shipped style-only and the result was "quite basic").
const juce::StringArray SessionPlayerMidiFXProcessor::playerChoices {
    "none", "james_jamerson", "pino", "bootsy", "marcus", "jaco_pastorius", "paul_chambers"
};

static juce::AudioProcessorValueTreeState::ParameterLayout makeLayout()
{
    juce::AudioProcessorValueTreeState::ParameterLayout layout;
    layout.add (std::make_unique<juce::AudioParameterChoice> (
        juce::ParameterID { "style", 1 }, "Style",
        SessionPlayerMidiFXProcessor::styleChoices, 0));
    layout.add (std::make_unique<juce::AudioParameterChoice> (
        juce::ParameterID { "player", 1 }, "Player",
        SessionPlayerMidiFXProcessor::playerChoices, 1)); // default: jamerson
    layout.add (std::make_unique<juce::AudioParameterFloat> (
        juce::ParameterID { "lock", 1 }, "Lock to Groove",
        juce::NormalisableRange<float> (0.0f, 1.0f, 0.01f), 0.5f));
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
}

// ---- engine polling --------------------------------------------------------

void SessionPlayerMidiFXProcessor::run()
{
    while (! threadShouldExit())
    {
        if (regenerateRequested_.exchange (false))
        {
            setStatus ("regenerating...");
            auto* styleParam = dynamic_cast<juce::AudioParameterChoice*> (apvts.getParameter ("style"));
            auto* playerParam = dynamic_cast<juce::AudioParameterChoice*> (apvts.getParameter ("player"));
            auto lockValue = apvts.getRawParameterValue ("lock")->load();
            juce::DynamicObject::Ptr body = new juce::DynamicObject();
            body->setProperty ("bass_style", styleChoices[styleParam ? styleParam->getIndex() : 0]);
            body->setProperty ("bass_player", playerChoices[playerParam ? playerParam->getIndex() : 0]);
            body->setProperty ("lock_to_groove", (double) lockValue);
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

        fetchPart();

        for (int i = 0; i < kPollMs / 100 && ! threadShouldExit() && ! regenerateRequested_.load(); ++i)
            wait (100);
    }
}

void SessionPlayerMidiFXProcessor::fetchPart()
{
    juce::URL url { kBassPartUrl };
    auto options = juce::URL::InputStreamOptions (juce::URL::ParameterHandling::inAddress)
                       .withConnectionTimeoutMs (3000);
    auto stream = url.createInputStream (options);
    if (stream == nullptr)
    {
        setStatus ("engine offline — start the Session Player backend");
        return;
    }
    const auto parsed = juce::JSON::parse (stream->readEntireStreamAsString());
    if (! parsed.isObject())
    {
        setStatus ("no bass part yet — generate one in the app");
        return;
    }

    auto fresh = std::make_shared<BassPart>();
    fresh->sessionId   = parsed.getProperty ("session_id", "").toString();
    fresh->barCount    = (int) parsed.getProperty ("bar_count", 0);
    fresh->beatsPerBar = (int) parsed.getProperty ("beats_per_bar", 4);
    fresh->preview     = parsed.getProperty ("preview", "").toString();
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

    {
        const juce::SpinLock::ScopedLockType l (partLock_);
        part_ = std::move (fresh);
    }
    {
        const juce::SpinLock::ScopedLockType l (partLock_);
        setStatus (juce::String (part_->notes.size()) + " notes · "
                   + juce::String (part_->barCount) + " bars · "
                   + (part_->preview.contains ("locked to the reference groove")
                          ? "reference-locked"
                          : (part_->preview.contains ("too thin") ? "no reference lock (thin evidence)"
                                                                  : "standard pocket")));
    }
}

std::shared_ptr<const BassPart> SessionPlayerMidiFXProcessor::currentPart() const
{
    const juce::SpinLock::ScopedLockType l (partLock_);
    return part_;
}

void SessionPlayerMidiFXProcessor::requestRegenerate()
{
    regenerateRequested_ = true;
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
        return;
    }
    wasPlaying_ = true;

    const double bpm = pos->getBpm().orFallback (120.0);
    const double ppq = pos->getPpqPosition().orFallback (0.0);
    const double beatsPerSample = (bpm / 60.0) / sampleRate_;
    const double blockBeats = beatsPerSample * numSamples;
    const double loopLen = part->loopBeats();

    // note-offs scheduled in samples
    for (auto it = active_.begin(); it != active_.end();)
    {
        if (it->samplesLeft <= numSamples)
        {
            midi.addEvent (juce::MidiMessage::noteOff (1, it->pitch),
                           juce::jmax (0, it->samplesLeft - 1));
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
            active_.push_back ({ n.pitch,
                                 (int) (n.durBeats / beatsPerSample) - samplePos });
        }
    }

    lastPpq_ = ppq;
}

// ---- state (the Meter Core v0.7.3 lesson: never ship empty stubs) ----------

void SessionPlayerMidiFXProcessor::getStateInformation (juce::MemoryBlock& destData)
{
    if (auto xml = apvts.copyState().createXml())
        copyXmlToBinary (*xml, destData);
}

void SessionPlayerMidiFXProcessor::setStateInformation (const void* data, int sizeInBytes)
{
    if (auto xml = getXmlFromBinary (data, sizeInBytes))
        if (xml->hasTagName (apvts.state.getType()))
            apvts.replaceState (juce::ValueTree::fromXml (*xml));
}

juce::AudioProcessorEditor* SessionPlayerMidiFXProcessor::createEditor()
{
    return new SessionPlayerMidiFXEditor (*this);
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new SessionPlayerMidiFXProcessor();
}
