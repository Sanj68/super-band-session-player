# v0.3a validation pack — first full run (2026-06-12)

> **UPDATE same day — key backport landed: 8/15 → 9/15, key fails 5 → 3
> (all three are suspect-truth / designed-ambiguous / modal-hard).** The
> backport's journey is instructive and is documented in §"What the
> backport taught us" below. Remaining fails: tempo ×3 (separate work
> item), vp01 (truth label suspect), vp05 (by design), vp07/vp11
> (50/50 vamp + dorian — honest limits of major/minor profiles).

Pack expanded 5 → 15 clips per BUILD_NOTES §19: 7 synthesized scenario clips
(`tools/generate_validation_clips.py` — ground truth by construction, the
Meter Core certification approach) + 3 real full-mix clips sliced from the
AutoFactory promo bounces (known DAW tempos; key unscored).

**Scoreboard: PASS 8 / FAIL 15.** Field-level failure counts:

| Field | Fails | Verdict |
|---|---|---|
| key_pc | 5 | **the dominant failure** |
| scale_mode | 3 | rides along with key |
| tempo_bpm | 3 | two distinct causes |
| downbeat/bars | 0 scored | (expectations not yet authored) |

## Finding 1 — key detection has classic circle-of-fifths confusion

Every key failure lands on a chord root of the progression instead of the
tonic, usually a fifth/fourth away:

- vp10 latin (C–G7 vamp): detected **G** (the V)
- vp11 modal vamp (Dm7–G7): detected **G** (the other chord)
- vp12 dense keys (Eb prog w/ Abmaj7): detected **Ab** (the IV)
- vp01: E vs C, vp05 (designed ambiguous): Bb vs E

`harmonic_analysis.py` uses plain Krumhansl-Kessler on an FFT pitch-class
profile — the exact baseline Pocket Export's `KeyBPMAnalyzer.swift` started
from before the 2026-06-02 hardening took it 7/20 → 11/20 exact on real
loops. **The fix is a backport** of that hardening:

1. harmonic pitch-class profile (peak-pick, attribute partials to
   fundamentals n=1..4 @ 0.6^(n-1), per-frame L1 norm)
2. **Albrecht-Shanahan profiles** instead of Krumhansl-Kessler (fixed most
   major/minor mode confusion there — would address the 3 scale_mode fails)
3. tonic-emphasis 0.30 + fifth-penalty 0.08 (kills exactly this IV/V
   confusion class)

## Finding 2 — tempo has a coarse-grid flat bias + a dense-mix miss

- 120-BPM clips read **117.19**; ~124–125 read **123.05**; the estimates sit
  on a quantized lag grid ~2–2.5% flat. Parabolic interpolation on the
  autocorrelation peak should collapse this (cheap, no behaviour change
  elsewhere). vp01's tempo "fail" is this bias at a ±2 tolerance.
- vp13 (real Beat 1 mix): **129.20 vs 137** — a real miss on dense
  material. Note the standalone onset script (launch/video bed prep) nailed
  137.00 on this same audio with spectral-flux hop 256; the analyzer's
  onset path is worth comparing against it.
- vp08 half-time: 143.56 vs 140 (+2.5%) — the predicted octave error did
  NOT happen (good); this is the grid bias again, plus jittered hats.

## Finding 3 — the confidence gate would already protect v0.3b

Every wrong key answer self-reported confidence ≤ 0.35, below the 0.45
`low_confidence_threshold`. A gate at 0.45 lets **zero wrong keys through**
on this pack. Cost: correct-but-timid answers (several passes sit at
0.25–0.35) would also be gated — so the gate is safe but the analyzer is
under-confident generally; the backport should raise separation.

## v0.3b work order (derived, in priority order)

1. Backport the Pocket Export key hardening into `harmonic_analysis.py`
   (expect key fails 5 → ~1–2, mode fails similarly).
2. Tempo lag-grid interpolation (expect vp01/vp08 to flip PASS).
3. Investigate vp13 dense-mix tempo against the spectral-flux/hop-256
   reference implementation.
4. Author downbeat/kick-slot expectations for the synth clips (truth is
   known by construction — the generator can emit them) to light up the
   currently-unscored fields.
5. Keep the 0.45 confidence gate as the fallback condition for
   reference-aware bass (v0.3b's original design assumption — validated).

## What the backport taught us (landed same day)

The naive transplant — AS profiles + penalties into the existing
CQT-chroma blend — made things WORSE (8/15 → 6/15). A 5-variant ×
2-chroma offline matrix over the pack settled it:

| | KK base | AS+penalties | AS+pen+mode3rd |
|---|---|---|---|
| CQT chroma | 6/5 (key/key+mode) | 5/3 | 5/5 |
| **HPCP chroma** | 6/6 | 7/7 (with 4th pen) | **7/7** |

**The Pocket Export stack only works as a unit** — harmonic-PCP chroma +
AS profiles + tonic-emphasis/fifth+fourth penalties. On CQT chroma the
AS profiles' asymmetric tonic weights (maj .238 vs min .220) tip
root-heavy material to major, and the fifth-penalty is swamped.

Then three pipeline mechanisms had to stand down under HPCP drive, each
caught by decomposition probes:
1. **Blend dilution** — even 20% CQT components erased the scorer's tonic
   margin (G-for-C returned at w_low=0.12).
2. **root_support bonus** — bass/tail energy on the vamp dominant re-leaked
   V into the vote.
3. **Relative-key disambiguation** — CQT low-chroma flipped the correct
   Eb major to C minor.

Final design: `global_pcp` (harmonic PCP from
`harmonic_analysis.extract_fft_chroma`, itself rewritten to the framed
peak-picking form) is the SOLE key voter; mode is reconciled by the
third degree; the CQT-era blend/support/relative logic remains as the
fallback path when no HPCP is supplied. Phrase-end structural weighting
also corrected (0.55→0.20; it was a systematic vote for the dominant —
tonics live at phrase ENTRIES, now 0.55).

## Repro

```bash
cd backend
.venv/bin/python tools/generate_validation_clips.py   # vp06-vp12 + manifest
.venv/bin/python tools/validate_reference_analysis.py # the scoreboard
```

Real clips (vp13–15) are sliced from the promo bounces — see manifest notes
for source timestamps; clips/ stays untracked per pack convention.
