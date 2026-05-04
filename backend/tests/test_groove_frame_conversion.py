"""Round-trip and merge between SourceAnalysis groove fields and GrooveFrame."""

from __future__ import annotations

from app.models.groove_frame import GROOVE_SLOTS, GrooveFrame
from app.models.session import SourceAnalysis
from app.services.groove_frame import (
    groove_frames_from_source_analysis,
    merge_groove_frames,
    source_analysis_from_groove_frames,
)


def _source_analysis(
    *,
    bars: int = 2,
    kick: list[list[float]] | None = None,
    pressure: list[list[float]] | None = None,
) -> SourceAnalysis:
    base_kick = kick or [[0.1 * (i + 1)] * GROOVE_SLOTS for i in range(bars)]
    base_pressure = pressure or [[0.05 * (s + 1) for s in range(GROOVE_SLOTS)] for _ in range(bars)]
    return SourceAnalysis(
        source_lane="reference_audio",
        tempo=120,
        tempo_estimate_bpm=118.5,
        tempo_confidence=0.8,
        beat_grid_seconds=[0.5 * j for j in range(bars * 4)],
        bar_starts_seconds=[2.0 * j for j in range(bars)],
        beat_phase_offset_beats=0,
        beat_phase_scores=[1.0, 0.0, 0.0, 0.0],
        beat_phase_confidence=0.7,
        phase_offset_used_for_generation_beats=0,
        bar_start_anchor_used_seconds=0.0,
        generation_aligned_to_anchor=False,
        downbeat_guess_bar_index=0,
        downbeat_confidence=0.6,
        bar_start_confidence=0.7,
        tonal_center_pc_guess=0,
        tonal_center_confidence=0.55,
        scale_mode_guess="major",
        scale_mode_confidence=0.55,
        sections=[],
        bar_energy=[0.5] * bars,
        bar_accent_profile=[0.4] * bars,
        bar_confidence_profile=[0.6] * bars,
        source_kick_weight=base_kick,
        source_slot_pressure=base_pressure,
        source_snare_weight=[[0.1] * GROOVE_SLOTS for _ in range(bars)],
        source_onset_weight=[[0.2] * GROOVE_SLOTS for _ in range(bars)],
        source_groove_confidence=[0.7] * bars,
        source_metadata={"groove_map_version": "v0.7.0"},
    )


def test_source_analysis_to_groove_frames_preserves_groove_fields() -> None:
    sa = _source_analysis(bars=2)
    frames = groove_frames_from_source_analysis(sa)
    assert len(frames) == 2
    assert frames[0].bar_index == 0
    assert frames[1].bar_index == 1
    assert len(frames[0].kick_weight) == GROOVE_SLOTS
    assert frames[0].kick_weight == sa.source_kick_weight[0]
    assert frames[0].slot_pressure == sa.source_slot_pressure[0]
    assert frames[0].confidence == 0.7


def test_round_trip_preserves_groove_matrices_and_other_fields() -> None:
    sa = _source_analysis(bars=3)
    frames = groove_frames_from_source_analysis(sa)
    rebuilt = source_analysis_from_groove_frames(sa, frames)

    assert rebuilt.tempo == sa.tempo
    assert rebuilt.tempo_estimate_bpm == sa.tempo_estimate_bpm
    assert rebuilt.beat_grid_seconds == sa.beat_grid_seconds
    assert rebuilt.bar_starts_seconds == sa.bar_starts_seconds
    assert rebuilt.scale_mode_guess == sa.scale_mode_guess
    assert rebuilt.bar_energy == sa.bar_energy

    assert rebuilt.source_kick_weight == sa.source_kick_weight
    assert rebuilt.source_slot_pressure == sa.source_slot_pressure
    assert rebuilt.source_snare_weight == sa.source_snare_weight
    assert rebuilt.source_groove_confidence == sa.source_groove_confidence


def test_merge_keeps_unmodified_bars_when_replace_false() -> None:
    sa = _source_analysis(bars=3)
    new_kick = [0.9] * GROOVE_SLOTS
    new_pressure = [0.8] * GROOVE_SLOTS
    frame = GrooveFrame(
        bar_index=1,
        kick_weight=new_kick,
        snare_weight=[0.2] * GROOVE_SLOTS,
        onset_weight=[0.3] * GROOVE_SLOTS,
        slot_pressure=new_pressure,
        confidence=0.85,
        source_tag="logic_au_bridge",
    )
    merged = merge_groove_frames(sa, [frame], replace_existing=False)

    assert merged.source_kick_weight[0] == sa.source_kick_weight[0]
    assert merged.source_kick_weight[2] == sa.source_kick_weight[2]
    assert merged.source_kick_weight[1] == new_kick
    assert merged.source_slot_pressure[1] == new_pressure
    assert merged.source_groove_confidence[1] == 0.85
    assert merged.source_metadata.get("last_groove_source_tag") == "logic_au_bridge"


def test_merge_replaces_all_bars_when_replace_true() -> None:
    sa = _source_analysis(bars=3)
    frames = [
        GrooveFrame(
            bar_index=0,
            kick_weight=[0.3] * GROOVE_SLOTS,
            snare_weight=[0.0] * GROOVE_SLOTS,
            onset_weight=[0.0] * GROOVE_SLOTS,
            slot_pressure=[0.4] * GROOVE_SLOTS,
            confidence=0.5,
        ),
    ]
    merged = merge_groove_frames(sa, frames, replace_existing=True)
    # Frame for bar 0 wins; bars 1 and 2 should be zero rows (replaced and unfilled).
    assert merged.source_kick_weight[0] == [0.3] * GROOVE_SLOTS
    assert merged.source_kick_weight[1] == [0.0] * GROOVE_SLOTS
    assert merged.source_kick_weight[2] == [0.0] * GROOVE_SLOTS
    assert merged.source_groove_confidence[0] == 0.5
    assert merged.source_groove_confidence[1] == 0.0


def test_merge_with_out_of_range_frame_does_not_crash_and_keeps_existing_bars() -> None:
    sa = _source_analysis(bars=2)
    frames = [
        GrooveFrame(bar_index=5, kick_weight=[0.7] * GROOVE_SLOTS, slot_pressure=[0.7] * GROOVE_SLOTS, confidence=0.6),
    ]
    merged = merge_groove_frames(sa, frames, replace_existing=False)
    # SourceAnalysis bar_count is bound by bar_energy length (preserved); extra frames are clipped.
    assert len(merged.source_kick_weight) == len(sa.bar_energy)
    assert merged.source_kick_weight[0] == sa.source_kick_weight[0]
    assert merged.source_kick_weight[1] == sa.source_kick_weight[1]
