# Session Player — Build Notes & Roadmap

Living document. Captures the agreed product direction, the current state,
and the staged plan that follows from the most recent strategic review.

## 1. Product North Star

Session Player is a **DAW-reactive, editable session musician**.

Core principle:

> session context + prompt/control → controlled editable MIDI performance.

Operating model — shared with PocketCarver:

> listen → analyse → respond intelligently.

Where PocketCarver listens to sidechain audio and creates *space*,
Session Player listens to session/audio context and creates *musical parts
that fit the space*. The two apps form a coherent family of "contextual
instruments": they pay attention to what is already there, then act.

Anchor sentence for every roadmap decision:

> Controlled editable MIDI performance, not black-box audio.

If a proposed feature cannot be justified against that sentence, it is
deferred or dropped.

## 2. What Session Player Is Not

- Not a Suno/Ace Studio clone.
- Not a black-box AI song generator.
- Not a lyrics / vocal / mastering / stem-export tool.
- Not a "make it sound like [artist]" style-transfer toy.
- Not a real-time live-play instrument (the engine renders bars on demand,
  in the same UX register as Logic's Drummer / Bass Player / Keyboard
  Player).
- Not a cloud / SaaS / multiplayer product. Local-first is part of the
  value.
- Not a custom MIDI extension format. Output stays General MIDI plus
  per-instrument profile overlays.

## 3. Current Stable Milestones

- **v0.1** — Logic MIDI export MVP. Backend + frontend + Tauri wrapper;
  full session MIDI export and per-lane MIDI download into Logic.
- **v0.2** — Custom chord progression support for bass. User-supplied chord
  symbols replace the hardcoded I–IV–V–I diatonic cycle; bass consumes the
  progression through the existing `ChordSegment` plumbing.
- **v0.2.1** — Supportive bass pocket tightening. Velocity / articulation /
  rest-bias polishing on the supportive style without changing the engine
  shape.

Tests: 29 passed. Engines exercised: drums, bass (baseline + phrase_v2),
chords, lead. Reference audio upload + analysis pipeline already present
but not yet driving generation in the demoable way v0.3 requires.

### 2026-07-24 controlled combined-source acceptance

The first combined-source audition exposed a harmonic-fit defect rather
than a scale-safety defect: a repeated B-flat was legal in D natural
minor but unsupported in the C bars of the source. The uploaded-audio
path had confirmed only a global key and silently supplied I-IV-V-I to
generation and scoring.

The bounded repair is now:

- audio analysis proposes an explicitly tentative repeating bar-level
  chord map;
- uploaded-audio sessions require separate confirmation of both
  key/scale and the bar-level map;
- a confirmed chord chart becomes the conditioning harmony used by the
  phrase engine and candidate scoring;
- supportive Phrase Engine v2 lines use confirmed chord tones plus
  controlled resolving approaches, rather than arbitrary global-scale
  passing tones;
- candidates with unsupported structural notes are rejected, with no
  unsafe final-fill fallback.

The exact combined Logic bounce still reads 87.598 BPM / 16 bars and
proposes `Bb | C | D | Gm` with four-bar repeat similarity 0.9913. The
chord confidences are low, so the map remains a user-confirmed
suggestion. The accepted browser run is
`cand_20260724T045514_2c69b4f6` in session
`92e7b255-8fba-43a3-8d8c-742d64a91778`.

All four downloaded takes are 88 BPM and 64 playable beats, with no
same-pitch overlaps, sub-25 ms notes, loop overruns, scale-unsafe notes
or chord-map guard violations. Pairwise note/slot/pitch Jaccard distance
is 0.267–0.409; 67.9–69.2% of note onsets land in each source bar's four
strongest detected kick slots at groove lock 0.70.

Verification: 486 backend tests passed and the frontend production build
passed. The validation-pack command currently reports 5/15, not the
11/15 stated in the morning handoff; this discrepancy predates the
bar-level harmony changes and must be reconciled separately rather than
reported as passing.

### 2026-07-24 controlled player-personality slice

The accepted four takes held the source pocket but were too closely
related. The next bounded slice now offers an explicit
`controlled_roles` candidate mode. It preserves the confirmed harmony,
Phrase Engine v2 and shared groove lock while generating one take for
each neutral musical purpose:

- **Pocket Keeper** — most restrained root/pocket reading;
- **Rhythmic Alternative** — more syncopated answers and shorter attacks;
- **Harmonic Alternative** — more chord-tone voice leading, constrained
  to the confirmed global scale as well as the confirmed chord map;
- **Performance Alternative** — conservative note choice with different
  accents, note lengths and laid-back timing.

The existing ranked hidden-pool workflow remains available as
`variation_mode=ranked` for backwards compatibility. Purposeful roles
require Phrase Engine v2 and the neutral player profile; the API fails
honestly instead of attaching role labels to engines that do not yet
implement them.

Real combined-source proof:

