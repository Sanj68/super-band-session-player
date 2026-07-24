"""Neutral bass-family capability profile coverage."""

from __future__ import annotations

import io

import pretty_midi

from app.services import generator
from app.services.bass_instrument_profiles import (
    bass_instrument_profile,
    constrain_instrument_controls,
    normalize_bass_instrument_family,
    public_bass_instrument_profiles,
)
from app.services.bass_performance import BassPerformanceNote, infer_bass_articulations
from app.services.bass_phrase_engine_v2 import bass_midi_program


def _note(**overrides: object) -> BassPerformanceNote:
    values: dict[str, object] = {
        "pitch": 40,
        "start": 0.0,
        "end": 0.1,
        "velocity": 42,
        "role": "answer",
        "bar_index": 0,
        "slot_index": 3,
        "source": "phrase_v2",
    }
    values.update(overrides)
    return BassPerformanceNote(**values)


def test_public_profiles_include_upright_as_its_own_family() -> None:
    profiles = public_bass_instrument_profiles()
    assert [profile.id for profile in profiles] == [
        "finger_bass",
        "fretless_bass",
        "upright_bass",
        "sub_bass",
    ]
    assert bass_instrument_profile("double_bass").id == "upright_bass"
    assert normalize_bass_instrument_family("synth_bass") == "sub_bass"


def test_sub_profile_constrains_density_and_expression() -> None:
    density, expression = constrain_instrument_controls(
        "sub_bass",
        density_bias=0.8,
        expression_amount=1.0,
    )
    assert density == -0.35
    assert expression == 0.6


def test_fretless_and_sub_suppress_incredible_muted_articulations() -> None:
    note = _note()
    fingered = infer_bass_articulations(
        (note,),
        tempo=120,
        style="rhythmic",
        expression_amount=1.0,
        instrument_family="finger_bass",
    )
    fretless = infer_bass_articulations(
        (note,),
        tempo=120,
        style="rhythmic",
        expression_amount=1.0,
        instrument_family="fretless_bass",
    )
    sub = infer_bass_articulations(
        (note,),
        tempo=120,
        style="rhythmic",
        expression_amount=1.0,
        instrument_family="sub_bass",
    )

    assert fingered[0].articulation == "ghost"
    assert fretless[0].articulation == "normal"
    assert sub[0].articulation == "normal"


def test_family_programs_use_closest_general_midi_bass() -> None:
    assert bass_midi_program("upright_bass", "supportive") == 32
    assert bass_midi_program("fretless_bass", "supportive") == 35
    assert bass_midi_program("sub_bass", "supportive") == 38


def test_all_core_families_generate_safe_complete_sixteen_bar_midi() -> None:
    tempo = 88
    spb = 60.0 / tempo
    loop_end = 64.0 * spb
    note_counts: dict[str, int] = {}

    for family in ("finger_bass", "fretless_bass", "upright_bass", "sub_bass"):
        raw, _preview = generator.generate_bass(
            tempo=tempo,
            bar_count=16,
            key="D",
            scale="natural_minor",
            bass_style="supportive",
            bass_instrument=family,
            bass_engine="phrase_v2",
            chord_progression=["Bb", "C", "D", "Gm"],
            density_bias=0.8,
            expression_amount=0.8,
            seed=240726,
        )
        pm = pretty_midi.PrettyMIDI(io.BytesIO(raw))
        notes = sorted(
            (note for instrument in pm.instruments for note in instrument.notes),
            key=lambda note: (note.start, note.pitch, note.end),
        )
        note_counts[family] = len(notes)
        assert notes
        assert max(note.end for note in notes) <= loop_end + 0.005
        assert min(note.end - note.start for note in notes) >= 0.025

        by_pitch: dict[int, list[pretty_midi.Note]] = {}
        for note in notes:
            by_pitch.setdefault(int(note.pitch), []).append(note)
        for same_pitch in by_pitch.values():
            same_pitch.sort(key=lambda note: note.start)
            assert all(
                left.end <= right.start + 0.005
                for left, right in zip(same_pitch, same_pitch[1:])
            )

    assert note_counts["sub_bass"] < note_counts["finger_bass"]
