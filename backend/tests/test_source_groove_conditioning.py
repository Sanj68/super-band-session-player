"""v0.7 source groove maps on ``UnifiedConditioning`` and helpers."""

from __future__ import annotations

from types import SimpleNamespace

from app.models.session import GrooveProfile, HarmonyPlan, SourceAnalysis
from app.services.conditioning import (
    UnifiedConditioning,
    build_unified_conditioning,
    has_source_groove,
    source_kick_weight,
    source_slot_pressure,
    source_snare_weight,
)


def _source_with_groove(*, bars: int = 2) -> SourceAnalysis:
    rows = [[(i + s) / 20.0 for s in range(16)] for i in range(bars)]
    pressure = [[min(1.0, 0.1 + 0.05 * s) for s in range(16)] for _ in range(bars)]
    return SourceAnalysis(
        source_lane="reference_audio",
        tempo=120,
        tempo_estimate_bpm=120.0,
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
        bar_start_confidence=0.65,
        tonal_center_pc_guess=0,
        tonal_center_confidence=0.5,
        scale_mode_guess="major",
        scale_mode_confidence=0.5,
        sections=[],
        bar_energy=[0.5] * bars,
        bar_accent_profile=[0.4] * bars,
        bar_confidence_profile=[0.6] * bars,
        source_onset_weight=rows,
        source_kick_weight=rows,
        source_snare_weight=rows,
        source_slot_pressure=pressure,
        source_groove_confidence=[0.7] * bars,
    )


def test_build_unified_conditioning_passes_groove_maps() -> None:
    src = _source_with_groove(bars=2)
    groove = GrooveProfile(
        pocket_feel="steady",
        syncopation_score=0.2,
        density_per_bar_estimate=4.0,
        accent_strength=0.5,
        confidence=0.8,
    )
    harmony = HarmonyPlan(key_center="C", scale="major", source="static_progression", bars=[])
    session = SimpleNamespace(bar_count=2, tempo=120)
    uc = build_unified_conditioning(session=session, source=src, groove=groove, harmony=harmony, context=None)
    assert uc.bar_count == 2
    assert len(uc.source_slot_pressure) == 2
    assert len(uc.source_kick_weight[0]) == 16
    assert tuple(uc.source_groove_confidence) == (0.7, 0.7)


def test_has_source_groove_true_when_pressure_nonempty() -> None:
    src = _source_with_groove()
    groove = GrooveProfile(
        pocket_feel="steady",
        syncopation_score=0.1,
        density_per_bar_estimate=2.0,
        accent_strength=0.3,
        confidence=0.9,
    )
    harmony = HarmonyPlan(key_center="C", scale="major", source="static_progression", bars=[])
    uc = build_unified_conditioning(
        session=SimpleNamespace(bar_count=2, tempo=120),
        source=src,
        groove=groove,
        harmony=harmony,
        context=None,
    )
    assert has_source_groove(uc) is True


def test_helpers_return_expected_clamped_indices() -> None:
    pressure = [[0.0] * 16, [0.0] * 16]
    pressure[0][5] = 0.42
    kick = [[0.0] * 16, [0.0] * 16]
    kick[1][3] = 0.88
    snare = [[0.0] * 16, [0.0] * 16]
    snare[0][15] = 0.31
    uc = UnifiedConditioning(
        tempo=100,
        bar_count=2,
        beat_phase_offset_beats=0,
        beat_phase_confidence=0.8,
        bar_start_anchor_sec=0.0,
        beat_grid_seconds=(0.0, 0.5),
        bar_starts_seconds=(0.0, 2.0),
        sections=(),
        groove_profile=GrooveProfile(
            pocket_feel="steady",
            syncopation_score=0.1,
            density_per_bar_estimate=2.0,
            accent_strength=0.3,
            confidence=0.9,
        ),
        harmonic_bars=(),
        source_slot_pressure=tuple(tuple(r) for r in pressure),
        source_kick_weight=tuple(tuple(r) for r in kick),
        source_snare_weight=tuple(tuple(r) for r in snare),
    )
    assert source_slot_pressure(uc, 0, 5) == 0.42
    assert source_kick_weight(uc, 1, 3) == 0.88
    assert source_snare_weight(uc, 0, 15) == 0.31


def test_helpers_clamp_bar_and_slot_indices() -> None:
    kick = [[0.25] * 16]
    uc = UnifiedConditioning(
        tempo=100,
        bar_count=1,
        beat_phase_offset_beats=0,
        beat_phase_confidence=0.5,
        bar_start_anchor_sec=0.0,
        beat_grid_seconds=tuple(0.5 * i for i in range(4)),
        bar_starts_seconds=(0.0,),
        sections=(),
        groove_profile=GrooveProfile(
            pocket_feel="steady",
            syncopation_score=0.1,
            density_per_bar_estimate=2.0,
            accent_strength=0.3,
            confidence=0.5,
        ),
        harmonic_bars=(),
        source_kick_weight=(tuple(kick[0]),),
    )
    assert source_kick_weight(uc, 99, 0) == 0.25
    assert source_kick_weight(uc, -3, 0) == 0.25
    assert source_kick_weight(uc, 0, 99) == source_kick_weight(uc, 0, 15)


def test_helpers_return_zero_for_missing_or_short_rows() -> None:
    uc = UnifiedConditioning(
        tempo=100,
        bar_count=4,
        beat_phase_offset_beats=0,
        beat_phase_confidence=0.5,
        bar_start_anchor_sec=0.0,
        beat_grid_seconds=tuple(0.5 * i for i in range(16)),
        bar_starts_seconds=tuple(2.0 * i for i in range(4)),
        sections=(),
        groove_profile=GrooveProfile(
            pocket_feel="steady",
            syncopation_score=0.1,
            density_per_bar_estimate=2.0,
            accent_strength=0.3,
            confidence=0.5,
        ),
        harmonic_bars=(),
        source_slot_pressure=((0.5, 0.1),),
    )
    assert has_source_groove(uc) is False
    assert source_slot_pressure(uc, 0, 0) == 0.5
    assert source_slot_pressure(uc, 3, 0) == 0.0
    assert source_kick_weight(uc, 0, 0) == 0.0
    assert source_snare_weight(None, 0, 0) == 0.0