- session: `92e7b255-8fba-43a3-8d8c-742d64a91778`
- run: `cand_20260724T155518_40d9df54`
- source: 88 BPM, 16 bars, D natural minor, confirmed
  `Bb | C | D | Gm`, groove lock 0.70
- note counts by role: 43 / 65 / 53 / 54
- pairwise note/slot/pitch distance: 0.494–0.820 (previous accepted set:
  0.267–0.409)
- all takes: exactly 64 playable beats, no same-pitch overlaps, no
  sub-25 ms notes, no loop overruns and no D-natural-minor-unsafe notes.

The first real-source pass caught an F-sharp edge: the plain `D` chord
symbol is parsed as D major while the session scale is D natural minor.
The Harmonic Alternative now uses only the intersection of confirmed
chord tones and confirmed scale tones; controlled chromatic approaches
are likewise suppressed when they would break the confirmed scale.

The React controls remain the development/audition lab. The role IDs and
API contract are deliberately UI-neutral so the same four-purpose
comparison can be surfaced in the existing Logic MIDI FX chassis. Do not
grow a separate web-only product. The next DAW task is to expose this
role comparison and candidate selection in the AU, before Trilian or
another instrument in Logic. Public controls remain neutral; do not add
artist names to the new role system.

Verification: 490 backend tests passed and the frontend production build
passed.

### 2026-07-24 Logic plugin exploration direction

The Logic plugin is not a static preset panel. Its purpose is to take the
producer on a recoverable musical journey:

1. listen to and analyse the supplied reference;
2. offer one strong best-fit performance;
3. describe the source as a blend of evidenced traits, using familiar
   genre words only as orientation;
4. recommend an **Accompany** route that reinforces the source and a
   **Counterpoint** route that complements it;
5. let the user explore bounded alternatives, keep what works and return
   to any earlier idea.

The listening language should be honest and mixed: for example, "strong
offbeat space, a syncopated pocket and moderate harmonic colour —
reggae/funk with some jazz-like movement, confidence medium." The engine
acts on timing, density, harmonic colour, phrase shape and articulation;
it does not reduce a source to one genre label. Every recommendation must
say what it preserves and what it changes.

Instrument choice is part of the musical reasoning, not merely a sound
selection after MIDI generation. The intended pipeline is:

> source traits → musical role → performance intent → instrument
> capability profile → MIDI.

Start with neutral bass families: fingered, picked, fretless, upright,
slap and sub/synth. A fretless profile can favour sustained connections,
slides and legato arrivals; a sub profile must protect low-register space,
limit rapid retriggers and respect envelope length. Exact products such
as Trilian come later as render profiles that translate the same universal
intent into their keyswitches, CCs and articulation layout.

Product decisions from the first controlled-take audition:

- prioritise human expression;
- simple default view with advanced controls behind disclosure;
- one restrained-to-bold Character control;
- style-aware articulations chosen automatically;
- analyse first, then create a complete editable MIDI region;
- experimentation must be recoverable: no destructive "surprise me"
  without history and recall.

The first bounded implementation slice now provides:

- `bass_expression` from 0.0 to 1.0 across session and plugin APIs;
- midpoint 0.5 preserves the prior conservative grace/ghost behaviour;
- above the midpoint, deterministic style-aware rules can add muted
  punctuation and connected-note intent;
- hammer/slide destinations get a bounded generic legato fallback while
  later instrument profiles remain responsible for keyswitch/CC/pitch-bend
  translation;
- the AU exposes Character while keeping advanced text control collapsed;
- public AU automation uses neutral trait labels. Legacy named profile ids
  remain an internal compatibility map only.

Still deliberately unclaimed: source-style mixture advice, the
Accompany/Counterpoint recommendation engine, keep/lock/history/recall,
and direct Logic-region materialisation. Those are the next plugin-journey
work, not reasons to expand the React audition lab.

Verification for this slice: 495 backend tests passed; the AU Release
build completed and `auval -v aumi SpMx SOne` passed.

### 2026-07-24 listening advisor and bass-family constraints

The next plugin foundation is now implemented without making exploration
destructive.

The read-only `/api/plugin/advice` contract:

- measures neutral source traits: syncopation, eighth-note offbeat
  emphasis, musical space and harmonic colour;
- reports strength, confidence and plain evidence for every trait;
- uses reggae/funk/jazz language only as a secondary orientation;
- offers Accompany, Counterpoint and Explore routes;
- states what each route preserves and changes;
- adjusts its suggestions for the selected bass family;
- never regenerates or promotes a take.

The actual accepted 88 BPM combined source currently yields: "subtle
harmonic colour — jazz orientation; confidence medium." That restraint is
intentional. The confirmed chord map is trustworthy, but the combined
audio's style/rhythmic evidence is not strong enough to invent a confident
reggae or funk label.

Four neutral capability profiles now sit between performance intent and
MIDI rendering:

- **Fingered** — balanced attacks, space, ghosts and connections;
- **Fretless** — more connected intent and sustain, no automatic dead or
  ghost notes;
