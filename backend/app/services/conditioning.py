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

    def harmonic_bar(self, bar: int) -> ConditioningHarmonicBar | None:
        if not self.harmonic_bars:
            return None
        i = max(0, min(bar, len(self.harmonic_bars) - 1))
        return self.harmonic_bars[i]


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
        bar_count=bars,
        beat_phase_offset_beats=max(0, min(3, phase)),
        beat_phase_confidence=max(0.0, min(1.0, phase_conf)),
        bar_start_anchor_sec=max(0.0, anchor_sec),
        beat_grid_seconds=tuple(float(x) for x in source.beat_grid_seconds),
        bar_starts_seconds=tuple(float(x) for x in source.bar_starts_seconds),
        sections=tuple(source.sections),
        groove_profile=groove,
        harmonic_bars=tuple(harm_rows),
    )
