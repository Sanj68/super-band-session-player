"""Reference-audio groove guidance gates for future generation passes."""

from __future__ import annotations

from dataclasses import dataclass

from app.services.conditioning import UnifiedConditioning, has_source_groove


@dataclass(frozen=True)
class ReferenceGrooveGuidance:
    available: bool
    source_tag: str
    reason: str
    bar_energy: tuple[float, ...] = ()
    bar_accent: tuple[float, ...] = ()
    bar_confidence: tuple[float, ...] = ()
    min_bar_conf: float = 0.35
    has_source_slot_groove: bool = False

    def should_apply_bar(self, bar_index: int) -> bool:
        if not self.available:
            return False
        if bar_index < 0 or bar_index >= len(self.bar_confidence):
            return False
        return float(self.bar_confidence[bar_index]) >= self.min_bar_conf


def _clean_values(values: tuple[float, ...] | list[float] | None) -> tuple[float, ...]:
    return tuple(float(v) for v in (values or ()))


def build_reference_guidance(
    conditioning: UnifiedConditioning | None,
    *,
    has_midi_anchor: bool,
    min_global_conf: float = 0.4,
    min_bar_conf: float = 0.35,
) -> ReferenceGrooveGuidance:
    """Build deterministic reference groove guidance with MIDI-anchor precedence."""
    if has_midi_anchor:
        return ReferenceGrooveGuidance(
            available=False,
            source_tag="session_midi",
            reason="midi_anchor_present",
            min_bar_conf=min_bar_conf,
            has_source_slot_groove=False,
        )
    if conditioning is None:
        return ReferenceGrooveGuidance(
            available=False,
            source_tag="fallback",
            reason="no_reference_audio",
            min_bar_conf=min_bar_conf,
            has_source_slot_groove=False,
        )

    bar_energy = _clean_values(conditioning.bar_energy)
    bar_accent = _clean_values(conditioning.bar_accent)
    bar_confidence = _clean_values(conditioning.bar_confidence)
    slot_groove_ok = has_source_groove(conditioning)
    base = {
        "bar_energy": bar_energy,
        "bar_accent": bar_accent,
        "bar_confidence": bar_confidence,
        "min_bar_conf": min_bar_conf,
        "has_source_slot_groove": slot_groove_ok,
    }

    tempo_conf = float(conditioning.tempo_confidence)
    if tempo_conf < min_global_conf:
        return ReferenceGrooveGuidance(False, "reference_audio", "low_tempo_confidence", **base)

    phase_conf = max(float(conditioning.beat_phase_confidence), float(conditioning.bar_start_confidence))
    if phase_conf < min_global_conf:
        return ReferenceGrooveGuidance(False, "reference_audio", "low_phase_confidence", **base)

    groove = conditioning.groove_profile
    if groove is not None and float(groove.confidence) < min_global_conf:
        return ReferenceGrooveGuidance(False, "reference_audio", "low_groove_confidence", **base)

    if not bar_energy or not bar_accent or not bar_confidence:
        return ReferenceGrooveGuidance(False, "reference_audio", "missing_bar_profiles", **base)

    return ReferenceGrooveGuidance(True, "reference_audio", "available", **base)
