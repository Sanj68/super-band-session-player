"""Listening contracts for bass Style, Activity, and regeneration.

These tests deliberately measure musical event structure rather than MIDI
bytes, preview copy, program changes, velocity jitter, or articulation labels.
The producer-facing controls should change the phrase a listener hears, not
merely its metadata or the number of otherwise identical attacks.
"""

from __future__ import annotations

from statistics import median

import pytest

from app.services import generator
from app.services.bass_performance import BassPerformanceNote


_ENGINES = ("baseline", "phrase_v2")
_STYLES = ("supportive", "melodic", "rhythmic", "slap", "fusion")
_SEEDS = tuple(range(8))
_REGENERATION_SEEDS = tuple(range(40))
_CHARACTER_SEEDS = tuple(range(40))


def _performance(
    *,
    engine: str,
    style: str,
    seed: int,
    activity: float = 0.0,
    character: float = 0.5,
) -> tuple[BassPerformanceNote, ...]:
    _midi, _preview, notes = generator.generate_bass(
        tempo=96,
        bar_count=8,
        key="D",
        scale="natural_minor",
        bass_style=style,
        bass_instrument="finger_bass",
        bass_engine=engine,
        seed=seed,
        density_bias=activity,
        expression_amount=character,
        bass_articulation_focus="clean",
        return_performance_notes=True,
    )
    assert all(note.bar_index is not None for note in notes)
    assert all(note.slot_index is not None for note in notes)
    return notes


def _onsets(notes: tuple[BassPerformanceNote, ...]) -> frozenset[tuple[int, int]]:
    return frozenset(
        (int(note.bar_index), int(note.slot_index))
        for note in notes
        if note.bar_index is not None and note.slot_index is not None
    )


def _onset_signature(
    notes: tuple[BassPerformanceNote, ...],
) -> tuple[tuple[int, int], ...]:
    return tuple(sorted(_onsets(notes)))


def _jaccard_distance(
    left: frozenset[tuple[int, int]],
    right: frozenset[tuple[int, int]],
) -> float:
    union = left | right
    if not union:
        return 0.0
    return 1.0 - (len(left & right) / len(union))


def _offbeat_share(notes: tuple[BassPerformanceNote, ...]) -> float:
    if not notes:
        return 0.0
    offbeats = sum(
        1
        for note in notes
        if note.slot_index is not None and int(note.slot_index) % 4 != 0
    )
    return offbeats / len(notes)


def _has_octave_register_pair(notes: tuple[BassPerformanceNote, ...]) -> bool:
    pitches = {int(note.pitch) for note in notes}
    return any((pitch + 12) in pitches for pitch in pitches)


def _pitch_architecture(
    notes: tuple[BassPerformanceNote, ...],
) -> dict[tuple[int, int], tuple[int, ...]]:
    grouped: dict[tuple[int, int], list[int]] = {}
    for note in notes:
        if note.bar_index is None or note.slot_index is None:
            continue
        key = (int(note.bar_index), int(note.slot_index))
        grouped.setdefault(key, []).append(int(note.pitch))
    return {
        key: tuple(sorted(pitches))
        for key, pitches in grouped.items()
    }


def _pitch_range(notes: tuple[BassPerformanceNote, ...]) -> int:
    pitches = [int(note.pitch) for note in notes]
    return max(pitches) - min(pitches) if pitches else 0


def _mean_abs_melodic_interval(
    notes: tuple[BassPerformanceNote, ...],
) -> float:
    ordered = sorted(
        notes,
        key=lambda note: (
            int(note.bar_index) if note.bar_index is not None else -1,
            int(note.slot_index) if note.slot_index is not None else -1,
            int(note.pitch),
        ),
    )
    intervals = [
        abs(int(right.pitch) - int(left.pitch))
        for left, right in zip(ordered, ordered[1:], strict=False)
    ]
    return sum(intervals) / len(intervals) if intervals else 0.0


@pytest.mark.parametrize("engine", _ENGINES)
@pytest.mark.parametrize("style", _STYLES[1:])
def test_style_recomposes_rhythmic_grammar_instead_of_only_changing_metadata(
    engine: str,
    style: str,
) -> None:
    """Every named style must move a material share of Supportive's onsets."""

    distances = []
    for seed in _SEEDS:
        supportive = _onsets(
            _performance(engine=engine, style="supportive", seed=seed)
        )
        styled = _onsets(_performance(engine=engine, style=style, seed=seed))
        distances.append(_jaccard_distance(supportive, styled))

    assert median(distances) >= 0.30, (
        f"{engine}/{style} retained Supportive's phrase skeleton; "
        f"median onset distance was {median(distances):.3f}"
    )


@pytest.mark.parametrize("engine", _ENGINES)
def test_fusion_has_syncopated_funk_motion_and_octave_register_play(
    engine: str,
) -> None:
    """Fusion's neutral setting should already read as a bass-lead vocabulary."""

    fusion_runs = [
        _performance(engine=engine, style="fusion", seed=seed)
        for seed in _SEEDS
    ]
    supportive_runs = [
        _performance(engine=engine, style="supportive", seed=seed)
        for seed in _SEEDS
    ]
    fusion_syncopation = [_offbeat_share(notes) for notes in fusion_runs]
    supportive_syncopation = [_offbeat_share(notes) for notes in supportive_runs]
    octave_runs = sum(_has_octave_register_pair(notes) for notes in fusion_runs)

    assert median(fusion_syncopation) >= 0.45, (
        "Fusion needs offbeat-led slap/funk motion at neutral Activity; "
        f"median offbeat share was {median(fusion_syncopation):.3f}"
    )
    assert median(fusion_syncopation) - median(supportive_syncopation) >= 0.15, (
        "Fusion was not materially more syncopated than Supportive"
    )
    assert octave_runs >= 6, (
        "Fusion needs reliable root/octave register play; "
        f"only {octave_runs}/{len(_SEEDS)} regenerated phrases contained it"
    )


