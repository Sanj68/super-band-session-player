"""Realtime harmonic analysis helpers shared by bridge ingestion and tests."""

from __future__ import annotations

import numpy as np

from app.utils import music_theory as mt

# Albrecht-Shanahan (2013) profiles — backported 2026-06-12 from Pocket
# Export's hardened KeyBPMAnalyzer (which itself mirrors this module; the
# hardening went 7/20 -> 11/20 exact keys and now comes home). KK profiles
# caused the circle-of-fifths failures in the v0.3a validation pack.
_MAJOR_PROFILE = np.asarray([0.238, 0.006, 0.111, 0.006, 0.137, 0.094, 0.016, 0.214, 0.009, 0.080, 0.008, 0.081], dtype=float)
_MINOR_PROFILE = np.asarray([0.220, 0.006, 0.104, 0.123, 0.019, 0.103, 0.012, 0.214, 0.062, 0.022, 0.061, 0.052], dtype=float)


def normalize_chroma_vector(values: list[float] | tuple[float, ...] | np.ndarray) -> tuple[float, ...]:
    arr = np.asarray(values, dtype=float).reshape(-1)
    if arr.size != 12:
        raise ValueError("chroma vector must contain 12 pitch-class values")
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    arr = np.clip(arr, 0.0, None)
    total = float(np.sum(arr))
    if total <= 1e-9:
        return tuple(0.0 for _ in range(12))
    return tuple(round(float(x / total), 6) for x in arr)


def _harmonic_frame_chroma(
    spectrum: np.ndarray,
    freqs: np.ndarray,
    *,
    min_hz: float,
    max_hz: float,
    harmonics: int = 4,
    harmonic_weight: float = 0.6,
    peak_rel_threshold: float = 0.08,
) -> np.ndarray:
    """Harmonic pitch-class profile for one frame (Pocket Export hardening).

    Spectral *peaks* only, each attributed back to the fundamentals it could
    be a harmonic of (n=1..harmonics, weighted harmonic_weight^(n-1)). This
    pulls overtone energy — the perfect-fifth and major-third leakage that
    bends key estimates toward V/IV and major — back onto the notes actually
    played.
    """
    chroma = np.zeros(12, dtype=float)
    in_band = (freqs >= float(min_hz)) & (freqs <= float(max_hz))
    if not np.any(in_band):
        return chroma
    band_max = float(np.max(spectrum[in_band]))
    if band_max <= 1e-12:
        return chroma
    thresh = band_max * float(peak_rel_threshold)
    mags = spectrum
    # local peaks above threshold, inside the band
    peak = np.zeros_like(mags, dtype=bool)
    peak[1:-1] = (mags[1:-1] >= mags[:-2]) & (mags[1:-1] >= mags[2:])
    idx = np.nonzero(peak & in_band & (mags >= thresh))[0]
    for k in idx:
        hz = float(freqs[k])
        m = float(mags[k])
        w = 1.0
        for nh in range(1, int(harmonics) + 1):
            f0 = hz / nh
            if f0 < 27.5:  # below A0 — no musical fundamental
                break
            midi = 69.0 + (12.0 * np.log2(f0 / 440.0))
            pc = int(round(midi)) % 12
            chroma[pc] += m * w
            w *= float(harmonic_weight)
    return chroma


def extract_fft_chroma(
    samples: list[float] | tuple[float, ...] | np.ndarray,
    *,
    sample_rate: float,
    min_hz: float = 55.0,
    max_hz: float = 5000.0,
) -> tuple[float, ...]:
    """Build a 12-bin pitch-class profile from mono audio.

    Framed harmonic PCP (8192/4096 hop, per-frame L1 norm) per the Pocket
    Export hardening; signals shorter than one frame fall back to a single
    whole-signal frame so short test vectors keep working.
    """
    y = np.asarray(samples, dtype=float).reshape(-1)
    if y.size == 0:
        return tuple(0.0 for _ in range(12))
    sr = float(sample_rate)
    if sr <= 0.0:
        raise ValueError("sample_rate must be positive")
    y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
    y = y - float(np.mean(y))

    frame_len = 8192 if y.size >= 8192 else y.size
    hop = max(1, frame_len // 2)
    window = np.hanning(frame_len) if frame_len > 1 else np.ones(frame_len)
    freqs = np.fft.rfftfreq(frame_len, d=1.0 / sr)

    chroma = np.zeros(12, dtype=float)
    pos = 0
    while pos + frame_len <= y.size:
        frame = y[pos : pos + frame_len] * window
        spectrum = np.abs(np.fft.rfft(frame))
        fc = _harmonic_frame_chroma(spectrum, freqs, min_hz=min_hz, max_hz=max_hz)
        fsum = float(np.sum(fc))
        if fsum > 1e-9:
            chroma += fc / fsum  # per-frame L1 norm: transients can't dominate
        pos += hop
    return normalize_chroma_vector(chroma)


def _template(mode: str) -> np.ndarray:
    base = _MAJOR_PROFILE if mode == "major" else _MINOR_PROFILE
    arr = np.asarray(base, dtype=float)
    return arr / max(1e-9, float(np.sum(arr)))


def infer_key_scale_from_chroma(chroma: list[float] | tuple[float, ...] | np.ndarray) -> tuple[int, str, float]:
    """Infer tonic pitch class and major/minor mode (Albrecht-Shanahan profiles).

    Tonic-emphasis 0.30 + fifth-penalty 0.08 per the Pocket Export hardening —
    combats the IV/V circle-of-fifths confusion on vamp-based material.
    """
    pc = np.asarray(normalize_chroma_vector(chroma), dtype=float)
    if float(np.sum(pc)) <= 1e-9:
        return 0, "major", 0.0
    rows: list[tuple[int, str, float]] = []
    for tonic in range(12):
        for mode in ("major", "minor"):
            templ = np.roll(_template(mode), tonic)
            score = (
                float(np.dot(pc, templ))
                + (0.30 * float(pc[tonic]))
                - (0.08 * float(pc[(tonic + 7) % 12]))
            )
            rows.append((tonic, mode, score))
    rows.sort(key=lambda row: row[2], reverse=True)
    best_pc, best_mode, best_score = rows[0]
    second = rows[1][2] if len(rows) > 1 else 0.0
    concentration = float(np.max(pc))
    confidence = max(0.0, min(1.0, 0.18 + (2.2 * max(0.0, best_score - second)) + (0.42 * concentration)))
    return int(best_pc), str(best_mode), round(confidence, 4)


def scale_pitch_classes(root_pc: int, scale: str) -> tuple[int, ...]:
    return tuple((int(root_pc) + int(i)) % 12 for i in mt.scale_intervals(scale))


def harmonic_targets_for_key(root_pc: int, scale: str) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    scale_pcs = scale_pitch_classes(root_pc, scale)
    intervals = mt.scale_intervals(scale)
    if len(intervals) >= 5:
        stable = tuple(dict.fromkeys(((root_pc + intervals[0]) % 12, (root_pc + intervals[2]) % 12, (root_pc + intervals[4]) % 12)))
    else:
        stable = (int(root_pc) % 12,)
    passing = tuple(pc for pc in scale_pcs if pc not in stable)
    avoid = tuple(pc for pc in range(12) if pc not in scale_pcs)
    return stable, passing, avoid
