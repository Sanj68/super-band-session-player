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
