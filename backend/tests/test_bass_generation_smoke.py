from __future__ import annotations

import io
import random

import pretty_midi

from app.models.session import GrooveProfile
from app.services.bass_generator import generate_bass
from app.services.conditioning import UnifiedConditioning


def _reference_conditioning(
    *,
    tempo_confidence: float = 0.8,
    beat_phase_confidence: float = 0.8,
    bar_start_confidence: float = 0.8,
    groove_confidence: float = 0.8,
    pocket_feel: str = "syncopated",
    syncopation_score: float = 0.8,
    bar_energy: tuple[float, ...] = (0.8, 0.8, 0.8, 0.8),
    bar_confidence: tuple[float, ...] = (0.8, 0.8, 0.8, 0.8),
) -> UnifiedConditioning:
    bars = len(bar_energy)
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
            pocket_feel=pocket_feel,
            syncopation_score=syncopation_score,
            density_per_bar_estimate=4.0,
            accent_strength=0.7,
            confidence=groove_confidence,
        ),
        harmonic_bars=(),
        tempo_confidence=tempo_confidence,
        bar_start_confidence=bar_start_confidence,
        bar_energy=bar_energy,
        bar_accent=tuple(0.6 for _ in range(bars)),
        bar_confidence=bar_confidence,
    )


def test_supportive_bass_seeded_smoke_is_repeatable_and_valid() -> None:
    random.seed(20260426)
    b1, preview1 = generate_bass(
        tempo=100,
        bar_count=8,
        key="B",
        scale="minor",
        bass_style="supportive",
        context=None,
    )
    random.seed(20260426)
    b2, preview2 = generate_bass(
        tempo=100,
        bar_count=8,
        key="B",
        scale="minor",
        bass_style="supportive",
        context=None,
    )
    assert b1 == b2
    assert preview1 == preview2

    pm = pretty_midi.PrettyMIDI(io.BytesIO(b1))
    notes = [n for inst in pm.instruments for n in inst.notes]
    assert len(notes) >= 8  # at least one note per bar on average
    total_len = (60.0 / 100.0) * 4.0 * 8.0
    eps = 1e-3
    for n in notes:
        assert 0.0 <= n.start < total_len
        assert 0.0 < n.end <= total_len + eps
        assert n.end > n.start
        assert 0 <= n.pitch <= 127
        assert 1 <= n.velocity <= 127


def test_supportive_bass_custom_progression_adds_release_approach_note() -> None:
    random.seed(20260501)
    data, _preview = generate_bass(
        tempo=100,
        bar_count=5,
        key="C",
        scale="major",
        bass_style="supportive",
        chord_progression=["Am7", "D7", "Gmaj7", "Cmaj7"],
        context=None,
    )

    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes = sorted([n for inst in pm.instruments for n in inst.notes], key=lambda n: (n.start, n.pitch))
    spb = 60.0 / 100.0
    bar_len = spb * 4.0
    release_bar_start = 3 * bar_len
    release_bar_end = 4 * bar_len

    # Bar 4 releases into the looped next chord, Am7. The approach should sit
    # very late in the release bar and land a semitone around A.
    late_release_notes = [
        n for n in notes
        if release_bar_end - (spb / 4.0) <= n.start < release_bar_end and n.pitch % 12 in {8, 10}
    ]
    assert late_release_notes


def test_high_confidence_reference_guidance_changes_supportive_bass_output() -> None:
    random.seed(20260502)
    no_ref, no_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        context=None,
    )
    random.seed(20260502)
    with_ref, with_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        context=None,
        conditioning=_reference_conditioning(),
    )

    assert with_ref != no_ref
    assert with_ref_preview != no_ref_preview
    assert "(ref groove: syncopated, conf 0.80)" in with_ref_preview


def test_low_confidence_reference_guidance_matches_no_reference() -> None:
    random.seed(20260503)
    no_ref, no_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        context=None,
    )
    random.seed(20260503)
    low_ref, low_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        context=None,
        conditioning=_reference_conditioning(tempo_confidence=0.2),
    )

    assert low_ref == no_ref
    assert low_ref_preview == no_ref_preview


def test_reference_guidance_does_not_change_non_supportive_style() -> None:
    random.seed(20260504)
    no_ref, no_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="melodic",
        context=None,
    )
    random.seed(20260504)
    with_ref, with_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="melodic",
        context=None,
        conditioning=_reference_conditioning(),
    )

    assert with_ref == no_ref
    assert with_ref_preview == no_ref_preview


def test_reference_guidance_does_not_change_phrase_v2() -> None:
    random.seed(20260505)
    no_ref, no_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        bass_engine="phrase_v2",
        context=None,
    )
    random.seed(20260505)
    with_ref, with_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        bass_engine="phrase_v2",
        context=None,
        conditioning=_reference_conditioning(),
    )

    assert with_ref == no_ref
    assert with_ref_preview == no_ref_preview