- **Upright (pizzicato)** — a distinct double-bass family with natural
  decay, restrained connected playing and voice-leading movement;
- **Sub / Synth** — lower density and expression ceilings, protected
  low-register space, longer tones and no automatic grace/ghost/dead or
  connected-note intent.

The AU displays the listening summary and its three suggested routes.
Bass Family lives in Advanced and updates the advice before regeneration.
Public automation exposes Fingered, Fretless, Upright and Sub / Synth.

Still deliberately pending: applying a recommended route. That action
must land with keep/lock/history/recall so trying Explore cannot destroy a
good idea.

First Logic UI check:

- the Bass AU loaded the advisor and all three paths successfully;
- UTF-8 middle-dot separators rendered as `Â·`, so all public C++ copy now
  uses ASCII separators;
- Character and Groove Lock labels were simplified to avoid crowding;
- the Bass AU now displays its **confirmed** session key/scale;
- the separate Listener's live estimate is explicitly labelled tentative
  with its confidence percentage.

The screenshot exposed a real trust boundary: the live Listener proposed
B-flat minor while the accepted session remained confirmed D natural
minor. The tentative Listener estimate must never silently replace
confirmed session harmony. Both rebuilt AUs pass Apple validation.

Second Logic UI check:

- the Listener now visibly reports a tentative G minor estimate at 50%
  confidence while the Bass AU independently reports D natural minor
  (confirmed);
- the live-harmonic bridge now preserves confirmed uploaded-source
  harmony while retaining Listener evidence inside source analysis;
- the Bass controls now hydrate once from the bound session, so Style,
  Bass Family, Character and Groove Lock describe the loaded part before
  the user asks for another idea. This fixes the accepted 0.70 groove
  lock appearing as the plug-in's 0.50 parameter default.

## 4. Revised Roadmap

Order has been re-staged based on the latest strategic review. Capability
must precede control; analysis reliability must precede reference-aware
generation; structured controls must exist before a prompt layer can add
useful leverage.

| Version | Theme | One-line goal |
|---------|-------|---------------|
| v0.3a   | Validation pack for reference audio analysis | Prove the analysis is trustworthy before any feature depends on it. |
| v0.3b   | Reference-aware bass groove | Drum loop / reference audio in → bass that locks to the kick, breathes around the snare, resolves through the chord chart. |
| v0.4    | Variation manager + bar-range regeneration | Complete: deterministic bass seeds, candidate promotion, selected-bar regeneration, and compact frontend controls. |
| v0.5    | Bass Performance MIDI / Articulation Layer | Make bass MIDI feel played: ghost/dead notes, slides, grace notes, timing push/pull, velocity phrasing, and clean vs performance MIDI modes. |
| v0.6    | Live audition bridge + first profile rendering | IAC/MIDI bridge into Logic / Trilian / MODO / Logic Studio Bass, plus a small set of hand-built profiles that render v0.5 performance intent through real instruments. |
| v0.7    | Instrument profile engine + Articulation Set / Expression Map import | Generalise the profile schema, ship the data-driven profile engine, and import existing Logic Pro Articulation Sets and Cubase Expression Maps for instant compatibility. |
| v0.8    | Assisted calibration / learned profiles | Guided, producer-friendly calibration that learns a user's instrument without manual keyswitch / note / CC configuration. Audio-response probing rides on the v0.6 bridge. |
| v0.9    | Prompt assistant | Prompt → structured edit, bounded by an allowlist of engine operations. Local-first / BYO-key. |
| v1.0    | DAW-native / plugin direction | AU/VST shell over the existing engine; MIDI in/out + sidechain; engine stays the brain. |

Re-staging note: v0.6 and v0.7 swapped roles relative to the previous
plan, and the audition bridge moved earlier. The reason is that
calibration cannot ship before the bridge — without an audio return loop
into the user's actual instrument, calibration degrades to "play this and
tell me what you heard," which is the nerdy UX this product explicitly
rejects. The bridge is the spine; profile work and calibration both
depend on it. Build the spine first.

## 5. v0.3a — Validation Pack First

Before any v0.3b generator code lands.

Goals:

- Prove that source-analysis outputs are reliable enough to drive bass
  generation on real audio.
- Establish a single executable script that returns a pass/fail table for
  each clip × each analysis field.
- Become the engineering compass for the whole `listen → analyse → respond`
  thesis: we cannot claim reference-aware generation if reference analysis
  is not trustworthy on the clips we would demo.

Deliverables:

- `backend/data/validation_pack/` populated with 8–12 short clips covering
  the realistic distribution: drum loop, full mix, sparse keys, dense keys,
  off-grid live recording, click-quantised loop, half-time feel, swung
  feel, latin feel, modal vamp.
- `manifest.json` with expected fields per clip: tempo, downbeat phase,
  kick slot pattern, key, basic chord changes.
- `backend/tools/run_validation_pack.py` extended (it already exists in
  scaffolded form) to print a per-clip pass/fail table:
  - tempo within ±2 BPM
  - downbeat phase exact
  - kick slot intersection-over-union ≥ 0.7
  - key correct
  - harmonic confidence ≥ a documented threshold.
