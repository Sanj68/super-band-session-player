# ACE-Step / Session Player Bass Benchmark

This utility compares bass-only audio generated from owned material with
Session Player bass MIDI. It is an offline research tool and does not call or
modify Session Player production services.

## Run

From the Session Player repository root:

```bash
backend/.venv/bin/python research/ace_step_bass_benchmark.py \
  --backing "/absolute/path/owned_backing_without_bass.wav" \
  --bass-wav "/absolute/path/ace_seed_101_bass.wav" \
  --bass-wav "/absolute/path/ace_seed_202_bass.wav" \
  --midi "/absolute/path/session_player_take_1.mid" \
  --midi "/absolute/path/session_player_take_2.mid" \
  --tempo 116 \
  --key D \
  --scale natural_minor \
  --chords "F|Gm|Dm|Bb" \
  --bars 8 \
  --name fusion_116_Dm
```

Repeat `--bass-wav` and `--midi` for every take. The chord progression is one
symbol per bar and cycles when shorter than the analysis window. Supported
scales and chord spellings are shown by validation errors; the intended
progression above is directly supported.

The default output directory is `research/benchmark_results/`, which is
gitignored because reports contain local source paths and results derived from
owned audio. Each run writes:

- `<name>_<timestamp>.json` for comparison or later scoring;
- `<name>_<timestamp>.md` for quick human review.

## JSON schema

The versioned top-level object is:

```text
schema_version
generated_at_utc
configuration
backing
  level_and_duration
  kick_proxy
bass_audio[]
  level_and_duration
  onsets
    density
  kick_proxy_alignment
  pitch_and_key_compliance
session_player_midi[]
  file
  notes
    density
  register
  kick_proxy_alignment
  harmonic_compliance
    scale
    chords
phrase_comparisons
  pairs[]
  group_summary[]
```

Important status rules:

- `backing.kick_proxy.status == "available"` means only that enough prominent
  low-frequency percussive evidence exists. It is not a verified kick stem.
- `pitch_and_key_compliance.status == "ok"` is required before using the audio
  in-scale percentage. Otherwise the percentage is `null`.
- MIDI scale membership is exact. `all_note_non_chord_count` includes legitimate
  passing notes; `strong_beat_non_chord_error_count` is the conservative review
  queue.
- Pairwise `phrase_similarity` is in the range 0–1 and
  `phrase_diversity = 1 - phrase_similarity`. Its `basis` field says whether
  the comparison could use pitch classes or had to fall back to quantized
  onsets.

## Test

```bash
backend/.venv/bin/pytest -q research/tests/test_ace_step_bass_benchmark.py
```

