"""Unified generation conditioning assembled from MIDI context and/or source analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.session import GrooveProfile, HarmonyPlan, SectionSpan, SourceAnalysis
from app.services.session_context import SessionAnchorContext


@dataclass(frozen=True)
class ConditioningHarmonicBar:
    bar_index: int
    root_pc: int
    target_pcs: tuple[int, ...]
    passing_pcs: tuple[int, ...]
    avoid_pcs: tuple[int, ...]
    confidence: float
    source: str


@dataclass(frozen=True)
class UnifiedConditioning:
    tempo: int
    bar_count: int
    beat_phase_offset_beats: int
    beat_phase_confidence: float
    bar_start_anchor_sec: float
    beat_grid_seconds: tuple[float, ...]
    bar_starts_seconds: tuple[float, ...]
    sections: tuple[SectionSpan, ...]
    groove_profile: GrooveProfile
    harmonic_bars: tuple[ConditioningHarmonicBar, ...]
    tempo_confidence: float = 0.0
    bar_start_confidence: float = 0.0
    bar_energy: tuple[float, ...] | None = None
    bar_accent: tuple[float, ...] | None = None
    bar_confidence: tuple[float, ...] | None = None
    source_groove_resolution: int | None = None
    source_onset_weight: tuple[tuple[float, ...], ...] = ()
    source_kick_weight: tuple[tuple[float, ...], ...] = ()
    source_snare_weight: tuple[tuple[float, ...], ...] = ()
    source_slot_pressure: tuple[tuple[float, ...], ...] = ()
    source_groove_confidence: tuple[float, ...] = ()

    def harmonic_bar(self, bar: int) -> ConditioningHarmonicBar | None:
        if not self.harmonic_bars:
            return None
        i = max(0, min(bar, len(self.harmonic_bars) - 1))
        return self.harmonic_bars[i]


def _groove_bar_slot_indices(conditioning: UnifiedConditioning, bar_index: int, slot_index: int) -> tuple[int, int]:
    """Clamp bar and slot like ``harmonic_bar`` clamps bar index."""
    bars = max(1, int(conditioning.bar_count))
    b = max(0, min(bar_index, bars - 1))
    s = max(0, min(int(slot_index), 15))
    return b, s


def has_source_groove(conditioning: UnifiedConditioning | None) -> bool:
    if conditioning is None:
        return False
    rows = conditioning.source_slot_pressure
    if not rows:
        return False
    if len(rows) < int(conditioning.bar_count):
        return False
    return any(float(x) > 1e-6 for row in rows for x in row)


def source_kick_weight(conditioning: UnifiedConditioning | None, bar_index: int, slot_index: int) -> float:
    if conditioning is None or not conditioning.source_kick_weight:
        return 0.0
    b, s = _groove_bar_slot_indices(conditioning, bar_index, slot_index)
    row = conditioning.source_kick_weight[b] if b < len(conditioning.source_kick_weight) else ()
    if not row or s >= len(row):
        return 0.0
    v = float(row[s])
    return v if v == v else 0.0


def source_snare_weight(conditioning: UnifiedConditioning | None, bar_index: int, slot_index: int) -> float:
    if conditioning is None or not conditioning.source_snare_weight:
        return 0.0
    b, s = _groove_bar_slot_indices(conditioning, bar_index, slot_index)
    row = conditioning.source_snare_weight[b] if b < len(conditioning.source_snare_weight) else ()
    if not row or s >= len(row):
        return 0.0
    v = float(row[s])
    return v if v == v else 0.0


def source_slot_pressure(conditioning: UnifiedConditioning | None, bar_index: int, slot_index: int) -> float:
    if conditioning is None or not conditioning.source_slot_pressure:
        return 0.0
    b, s = _groove_bar_slot_indices(conditioning, bar_index, slot_index)
    row = conditioning.source_slot_pressure[b] if b < len(conditioning.source_slot_pressure) else ()
    if not row or s >= len(row):
        return 0.0
    v = float(row[s])
    return v if v == v else 0.0


def build_unified_conditioning(
    *,
    session: Any,
    source: SourceAnalysis,
    groove: GrooveProfile,
    harmony: HarmonyPlan,
    context: SessionAnchorContext | None,
) -> UnifiedConditioning:
    bars = max(1, int(getattr(session, "bar_count", 8) or 8))
    tempo = int(getattr(session, "tempo", source.tempo) or source.tempo)
    phase = int(source.beat_phase_offset_beats)
    phase_conf = float(source.beat_phase_confidence)
    anchor_sec = float(source.bar_start_anchor_used_seconds)
    if context is not None:
        phase = int(context.beat_phase_offset_beats)
        phase_conf = float(context.beat_phase_confidence)
        anchor_sec = float(context.bar_start_anchor_sec)

    harm_rows: list[ConditioningHarmonicBar] = []
    if context is not None and context.harmonic_target_pcs_per_bar:
        for bar in range(bars):
            i = min(bar, len(context.harmonic_target_pcs_per_bar) - 1)
            harm_rows.append(
                ConditioningHarmonicBar(
                    bar_index=bar,
                    root_pc=int(context.harmonic_root_pc_per_bar[i]),
                    target_pcs=tuple(int(x) % 12 for x in context.harmonic_target_pcs_per_bar[i]),
                    passing_pcs=tuple(int(x) % 12 for x in context.harmonic_passing_pcs_per_bar[i]),
                    avoid_pcs=tuple(int(x) % 12 for x in context.harmonic_avoid_pcs_per_bar[i]),
                    confidence=float(context.harmonic_confidence_per_bar[i]),
                    source=str(context.harmonic_source_per_bar[i]),
                )
            )
    elif harmony.bars:
        for row in harmony.bars:
            harm_rows.append(
                ConditioningHarmonicBar(
                    bar_index=int(row.bar_index),
                    root_pc=int(row.root_pc) % 12,
                    target_pcs=tuple(int(x) % 12 for x in row.target_pcs),
                    passing_pcs=tuple(int(x) % 12 for x in row.passing_pcs),
                    avoid_pcs=tuple(int(x) % 12 for x in row.avoid_pcs),
                    confidence=float(row.confidence),
                    source=str(row.source),
                )
            )

    return UnifiedConditioning(
        tempo=tempo,
        tempo_confidence=max(0.0, min(1.0, float(source.tempo_confidence))),
        bar_count=bars,
        beat_phase_offset_beats=max(0, min(3, phase)),
        beat_phase_confidence=max(0.0, min(1.0, phase_conf)),
        bar_start_confidence=max(0.0, min(1.0, float(source.bar_start_confidence))),
        bar_start_anchor_sec=max(0.0, anchor_sec),
        beat_grid_seconds=tuple(float(x) for x in source.beat_grid_seconds),
        bar_starts_seconds=tuple(float(x) for x in source.bar_starts_seconds),
        bar_energy=tuple(float(x) for x in source.bar_energy),
        bar_accent=tuple(float(x) for x in source.bar_accent_profile),
        bar_confidence=tuple(float(x) for x in source.bar_confidence_profile),
        sections=tuple(source.sections),
        groove_profile=groove,
        harmonic_bars=tuple(harm_rows),
        source_groove_resolution=source.source_groove_resolution,
        source_onset_weight=tuple(tuple(float(x) for x in row) for row in source.source_onset_weight),
        source_kick_weight=tuple(tuple(float(x) for x in row) for row in source.source_kick_weight),
        source_snare_weight=tuple(tuple(float(x) for x in row) for row in source.source_snare_weight),
        source_slot_pressure=tuple(tuple(float(x) for x in row) for row in source.source_slot_pressure),
        source_groove_confidence=tuple(float(x) for x in source.source_groove_confidence),
    )
