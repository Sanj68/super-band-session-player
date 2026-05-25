# ARCHITECTURE.md

Audit of the `super-band-session-player` backend (FastAPI + `pretty_midi`) as it stands
on `main` at the time of writing. Frontend, desktop wrapper, and validation tooling are
referenced where they touch the generation pipeline, but the focus is the audio-analysis
to MIDI-generation path inside `backend/app/`.

The codebase is currently a **batch, rule-based, loop-MIDI generator** with an early
contract for a streaming "bridge" surface. Everything is in-process, in-memory, and
designed around fixed bar counts. There is no ML model, no remote agent, no
external API.

---

## 1. Services inventory (`backend/app/services/`)

One paragraph per module. Order is logical (top-level → lane generators → conditioning
→ bass deep stack → bridge → persistence), not alphabetical.

### Top-level orchestration

- **`generator.py`** — Thin façade that re-exports `generate_drums`, `generate_bass`,
  `generate_chords`, and `generate_lead` from the per-lane modules. Each call takes
  tempo, bar count, key/scale, style/instrument/player choices, optional
  `SessionAnchorContext`, optional `UnifiedConditioning`, and returns
  `(midi_bytes, preview_string)`. Bass also optionally returns `BassPerformanceNote`
  tuples for the parallel performance render path.

- **`session_context.py`** — Builds a `SessionAnchorContext` from an already-rendered
  anchor lane's MIDI bytes. Other lanes then read density, slot pressure, drum kick/snare
  weights, and register hints to stay coherent with the anchor. This is the "look at the
  lane the user already likes and follow it" mechanism. It is purely batch — it parses
  bytes after generation, not streaming events.

- **`anchor_lane_roles.py`** — Maps `(generating_lane, anchor_lane)` pairs to a named
  *role* (`groove_lock`, `harmonic_support`, `lead_anchor`, `pocket_follow`, …) and
  returns knob dicts that the per-lane generators merge into their style/player
  profile. The role table is fixed; choices happen before note generation, not as a
  post-pass.

- **`conditioning.py`** — Assembles a single immutable `UnifiedConditioning` snapshot
  combining session settings, MIDI-derived context, optional audio source analysis
  (groove profile + harmony plan), and per-bar / per-slot accent/pressure rows from
  the source. Bass generators read this snapshot for tempo, bar grid, harmonic bars,
  and source-groove kick/snare/pressure weights. This is the canonical "what does the
  generator know about the source?" object.

### Lane generators

- **`drum_generator.py`** — Rule-based drum lane. Supports styles `straight`, `broken`,
  `shuffle`, `funk`, `latin`, `laid_back_soul`, plus a `drum_player` bias layer (e.g.
  `bootsy`, `marcus`, `pino` styling indirectly). Emits GM drum-channel notes via
  `pretty_midi`; reads slot pressure from `SessionAnchorContext` when present.

- **`chord_generator.py`** — Rule-based chord lane. Styles `simple`, `jazzy`, `wide`,
  `dense`, `stabs`, `warm_broken`, with optional `chord_player` bias. Honors the
  anchor role knobs (e.g. density, sustain) and consumes `SessionAnchorContext` for
  per-bar density.

- **`lead_generator.py`** — Rule-based lead lane (sparse, sparse_emotional, melodic,
  rhythmic, bluesy, fusion). Has a `suit_*` set of parameters used by the
  "add-part-to-suit" endpoint, which generates a context-aware lead idea on top of
  whatever the user has already locked in.

- **`bass_generator.py`** — Entry point for the bass lane. Selects between the baseline
  per-style generator and the v2 phrase engine via `bass_engine`. Wires up groove cells,
  anchors, optional `bass_player` profile (Bootsy / Marcus / Pino), per-bar phrase plan,
  articulation shaping, and the conditioning snapshot. Returns clean MIDI bytes,
  preview, and (optionally) a tuple of `BassPerformanceNote` records that the
  performance renderer uses to build a parallel "Performance" track.

### Bass deep stack

- **`bass_phrase_plan.py`** — Builds a four-bar phrase plan (`anchor / answer / push /
  release`) using `UnifiedConditioning`. The plan is purely structural — what each
  bar is *for* — and downstream code (engine v2, articulation) consumes it.