- A short summary at the bottom: which fields pass on which clips, where
  evidence is thin, and a confidence-gated fallback recommendation.

Exit criterion for v0.3a: the script runs cleanly, prints the table, and
the team agrees on which fields are demo-grade and which need a fallback.
**Whatever fails most is what v0.3b must protect against.**

No generator code is touched in v0.3a.

## 6. v0.3b — Reference-Aware Bass Groove

Goal: with a drum loop or reference audio uploaded and a manual chord
progression set, the bass locks to the kick, breathes around the snare,
and resolves through the user's chord changes.

Smallest safe change. Use what is already there.

- Read from `SessionAnchorContext`: `kick_slot_weight`, `snare_slot_weight`,
  `density_per_bar`, `bar_start_anchor_sec`, `beat_phase_offset_beats`.
  Don't introduce new analysis surface in this milestone.
- Pocket *gating*, not pocket *mimicking*. The bass should avoid slots
  where the snare lands and prefer slots adjacent to the kick — but it
  should not just play wherever the kick plays. That's a sample replacer,
  not a bassist.
- Add one `space_score` per slot, combining `1 - snare_weight - 0.5 *
  pressure + kick_weight`, threshold it. One concept, one place.
- Resolve into the next chord, every bar. Combine the v0.2 chord
  progression with leading-tone approach in release/answer bars. The
  scaffolding for this is already in `bass_phrase_plan.py` cells.
- Expose a single user-facing knob: **lock-to-groove (0–1)**, from "loose
  pocket" to "glued to kick". Internally drives `kick_lock_mult`,
  `restraint_mult`, and pressure-aversion. Resist adding five knobs that
  do the same thing.
- Confidence-gated fallback: when source-analysis evidence is thin (per
  v0.3a thresholds), fall back to the manual chord chart and standard
  pocket without claiming a reference lock. Surface this honestly in the
  preview text.

**Important: do NOT consolidate or rewrite the two bass engines in v0.3b.**

The temptation to merge `baseline` and `phrase_v2` is real. Resist it
here. The current engines work; a refactor at this stage is a trap that
delays the demo without changing the user-visible result. Land
reference-aware groove in whichever engine is the cleaner host (likely
`phrase_v2`) and leave the other engine alone.

A merge is allowed only when v0.4 (variation manager) or v0.5 (performance
MIDI) make the parallel engines provably painful.

Demo proof point: side-by-side toggle "ignore drum loop / lock to drum
loop" on the same chord chart, same seed. The difference must be
unmistakable.

### Two-source product path (implemented 2026-07-23)

The bass workspace now accepts two explicitly assigned inputs:

- **Musical source** — owns key, scale, harmony and phrase structure.
- **Groove reference** (optional) — owns tempo grid, kick/snare evidence,
  rhythmic pressure and pocket confidence.

The analyses are fused only at the conditioning boundary. Drum-loop
chroma is never allowed to overwrite musical-source harmony. The
confidence-gated single-source fallback remains available when no groove
reference is supplied.

First controlled acceptance pair: `jmh_keys_88_voni_Dm.wav` plus
`OLIVER_88_drum_loop_mixready_vintage_indie_funk_slam.wav`, both exactly
four bars at 88 BPM. With seed 424245, lock 0 produced 17 notes and lock
1 produced 13 with changed onsets, confirming that the second source
changes the bass performance rather than only the UI state.

Combined-source regression found 2026-07-23: a 16-bar Logic bounce at
88 BPM was interpreted as 8 bars at 44 BPM because the upload screen's
placeholder bar count was incorrectly treated as trusted evidence. The
upload-first analyser now derives tempo independently, then derives bar
count from the tracked beat train. The same bounce resolves to 87.598
BPM / 16 bars. Trusted bar-count callers (validation and explicit
session contexts) retain their duration anchor. Low-confidence key reads
without filename metadata now pause for one confirmation/correction
instead of silently generating in a possibly wrong key. This gate is
enforced by both the UI and generation API; a tentative session cannot
bypass it by calling the candidate workflow directly.

## 6b. Product Surface — the plugin vision (Sanjeev, 2026-06-12)

Agreed end-state UX, banked verbatim from the strategy talk after v0.3b
landed. This is the surface the engine has been building toward; it is
NOT a pivot.

> A plugin that sits in front of a VST instrument (e.g. Logic stock bass)
> and generates the MIDI that triggers it. It hears the session through a
> listener on the 2-bus or an audio channel. Workflow: load a Splice
> sample (drums + keys), Session Player generates a bass part from that
> live input. The plugin UI shows style options and nothing else. Plus a
> command layer: "make it busier", "add a turnaround on the 4th bar".

Architecture (most pieces already exist):

