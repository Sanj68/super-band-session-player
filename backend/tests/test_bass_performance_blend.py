"""Phrase-aware contracts for blended bass-performance articulation."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from app.services import generator
from app.services.bass_performance import (
    BassPerformanceNote,
    _is_blend_punctuation_candidate,
    infer_bass_articulations,
)


def _note(**overrides: object) -> BassPerformanceNote:
    values: dict[str, object] = {
        "pitch": 40,
        "start": 0.0,
        "end": 0.1,
        "velocity": 84,
        "articulation": "normal",
        "role": "answer",
        "bar_index": 0,
        "slot_index": 3,
        "source": "phrase_v2",
    }
    values.update(overrides)
    return BassPerformanceNote(**values)


def _live_fusion_phrase() -> tuple[BassPerformanceNote, ...]:
    """Use the current 88-BPM D-minor Fusion/Upright seed-style fixture."""

    _midi, _preview, notes = generator.generate_bass(
        tempo=88,
        bar_count=16,
        key="D",
        scale="natural_minor",
        bass_style="fusion",
        bass_instrument="upright_bass",
        bass_engine="phrase_v2",
        chord_progression=["Bb", "C", "D", "Gm"],
        seed=1_956_324_183,
        density_bias=0.87,
        expression_amount=1.0,
        bass_articulation_focus="natural",
        lock_to_groove=1.0,
        return_performance_notes=True,
    )
    # The planner receives phrase intent before legacy Natural inference.
    return tuple(
        replace(note, articulation="normal")
        for note in notes
    )


def _assert_musical_fields_unchanged(
    before: tuple[BassPerformanceNote, ...],
    after: tuple[BassPerformanceNote, ...],
) -> None:
    assert len(after) == len(before)
    for source, planned in zip(before, after, strict=True):
        assert replace(planned, articulation=source.articulation) == source


def test_live_fusion_high_ghost_amount_is_bounded_and_blended() -> None:
    notes = _live_fusion_phrase()

    first = infer_bass_articulations(
        notes,
        tempo=88,
        style="fusion",
        source="phrase_v2",
        instrument_family="upright_bass",
        ghost_amount=1.0,
        mute_amount=0.0,
        slide_amount=1.0,
        legato_amount=1.0,
    )
    second = infer_bass_articulations(
        notes,
        tempo=88,
        style="fusion",
        source="phrase_v2",
        instrument_family="upright_bass",
        ghost_amount=1.0,
        mute_amount=0.0,
        slide_amount=1.0,
        legato_amount=1.0,
    )

    assert first == second
    _assert_musical_fields_unchanged(notes, first)
    counts = Counter(note.articulation for note in first)
    sixteenth = 60.0 / 88.0 / 4.0
    eligible_count = sum(
        _is_blend_punctuation_candidate(
            note,
            sixteenth=sixteenth,
        )
        for note in notes
    )
    ghost_share = counts["ghost"] / eligible_count

    assert 0.08 <= ghost_share <= 0.18
    assert counts["ghost"] > 0
    assert counts["slide_to"] + counts["hammer"] > 0
    assert counts["normal"] > counts["ghost"]


def test_strong_grid_and_anchor_notes_never_become_punctuation() -> None:
    notes = tuple(
        _note(
            pitch=40 + index,
            start=index * 0.2,
            end=index * 0.2 + 0.1,
            role="anchor" if slot in (0, 8) else "answer",
            bar_index=index // 4,
            slot_index=slot,
        )
        for index, slot in enumerate(
            (0, 1, 4, 3, 8, 5, 12, 7, 0, 9, 4, 11)
        )
    )

    planned = infer_bass_articulations(
        notes,
        tempo=120,
        style="fusion",
        instrument_family="finger_bass",
        ghost_amount=1.0,
        mute_amount=1.0,
        slide_amount=0.0,
        legato_amount=0.0,
    )

    for note in planned:
        if note.slot_index in (0, 4, 8, 12) or note.role == "anchor":
            assert note.articulation not in {"ghost", "dead"}
    assert any(
        note.articulation in {"ghost", "dead"}
        for note in planned
    )
    _assert_musical_fields_unchanged(notes, planned)


def test_slide_and_legato_amounts_gate_independent_connected_intent() -> None:
    notes = (
        _note(
            pitch=40,
            start=0.0,
            end=0.20,
            slot_index=0,
        ),
        _note(
            pitch=42,
            start=0.22,
            end=0.40,
            slot_index=2,
        ),
        _note(
            pitch=47,
            start=0.42,
            end=0.60,
            slot_index=3,
        ),
    )

    slides_only = infer_bass_articulations(
        notes,
        tempo=120,
        style="fusion",
        instrument_family="finger_bass",
        ghost_amount=0.0,
        mute_amount=0.0,
        slide_amount=1.0,
        legato_amount=0.0,
    )
    legato_only = infer_bass_articulations(
        notes,
        tempo=120,
        style="fusion",
        instrument_family="finger_bass",
        ghost_amount=0.0,
        mute_amount=0.0,
        slide_amount=0.0,
        legato_amount=1.0,
    )

    assert [note.articulation for note in slides_only] == [
        "normal",
        "normal",
        "slide_to",
    ]
    assert [note.articulation for note in legato_only] == [
        "normal",
        "hammer",
        "normal",
    ]
    _assert_musical_fields_unchanged(notes, slides_only)
    _assert_musical_fields_unchanged(notes, legato_only)


def test_blend_preserves_pre_authored_non_normal_intent() -> None:
    notes = (
        _note(
            pitch=39,
            start=0.0,
            end=0.05,
            velocity=48,
            articulation="grace",
            slot_index=1,
        ),
        _note(
            pitch=40,
            start=0.08,
            end=0.22,
            slot_index=2,
        ),
        _note(
            pitch=45,
            start=0.24,
            end=0.40,
            articulation="slide_to",
            slot_index=3,
        ),
    )

    planned = infer_bass_articulations(
        notes,
        tempo=120,
        style="fusion",
        instrument_family="finger_bass",
        ghost_amount=1.0,
        mute_amount=1.0,
        slide_amount=1.0,
        legato_amount=1.0,
    )

    assert planned[0].articulation == "grace"
    assert planned[2].articulation == "slide_to"
    _assert_musical_fields_unchanged(notes, planned)


def test_all_none_amounts_preserve_legacy_focus_behavior() -> None:
    notes = (
        _note(
            pitch=40,
            start=0.0,
            end=0.48,
            slot_index=0,
        ),
        _note(
            pitch=42,
            start=0.5,
            end=0.9,
            slot_index=2,
        ),
    )

    legacy = infer_bass_articulations(
        notes,
        tempo=120,
        style="fusion",
        expression_amount=0.5,
        instrument_family="finger_bass",
        bass_articulation_focus="connected",
    )
    explicit_none = infer_bass_articulations(
        notes,
        tempo=120,
        style="fusion",
        expression_amount=0.5,
        instrument_family="finger_bass",
        bass_articulation_focus="connected",
        ghost_amount=None,
        mute_amount=None,
        slide_amount=None,
        legato_amount=None,
    )

    assert explicit_none == legacy
