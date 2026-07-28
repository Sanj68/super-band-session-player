"""Controlled roles must edit — never replace — Phrase-v2's Style grammar."""

from __future__ import annotations

import pytest

from app.services.bass_candidate_roles import ROLE_ORDER
from app.services.bass_performance import BassPerformanceNote
from app.services.bass_phrase_engine_v2 import generate_bass_phrase_v2
from app.utils import music_theory as mt


_TEMPO = 116
_CHORDS = ("F", "Gm", "Dm", "Bb")
_SEED = 424_245


def _fusion(
    candidate_role: str | None,
) -> tuple[BassPerformanceNote, ...]:
    _midi, preview, notes = generate_bass_phrase_v2(
        tempo=_TEMPO,
        bar_count=8,
        key="D",
        scale="natural_minor",
        bass_style="fusion",
        bass_instrument="finger_bass",
        chord_progression=list(_CHORDS),
        seed=_SEED,
        density_bias=1.0,
        expression_amount=1.0,
        bass_articulation_focus="clean",
        candidate_role=candidate_role,
        return_performance_notes=True,
    )
    assert "fusion" in preview
    assert notes
    return notes


def _onset_signature(
    notes: tuple[BassPerformanceNote, ...],
) -> tuple[tuple[int, int], ...]:
    return tuple(
        (int(note.bar_index), int(note.slot_index))
        for note in notes
        if note.bar_index is not None and note.slot_index is not None
    )


def _pitch_signature(
    notes: tuple[BassPerformanceNote, ...],
) -> tuple[tuple[int, int, int], ...]:
    return tuple(
        (int(note.bar_index), int(note.slot_index), int(note.pitch))
        for note in notes
        if note.bar_index is not None and note.slot_index is not None
    )


def _performance_signature(
    notes: tuple[BassPerformanceNote, ...],
) -> tuple[tuple[int, int, int, int, float, float], ...]:
    return tuple(
        (
            int(note.bar_index),
            int(note.slot_index),
            int(note.pitch),
            int(note.velocity),
            round(float(note.start), 6),
            round(float(note.end) - float(note.start), 6),
        )
        for note in notes
        if note.bar_index is not None and note.slot_index is not None
    )


def _note_at(
    notes: tuple[BassPerformanceNote, ...],
    *,
    bar: int,
    slot: int,
) -> BassPerformanceNote:
    return next(
        note
        for note in notes
        if note.bar_index == bar and note.slot_index == slot
    )


