"""Realtime harmonic analysis helpers shared by bridge ingestion and tests."""

from __future__ import annotations

import numpy as np

from app.utils import music_theory as mt

_MAJOR_PROFILE = np.asarray([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88], dtype=float)
_MINOR_PROFILE = np.asarray([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17], dtype=float)


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


def extract_fft_chroma(
    samples: list[float] | tuple[float, ...] | np.ndarray,
    *,
    sample_rate: float,
    min_hz: float = 55.0,
    max_hz: float = 5000.0,
) -> tuple[float, ...]:
    """Build a 12-bin pitch-class profile from mono audio using an FFT magnitude spectrum."""
    y = np.asarray(samples, dtype=float).reshape(-1)
    if y.size == 0:
        return tuple(0.0 for _ in range(12))
    sr = float(sample_rate)
    if sr <= 0.0:
        raise ValueError("sample_rate must be positive")
    y = np.nan_to_num(y, nan=0.0, posinf=0.0, neginf=0.0)
    y = y - float(np.mean(y))
    if y.size > 1:
        y = y * np.hanning(y.size)
    spectrum = np.abs(np.fft.rfft(y))
    freqs = np.fft.rfftfreq(y.size, d=1.0 / sr)
    chroma = np.zeros(12, dtype=float)
    mask = (freqs >= float(min_hz)) & (freqs <= float(max_hz)) & (spectrum > 1e-12)
    for hz, mag in zip(freqs[mask], spectrum[mask]):
        midi = 69.0 + (12.0 * np.log2(float(hz) / 440.0))
        pc = int(round(midi)) % 12
        chroma[pc] += float(mag)
    return normalize_chroma_vector(chroma)


def _template(mode: str) -> np.ndarray:
    base = _MAJOR_PROFILE if mode == "major" else _MINOR_PROFILE
    arr = np.asarray(base, dtype=float)
    return arr / max(1e-9, float(np.sum(arr)))


def infer_key_scale_from_chroma(chroma: list[float] | tuple[float, ...] | np.ndarray) -> tuple[int, str, float]:
    """Infer tonic pitch class and major/minor mode with Krumhansl-Kessler profiles."""
    pc = np.asarray(normalize_chroma_vector(chroma), dtype=float)
    if float(np.sum(pc)) <= 1e-9:
        return 0, "major", 0.0
    rows: list[tuple[int, str, float]] = []
    for tonic in range(12):
        for mode in ("major", "minor"):
            templ = np.roll(_template(mode), tonic)
            score = float(np.dot(pc, templ)) + (0.10 * float(pc[tonic]))
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
