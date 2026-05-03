"""v0.5 Step 1: BassPerformanceNote foundation.

These tests pin the locked articulation vocabulary, the dataclass invariants,
and the Step 1 conversion contract (articulation is recorded but not rendered).
"""

from __future__ import annotations

import dataclasses

import pretty_midi
import pytest

from app.services.bass_performance import (
    BassPerformanceNote,
    _VALID_ARTICULATIONS,
    performance_note_to_pretty_midi_note,
)


_LOCKED_VOCABULARY = frozenset(
    {"normal", "ghost", "dead", "slide_from", "slide_to", "hammer", "grace"}
)


def _good_kw(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(pitch=40, start=0.0, end=0.5, velocity=90)
    base.update(overrides)
    return base


def test_vocabulary_exactly_matches_locked_seven_values() -> None:
    assert _VALID_ARTICULATIONS == _LOCKED_VOCABULARY
    assert len(_VALID_ARTICULATIONS) == 7


@pytest.mark.parametrize("articulation", sorted(_LOCKED_VOCABULARY))
def test_each_valid_articulation_constructs_successfully(articulation: str) -> None:
    note = BassPerformanceNote(**_good_kw(articulation=articulation))
    assert note.articulation == articulation


def test_default_articulation_is_normal() -> None:
    note = BassPerformanceNote(**_good_kw())
    assert note.articulation == "normal"


@pytest.mark.parametrize("bad", ["slide_into", "slide_out", "pull", "sustain", "", "Normal", "GHOST"])
def test_invalid_articulation_rejected(bad: str) -> None:
    with pytest.raises(ValueError) as exc:
        BassPerformanceNote(**_good_kw(articulation=bad))
    assert "articulation" in str(exc.value)


@pytest.mark.parametrize("start,end", [(0.5, 0.5), (0.6, 0.5), (1.0, 0.0)])
def test_start_must_be_strictly_less_than_end(start: float, end: float) -> None:
    with pytest.raises(ValueError) as exc:
        BassPerformanceNote(**_good_kw(start=start, end=end))
    msg = str(exc.value)
    assert "start" in msg and "end" in msg


@pytest.mark.parametrize("pitch", [0, 1, 60, 126, 127])
def test_pitch_accepts_full_midi_range(pitch: int) -> None:
    note = BassPerformanceNote(**_good_kw(pitch=pitch))
    assert note.pitch == pitch


@pytest.mark.parametrize("pitch", [-1, 128, 255])
def test_pitch_outside_range_rejected(pitch: int) -> None:
    with pytest.raises(ValueError) as exc:
        BassPerformanceNote(**_good_kw(pitch=pitch))
    assert "pitch" in str(exc.value)


@pytest.mark.parametrize("velocity", [1, 64, 127])
def test_velocity_accepts_valid_range(velocity: int) -> None:
    note = BassPerformanceNote(**_good_kw(velocity=velocity))
    assert note.velocity == velocity


@pytest.mark.parametrize("velocity", [0, -1, 128, 200])
def test_velocity_outside_range_rejected(velocity: int) -> None:
    with pytest.raises(ValueError) as exc:
        BassPerformanceNote(**_good_kw(velocity=velocity))
    assert "velocity" in str(exc.value)


@pytest.mark.parametrize("confidence", [None, 0.0, 0.25, 0.5, 1.0])
def test_confidence_accepts_none_or_zero_to_one(confidence: float | None) -> None:
    note = BassPerformanceNote(**_good_kw(confidence=confidence))
    assert note.confidence == confidence


@pytest.mark.parametrize("confidence", [-0.0001, -1.0, 1.0001, 2.0])
def test_confidence_outside_range_rejected(confidence: float) -> None:
    with pytest.raises(ValueError) as exc:
        BassPerformanceNote(**_good_kw(confidence=confidence))
    assert "confidence" in str(exc.value)


def test_conversion_preserves_core_fields_exactly() -> None:
    src = BassPerformanceNote(
        pitch=43,
        start=0.125,
        end=0.875,
        velocity=104,
        articulation="ghost",
        role="anchor",
        bar_index=2,
        slot_index=8,
        source="baseline",
        confidence=0.7,
    )
    note = performance_note_to_pretty_midi_note(src)
    assert isinstance(note, pretty_midi.Note)
    assert note.pitch == 43
    assert note.velocity == 104
    assert note.start == 0.125
    assert note.end == 0.875


@pytest.mark.parametrize("articulation", sorted(_LOCKED_VOCABULARY))
def test_conversion_ignores_articulation_in_step1(articulation: str) -> None:
    """Step 1 contract: every articulation yields a structurally identical Note."""
    base_kw = dict(pitch=40, start=0.0, end=0.5, velocity=90)
    src = BassPerformanceNote(**base_kw, articulation=articulation)
    out = performance_note_to_pretty_midi_note(src)
    assert (out.pitch, out.velocity, out.start, out.end) == (40, 90, 0.0, 0.5)


def test_dataclass_is_frozen_and_immutable() -> None:
    note = BassPerformanceNote(**_good_kw())
    with pytest.raises(dataclasses.FrozenInstanceError):
        note.pitch = 60  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        note.articulation = "ghost"  # type: ignore[misc]


def test_slots_prevents_arbitrary_attributes() -> None:
    note = BassPerformanceNote(**_good_kw())
    # frozen+slots: the frozen __setattr__ check fires first and raises
    # FrozenInstanceError; without slots Python would raise AttributeError;
    # in some interpreter versions the slots interaction surfaces as TypeError.
    with pytest.raises((AttributeError, dataclasses.FrozenInstanceError, TypeError)):
        note.unknown_field = 1  # type: ignore[attr-defined]


def test_equal_notes_compare_equal_and_hash_consistently() -> None:
    a = BassPerformanceNote(
        pitch=40, start=0.0, end=0.5, velocity=90,
        articulation="ghost", role="anchor", bar_index=1, slot_index=4,
        source="baseline", confidence=0.5,
    )
    b = BassPerformanceNote(
        pitch=40, start=0.0, end=0.5, velocity=90,
        articulation="ghost", role="anchor", bar_index=1, slot_index=4,
        source="baseline", confidence=0.5,
    )
    c = BassPerformanceNote(
        pitch=41, start=0.0, end=0.5, velocity=90,
        articulation="ghost", role="anchor", bar_index=1, slot_index=4,
        source="baseline", confidence=0.5,
    )
    assert a == b
    assert hash(a) == hash(b)
    assert a != c
    assert {a, b, c} == {a, c}
