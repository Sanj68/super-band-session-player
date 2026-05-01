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

## 4. Revised Roadmap

Order has been re-staged based on the latest strategic review. Capability
must precede control; analysis reliability must precede reference-aware
generation; structured controls must exist before a prompt layer can add
useful leverage.

| Version | Theme | One-line goal |
|---------|-------|---------------|
| v0.3a   | Validation pack for reference audio analysis | Prove the analysis is trustworthy before any feature depends on it. |
| v0.3b   | Reference-aware bass groove | Drum loop / reference audio in → bass that locks to the kick, breathes around the snare, resolves through the chord chart. |
| v0.4    | Variation manager + bar-range regeneration | Take comparison, promote, regenerate bars 5–8 only, keep-notes/change-rhythm and inverse. |
| v0.5    | Performance MIDI / phrasing layer (bass first) | Articulations as first-class plan output: ghost, dead, slide, grace, hammer; one reference instrument profile. |
| v0.6    | SDK / prompt assistant control layer | Natural language → structured engine controls; user-reviewed patches; never autonomous generation. |
| v1.0    | DAW-native / plugin direction | AU/VST shell over the existing engine; MIDI in/out + sidechain; engine stays the brain. |

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

## 7. v0.4 — Variation Manager + Bar-Range Regeneration

Goal: turn the engine's existing knobs into a producer workflow.

- Generate N takes, compare, promote one. (The bass candidate workflow
  already exists; generalise its UX.)
- Regenerate bars 5–8 only, leaving the rest of the lane untouched.
- "Keep notes / change rhythm" and "Keep rhythm / change notes" toggles
  on a single take.
- Per-bar deterministic seeding: every emitted note is reproducible from
  `(session_id, lane, bar, take_id)`. Today's `random.randint(0, 127)`
  salt at the top of `generate_bass` makes bar-range regeneration
  inconsistent — fix that as part of v0.4.
- This milestone is also the **first earned opportunity** to decompose
  `bass_generator.py` into per-style modules, because per-bar regeneration
  will repeatedly stress the giant `generate_bass` function. If the
  decomposition is not necessary to ship v0.4 cleanly, defer it again.

Acceptance: a producer can take a generated 16-bar bass, regenerate bars
9–12 with a different seed, and the rest of the part stays bit-identical.

## 8. v0.5 — Performance MIDI / Phrasing Layer (Bass First)

Goal: MIDI that sounds played, not programmed.

- Articulation as a first-class output of the phrase plan, not a
  post-processing decoration. Each emitted bass note carries an
  `articulation` enum:
  `normal`, `ghost`, `dead`, `slide_from`, `slide_to`, `hammer`, `grace`.
- Velocity is a curve, not a number. Ghost notes ~30–45, dead notes ~20,
  accents follow phrase role (push > anchor > release). Extend the
  existing `shape_note` rather than adding a parallel system.
- Micro-timing tied to the analysis: lay back when the source is
  dragging, push when it is pushing. Built on `bar_start_anchor_sec` and
  per-slot pressure, not free random jitter.
- Pitch bends used sparingly and only on slides / grace notes. Continuous
  bends are the fastest way to make MIDI sound *worse* in the wrong
  instrument profile.
- One reference instrument profile to start: **Logic Studio Bass**
  (`profiles/logic_studio_bass.json`). Profile is a translation from the
  articulation enum to keyswitches / CCs / note offsets. Other profiles
  (MODO Bass, Trilian, Ample, Kontakt user libraries) follow only after
  the first one is loved.
- Profiles are user-authorable: drop in a JSON, it works. This is a moat.

Bass first. Chords and lead get this layer in a later milestone, once the
articulation vocabulary is settled and at least one profile is solid.

Acceptance: A/B clip of the same session at v0.4 vs v0.5. The v0.5
version is unmistakably more human in informal listening.

## 9. v0.6 — SDK / Prompt Assistant

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

## 10. v1.0 — Plugin / DAW-Native Path

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

## 11. MIDI Strategy

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

## 12. Phrasing / Articulation Concern

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

## 13. Build Discipline / Decision Rules

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

## 14. What Not To Build Yet

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

## 15. Immediate Next Step

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