```
Logic session
├─ loop/drums/keys → [Session Player Listener AU on the bus]
│                         └→ bridge frames → engine (analysis, certified v0.3a)
└─ bass channel: [Session Player MIDI FX (aumf)] → any instrument
      │  UI: style · lock-to-groove · regenerate (macro-knob register,
      │      per the validated AF chassis pattern — no surgical knobs)
      └─ command layer: NL → existing API (density/restraint knobs,
         v0.4 bar-range regeneration covers "turnaround on bar 4");
         natural Max voice integration ("hey Mycroft, busier last 2 bars")
```

Piece status: listener AU ✅ installed · live frames → conditioning ✅ ·
reference-aware generation ✅ (v0.3b) · IAC MIDI routing ✅ · bar-range
regen ✅ (v0.4) · **MIDI FX plugin chassis = the missing piece** ·
NL command layer = thin LLM mapping over existing endpoints (later).

Suite positioning: PocketCarver listens and makes SPACE; Session Player
listens and makes PARTS — "contextual instruments" as a brand pillar.

Milestone: **v0.6 — MIDI FX chassis**: aumf plugin, host-transport-synced
playback of the engine's bass part, style/lock/regenerate params, tiny
dedicated backend surface (`/api/plugin/*`), auval PASS. NL command layer
is v0.7, after the chassis proves the loop.

## 7. v0.4 — Variation Manager + Bar-Range Regeneration

Status: **complete**.

Tag: `v0.4-variation-manager`.

Completed scope:

- Deterministic bass seed support in `generate_bass(seed=...)` and the
  phrase-v2 path.
- `bass_seed` persisted on the session and returned in `SessionState`.
- Bass candidate promotion carries the candidate seed into the active
  session bass lane.
- Bass candidate generation now uses direct seeded generation instead of
  global random-state wrapping.
- Bass-only selected-bar regeneration endpoint:
  `POST /api/sessions/{session_id}/lanes/bass/regenerate-bars`.
- Compact frontend controls for adjusting selected bass bars inside the
  existing bass candidate workflow.
- Outside bars are preserved during selected-bar regeneration; only notes
  whose start falls inside the selected zero-based bar range are replaced.

What this unlocks: a producer can generate takes, promote one, adjust
selected bars with a fresh or repeatable variation seed, and download the
resulting editable MIDI without losing the rest of the part.

Deferred from the original v0.4 ambition:

- "Keep notes / change rhythm" and "Keep rhythm / change notes" toggles.
- Decomposition of `bass_generator.py` into per-style modules.

Both remain valid future tools, but v0.4 shipped the smaller workflow that
matters first: promote a bass take, adjust a bar range, preserve everything
outside that edit.

## 8. v0.5 — Performance MIDI / Phrasing Layer (Bass First)

Goal: bass MIDI that sounds played, not merely correct.

Core mission:

> Session Player must produce killer, sellable musical output. Correct
> notes are not enough.

The next bottleneck is musical feel. The system now has deterministic
takes, promotion, and selected-bar edits. v0.5 should make those edits
worth keeping by adding performance information that works especially well
for fusion, hip-hop, broken beat, jazz-funk, deep house, DnB, and
sample-based grooves.

- Articulation as a first-class output of the phrase plan, not a
  post-processing decoration. Each emitted bass note carries an
  `articulation` enum:
  `normal`, `ghost`, `dead`, `slide_from`, `slide_to`, `hammer`, `grace`.
- Ghost notes and dead/muted notes are essential, not ornamental. They are
  what make bass parts breathe in funk, hip-hop, broken beat, DnB, and
  sample-based grooves.
- Grace notes, slides, hammer-ons, and pull-offs should appear only where
  phrase role and instrument register make them believable.
- Legato feel should be explicit: note overlap, release shortening, and
  phrase connection need to be intentional rather than accidental.
- Velocity is phrase dynamics, not a random number. Ghosts sit low,
  anchors speak clearly, pushes lean forward, releases relax. Extend the
  existing note-shaping path rather than adding a parallel system.
- Timing push/pull should be musically directed. It can lean into kicks,
  sit behind snares, rush fills slightly, or relax cadences, but it should
  not become free random jitter.
- Support a clean MIDI vs performance MIDI output mode. Clean mode keeps
  editable note choices simple; performance mode includes the feel layer.
- Instrument profile direction comes later. v0.5 should design the
  articulation vocabulary and MIDI output behavior first, then map it to
  specific instruments once the musical result is worth preserving.

Bass first. Chords and lead get this layer in a later milestone, once the
articulation vocabulary is settled.

Acceptance: A/B clip of the same session at v0.4 vs v0.5. The v0.5
version is unmistakably more human in informal listening.

## 9. Instrument / VST Direction

Core product promise:

> Session Player learns how to play your instruments.

Session Player is the **player brain**, not the sound library.

It should drive the user's existing instruments and VSTs. Trilian,
Kontakt, MODO Bass, Ample, Addictive Drums, Logic instruments, synths,
Splice-style sample instruments, and personal patches are sound sources.
Session Player supplies the playing: performance intent, groove response,
phrasing, dynamics, timing, and articulation instructions.

