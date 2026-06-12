# Validation Pack (Step 2)

This folder is the acceptance gate for audio analysis reliability.

Use it before changing generation behavior.

## Goal

Create a small set of clips (start with 5) that exercise:

1. clear rhythmic anchor
2. sparse / weak rhythmic anchor
3. pre-roll silence
4. harmonically rich content
5. harmonically ambiguous content

## Layout

- `clips/` - put local test audio files here (not committed by default)
- `manifest.json` - expected references and notes per clip

## How to add the 5 clips

1. Add your files to `clips/` using the manifest names:
   - `vp01_clear_rhythm_harmony.wav`
   - `vp02_sparse_anchor.wav`
   - `vp03_preroll_silence.wav`
   - `vp04_harmonic_rich.wav`
   - `vp05_harmonic_ambiguous.wav`
2. Keep each clip short (10-30s is enough for V1 checks).
3. Use mono or stereo WAV when possible for predictable decode behavior.
4. Fill each clip's `expected` fields in `manifest.json`:
   - tempo: `tempo_bpm_approx` + `tempo_tolerance_bpm` (or `tempo_bpm_min/max`)
   - tonal center: `expected_key` (preferred, e.g. `C`, `F#`, `Bb`) or `tonal_center_pc_guess`
   - mode: `scale_mode_guess` (optional)
   - sections: `expected_section_count` or `section_count_min/max` (optional)
   - trim: `head_trim_seconds_min/max` (optional)
   - confidence expectation: `expect_low_confidence` (optional)

## Manual sanity checklist (first pass)

For each clip, run analysis and check:

- `tempo_estimate_bpm` is plausible
- `tempo_confidence` is lower on sparse/noisy clips
- `bar_start_confidence` does not look high when beat phase is uncertain
- `head_trim_seconds` is > 0 for intentional pre-roll silence clips
- `tonal_center_pc_guess` / `scale_mode_guess` are plausible where harmony is clear

## Runner

Use `backend/tools/run_validation_pack.py` to:

- create a temporary session
- upload each clip
- run `/analyze-audio`
- print per-clip analysis summary
- evaluate expectations with `PASS`, `WARN`, or `FAIL`

Exit behavior:

- returns non-zero if any `FAIL`
- optional `--strict-warn` also returns non-zero on any `WARN`

Example:

```bash
cd backend
. .venv/bin/activate
python tools/run_validation_pack.py --api-base http://127.0.0.1:8000
python tools/run_validation_pack.py --api-base http://127.0.0.1:8000 --strict-warn
```

