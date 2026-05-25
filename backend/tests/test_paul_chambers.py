"""Tests for the Paul Chambers bass vocabulary module."""

from __future__ import annotations

import pytest

from app.services.bass_vocabulary.paul_chambers import (
    GROOVE_FEEL,
    PAUL_CHAMBERS_PROFILE,
    get_arco_phrase,
    get_chromatic_approaches,
    get_walking_cell,
)


_SUPPORTED_QUALITIES = ("maj7", "min7", "dom7", "dim", "half-dim")


# --- PAUL_CHAMBERS_PROFILE -------------------------------------------------


def test_profile_has_required_keys() -> None:
    for key in ("era", "feel", "tempo_range", "preferred_modes"):
        assert key in PAUL_CHAMBERS_PROFILE, f"missing key: {key}"


def test_profile_tempo_range_covers_ballads_to_fast_bop() -> None:
    lo, hi = PAUL_CHAMBERS_PROFILE["tempo_range"]  # type: ignore[index,misc]
    assert isinstance(lo, int) and isinstance(hi, int)
    assert lo < hi
    assert lo <= 70 <= hi  # ballad tempos
    assert lo <= 240 <= hi  # fast bop tempos


def test_profile_preferred_modes_includes_dorian() -> None:
    modes = PAUL_CHAMBERS_PROFILE["preferred_modes"]
    assert "dorian" in modes  # type: ignore[operator]


# --- GROOVE_FEEL ------------------------------------------------------------


def test_groove_feel_matches_required_shape() -> None:
    assert GROOVE_FEEL == {
        "placement": "behind",
        "swing_ratio": 0.62,
        "velocity_accent_beats": [1, 3],
    }


# --- get_chromatic_approaches ----------------------------------------------


def test_chromatic_approaches_identical_pitches_returns_empty() -> None:
    assert get_chromatic_approaches(60, 60) == []


def test_chromatic_approaches_adjacent_pitches_returns_empty() -> None:
    assert get_chromatic_approaches(60, 61) == []
    assert get_chromatic_approaches(61, 60) == []


def test_chromatic_approaches_short_gap_returns_one_tone_ascending() -> None:
    # C (60) up to D (62): one chromatic neighbour just below the target.
    result = get_chromatic_approaches(60, 62)
    assert result == [61]


def test_chromatic_approaches_short_gap_returns_one_tone_descending() -> None:
    # D (62) down to C (60): one chromatic neighbour just above the target.
    result = get_chromatic_approaches(62, 60)
    assert result == [61]


def test_chromatic_approaches_distance_three_returns_one_tone() -> None:
    # C (60) up to Eb (63): just one chromatic neighbour at 62.
    result = get_chromatic_approaches(60, 63)
    assert result == [62]


def test_chromatic_approaches_larger_gap_returns_two_tones() -> None:
    # C (60) up to G (67): two stepwise tones leading into the target.
    result = get_chromatic_approaches(60, 67)
    assert result == [65, 66]
    assert all(60 < n < 67 for n in result)


def test_chromatic_approaches_descending_larger_gap() -> None:
    # G (67) down to C (60): two passing tones on the descending side.
    result = get_chromatic_approaches(67, 60)
    assert result == [62, 61]
    assert all(60 < n < 67 for n in result)


def test_chromatic_approaches_always_returns_ints_in_safe_range() -> None:
    for root, target in [(28, 40), (40, 28), (52, 55), (60, 72)]:
        result = get_chromatic_approaches(root, target)
        assert all(isinstance(n, int) for n in result)
        assert 0 <= min(result + [root, target]) <= max(result + [root, target]) <= 127


# --- get_walking_cell ------------------------------------------------------