The product should not compete with dedicated libraries by bundling
sounds. It should make the user's libraries feel like better musicians are
playing them.

Instrument / VST profiles:

- Profiles translate universal performance intent into plugin-specific
  MIDI behavior.
- Bass intent can target a Trilian profile, MODO profile, Ample profile,
  Logic Studio Bass profile, or a generic bass fallback.
- Drum intent can target Addictive Drums, Logic Drummer kits, GM drums,
  or a user's custom drum rack.
- Keys intent can target Rhodes, organ, synth, piano, or sample-based
  instruments.
- The generated musical intent remains stable; the profile decides how to
  express that intent as keyswitches, note offsets, CCs, channel routing,
  velocity ranges, or no-op fallbacks.

Manual mapping is not acceptable as the default workflow.

Manual JSON/profile editing may exist as an advanced escape hatch, but it
cannot be the normal product path. Normal users should not have to know or
manually configure keyswitches, drum-note maps, articulation names, CC
numbers, velocity thresholds, or plugin-specific quirks.

Preferred setup workflow:

- Built-in profiles for common instruments.
- Plugin detection where possible.
- Generic fallback profiles when the exact instrument is unknown.
- Guided calibration when detection is not enough.
- MIDI probing to discover note maps, keyswitches, and response ranges.
- Audio response probing later, when the bridge can hear what the
  instrument produced.
- Saved learned profiles that can be reused across sessions.

Guided calibration examples:

- "Did that sound like a slide?"
- "Did that sound like a ghost note?"
- "Did you hear a kick?"
- "Try another mapping."
- "Save learned profile."

The calibration UX should sound like a producer checking a sound, not a
developer configuring a protocol.

Product rule:

> If a feature makes the user manually configure technical mapping, it is
> not default-product-ready.

## 10. v0.6 — Live Audition Bridge + First Profile Rendering

Goal: send v0.5 performance MIDI directly into the user's real instrument
and hear it played correctly, with a small set of hand-tested profiles.

Bridge before calibration. Calibration only makes sense once the user can
audition quickly through their actual instrument; without that loop,
calibration becomes "play this and tell me what you heard," which is
exactly the nerdy UX this product rejects. v0.6 builds the spine that
v0.7 and v0.8 both ride on.

This comes after v0.5 because articulation intent must exist before any
profile can translate it. v0.6 does not invent new musical intent; it
makes the v0.5 intent audible through real instruments.

Priorities:

- **IAC / virtual-MIDI bridge out.** Session Player should be able to send
  generated MIDI directly to Logic, Trilian, MODO, or another target
  instrument for fast audition — without drag-and-drop.
- **First supported profiles, hand-tested:**
  - Trilian
  - MODO Bass
  - Logic Studio Bass
  Addictive Drums (or another drum target) is a candidate later in the
  milestone if bass is solid. Bass first.
- **Three profiles tuned obsessively** beats thirty mediocre ones. These
  profiles set the bar for what "good" means before any importer or
  calibrator exists.
- Profile output is deterministic: same `(performance notes, profile)`
  pair produces identical MIDI bytes.
- The bridge is for audition, not real-time generation. The engine still
  renders bars on demand.

Platform note: IAC is macOS-only. Windows requires loopMIDI or a bundled
virtual MIDI driver and is explicitly deferred. Do not promise Windows
audition in v0.6 marketing.

Exit criterion: generate a bass performance, send it to Trilian / MODO /
Logic Studio Bass over IAC, and hear the v0.5 articulation intent
expressed correctly through that instrument. Same performance, three
profiles, three musically-correct readings.

## 11. v0.7 — Instrument Profile Engine + Articulation Set / Expression Map Import

Goal: generalise the v0.6 hand-built profiles into a data-driven engine,
and unlock instant compatibility with hundreds of community-built maps by
importing existing DAW articulation files.

Profile system rules:

- **Profiles are data, not code.** JSON Schema + validator. Adding a
  profile must never require editing engine code.
- **Versioned profile schema.** Every profile declares its schema version;
  the engine refuses unknown future versions cleanly.
- **Golden tests for profile translation.** For each profile, assert that
  a fixed performance input produces fixed MIDI bytes. Without golden
  tests, profiles drift silently when the engine changes.
- **Generic fallback profiles** per family: `generic_bass`, `generic_drums`,
  `generic_keys`. An unknown instrument always gets a working part, never
  an error.
- **Do not try to support every instrument at launch.** Top targets plus
  fallbacks. Quality over coverage.
- **Versioned, fetched profile bundle.** Profiles ship as a versioned data
  bundle that can be patched without an app release. A bad profile must
  be fixable without shipping a binary.

Import strategy — the high-leverage shortcut:

- **Logic Pro Articulation Set import** (`.plist`, typically under
  `~/Music/Audio Music Apps/Articulation Settings/`).
- **Cubase Expression Map import** (`.expressionmap`).
- Importing a user-local map is acceptable. Mapping the user's existing
  configuration is the fastest path to "it just works."

