"""Focused tests for reference-audio analyzer decision helpers."""

from __future__ import annotations

import numpy as np
import pytest

from app.services import audio_source_analysis as asa


def test_moving_average_uses_available_edge_window() -> None:
    assert asa._moving_average([1.0, 3.0, 9.0], radius=1) == [2.0, pytest.approx(13.0 / 3.0), 6.0]


def test_build_sections_returns_empty_for_no_bars() -> None:
    assert asa._build_sections([0.2], [0.3], 0) == []


def test_build_sections_respects_four_bar_minimum_between_boundaries() -> None:
    energy = [0.1, 0.1, 0.2, 0.2, 1.0, 1.0, 0.2, 0.2]
    accent = [0.1, 0.1, 0.1, 0.1, 0.9, 0.9, 0.1, 0.1]

    sections = asa._build_sections(energy, accent, bars=8)

    assert [(s.label, s.start_bar, s.end_bar) for s in sections] == [("S1", 0, 3), ("S2", 4, 7)]


def test_tempo_candidates_deduplicates_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        asa.librosa.feature,
        "tempo",
        lambda **_kwargs: np.asarray([119.8, 120.1, 500.0, 12.0]),
    )
    monkeypatch.setattr(asa.librosa.feature, "tempogram", lambda **_kwargs: np.asarray([[0.1], [0.9], [0.7]]))
    monkeypatch.setattr(asa.librosa, "tempo_frequencies", lambda *_args, **_kwargs: np.asarray([0.0, 241.0, 60.0]))

    candidates = asa._tempo_candidates(np.ones(12), sr=22050, fallback_tempo=90)

    assert candidates[0] == pytest.approx(119.95)
    assert 90.0 in candidates
    assert 60.0 in candidates
    assert all(40.0 <= bpm <= 240.0 for bpm in candidates)
    assert len(candidates) == len({round(bpm, 2) for bpm in candidates})


def test_select_tempo_prefers_reasonable_anchor_when_pick_is_octave_like(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(asa, "_tempo_candidates", lambda *_args, **_kwargs: [60.0])
    monkeypatch.setattr(asa, "_tempo_objective", lambda _env, _sr, bpm: (float(bpm), 0.65 if bpm == 60.0 else 0.5))
    monkeypatch.setattr(asa, "_pulse_strength", lambda _env, _sr, bpm: 1.0 if bpm == 60.0 else 0.5)

    tempo, confidence = asa._select_tempo(np.ones(32), sr=22050, fallback_tempo=120, anchor_bpm=120.0)

    assert tempo == 120.0
    assert 0.35 <= confidence <= 0.75


def test_estimate_tonal_center_uses_fallback_when_chroma_empty() -> None:
    tonic, tonal_conf, mode, mode_conf = asa._estimate_tonal_center_mode(
        np.asarray([]),
        None,
        None,
        phase_confidence=0.0,
        bar_start_confidence=0.0,
        fallback_key="D",
        fallback_scale="natural_minor",
    )

    assert tonic == 2
    assert tonal_conf == 0.2
    assert mode == "minor"
    assert mode_conf == 0.2


def test_estimate_tonal_center_identifies_simple_major_distribution() -> None:
    chroma = np.zeros((12, 8), dtype=float)
    chroma[0, :] = 1.0
    chroma[4, :] = 0.55
    chroma[7, :] = 0.7
    low_chroma = np.zeros((12, 8), dtype=float)
    low_chroma[0, :] = 1.0
    structural = np.zeros(12, dtype=float)
    structural[0] = 1.0

    tonic, tonal_conf, mode, mode_conf = asa._estimate_tonal_center_mode(
        chroma,
        low_chroma,
        structural,
        phase_confidence=0.9,
        bar_start_confidence=0.9,
        fallback_key="F#",
        fallback_scale="minor",
    )

    assert tonic == 0
    assert mode == "major"
    assert tonal_conf > 0.25
    assert mode_conf > 0.2
