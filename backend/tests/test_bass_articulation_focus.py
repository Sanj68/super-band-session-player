"""Focused contracts for producer-selected bass articulation families."""

from __future__ import annotations

from collections import Counter

import pytest

from app.services import generator
from app.services.bass_performance import (
    BassPerformanceNote,
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


def test_natural_focus_is_the_legacy_default() -> None:
    notes = (
        _note(start=0.25, end=0.34, velocity=48),
        _note(
            pitch=42,
            start=0.5,
            end=0.98,
            velocity=88,
            slot_index=4,
        ),
    )

    legacy = infer_bass_articulations(
        notes,
        tempo=120,
        style="rhythmic",
        expression_amount=1.0,
        instrument_family="finger_bass",
    )
    explicit = infer_bass_articulations(
        notes,
        tempo=120,
        style="rhythmic",
        expression_amount=1.0,
        instrument_family="finger_bass",
        bass_articulation_focus="natural",
    )

    assert explicit == legacy


def test_clean_focus_forces_every_note_to_normal() -> None:
    notes = (
        _note(articulation="ghost"),
        _note(
            pitch=42,
            start=0.5,
            end=0.9,
            articulation="slide_to",
        ),
    )

    focused = infer_bass_articulations(
        notes,
        tempo=120,
        style="fusion",
        expression_amount=1.0,
        instrument_family="finger_bass",
        bass_articulation_focus="clean",
    )

    assert [note.articulation for note in focused] == ["normal", "normal"]


@pytest.mark.parametrize(
    ("focus", "expected"),
    [
        ("ghosted", "ghost"),
        ("muted", "dead"),
    ],
)
def test_explicit_punctuation_focus_is_audible_at_default_expression(
    focus: str,
    expected: str,
) -> None:
    notes = (
        _note(
            pitch=36,
            start=0.0,
            end=0.45,
            velocity=96,
            role="anchor",
            slot_index=0,
        ),
        _note(
            pitch=40,
            start=0.5,
            end=0.6,
            velocity=82,
            role="answer",
            slot_index=3,
        ),
        _note(
            pitch=43,
            start=1.0,
            end=1.1,
            velocity=80,
            role="push",
            slot_index=7,
        ),
    )

    first = infer_bass_articulations(
        notes,
        tempo=120,
        style="supportive",
        expression_amount=0.5,
        instrument_family="finger_bass",
        bass_articulation_focus=focus,
    )
    second = infer_bass_articulations(
        notes,
        tempo=120,
        style="supportive",
        expression_amount=0.5,
        instrument_family="finger_bass",
        bass_articulation_focus=focus,
    )

    assert first == second
    assert expected in {note.articulation for note in first}
    assert {
        note.articulation for note in first
    }.issubset({"normal", expected})


def test_connected_focus_adds_connected_intent_at_default_expression() -> None:
    notes = (
        _note(
            pitch=40,
            start=0.0,
            end=0.48,
            velocity=92,
            role="answer",
            slot_index=0,
        ),
        _note(
            pitch=42,
            start=0.5,
            end=0.9,
            velocity=86,
            role="answer",
            slot_index=2,
        ),
    )

    focused = infer_bass_articulations(
        notes,
        tempo=120,
        style="melodic",
        expression_amount=0.5,
        instrument_family="finger_bass",
        bass_articulation_focus="connected",
    )

    assert focused[1].articulation == "hammer"
    assert {
        note.articulation for note in focused
    }.issubset({"normal", "grace", "hammer", "slide_to"})


def test_connected_focus_covers_grace_and_slide_intent() -> None:
    grace_pair = (
        _note(
            pitch=39,
            start=0.18,
            end=0.23,
            velocity=50,
            role="answer",
            slot_index=1,
        ),
        _note(
            pitch=40,
            start=0.27,
            end=0.7,
            velocity=88,
            role="answer",
            slot_index=2,
        ),
    )
    slide_pair = (
        _note(
            pitch=40,
            start=0.0,
            end=0.48,
            velocity=92,
            role="answer",
            slot_index=0,
        ),
        _note(
            pitch=45,
            start=0.5,
            end=0.9,
            velocity=86,
            role="answer",
            slot_index=2,
        ),
    )

    grace_focused = infer_bass_articulations(
        grace_pair,
        tempo=120,
        expression_amount=0.5,
        instrument_family="finger_bass",
        bass_articulation_focus="connected",
    )
    slide_focused = infer_bass_articulations(
        slide_pair,
        tempo=120,
        expression_amount=0.5,
        instrument_family="finger_bass",
        bass_articulation_focus="connected",
    )

    assert grace_focused[0].articulation == "grace"
    assert slide_focused[1].articulation == "slide_to"


@pytest.mark.parametrize("source", ["baseline", "phrase_v2"])
def test_generated_connected_fallback_is_bounded_to_one_beat(
    source: str,
) -> None:
    sixteenth = 60.0 / 120.0 / 4.0
    previous = _note(
        pitch=40,
        start=0.0,
        end=0.25,
        slot_index=0,
    )
    viable = _note(
        pitch=45,
        start=0.25 + (sixteenth * 3.5),
        end=0.25 + (sixteenth * 6.5),
        slot_index=4,
    )
    too_far = _note(
        pitch=45,
        start=0.25 + (sixteenth * 4.1),
        end=0.25 + (sixteenth * 7.1),
        slot_index=5,
    )

    connected = infer_bass_articulations(
        (previous, viable),
        tempo=120,
        source=source,  # type: ignore[arg-type]
        expression_amount=0.5,
        instrument_family="finger_bass",
        bass_articulation_focus="connected",
    )
    disconnected = infer_bass_articulations(
        (previous, too_far),
        tempo=120,
        source=source,  # type: ignore[arg-type]
        expression_amount=0.5,
        instrument_family="finger_bass",
        bass_articulation_focus="connected",
    )

    assert connected[1].articulation == "slide_to"
    assert {note.articulation for note in disconnected} == {"normal"}


@pytest.mark.parametrize(
    ("focus", "instrument"),
    [
        ("ghosted", "fretless_bass"),
        ("muted", "upright_bass"),
        ("connected", "sub_bass"),
    ],
)
def test_unsupported_focus_resolves_clean(
    focus: str,
    instrument: str,
) -> None:
    notes = (
        _note(pitch=40, start=0.0, end=0.48, slot_index=3),
        _note(pitch=42, start=0.5, end=0.9, slot_index=7),
    )

    focused = infer_bass_articulations(
        notes,
        tempo=120,
        style="fusion",
        expression_amount=1.0,
        instrument_family=instrument,
        bass_articulation_focus=focus,
    )

    assert {note.articulation for note in focused} == {"normal"}


@pytest.mark.parametrize(
    ("focus", "audible_labels"),
    [
        ("ghosted", {"ghost"}),
        ("muted", {"dead"}),
        ("connected", {"grace", "hammer", "slide_to"}),
    ],
)
@pytest.mark.parametrize("engine", ["baseline", "phrase_v2"])
def test_focus_is_audible_through_both_engines_without_changing_clean_midi(
    engine: str,
    focus: str,
    audible_labels: set[str],
) -> None:
    common = {
        "tempo": 88,
        "bar_count": 8,
        "key": "D",
        "scale": "natural_minor",
        "bass_style": "fusion",
        "bass_instrument": "finger_bass",
        "bass_engine": engine,
        "seed": 240726,
        "return_performance_notes": True,
        "expression_amount": 0.5,
    }
    natural_bytes, _natural_preview, natural_notes = generator.generate_bass(
        **common,
        bass_articulation_focus="natural",
    )
    focused_bytes, _focused_preview, focused_notes = generator.generate_bass(
        **common,
        bass_articulation_focus=focus,
    )

    focused_counts = Counter(note.articulation for note in focused_notes)
    assert focused_bytes == natural_bytes
    assert any(focused_counts[label] > 0 for label in audible_labels)
    assert focused_notes != natural_notes


@pytest.mark.parametrize(
    "instrument",
    ["finger_bass", "fretless_bass", "upright_bass"],
)
@pytest.mark.parametrize(
    "style",
    ["supportive", "melodic", "rhythmic", "slap", "fusion"],
)
@pytest.mark.parametrize("engine", ["baseline", "phrase_v2"])
def test_connected_focus_has_a_bounded_gesture_across_public_styles(
    engine: str,
    style: str,
    instrument: str,
) -> None:
    connected_labels = {"grace", "hammer", "slide_to"}
    for seed in range(12):
        _midi, _preview, notes = generator.generate_bass(
            tempo=96,
            bar_count=8,
            key="D",
            scale="natural_minor",
            bass_style=style,
            bass_instrument=instrument,
            bass_engine=engine,
            seed=seed,
            return_performance_notes=True,
            expression_amount=0.5,
            bass_articulation_focus="connected",
        )
        labels = Counter(note.articulation for note in notes)
        assert sum(labels[label] for label in connected_labels) >= 1