Licensing rule:

> Do not redistribute third-party articulation maps unless properly
> licensed. Importing user-local maps is acceptable; bundling other
> vendors' maps in the app is not.

Decide and document the line in v0.7. Get it right early.

Exit criterion: the same generated bass performance can be exported as
clean MIDI and as profile-targeted MIDI for any of the v0.6 profiles plus
at least one user-imported Logic Articulation Set, without changing the
musical notes.

## 12. v0.8 — Assisted Calibration / Learned Profiles

Goal: teach Session Player a user's instrument without asking them to
manually map technical details.

Calibration is guided, short, and producer-friendly. Manual JSON/profile
editing is advanced-only. There is no default manual mapping path.

The user-facing flow should sound like a producer checking a sound:

- Play a probe through the v0.6 bridge.
- Ask what the user heard, in plain language ("did that sound like a
  ghost note?", "did you hear a kick?").
- Try another mapping when the answer is wrong.
- Save the learned profile, with a vibe name, when the answer is right.

Constraints:

- **Five questions max** before "your instrument is ready." Calibration
  longer than ~90 seconds will be abandoned.
- **First probe must already sound musical.** Generic-family fallback has
  to be demo-grade before calibration begins.
- **Always recoverable.** "This profile isn't right anymore" → restart
  with one button.

Calibration starts with MIDI probing (riding on the v0.6 bridge):

- note ranges
- drum-note maps
- likely keyswitch zones
- articulation trigger notes
- velocity response bands

Audio response probing comes second, once the bridge can reliably capture
the instrument's audio return. Audio analysis is heuristic and fallible:
always confirm with the user ("I think this was a ghost — yes / no?"),
never assert without confirmation. Skip ML; heuristic features are enough
for the first cut.

Exit criterion: a user can calibrate an unsupported bass or drum
instrument through guided questions in under 90 seconds and reuse the
learned profile across sessions.

## 13. v0.9 — SDK / Prompt Assistant

Goal: replace six dropdown clicks with one sentence, without becoming a
chatbot that "generates a song."

Why this comes after v0.4 and v0.5: the assistant is only as good as the
structured surface it can act on. v0.4 and v0.5 multiply that surface
(takes, bar ranges, articulation knobs, lock-to-groove). Shipping the
assistant earlier would route it to the same handful of enum dropdowns
already in the UI.

Design rules:

- Prompt → **structured edit**, not prompt → generation. Output is a JSON
  patch against session/lane state. The user reviews it, then applies it.
- Strict allowlist of operations. Examples: `set_bass_density(0..1)`,
  `regenerate_bars(start, end, lane, seed?)`,
  `lock_kick_following(0..1)`, `darker_voicing()`,
  `add_variation_count(n)`, `keep_rhythm_change_notes()`. Each maps 1:1
  to an engine call that already exists.
- Two-stage UI: prompt → proposed patch (rendered in plain English plus a
  diff of controls) → Apply.
- Local-first or BYO-key. Ship a small intent classifier or keyword
  router for offline operation. Escalate to a hosted LLM only when the
  user opts in.
- Determinism by default. Seeds are saved with every generation; same
  prompt + same seed = same output.

The thing to avoid: a chatbot that "generates a song." The thing to
build: a power-user shortcut for an instrument.

## 14. v1.0 — Plugin / DAW-Native Path

Don't rewrite. Add channels.

- **Phase 1 (now → v0.5):** Tauri app remains the lab. Add export of a
  "session JSON" so a future plugin can import an in-progress session.
- **Phase 2:** Thin AU/VST shell (JUCE) that does only MIDI in/out plus
  sidechain audio in. The plugin sends audio frames + MIDI to the
  existing Python backend over local IPC. The backend stays the brain.
- **Phase 3:** When the audio analysis pipeline is fast enough to run
  live (it is not today — `audio_source_analysis.py` is offline), inline
  the hot path in C++ / Rust. Keep the rule-based engine in Python until
  it is profitable to port.
- The plugin's job is "render bars on demand," not "play live." Logic's
  Drummer / Bass Player / Keyboard Player is the reference UX.
- Sidechain *MIDI input* before sidechain *audio input*. A kick or clave
  MIDI track routed in is strictly easier and serves 70% of the use case.
- Marketing rule: do not put "AU/VST coming soon" anywhere until a JUCE
  prototype already imports a session. People remember promises.

## 15. MIDI Strategy

- Output stays General MIDI. No proprietary extension format.
- Articulation is expressed via the engine's articulation enum and
  **translated** to GM + per-instrument profile (keyswitches, CCs, note
  offsets, channel mapping).
- A user can swap profiles to retarget the same generated part to MODO,
  Trilian, Ample, Logic Studio Bass, or a personal Kontakt patch without
  regenerating notes.
- Drag-into-Logic remains the always-on baseline workflow.
- Determinism is a feature: same `(session, seed, profile)` triple
  produces identical MIDI bytes.
- The candidate / take system is the canonical way to commit a "version"
  of a part; ad-hoc regeneration is for exploration.

## 16. Phrasing / Articulation Concern

The single highest-risk perception problem is: **plain MIDI feels
robotic.** Performance MIDI (v0.5) is the structural answer, but the
problem cannot wait for v0.5 in isolation.

Mitigations carried into earlier milestones:

- v0.2.1's pocket tightening is already in this direction; further
  velocity / micro-timing polish on the supportive default is fair game
  whenever it can be made *before* a public demo.
- v0.3b's "lock-to-groove" knob will fail the demo if the resulting bass
  is rigidly quantised. Micro-timing must move with the analysis from
  day one.
- Default outputs must already feel breathing before anyone is shown the
  app. The first impression problem doesn't wait for v0.5.
- Avoid stacking randomness as a substitute for phrasing. Random jitter
  on a wrong rhythm sounds worse than a quantised right rhythm.

Articulation vocabulary, fixed in v0.5 and stable thereafter:

`normal`, `ghost`, `dead`, `slide_from`, `slide_to`, `hammer`, `grace`.

Profiles must implement all of these (with sensible no-op fallbacks).
Keep the vocabulary small.

## 17. Build Discipline / Decision Rules

- Every feature is justified against the anchor sentence: *controlled
  editable MIDI performance, not black-box audio*. If it isn't, defer.
- Capability before control. Workflow before assistant.
- Smallest safe change. No refactor that isn't forced by the next
  milestone.
- Reliability before features. Validation pack passes before reference
  groove ships.
- One knob beats five knobs that mean the same thing.
- Hide complexity behind musical labels ("tighter pocket," "more
  space"), never expose internal multipliers as UI.
- Determinism by default. Seed every generation; same inputs → same
  output.
- Don't promise what hasn't been prototyped.
- Demo-grade default before knob-grade flexibility. The first impression
  must already feel human.
- The candidate / take system is the canonical commit point for a
  generated part. Ad-hoc regeneration is exploratory.
- Tests stay green. New behaviour comes with new tests; existing tests
  are not edited to accommodate.

Refactor rules specifically:

- Do **not** consolidate `baseline` + `phrase_v2` in v0.3b.
- A bass-engine merge is allowed only when v0.4 or v0.5 makes parallel
  engines provably painful.
- `bass_generator.py` decomposition into per-style modules is a v0.4
  candidate, not earlier.

## 18. What Not To Build Yet

- Lyrics, vocals, audio rendering, mastering, stem export.
- Style-transfer "make it sound like [artist]".
- Cloud accounts, multiplayer, hosted sessions.
- A real-time engine. The plugin doesn't need it.
- A custom MIDI extension format.
- A chord recogniser that overrides the user's chord chart. Manual chart
  wins until detection is provably better.
- A prompt assistant ahead of the variation manager.
- Drum / chord / lead performance MIDI before the bass performance MIDI
  is shipped and loved.
- A web / SaaS version. The desktop wrapper *is* the product.
- A bass-engine merge "for cleanliness" before a milestone forces it.
- New analysis surfaces in v0.3b. Use what `SessionAnchorContext` already
  exposes.

v0.6+ specific exclusions:

- **No profile editor UI in v0.6.** Hand-build profiles in JSON to let
  the schema settle. Editor UI is a v0.7 question at earliest.
- **No full plugin host inside Tauri.** Wait for the v1.0 JUCE shell.
  Tauri talks to the user's DAW via IAC; it does not load AUs/VSTs
  itself.
- **No audio probing before the MIDI bridge works.** Audio response
  capture rides on top of v0.6, not in front of it.
- **No ML articulation detection yet.** Heuristic features + user
  confirmation are enough through v0.8.
- **No marketplace or profile sharing yet.** Local-first. Sharing is a
  v1.x conversation.
- **No support for 50 instruments at launch.** Top targets plus generic
  fallbacks. Quality of three profiles beats coverage of thirty.
- **No redistribution of third-party articulation maps without proper
  licensing.** Importing user-local maps is fine; bundling vendor maps
  is not.

## 19. Immediate Next Step

Build the v0.3a validation pack.

Concretely:

1. Add 8–12 short clips under `backend/data/validation_pack/clips/`,
   covering: drum loop, full mix, sparse keys, dense keys, off-grid live
   recording, click-quantised loop, half-time feel, swung feel, latin
   feel, modal vamp.
2. Fill `backend/data/validation_pack/manifest.json` with expected
   fields per clip: tempo, downbeat phase, kick slot pattern, key,
   chord changes.
3. Extend `backend/tools/run_validation_pack.py` to print a per-clip
   pass/fail table for tempo (±2 BPM), downbeat phase (exact), kick
   slots (IoU ≥ 0.7), key (exact), harmonic confidence (threshold).
4. Run it once. The fields that fail most define what v0.3b's
   confidence-gated fallback must protect against.

No generator, frontend, test, or Tauri code is touched in this step.
