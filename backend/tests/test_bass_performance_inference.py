"""v0.5 Step 3: metadata-only bass articulation inference."""

from __future__ import annotations

import dataclasses

from app.services.bass_performance import BassPerformanceNote, _VALID_ARTICULATIONS, infer_bass_articulations


def _note(**overrides: object) -> BassPerformanceNote:
    kw: dict[str, object] = dict(
        pitch=40,
        start=0.0,
        end=0.25,
        velocity=86,
        articulation="normal",
        role="answer",
        bar_index=0,
        slot_index=2,
        source="baseline",
        confidence=0.5,
    )
    kw.update(overrides)
    return BassPerformanceNote(**kw)


def test_constructed_ghost_note_classifies_as_ghost() -> None:
    inferred = infer_bass_articulations(
        (_note(start=0.25, end=0.35, velocity=48, slot_index=3),),
        tempo=100,
        style="supportive",
        source="baseline",
    )

    assert inferred[0].articulation == "ghost"


def test_constructed_grace_note_classifies_as_grace() -> None:
    grace = _note(pitch=39, start=0.18, end=0.23, velocity=50, slot_index=1)
    target = _note(pitch=40, start=0.27, end=0.7, velocity=88, slot_index=2)

    inferred = infer_bass_articulations((grace, target), tempo=100)

    assert inferred[0].articulation == "grace"
    assert inferred[1].articulation == "normal"


def test_grace_wins_over_ghost_when_both_match() -> None:
    grace_like_ghost = _note(pitch=39, start=0.18, end=0.23, velocity=42, slot_index=1)
    target = _note(pitch=40, start=0.27, end=0.7, velocity=88, slot_index=2)

    inferred = infer_bass_articulations((grace_like_ghost, target), tempo=100)

    assert inferred[0].articulation == "grace"


def test_normal_anchor_remains_normal() -> None:
    anchor = _note(pitch=36, start=0.0, end=0.7, velocity=96, role="anchor", slot_index=0)

    inferred = infer_bass_articulations((anchor,), tempo=100)

    assert inferred[0].articulation == "normal"


def test_inference_preserves_all_fields_except_articulation() -> None:
    src = _note(
        pitch=41,
        start=0.25,
        end=0.34,
        velocity=49,
        role="push",
        bar_index=3,
        slot_index=5,
        source="phrase_v2",
        confidence=0.25,
    )

    inferred = infer_bass_articulations((src,), tempo=100)[0]

    assert dataclasses.replace(inferred, articulation=src.articulation) == src
    assert inferred.articulation == "ghost"


def test_all_output_articulations_are_locked_vocabulary() -> None:
    notes = (
        _note(start=0.0, end=0.08, velocity=42, slot_index=1),
        _note(pitch=39, start=0.18, end=0.23, velocity=50, slot_index=2),
        _note(pitch=40, start=0.27, end=0.7, velocity=88, slot_index=3),
    )

    inferred = infer_bass_articulations(notes, tempo=100)

    assert {n.articulation for n in inferred}.issubset(_VALID_ARTICULATIONS)