@pytest.mark.parametrize("engine", _ENGINES)
def test_fusion_regeneration_explores_distinct_phrase_motifs(engine: str) -> None:
    """Regenerate should explore the style, not replay one fixed slot grid."""

    signatures = {
        _onset_signature(
            _performance(engine=engine, style="fusion", seed=seed)
        )
        for seed in _REGENERATION_SEEDS
    }
    assert len(signatures) >= 24, (
        f"{engine}/fusion produced only {len(signatures)} distinct rhythmic "
        f"motif(s) across {len(_REGENERATION_SEEDS)} regenerations"
    )


@pytest.mark.parametrize("engine", _ENGINES)
def test_character_changes_fusion_pitch_architecture_not_only_rendering(
    engine: str,
) -> None:
    """Bold Character must widen and reshape Fusion's composed pitch contour."""

    repitched_shares: list[float] = []
    restrained_ranges: list[int] = []
    bold_ranges: list[int] = []
    restrained_intervals: list[float] = []
    bold_intervals: list[float] = []

    for seed in _CHARACTER_SEEDS:
        restrained = _performance(
            engine=engine,
            style="fusion",
            seed=seed,
            character=0.0,
        )
        bold = _performance(
            engine=engine,
            style="fusion",
            seed=seed,
            character=1.0,
        )
        restrained_pitch = _pitch_architecture(restrained)
        bold_pitch = _pitch_architecture(bold)
        shared_onsets = restrained_pitch.keys() & bold_pitch.keys()
        assert shared_onsets
        repitched_shares.append(
            sum(
                restrained_pitch[onset] != bold_pitch[onset]
                for onset in shared_onsets
            )
            / len(shared_onsets)
        )
        restrained_ranges.append(_pitch_range(restrained))
        bold_ranges.append(_pitch_range(bold))
        restrained_intervals.append(_mean_abs_melodic_interval(restrained))
        bold_intervals.append(_mean_abs_melodic_interval(bold))

    assert median(repitched_shares) >= 0.25, (
        f"{engine}/fusion Character repitched only "
        f"{median(repitched_shares):.1%} of shared attacks"
    )
    assert median(bold_ranges) >= median(restrained_ranges) + 3, (
        f"{engine}/fusion bold Character did not materially widen the register "
        f"({median(restrained_ranges):.1f} -> {median(bold_ranges):.1f} semitones)"
    )
    assert median(bold_intervals) >= median(restrained_intervals) + 0.5, (
        f"{engine}/fusion bold Character did not materially reshape the contour "
        f"({median(restrained_intervals):.2f} -> "
        f"{median(bold_intervals):.2f} mean semitones)"
    )
    if engine == "phrase_v2":
        assert median(bold_intervals) <= 5.0, (
            "Phrase-v2 Bold Character became disconnected chord-tone "
            f"pinball ({median(bold_intervals):.2f} mean semitones)"
        )


@pytest.mark.parametrize("engine", _ENGINES)
@pytest.mark.parametrize("style", _STYLES)
def test_busy_activity_recomposes_the_motif_instead_of_only_inserting_hits(
    engine: str,
    style: str,
) -> None:
    """Busy Activity must replace/reposition some neutral attacks as it develops."""

    retired_neutral_shares: list[float] = []
    for seed in _SEEDS:
        neutral = _onsets(
            _performance(
                engine=engine,
                style=style,
                seed=seed,
                activity=0.0,
            )
        )
        busy = _onsets(
            _performance(
                engine=engine,
                style=style,
                seed=seed,
                activity=1.0,
            )
        )
        retired_neutral_shares.append(len(neutral - busy) / max(1, len(neutral)))

    assert median(retired_neutral_shares) >= 0.10, (
        f"{engine}/{style} treated Activity as note insertion over the same "
        "phrase; no material share of neutral onsets was recomposed"
    )


def test_phrase_v2_fusion_activity_has_five_distinct_musical_gears() -> None:
    """Every producer-facing Activity detent must change the written phrase."""

    counts: list[int] = []
    signatures: list[tuple[tuple[int, int], ...]] = []
    for activity in (-1.0, -0.5, 0.0, 0.5, 1.0):
        notes = _performance(
            engine="phrase_v2",
            style="fusion",
            seed=424_245,
            activity=activity,
            character=1.0,
        )
        counts.append(len(notes))
        signatures.append(_onset_signature(notes))

    assert counts == sorted(counts)
    assert all(
        right > left
        for left, right in zip(counts, counts[1:], strict=False)
    )
    assert len(set(signatures)) == 5


def test_phrase_v2_fusion_character_has_five_distinct_contour_gears() -> None:
    """Every producer-facing Character detent must revoice the same rhythm."""

    onsets: list[tuple[tuple[int, int], ...]] = []
    pitches: list[tuple[tuple[tuple[int, int], tuple[int, ...]], ...]] = []
    for character in (0.0, 0.25, 0.5, 0.75, 1.0):
        notes = _performance(
            engine="phrase_v2",
            style="fusion",
            seed=424_245,
            activity=0.5,
            character=character,
        )
        onsets.append(_onset_signature(notes))
        pitches.append(tuple(sorted(_pitch_architecture(notes).items())))

    assert len(set(onsets)) == 1
    assert len(set(pitches)) == 5
