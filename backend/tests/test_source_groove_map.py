"""v0.7 source groove map contract on ``SourceAnalysis``."""

from __future__ import annotations

from app.models.session import SourceAnalysis


def _minimal_source_analysis(**kwargs: object) -> SourceAnalysis:
    base = dict(
        source_lane="reference_audio",
        tempo=120,
        tempo_estimate_bpm=120.0,
        tempo_confidence=0.8,
        beat_grid_seconds=[0.0, 0.5, 1.0, 1.5],
        bar_starts_seconds=[0.0, 2.0],
        beat_phase_offset_beats=0,
        beat_phase_scores=[1.0, 0.0, 0.0, 0.0],
        beat_phase_confidence=0.7,
        phase_offset_used_for_generation_beats=0,
        bar_start_anchor_used_seconds=0.0,
        generation_aligned_to_anchor=False,
        downbeat_guess_bar_index=0,
        downbeat_confidence=0.6,
        bar_start_confidence=0.65,
        tonal_center_pc_guess=0,
        tonal_center_confidence=0.5,
        scale_mode_guess="major",
        scale_mode_confidence=0.5,
        sections=[],
        bar_energy=[0.5, 0.5],
        bar_accent_profile=[0.4, 0.4],
        bar_confidence_profile=[0.6, 0.6],
    )
    base.update(kwargs)
    return SourceAnalysis(**base)  # type: ignore[arg-type]


def test_source_analysis_serializes_new_groove_fields() -> None:
    sa = _minimal_source_analysis(
        source_groove_resolution=16,
        source_metadata={"groove_map_version": "test"},
        source_onset_weight=[[0.1] * 16, [0.2] * 16],
    )
    data = sa.model_dump()
    assert data["source_groove_resolution"] == 16
    assert len(data["source_onset_weight"]) == 2
    assert data["source_metadata"]["groove_map_version"] == "test"


def test_groove_rows_are_sixteen_slots() -> None:
    sa = _minimal_source_analysis(
        source_onset_weight=[[0.25] * 10, [0.5] * 20],
        source_slot_pressure=[],
    )
    assert len(sa.source_onset_weight) == 2
    assert all(len(row) == 16 for row in sa.source_onset_weight)
    assert len(sa.source_slot_pressure) == 2
    assert all(len(row) == 16 for row in sa.source_slot_pressure)


def test_groove_values_clamped() -> None:
    sa = _minimal_source_analysis(
        source_kick_weight=[[-1.0, 2.0] + [0.0] * 14, [0.3] * 16],
    )
    assert sa.source_kick_weight[0][0] == 0.0
    assert sa.source_kick_weight[0][1] == 1.0


def test_empty_groove_inputs_become_zero_grids() -> None:
    sa = _minimal_source_analysis()
    assert sa.source_onset_weight == [[0.0] * 16, [0.0] * 16]
    assert sa.source_groove_confidence == [0.0, 0.0]


def test_bar_count_derived_from_bar_energy_for_padding() -> None:
    sa = SourceAnalysis(
        source_lane="none",
        tempo=100,
        tempo_estimate_bpm=100.0,
        tempo_confidence=0.5,
        beat_grid_seconds=[],
        bar_starts_seconds=[],
        beat_phase_offset_beats=0,
        beat_phase_scores=[0.25, 0.25, 0.25, 0.25],
        beat_phase_confidence=0.0,
        phase_offset_used_for_generation_beats=0,
        bar_start_anchor_used_seconds=0.0,
        generation_aligned_to_anchor=False,
        downbeat_guess_bar_index=0,
        downbeat_confidence=0.0,
        bar_start_confidence=0.0,
        tonal_center_pc_guess=0,
        tonal_center_confidence=0.0,
        scale_mode_guess="major",
        scale_mode_confidence=0.0,
        sections=[],
        bar_energy=[0.1, 0.2, 0.3],
        bar_accent_profile=[0.0, 0.0, 0.0],
        bar_confidence_profile=[0.0, 0.0, 0.0],
    )
    assert len(sa.source_snare_weight) == 3
    assert all(len(r) == 16 for r in sa.source_snare_weight)


def test_analyze_reference_audio_produces_sixteen_slot_maps(tmp_path) -> None:
    import numpy as np
    import soundfile as sf

    from app.services.audio_source_analysis import analyze_reference_audio

    sr = 22050
    dur = 2.0
    t = np.linspace(0.0, dur, int(sr * dur), endpoint=False)
    y = 0.4 * np.sin(2 * np.pi * 80.0 * t) * (1.0 + 0.5 * np.sin(2 * np.pi * 2.0 * t))
    path = tmp_path / "ref.wav"
    sf.write(str(path), y.astype(np.float32), sr)

    out = analyze_reference_audio(
        audio_path=path,
        session_tempo=120,
        bar_count=2,
        session_key="C",
        session_scale="major",
    )
    sa = out.source_analysis
    assert len(sa.source_slot_pressure) == 2
    assert all(len(r) == 16 for r in sa.source_slot_pressure)
    assert all(0.0 <= v <= 1.0 for row in sa.source_onset_weight for v in row)
    assert sa.source_metadata.get("groove_map_version") == "v0.7.0"
