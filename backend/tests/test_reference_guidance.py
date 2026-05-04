from __future__ import annotations

from app.models.session import GrooveProfile
from app.services.conditioning import UnifiedConditioning
from app.services.reference_guidance import build_reference_guidance


def _conditioning(
    *,
    tempo_confidence: float = 0.8,
    beat_phase_confidence: float = 0.8,
    bar_start_confidence: float = 0.8,
    groove_confidence: float = 0.8,
    bar_confidence: tuple[float, ...] = (0.8, 0.8, 0.8, 0.8),
    source_slot_pressure: tuple[tuple[float, ...], ...] | None = None,
) -> UnifiedConditioning:
    bars = len(bar_confidence)
    return UnifiedConditioning(
        tempo=100,
        bar_count=bars,
        beat_phase_offset_beats=0,
        beat_phase_confidence=beat_phase_confidence,
        bar_start_anchor_sec=0.0,
        beat_grid_seconds=tuple(i * 0.6 for i in range(bars * 4)),
        bar_starts_seconds=tuple(i * 2.4 for i in range(bars)),
        sections=(),
        groove_profile=GrooveProfile(
            pocket_feel="steady",
            syncopation_score=0.2,
            density_per_bar_estimate=4.0,
            accent_strength=0.7,
            confidence=groove_confidence,
        ),
        harmonic_bars=(),
        tempo_confidence=tempo_confidence,
        bar_start_confidence=bar_start_confidence,
        bar_energy=tuple(0.7 for _ in range(bars)),
        bar_accent=tuple(0.6 for _ in range(bars)),
        bar_confidence=bar_confidence,
        source_slot_pressure=source_slot_pressure or (),
    )


def test_missing_conditioning_falls_back_unavailable() -> None:
    guidance = build_reference_guidance(None, has_midi_anchor=False)

    assert guidance.available is False
    assert guidance.source_tag == "fallback"
    assert guidance.should_apply_bar(0) is False


def test_low_confidence_reference_is_unavailable() -> None:
    guidance = build_reference_guidance(
        _conditioning(tempo_confidence=0.2),
        has_midi_anchor=False,
    )

    assert guidance.available is False
    assert guidance.source_tag == "reference_audio"


def test_midi_anchor_wins_and_disables_reference_guidance() -> None:
    guidance = build_reference_guidance(None, has_midi_anchor=True)

    assert guidance.available is False
    assert guidance.source_tag == "session_midi"


def test_midi_anchor_wins_when_reference_is_present() -> None:
    guidance = build_reference_guidance(_conditioning(), has_midi_anchor=True)

    assert guidance.available is False
    assert guidance.source_tag == "session_midi"


def test_high_confidence_reference_without_anchor_is_available() -> None:
    guidance = build_reference_guidance(_conditioning(), has_midi_anchor=False)

    assert guidance.available is True
    assert guidance.source_tag == "reference_audio"
    assert guidance.should_apply_bar(0) is True


def test_per_bar_low_confidence_is_not_applied() -> None:
    guidance = build_reference_guidance(
        _conditioning(bar_confidence=(0.8, 0.2, 0.8, 0.8)),
        has_midi_anchor=False,
    )

    assert guidance.available is True
    assert guidance.should_apply_bar(1) is False
    assert guidance.should_apply_bar(2) is True


def test_has_source_slot_groove_false_without_maps() -> None:
    guidance = build_reference_guidance(_conditioning(), has_midi_anchor=False)
    assert guidance.has_source_slot_groove is False


def test_has_source_slot_groove_true_when_pressure_maps_present() -> None:
    bars = 4
    pressure = tuple(tuple(0.2 for _ in range(16)) for _ in range(bars))
    guidance = build_reference_guidance(
        _conditioning(bar_confidence=(0.8,) * bars, source_slot_pressure=pressure),
        has_midi_anchor=False,
    )
    assert guidance.has_source_slot_groove is True