- **`bass_phrase_engine_v2.py`** — Kick-aware phrase engine. Uses session context kick
  weights + slot pressure to place notes around the drum kit's groove. Constrains
  styles, instruments, and player profiles to the supported sets and reads from
  `mt` (`music_theory`) for chord/scale resolution.

- **`bass_articulation.py`** — Per-note articulation/performance shaping (velocity,
  duration scaling, ghost/dead handling) given slot, role, style, cadence bias, and
  sustain multiplier. Pure symbolic — no MIDI here.

- **`bass_performance.py`** — Trusted-input dataclass `BassPerformanceNote` carrying
  the symbolic articulation information from the generator into the performance
  renderer. Deliberately not a Pydantic model.

- **`bass_performance_render.py`** — Renders a parallel "Bass (Performance)" MIDI
  instrument from `BassPerformanceNote` tuples. Layered shaping: role-based velocity
  and duration, 4-bar phrase arc dynamics, bounded deterministic micro-timing,
  dead-note shaping, and optional source-pressure response (kick-aligned accent,
  snare-without-kick attenuation). All shaping is bounded and deterministic.

- **`bass_quality.py`** — Take analysis and quality scoring for candidate ranking.
  Reads notes + conditioning and returns a `total` score, per-axis scores, signature,
  and reason string. Used by `/bass-candidates` to rank takes.

- **`bass_bar_splice.py`** — Splices regenerated bars into an existing bass lane using
  `mido` at the message level (rather than re-rendering the whole lane). This is what
  powers "regenerate bars X through Y" without disturbing the rest.

- **`bass_loop_boundary.py`** — Idempotent boundary fixer. Guarantees the first note
  starts at `0.0` and the final bar contains a resolution near loop end, regardless of
  which generator path produced the bytes. Runs at every egress point.

- **`bass_candidate_store.py`** — Local JSON persistence for bass candidate-run
  metadata (under `backend/data/`). Stores enough to re-promote a take as the
  session's active bass lane.

- **`bass_vocabulary/`** — The "Sub One" vocabulary subpackage. Abstract templates
  (`templates.py`), pitch-role resolution (`pitch_roles.py`), candidate rendering
  with the v0.7 groove-aware path (`candidates.py`), and a profile of references and
  avoid-rules (`profile.py`). Templates do not copy recorded basslines — they encode
  slot positions, pitch roles, density, energy, grit, improvisation, and per-template
  rules (swing amount, head-nod delay, turnaround behaviors). `paul_chambers.py`
  is a peer of `templates.py` for the Chambers-style walking/arco vocabulary.

### Audio analysis / source-aware features

- **`audio_source_analysis.py`** — Librosa-driven analyser for uploaded reference
  audio. Estimates tempo, builds an onset envelope, derives per-bar energy/accent,
  segments sections via novelty, and produces a `SourceAnalysis` with groove rows
  (kick/snare/pressure heuristics) and a Krumhansl-style key/mode guess. This is the
  *batch* audio path.

- **`source_analysis.py`** — MIDI-side source analyser. Picks an existing lane
  (preferring bass → chords → drums → lead) and derives a structural `SourceAnalysis`
  + `GrooveProfile` + `HarmonyPlan` from it. Used when the user has not uploaded
  reference audio but the session already has a lane to "learn from".

- **`reference_guidance.py`** — Lightweight gates that read a `UnifiedConditioning`
  and decide whether reference groove guidance is available and usable by downstream
  generation passes.

- **`groove_frame.py`** — Conversion helpers between `SourceAnalysis` groove fields
  and per-bar `GrooveFrame` DTOs. Also contains the `merge_groove_frames` used by the
  bridge commit endpoint.

### Bridge (real-time spike)