def test_high_activity_fusion_roles_are_four_purposeful_style_edits() -> None:
    """The role comparison stays recognisably Fusion in the current Dm song."""

    base = _fusion(None)
    by_role = {role: _fusion(role) for role in ROLE_ORDER}

    # Pocket and Rhythmic alter the busy onset cell. Harmonic keeps that cell
    # for chord-tone voice-leading; Performance keeps it for feel changes.
    assert len({_onset_signature(notes) for notes in by_role.values()}) >= 3
    assert len(
        {_performance_signature(notes) for notes in by_role.values()}
    ) == 4
    assert len(by_role["pocket_keeper"]) < len(
        by_role["harmonic_alternative"]
    )
    assert _onset_signature(by_role["rhythmic_alternative"]) != (
        _onset_signature(by_role["harmonic_alternative"])
    )

    # Performance edits timing, length, and accents while preserving the exact
    # Fusion pitch architecture chosen by the style engine.
    assert _pitch_signature(by_role["performance_alternative"]) == (
        _pitch_signature(base)
    )

    chord_plan = mt.progression_chords_for_bars(_CHORDS, 8)
    scale_pcs = {
        (mt.key_root_pc("D") + interval) % 12
        for interval in mt.scale_intervals("natural_minor")
    }
    for role, notes in by_role.items():
        # Every role retains Fusion's offbeat-led motion and register play.
        offbeat_share = sum(
            int(note.slot_index) % 4 != 0 for note in notes
        ) / len(notes)
        assert offbeat_share >= 0.60, role
        assert max(note.pitch for note in notes) - min(
            note.pitch for note in notes
        ) >= 12, role
        assert all(30 <= note.pitch <= 62 for note in notes), role

        # A confirmed chart is authoritative on structural attacks. The final
        # off-grid hit of an answer bar may instead be one scale-safe approach
        # that resolves by step into the following chord root.
        for note in notes:
            assert note.bar_index is not None
            assert note.slot_index is not None
            bar_index = int(note.bar_index)
            allowed = {
                int(pc) % 12
                for pc in chord_plan[bar_index].tone_pcs
            }
            if note.pitch % 12 in allowed:
                continue

            bar_notes = [
                candidate
                for candidate in notes
                if candidate.bar_index == bar_index
                and candidate.slot_index is not None
            ]
            next_bar_notes = [
                candidate
                for candidate in notes
                if candidate.bar_index == bar_index + 1
                and candidate.slot_index is not None
            ]
            resolving_note = min(
                next_bar_notes,
                key=lambda candidate: int(candidate.slot_index),
            )
            next_root_pc = int(chord_plan[bar_index + 1].root_pc) % 12
            assert note.pitch % 12 in scale_pcs, role
            assert bar_index % 4 == 1, role
            assert int(note.slot_index) == max(
                int(candidate.slot_index) for candidate in bar_notes
            ), role
            assert int(note.slot_index) >= 10, role
            assert int(note.slot_index) % 4 != 0, role
            assert int(resolving_note.slot_index) == 0, role
            assert resolving_note.pitch % 12 == next_root_pc, role
            assert abs(resolving_note.pitch - note.pitch) <= 2, role

        root_octave_bars = 0
        for bar, chord in enumerate(chord_plan):
            root_pitches = {
                note.pitch
                for note in notes
                if note.bar_index == bar
                and note.pitch % 12 == int(chord.root_pc) % 12
            }
            root_octave_bars += any(
                pitch + 12 in root_pitches for pitch in root_pitches
            )
        assert root_octave_bars >= 1, role

    # Harmonic changes safe chord colour; Rhythmic remains root-led without
    # collapsing the whole Fusion line to roots.
    non_roots: dict[str, int] = {}
    for role, notes in by_role.items():
        non_roots[role] = sum(
            note.pitch % 12
            != int(chord_plan[int(note.bar_index)].root_pc) % 12
            for note in notes
            if note.bar_index is not None
        )
    assert non_roots["harmonic_alternative"] > non_roots["pocket_keeper"]
    assert non_roots["rhythmic_alternative"] >= (
        len(by_role["rhythmic_alternative"]) // 4
    )


def test_fusion_role_durations_and_accents_modify_fusion_baselines() -> None:
    """Role shaping multiplies Fusion's short attacks instead of generic ones."""

    base = _fusion(None)
    by_role = {role: _fusion(role) for role in ROLE_ORDER}
    sixteenth = (60.0 / _TEMPO) / 4.0

    expected_strong_duration = {
        "pocket_keeper": 1.05 * 1.18,
        "rhythmic_alternative": 1.05 * 0.88,
        "harmonic_alternative": 1.05,
        "performance_alternative": 1.05 * 1.24,
    }
    for role, multiplier in expected_strong_duration.items():
        note = _note_at(by_role[role], bar=0, slot=0)
        assert note.end - note.start == pytest.approx(
            sixteenth * multiplier
        )

    # The first Fusion accent is 99 before seeded humanisation. Candidate
    # roles retain that style accent; Performance adds its explicit +5.
    base_one = _note_at(base, bar=0, slot=0)
    for role in (
        "pocket_keeper",
        "rhythmic_alternative",
        "harmonic_alternative",
    ):
        assert _note_at(by_role[role], bar=0, slot=0).velocity == (
            base_one.velocity
        )
    assert _note_at(
        by_role["performance_alternative"],
        bar=0,
        slot=0,
    ).velocity == min(112, base_one.velocity + 5)

    expected_offbeat_duration = {
        "pocket_keeper": 0.68,
        "rhythmic_alternative": 0.68 * 0.72,
        "harmonic_alternative": 0.68,
        "performance_alternative": 0.68 * 0.56,
    }
    for role, multiplier in expected_offbeat_duration.items():
        offbeat = next(
            note
            for note in by_role[role]
            if note.bar_index == 0
            and note.slot_index is not None
            and int(note.slot_index) % 4 != 0
            and int(note.slot_index) != 15
        )
        assert offbeat.end - offbeat.start == pytest.approx(
            sixteenth * multiplier
        )