@pytest.mark.parametrize("quality", _SUPPORTED_QUALITIES)
@pytest.mark.parametrize("bar_position", [0, 1, 2, 3, 4, 7, 12])
def test_walking_cell_returns_four_midi_notes(quality: str, bar_position: int) -> None:
    notes = get_walking_cell(36, quality, bar_position)
    assert isinstance(notes, list)
    assert len(notes) == 4
    assert all(isinstance(n, int) for n in notes)
    # Notes should sit in a reasonable bass-walking window relative to root.
    assert all(36 <= n <= 36 + 13 for n in notes)


def test_walking_cell_beat_one_is_root() -> None:
    for quality in _SUPPORTED_QUALITIES:
        for bar_position in range(8):
            notes = get_walking_cell(40, quality, bar_position)
            assert notes[0] == 40, f"beat 1 not root for {quality} bar {bar_position}"


def test_walking_cell_min7_bar0_walks_chord_tones() -> None:
    # D-7 at MIDI 38: pure chord-tone walk on bar 0 -> D, F, A, C.
    notes = get_walking_cell(38, "min7", 0)
    assert notes == [38, 41, 45, 48]


def test_walking_cell_maj7_bar0_walks_chord_tones() -> None:
    # Cmaj7 at MIDI 36: root, M3, P5, M7 -> C, E, G, B.
    notes = get_walking_cell(36, "maj7", 0)
    assert notes == [36, 40, 43, 47]


def test_walking_cell_dom7_bar0_has_minor_seventh() -> None:
    # G7 at MIDI 43: root, M3, P5, m7 -> G, B, D, F.
    notes = get_walking_cell(43, "dom7", 0)
    assert notes == [43, 47, 50, 53]


def test_walking_cell_pattern_cycles_every_four_bars() -> None:
    a = get_walking_cell(36, "min7", 0)
    b = get_walking_cell(36, "min7", 4)
    c = get_walking_cell(36, "min7", 8)
    assert a == b == c


def test_walking_cell_unknown_quality_raises() -> None:
    with pytest.raises(ValueError):
        get_walking_cell(36, "sus4", 0)


# --- get_arco_phrase --------------------------------------------------------


@pytest.mark.parametrize("quality", _SUPPORTED_QUALITIES)
def test_arco_phrase_returns_pairs_of_int_and_float(quality: str) -> None:
    phrase = get_arco_phrase(40, quality, bars=4)
    assert isinstance(phrase, list)
    assert phrase, "phrase should not be empty"
    for note, duration in phrase:
        assert isinstance(note, int)
        assert isinstance(duration, float)
        assert duration > 0


@pytest.mark.parametrize("quality", _SUPPORTED_QUALITIES)
@pytest.mark.parametrize("bars", [1, 2, 3, 4, 8])
def test_arco_phrase_durations_sum_to_full_bars(quality: str, bars: int) -> None:
    phrase = get_arco_phrase(40, quality, bars)
    total = sum(d for _, d in phrase)
    assert total == pytest.approx(4.0 * bars)


def test_arco_phrase_starts_on_root() -> None:
    phrase = get_arco_phrase(40, "min7", bars=1)
    assert phrase[0][0] == 40
    assert phrase[0][1] == 4.0  # bar-0 anchor is a whole note


def test_arco_phrase_bars_clamped_to_at_least_one() -> None:
    for bars in (0, -1, -10):
        phrase = get_arco_phrase(40, "maj7", bars)
        assert sum(d for _, d in phrase) == pytest.approx(4.0)


def test_arco_phrase_min7_uses_minor_third_guide_tone() -> None:
    # Bar 1 (pos == 1) of D-7 (38): minor 3rd (38+3=41) then m7 (38+10=48).
    phrase = get_arco_phrase(38, "min7", bars=2)
    # phrase[0] is bar 0 (whole-note root), phrase[1..] is bar 1.
    bar1 = phrase[1:]
    assert bar1[0][0] == 41
    assert bar1[1][0] == 48


def test_arco_phrase_unknown_quality_raises() -> None:
    with pytest.raises(ValueError):
        get_arco_phrase(40, "sus2", bars=2)
