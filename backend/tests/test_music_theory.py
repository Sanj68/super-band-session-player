from __future__ import annotations

import pytest

from app.utils import music_theory as mt


def test_key_root_pc_alias_and_invalid() -> None:
    assert mt.key_root_pc("C#") == 1
    assert mt.key_root_pc("Db") == 1
    with pytest.raises(ValueError):
        mt.key_root_pc("H")


def test_describe_scale_alias_and_fallback() -> None:
    assert mt.describe_scale("minor") == "natural_minor"
    assert mt.describe_scale("aeolian") == "natural_minor"
    assert mt.describe_scale("major") == "major"
    assert mt.describe_scale("something_unknown") == "major"


def test_chord_tones_midi_are_ascending() -> None:
    triad = mt.chord_tones_midi("C", "major", degree_one_indexed=1, octave=2, seventh=False)
    seventh = mt.chord_tones_midi("C", "major", degree_one_indexed=1, octave=2, seventh=True)
    assert len(triad) == 3
    assert len(seventh) == 4
    assert triad == sorted(triad)
    assert seventh == sorted(seventh)
    assert triad[0] < triad[1] < triad[2]
    assert seventh[0] < seventh[1] < seventh[2] < seventh[3]


def test_progression_degrees_pattern_by_scale_size() -> None:
    assert mt.progression_degrees_for_bars(8, "major") == [1, 4, 5, 1, 1, 4, 5, 1]
    assert mt.progression_degrees_for_bars(8, "blues") == [1, 3, 4, 1, 1, 3, 4, 1]


@pytest.mark.parametrize(
    ("symbol", "root_pc", "quality", "tone_pcs"),
    [
        ("C", 0, "major", (0, 4, 7)),
        ("Cmaj", 0, "major", (0, 4, 7)),
        ("Cm", 0, "minor", (0, 3, 7)),
        ("Cmin", 0, "minor", (0, 3, 7)),
        ("C7", 0, "dominant7", (0, 4, 7, 10)),
        ("Cmaj7", 0, "major7", (0, 4, 7, 11)),
        ("CM7", 0, "major7", (0, 4, 7, 11)),
        ("Cm7", 0, "minor7", (0, 3, 7, 10)),
        ("Cmin7", 0, "minor7", (0, 3, 7, 10)),
        ("Cm7b5", 0, "half_diminished", (0, 3, 6, 10)),
        ("Cdim", 0, "diminished", (0, 3, 6)),
        ("Csus2", 0, "sus2", (0, 2, 7)),
        ("Csus4", 0, "sus4", (0, 5, 7)),
        ("Cadd9", 0, "major_add9", (0, 4, 7, 2)),
        ("Bbmaj7", 10, "major7", (10, 2, 5, 9)),
    ],
)
def test_parse_chord_symbol(symbol: str, root_pc: int, quality: str, tone_pcs: tuple[int, ...]) -> None:
    chord = mt.parse_chord_symbol(symbol)
    assert chord.root_pc == root_pc
    assert chord.quality == quality
    assert chord.tone_pcs == tone_pcs


def test_parse_chord_symbol_rejects_unknown_quality() -> None:
    with pytest.raises(ValueError):
        mt.parse_chord_symbol("C13")


def test_progression_chords_for_bars_loops_supplied_chords() -> None:
    chords = mt.progression_chords_for_bars(["Am7", "D7", "Gmaj7", "Cmaj7"], 6)
    assert [c.root for c in chords] == ["A", "D", "G", "C", "A", "D"]
    assert [c.quality for c in chords[:4]] == ["minor7", "dominant7", "major7", "major7"]
    assert mt.progression_chords_for_bars([], 4) == []
