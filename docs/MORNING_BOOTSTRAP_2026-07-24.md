# Session Player Morning Bootstrap — Friday 24 July 2026

Prepared: 2026-07-23 21:40 EEST  
Owner: Sanjeev Sharma  
Repo: `/Users/sub/Work/Code/Projects/super-band-session-player`

## Morning objective

Complete one clean, controlled acceptance run from the combined Logic
bounce. Do not add another feature before this proof is heard and
recorded.

## Current truth

- Branch: `main`
- Worktree was clean before this handoff.
- Local branch was 26 commits ahead of `origin/main`; never push without
  Sanjeev.
- Backend and frontend were running locally at the end of the evening:
  - frontend: `http://localhost:5173`
  - backend: `http://127.0.0.1:8000`
- Full backend suite: **481 passed**.
- Frontend production build: **passed**.
- Reference-analysis validation remains at its existing **11/15 clips**
  overall; the four known failures are key-analysis failures, not new
  tempo regressions.

## What was built tonight

1. Separate **Musical source** and optional **Groove reference** inputs.
   Harmony comes from the musical source; timing/kick/snare evidence
   comes from the groove reference.
2. The browser bass voice was improved and downloaded candidates were
   hardened: monophonic output, minimum playable articulation and exact
   loop release.
3. Upload-first tempo analysis no longer treats the screen's placeholder
   eight bars as trusted evidence.
4. The exact combined Logic bounce now resolves to:
   - 87.598 BPM, presented/applied as 88 BPM
   - 16 bars
   - detected source groove across all 16 bars
5. Low-confidence key analysis now requires confirmation. Both UI and
   API block candidate generation until key/scale are confirmed or
   corrected.

Relevant commits:

- `d64d9cb` — separate groove-reference workflow
- `a242809` — combined-source half-time fix and tentative-key UX
- `cb5a8d5` — hard generation gate until harmony confirmation

## Exact test material

Original harmonic source:

`/Users/sub/Music/Splice/sounds/packs/Jazz Melodics & Hooks/SM101_-_Jazzy_Melodics___Hooks_-_Wav/keys/jmh_keys_88_voni_Dm.wav`

Original matched drum loop:

`/Users/sub/Music/Splice/sounds/packs/Oliver Power Tools Sample Pack III/OLIVER_VOL3_sample_pack/OLIVER_drums/OLIVER_drum_loops/OLIVER_drum_loops/OLIVER_vintage/OLIVER_88_drum_loop_mixready_vintage_indie_funk_slam.wav`

Combined Logic bounce stored by Session Player:

`backend/data/reference_audio/e35d86e6-7e92-4ded-87a6-6cf1f3c782a3/20260723T164637_adca2262.wav`

Original uploaded filename:

`SessionPlayertest23.07.26.wav`

The bounce is known to be **88 BPM**. The harmonic source is known from
its original metadata as **D natural minor**.

## Do not use these candidate runs

- `cand_20260723T164642_a46deb44`
  - invalid: 44 BPM, eight bars, A minor, baseline engine
- `cand_20260723T182936_8037a369`
  - invalid: tempo/length repaired, but it bypassed confirmation and
    generated in default C major with the baseline engine

The bypass that allowed the second run is fixed by `cb5a8d5`.

## Valid control set

These files were generated from the repaired combined-source analysis
after deliberately applying D natural minor, Phrase Engine v2 and groove
lock 0.70:

- `/Users/sub/Downloads/bass_corrected_88_Dm_combined_t1.mid`
- `/Users/sub/Downloads/bass_corrected_88_Dm_combined_t2.mid`
- `/Users/sub/Downloads/bass_corrected_88_Dm_combined_t3.mid`
- `/Users/sub/Downloads/bass_corrected_88_Dm_combined_t4.mid`

They are structurally valid: 88 BPM, 16 bars, no overlaps, no broken
micro-notes and exact loop length. They are fairly closely related
because a strong shared groove lock constrains rhythmic variation.

## First morning run

1. Refresh `http://localhost:5173`.
2. Upload the combined Logic bounce as **Musical source**.
3. Expected AI read:
   - tempo approximately 88 BPM
   - length 16 bars
   - tentative key warning
4. In the automatically opened correction card select:
   - key: `D`
   - scale: `natural_minor`
5. Click **Apply Correction & Generate**.
6. Confirm the session no longer shows the harmony-confirmation block.
7. Set/confirm:
   - bass engine: `phrase_v2`
   - bass style: `supportive`
   - groove lock: start at `0.70`
8. Generate four candidates.
9. Download all four and audit them before changing any engine code.

If candidates can be generated before step 5, stop: the gate has
regressed.

## Acceptance criteria

- MIDI says 88 BPM.
- MIDI spans 64 playable beats / 16 bars.
- Session and candidate previews say D natural minor.
- Candidate preview says Phrase Engine v2 and reference groove locked.
- No raw MIDI same-pitch overlaps.
- No sub-25 ms artefacts.
- Final playable note does not exceed the 16-bar boundary.
- Audition against the combined source through one proper bass
  instrument—not the browser voice alone.
- Record which take wins and why: pocket, note choice, phrase shape,
  restraint and usefulness.

## Honest unresolved work

- The combined bounce's audio-only key detector still prefers A minor at
  low confidence. This is not solved; it is safely gated. The original
  D-minor filename metadata is authoritative for this test.
- The current four candidates can remain too related under a strong
  shared groove lock. Do not solve that by randomising harder. The next
  proper layer is phrase/motif structure, followed by neutral
  trait-based player profiles and performance expression.
- Combined-source harmonic understanding will eventually need better
  source separation or a stronger chord/key model. HPSS is already used,
  but it does not fully resolve ambiguous tonal-centre material.

## Restart and verification commands

Backend:

```bash
cd /Users/sub/Work/Code/Projects/super-band-session-player/backend
SESSION_PLAYER_ENABLE_GROOVE_BRIDGE=true .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Frontend:

```bash
cd /Users/sub/Work/Code/Projects/super-band-session-player/frontend
npm run dev -- --host 127.0.0.1
```

Verification:

```bash
cd /Users/sub/Work/Code/Projects/super-band-session-player/backend
.venv/bin/pytest -q

cd /Users/sub/Work/Code/Projects/super-band-session-player/frontend
npm run build
```

## Morning doctrine

Do not judge or tune the bassist until tempo, length, harmony and engine
mode are visibly correct. One clean audible proof is worth more than
another evening of feature accumulation.
