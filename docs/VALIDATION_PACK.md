# Validation Pack — v0.3a

The validation pack is the acceptance gate for **reference audio
analysis reliability**. v0.3b (reference-aware bass groove) is allowed
to depend on the analyser only after the fields it consumes are passing
on the clips we would actually demo.

This page describes the canonical v0.3a manifest schema, the
in-process runner, and the conventions for adding clips without
bloating the repo.

## Layout

```
backend/data/validation_pack/
  manifest.json         declarative expectations per clip
  README.md             quick-start (kept for the existing HTTP runner)
  clips/                local audio (NOT committed to git)
backend/data/validation_pack_commercial/
  manifest.json         second pack of commercial references
  README.md
  clips/
backend/tools/
  validate_reference_analysis.py   v0.3a in-process runner (no server)
  run_validation_pack.py           HTTP runner (requires backend up)
```

## Two runners, one schema

* `validate_reference_analysis.py` — **the v0.3a daily driver**. Calls
  `analyze_reference_audio()` directly, so it works without the API
  server. This is the one to use when iterating on analyser reliability
  or when running the pack inside CI/local checks.
* `run_validation_pack.py` — pre-existing HTTP-based runner. Hits the
  live `/reference-audio` and `/analyze-audio` endpoints. Useful for
  end-to-end checks but requires the backend to be running.

Both runners read the same `manifest.json`. The schema below is the
canonical v0.3a form; older field names are still accepted for
backward compatibility.

## Manifest schema

```json
{
  "schema_version": "0.3a",
  "defaults": {
    "tempo_tolerance_bpm": 3,
    "low_confidence_threshold": 0.45
  },
  "clips": [
    {
      "id":                   "vp01_clear_rhythm_harmony",
      "file":                 "clips/vp01_clear_rhythm_harmony.wav",
      "description":          "Clear groove and clear C-minor harmony.",
      "notes":                "Demo-grade reference.",
      "expected": {
        "expected_tempo_bpm":   120,
        "tempo_tolerance_bpm":  2,
        "expected_key_pc":      0,
        "expected_scale_mode":  "minor",
        "expected_bar_count":   8,
        "expected_kick_slots":  [0, 4, 6, 10]
      }
    }
  ]
}
```

Field reference (all clip fields optional unless noted):

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | recommended | Stable string identifier; defaults to the file stem if omitted. |
| `file` | yes | Path to audio, relative to the manifest. (`filename` is also accepted.) |
| `description` | no | One-line human description of the clip. |
| `notes` | no | Free text — use for evidence quality, source, "watch out for…". |
| `expected.expected_tempo_bpm` | no | Target tempo in BPM. (`tempo_bpm_approx` accepted.) |
| `expected.tempo_tolerance_bpm` | no | ± tolerance in BPM; falls back to `defaults.tempo_tolerance_bpm`. |
| `expected.expected_key_pc` | no | Pitch class 0–11 (0 = C). |
| `expected.expected_key` | no | Key name (`"C"`, `"F#"`, `"Bb"`); resolved to a pitch class. |
| `expected.expected_scale_mode` | no | `"major"` or `"minor"`. (`scale_mode_guess` accepted.) |
| `expected.expected_bar_count` | no | Bar count to drive the analysis with. |
| `expected.expected_kick_slots` | no | Reserved; **not yet checked** — analyser does not surface kick slots in v0.3a. |
| `expected.head_trim_seconds_min/max` | no | Used only by the HTTP runner today. |
| `expected.section_count_min/max` | no | Used only by the HTTP runner today. |

The in-process runner reports `N/A` for fields that are either not
declared or that the analyser does not yet surface. **Nothing is
fabricated.** If `expected_kick_slots` is declared, it appears in the
table with `N/A` and an explanation, not an invented match.

## Adding a clip

1. Drop the audio under `backend/data/validation_pack/clips/` with a
   short, stable filename (`vpNN_short_label.wav`). Keep clips short
   (10–30 s is plenty for v0.3a checks).
2. Add a new entry to `manifest.json` with `id`, `file`, `description`,
   and any expectation fields you can confidently assert. **Only assert
   what you would stake the demo on.** A missing expectation is fine
   and prints `N/A`; a *wrong* expectation poisons the pack.
3. Prefer mono or stereo WAV for predictable decode behaviour.
4. Run the runner once and confirm the result before committing.

### Avoiding huge audio files in git

Audio is local-only. Do not commit clip files unless they are tiny and
explicitly licensed for redistribution.

* The recommended workflow is to keep `backend/data/validation_pack/clips/`
  out of git via `.gitignore` (see existing repo policy).
* `validation_pack_commercial/` clips should never be committed —
  these are full-track references with no redistribution rights.
* The runner reports `SKIP` (with reason) for any clip whose file is
  missing locally. That is the correct behaviour: contributors who do
  not have the clips still see a useful pack/fail summary, just with
  the missing clips skipped.

## Running the v0.3a runner

```bash
cd backend
. .venv/bin/activate
python tools/validate_reference_analysis.py
```

Useful flags:

```bash
# Use a different manifest (e.g. the commercial pack)
python tools/validate_reference_analysis.py \
    --manifest data/validation_pack_commercial/manifest.json

# Run a single clip by id
python tools/validate_reference_analysis.py --clip-id vp01_clear_rhythm_harmony
```

Exit codes:

* `0` — every present clip passed (skips don't fail).
* `1` — at least one clip failed.
* `2` — manifest not found / no matching clip id.

## Output

For each clip the runner prints the verdict, a one-line confidence
summary from the analyser, and one row per checked field:

```
[PASS] vp01_clear_rhythm_harmony  (clips/vp01_clear_rhythm_harmony.wav)
    tempo_conf=0.78 key_conf=0.71 mode_conf=0.66 phase_conf=0.62 bar_start_conf=0.55 head_trim=0.012s
    tempo_bpm   detected=120.13  expected=120.00±2.0   PASS  [|delta|=0.13]
    key_pc      detected=0 (C)   expected=0 (C)        PASS  [conf=0.71]
    scale_mode  detected=minor   expected=minor        PASS  [conf=0.66]
    bar_count   detected=8       expected=8            PASS

[SKIP] vp03_preroll_silence  (clips/vp03_preroll_silence.wav)
    SKIP: missing audio file: clips/vp03_preroll_silence.wav
```

A summary line follows: `Summary: PASS=N FAIL=M SKIP=K (of T clip(s))`.

## What this pack is for

* Verifying analyser reliability before any v0.3b code reads from
  `SessionAnchorContext` for kick-aware bass generation.
* Identifying the per-field confidence threshold that a future
  *confidence-gated fallback* in the bass engine should respect.
* Catching regressions when the analyser is touched.

## What it is **not**

* Not a benchmark of generation quality.
* Not a substitute for ear-checking the bass output.
* Not a contract on kick-slot detection until the analyser actually
  surfaces it. Until then, `expected_kick_slots` is reported `N/A`.
* Not exhaustive: a passing pack means "good enough on these clips,"
  not "the analyser is correct in general."