- **`bridge_store.py`** — In-memory store for the v0.7.1 bridge contract. Records
  heartbeats, transport frames, and per-bar `BridgeSourceFeatureFrame` rows from a
  hypothetical Logic AU plugin. Summarises captured frames into per-bar `GrooveFrame`
  objects with simple heuristics (low-band → kick, mid/high + onset → snare, RMS +
  onset + band-avg → pressure). Cap of 4096 frames per session; no persistence; not a
  real-time pipeline (it's a buffered ingestion + batch summariser).

### MIDI utilities

- **`midi_note_extract.py`** — Parses note events out of MIDI bytes for piano-roll
  previews and for `SessionAnchorContext` ingestion. Safe-by-default: returns `[]` on
  parse failure.

- **`midi_export.py`** — `Response`/`StreamingResponse` helpers that wrap MIDI bytes
  in `audio/midi` downloads, plus `merge_lane_midis` and `zip_all_lanes` for combined
  exports.

- **`midi_audition.py`** — Discovers system MIDI output ports (mido + python-rtmidi)
  and exposes a Protocol-typed player; the implementation playback path is filled in
  by later patches but enumeration works today.

### Persistence (local JSON only)

- **`setup_store.py`** — Saved band setups under `backend/data/band_setups.json`. Atomic
  write via temp file + replace.

- **`setup_apply.py`** — Translates a saved `BandSetup` into a PATCH payload for
  `/api/sessions/{id}`. Preset values are applied first, then explicit fields, so
  the order matches the session route.

- **`evaluation_store.py`** — JSON persistence for bass clip/take evaluations.

---

## 2. The audio analysis → MIDI generation pipeline

The full path the system runs today, end-to-end, for a session that uses uploaded
reference audio:

1. **Session create.** `POST /api/sessions/` builds a `StoredSession` in
   `_SESSIONS: dict[str, StoredSession]`. No DB; lives in-process.

2. **Reference audio upload.** `POST /api/sessions/{id}/reference-audio` writes the
   uploaded file to `backend/data/reference_audio/{session_id}/` and clears any
   previous `source_analysis_override`.

3. **Audio analysis (batch).** `POST /api/sessions/{id}/analyze-audio` calls
   `audio_source_analysis.analyze_reference_audio(...)`:
   - Loads audio with librosa at 22 050 Hz mono.
   - Estimates tempo candidates from an onset envelope.
   - Derives per-bar energy, accent, and section boundaries via novelty.
   - Scores `(root_pc × {major,minor})` against Krumhansl profiles for a key/mode guess.
   - Builds a `SourceAnalysis` with `groove_profile` rows (onset / kick / snare /
     pressure heuristics, 16 slots/bar) and a `harmony_plan` of per-bar root + target
     + passing + avoid pitch classes.
   - Stores the result on the session as `source_analysis_override`.

4. **Conditioning assembly.** When a generate or regenerate endpoint is hit:
   - `session_context.build_session_context(session)` parses any already-rendered
     anchor lane MIDI into a `SessionAnchorContext` (density per bar, slot pressure,
     kick/snare weights, register hints).
   - `source_analysis.build_source_analysis(session, context=ctx)` returns the existing
     override or a MIDI-derived `SourceAnalysis`.
   - `source_analysis.build_groove_profile(src, context=ctx)` and
     `build_harmony_plan(session, src)` derive the lane-facing rows and harmony.
   - `conditioning.build_unified_conditioning(...)` merges all of the above into a
     single immutable `UnifiedConditioning` snapshot — the canonical input for
     generators.

5. **Anchor lane resolution.** If `session.anchor_lane` is set, that lane is generated
   first without context (so it doesn't try to follow itself). Then a context is built
   from its bytes, and the remaining lanes are generated with that context plus the
   conditioning snapshot.

6. **Per-lane generation.** Each lane resolves its style/player into a profile,
   merges anchor-role knobs, reads conditioning rows where it cares (bass deeply,
   chords moderately, drums lightly, lead lightly), and emits notes via `pretty_midi`.

7. **Bass-specific extras.**
   - Phrase plan (`bass_phrase_plan`) decides per-bar roles.
   - Engine v2 (`bass_phrase_engine_v2`) or the baseline path emits a clean lane and a
     parallel `BassPerformanceNote` tuple.
   - `bass_performance_render` builds a "Performance" instrument with deterministic
     micro-timing, ghost/dead shaping, and source-pressure response.
   - `bass_loop_boundary.normalize_bass_loop_bytes` runs at every egress.
   - For `bass_candidates`: a hidden pool is generated by reseeding, scored by
     `bass_quality.analyze_bass_take`, diversified by signature distance and motif
     family, and merged with `bass_vocabulary` templated candidates (the Sub One pool).
     The result is a ranked list of takes the user can audition and promote.

8. **Export.** `midi_export.merge_lane_midis` produces the combined MIDI for Logic;
   `lane_midi_response` / `zip_all_lanes` handle individual lane downloads. The
   frontend renders the piano-roll preview from `extract_lane_notes` results.

9. **Bridge path (parallel, gated).** When `SESSION_PLAYER_ENABLE_GROOVE_BRIDGE=1` is
   set, the `bridge_routes` surface accepts heartbeats, transport frames, and
   `BridgeSourceFeatureFrame` rows from a Logic AU plugin into `bridge_store`. On
   `commit-source-groove`, frames are summarised to per-bar `GrooveFrame`s and merged
   into the session's `SourceAnalysis` via `merge_groove_frames`, after which step 6
   onward proceeds normally. The bridge does *not* feed generation in real time; it
   is a buffered ingestion that updates the same batch conditioning snapshot.

---

## 3. The bridge module — what it is and what it needs to become

### What it is today

- **`backend/app/routes/bridge_routes.py`** + **`backend/app/services/bridge_store.py`** +
  **`backend/app/models/bridge.py`** form a v0.7.1 contract spike.
- It is **feature-flagged** behind `SESSION_PLAYER_ENABLE_GROOVE_BRIDGE`; when off, the
  routes return 404.
- It accepts four POSTs: `heartbeat`, `transport`, `source-frames` (bulk), and
  `commit-source-groove`, plus a `state` GET.
- Frames are appended to an in-memory list per session (cap 4 096, oldest dropped).
  There is no streaming worker, no scheduler, no diff broadcast.
- `summarize_frames_to_groove_frames` runs a simple `max`-aggregator across slots
  within a bar and normalises each row; `commit-source-groove` calls this once on
  demand and merges the result into the session's `SourceAnalysis` override.
- It is conceptually a "batched audio-feature side channel that lets a Logic plugin
  feed the same generation pipeline as an uploaded reference clip" — not a real-time
  collaborator.

### What it needs to become for real-time use

To support live keys+drums → AI bassline, the bridge must change from buffered
ingestion to a streaming control plane. Concretely:

1. **Sub-bar update cadence.** Today, summarisation is keyed on `bar_index` and only
   runs at commit time. A live setup needs per-slot or per-beat aggregation that
   produces partial `GrooveFrame`s as soon as the current bar has enough evidence.
2. **Push, not pull.** The frontend / Logic plugin currently posts JSON over HTTP.
   For real-time, the bridge needs a persistent transport — WebSocket, gRPC stream,
   or OSC — with backpressure handling.
3. **Time alignment.** Each `BridgeSourceFeatureFrame` carries `host_tempo`,
   `ppq_position`, `bar_index`, `beat_index`, `frame_start_seconds`, `duration_seconds`.
   These are recorded but not currently used to align onto the host's transport
   clock. A real-time pipeline must reconcile host time, processor time, and an
   AI-emit time horizon (lookahead).
4. **A generator that can emit incrementally.** Today every generator returns a
   complete MIDI byte blob for a fixed bar count. A live bass generator needs to emit
   note-on / note-off events into a stream, holding state from the previous slot
   forward, with a planning horizon shorter than the bar (probably 1–2 beats).
5. **A separate conditioning view for live state.** The current `UnifiedConditioning`
   is immutable per generate call. Live mode needs a sliding `LiveConditioning`
   object that's mutated as frames arrive, with deterministic snapshots taken at
   each emit boundary.
6. **Persistence / idempotency story.** In-memory store is fine for a spike, not for
   live performance — a crash mid-tune drops the captured groove. Even a flat-file
   ring buffer would be enough to restart cleanly.

The bridge is not blocked by external dependencies; the change is structural —
replace the buffered POST surface with a streaming surface, then push the
streaming events into a live-aware conditioning + generator stack.

---

## 4. Implemented vs stubbed

### Implemented (used in the live request path)

- All four batch lane generators (drums, bass, chords, lead) with style/player/preset
  selection, anchor lane support, and lane locks.
- Batch reference-audio analysis with librosa: tempo, sections, groove rows, key/mode.
- Batch MIDI-source analysis fallback (when no reference audio is uploaded).
- Unified conditioning snapshot consumed by bass deeply.
- Bass: phrase plan, engine v2, articulation, performance render, loop-boundary
  normalisation, bar splicing, candidate run + quality scoring + diversification,
  vocabulary templates and their guarded rendering path.
- Bass vocabulary "Sub One" templates (warm jazz-funk, dark slinky, fusion answer,
  hip-hop soul, tight head-nod, raw funk, modal vamp, cinematic, breakbeat,
  Chambers — once `paul_chambers.py` is wired).
- Bridge POST surface for heartbeat, transport, source-frames, commit (gated).
- MIDI export endpoints (per-lane + combined zip).
- Saved band setups + setup→PATCH application.
- Bass take evaluation persistence.
- MIDI output port enumeration (audition path scaffold).

### Stubbed / partial

- **Bridge real-time:** routes accept frames, but there's no streaming transport, no
  partial-bar summarisation, no live conditioning. Commit is on-demand only.
- **MIDI audition playback:** enumeration works; "open port and play this lane" is
  a Protocol with no concrete production implementation in this branch.
- **Lead generator richness:** styles exist and respond to suit_* parameters, but
  conditioning consumption is shallow compared to bass.
- **Chord generator conditioning:** consumes session context density but does not
  currently consume the same source kick/snare/pressure rows that bass does.
- **`paul_chambers.py` vocabulary:** the module exists (added in this change) but is
  not yet wired into the candidate pool in `bass_vocabulary/candidates.py` — selection
  still goes through `_CANDIDATE_TEMPLATE_IDS`.
- **Persistence:** sessions are in-memory only. A backend restart loses every session.
- **Auth / multi-user:** explicitly out of scope per README.

---

## 5. Top 5 gaps for "live keys+drums → AI bassline in Paul Chambers' style"

Ranked by what's blocking the use case, not by effort:

1. **No streaming input.** The bridge POSTs JSON, buffers, and summarises on commit.
   Live keys+drums need a persistent, low-latency transport (WebSocket / OSC) that
   delivers feature frames at sub-beat cadence and a worker that consumes them.

2. **No streaming generator.** Every lane generator produces a complete fixed-bar
   MIDI blob. A live bass generator must emit notes incrementally with a short
   lookahead (1–2 beats), holding state between slots, and be cancellable.

3. **No live conditioning view.** `UnifiedConditioning` is immutable per call. Live
   mode needs a mutable, slot-indexed conditioning that aggregates incoming frames
   plus the most recent keys-derived chord/root estimate, with deterministic
   snapshots taken at each emit boundary.

4. **No real-time chord / key estimator.** Today, harmony comes from the user's
   stored progression or from offline reference analysis. To follow a live keyboardist
   we need a low-latency root + quality estimator (could be a simple
   chroma-template matcher on MIDI from the keys lane) feeding the live conditioning.

5. **No Paul Chambers style head.** Even with all the plumbing above, the existing
   bass generators are stylistically tuned for soul / funk / jazz-funk pockets.
   Walking-bass behaviour with chromatic approach tones, guide-tone targeting on the
   "and of 4", and arco ballad phrasing needs an explicit style head — the
   `paul_chambers.py` vocabulary module is the first piece of that.

### Recommended build order

Build in this order so each step has a usable next-step demo:

1. **Paul Chambers vocabulary module (offline first).** Land the style head as a
   pure function module — `paul_chambers.py`, walking cells, chromatic approaches,
   arco phrases, groove feel. Plug it into the existing batch candidate path so a
   user can render Chambers-style bass over a saved chord progression *today*. This
   gives a regression target for everything that follows.

2. **Real-time chord/root estimator from live keys MIDI.** A small module that takes
   a rolling window of keys notes (last 1–2 beats), returns a `(root_pc, quality,
   confidence)` estimate, and is callable from a streaming worker. Start with chroma
   template matching against `mt`'s chord library — no ML needed. Validate against a
   batch corpus first.

3. **Streaming bridge transport (WebSocket).** Replace the buffered POSTs with a
   WebSocket session that delivers `BridgeSourceFeatureFrame` and live keys MIDI
   events into an in-process queue. Keep the existing batch routes too — they're
   useful for offline analysis and validation.

4. **Live conditioning + emit-cycle scheduler.** Build the mutable `LiveConditioning`
   view, fed by the streaming queue, and a scheduler that runs an emit-cycle every
   eighth or sixteenth note with a fixed lookahead. At each cycle, snapshot the
   conditioning and call the Chambers vocabulary's walking-cell function for the
   next emission window.

5. **Incremental bass generator.** Refactor the Chambers cell selection into an
   incremental emitter that takes the live conditioning, the current bar position,
   and the previous emitted note, and returns the next 1–2 beats of notes with
   bounded micro-timing. Route emitted notes to a MIDI output via `midi_audition`.

In short: ship the style head first (offline-usable from day one), then bring the
streaming plumbing online step by step until the generator itself becomes
incremental.
