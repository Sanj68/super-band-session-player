"""FFT chroma and Krumhansl key inference tests."""

from __future__ import annotations

import numpy as np

from app.services.harmonic_analysis import extract_fft_chroma, infer_key_scale_from_chroma


def _sine(freq: float, *, seconds: float = 1.0, sr: int = 48000) -> np.ndarray:
    t = np.arange(int(seconds * sr), dtype=float) / float(sr)
    return np.sin(2.0 * np.pi * float(freq) * t)


def test_fft_chroma_extracts_pitch_class_from_audio() -> None:
    y = _sine(440.0)
    chroma = extract_fft_chroma(y, sample_rate=48000)
    assert len(chroma) == 12
    assert int(np.argmax(chroma)) == 9
    assert chroma[9] > 0.65


def test_key_inference_prefers_c_major_triad_profile() -> None:
    chroma = [0.0] * 12
    chroma[0] = 1.0
    chroma[4] = 0.72
    chroma[7] = 0.82
    key_pc, scale, confidence = infer_key_scale_from_chroma(chroma)
    assert key_pc == 0
    assert scale == "major"
    assert confidence > 0.25


def test_key_inference_prefers_a_minor_triad_profile() -> None:
    chroma = [0.0] * 12
    chroma[9] = 1.0
    chroma[0] = 0.72
    chroma[4] = 0.82
    key_pc, scale, confidence = infer_key_scale_from_chroma(chroma)
    assert key_pc == 9
    assert scale == "minor"
    assert confidence > 0.25
