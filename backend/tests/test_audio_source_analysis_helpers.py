"""Focused tests for reference-audio analyzer decision helpers."""

from __future__ import annotations

import numpy as np
import pytest

from app.services import audio_source_analysis as asa


def test_filename_musical_hints_reads_strict_sample_pack_tokens() -> None:
    hints = asa.parse_filename_musical_hints("jmh_keys_88_voni_Dm.wav")

    assert hints.tempo_bpm == 88
    assert hints.key == "D"
    assert hints.scale == "natural_minor"


def test_filename_musical_hints_ignores_embedded_catalog_numbers_and_words() -> None:
    hints = asa.parse_filename_musical_hints("SM101_session_take_final.wav")

    assert hints.tempo_bpm is None
    assert hints.key is None
    assert hints.scale is None


@pytest.mark.parametrize(
    ("duration_seconds", "tempo_bpm", "expected"),
    [
        (10.909093, 88.0, 4),
        (16.0, 120.0, 8),
        (10.3, 88.0, None),
    ],
)
def test_infer_bar_count_from_duration_requires_close_integer_fit(
    duration_seconds: float,
    tempo_bpm: float,
    expected: int | None,
) -> None:
    assert asa.infer_bar_count_from_duration(duration_seconds, tempo_bpm) == expected


@pytest.mark.parametrize(
    ("beat_count", "expected"),
    [
        (15, 4),
        (16, 4),
        (17, 4),
        (63, 16),
        (64, 16),
        (65, 16),
        (2, None),
    ],
)
def test_infer_bar_count_from_beats_tolerates_missing_edge_beats(
    beat_count: int,
    expected: int | None,
) -> None:
    assert asa.infer_bar_count_from_beats(beat_count) == expected


def test_audio_analysis_applies_filename_context_before_building_structure(tmp_path) -> None:
    import soundfile as sf

    sr = 22050
    duration = 2.0
    time = np.linspace(0.0, duration, int(sr * duration), endpoint=False)
    audio = 0.35 * np.sin(2 * np.pi * 146.83 * time)
    path = tmp_path / "stored-upload.wav"
    sf.write(str(path), audio.astype(np.float32), sr)

    result = asa.analyze_reference_audio(
        audio_path=path,
        session_tempo=108,
        bar_count=8,
        session_key="A",
        session_scale="major",
        source_filename="pack_keys_120_Cm.wav",
    )

    source = result.source_analysis
    assert source.tempo_estimate_bpm == 120.0
    assert source.tonal_center_pc_guess == 0
    assert source.scale_mode_guess == "minor"
    assert len(source.bar_starts_seconds) == 1
    assert source.source_metadata["filename_hints"]["bar_count"] == 1


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
