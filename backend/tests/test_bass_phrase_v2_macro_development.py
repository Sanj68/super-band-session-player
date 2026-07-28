"""Behavioral contracts for Phrase-v2's large-scale musical development.

These tests intentionally avoid pinning exact MIDI notes.  They measure the
things a listener should hear before articulation detail is considered:
repeatability, harmonic safety, style-specific rhythm, four-bar shape, fresh
macro choices on Regenerate, and orthogonal Activity/Character controls.
"""

from __future__ import annotations

from dataclasses import replace
import io
from itertools import combinations
import math
import random
from statistics import mean, median, pstdev

import pretty_midi
import pytest

from app.services.bass_performance import BassPerformanceNote
from app.services import bass_phrase_engine_v2 as phrase_v2
from app.services.bass_phrase_engine_v2 import generate_bass_phrase_v2
from app.services.session_context import SessionAnchorContext
from app.utils import music_theory as mt


_STYLES = ("supportive", "melodic", "rhythmic", "slap", "fusion")
_CHORDS = ("F", "Gm", "Dm", "Bb")
_SEEDS = tuple(range(8))
_FUSION_BENCHMARK_SEEDS = tuple(range(2001, 2013))
_BEATS_PER_BAR = 4
_SLOTS_PER_BEAT = 4
_SLOTS_PER_BAR = _BEATS_PER_BAR * _SLOTS_PER_BEAT


def _phrase(
    *,
    style: str,
    seed: int,
    activity: float = 0.5,
    character: float = 0.75,
) -> tuple[BassPerformanceNote, ...]:
    _midi, _preview, notes = generate_bass_phrase_v2(
        tempo=116,
        bar_count=16,
        key="D",
        scale="natural_minor",
        bass_style=style,
        bass_instrument="finger_bass",
        chord_progression=list(_CHORDS),
        seed=seed,
        density_bias=activity,
        expression_amount=character,
        bass_articulation_focus="clean",
        return_performance_notes=True,
    )
    assert notes
    assert all(note.bar_index is not None for note in notes)
    assert all(note.slot_index is not None for note in notes)
    return notes


def _onsets(
    notes: tuple[BassPerformanceNote, ...],
) -> frozenset[tuple[int, int]]:
    return frozenset(
        (int(note.bar_index), int(note.slot_index))
        for note in notes
        if note.bar_index is not None and note.slot_index is not None
    )


def _onset_signature(
    notes: tuple[BassPerformanceNote, ...],
) -> tuple[tuple[int, int], ...]:
    return tuple(sorted(_onsets(notes)))


def _pitch_architecture(
    notes: tuple[BassPerformanceNote, ...],
) -> tuple[tuple[int, int, int], ...]:
    return tuple(
        sorted(
            (
                int(note.bar_index),
                int(note.slot_index),
                int(note.pitch),
            )
            for note in notes
            if note.bar_index is not None and note.slot_index is not None
        )
    )


def _normalized_arc_onsets(
    notes: tuple[BassPerformanceNote, ...],
    arc_index: int,
) -> frozenset[tuple[int, int]]:
    """Return one four-bar phrase with its absolute arc number removed."""

    return frozenset(
        (int(note.bar_index) % 4, int(note.slot_index))
        for note in notes
        if note.bar_index is not None
        and note.slot_index is not None
        and int(note.bar_index) // 4 == arc_index
    )


def _jaccard_distance(
    left: frozenset[tuple[int, int]],
    right: frozenset[tuple[int, int]],
) -> float:
    union = left | right
    if not union:
        return 0.0
    return 1.0 - (len(left & right) / len(union))


def _jaccard_similarity(
    left: frozenset[object],
    right: frozenset[object],
) -> float:
    union = left | right
    assert union, "macro similarity is undefined for two empty phrases"
    return len(left & right) / len(union)


def _full_rhythm_tokens(
    notes: tuple[BassPerformanceNote, ...],
) -> frozenset[int]:
    return frozenset(
        int(note.bar_index) * _SLOTS_PER_BAR + int(note.slot_index)
        for note in notes
        if note.bar_index is not None and note.slot_index is not None
    )


def _beat_skeleton_tokens(
    notes: tuple[BassPerformanceNote, ...],
    *,
    start_bar: int = 0,
    bar_count: int = 16,
) -> frozenset[tuple[int, int]]:
    """Keep each beat's first pitch, expressed relative to its chord root."""

    chord_plan = mt.progression_chords_for_bars(_CHORDS, 16)
    first_by_beat: dict[int, BassPerformanceNote] = {}
    for note in sorted(
        notes,
        key=lambda item: (
            int(item.bar_index),
            int(item.slot_index),
            float(item.start),
            int(item.pitch),
        ),
    ):
        bar = int(note.bar_index)
        if not start_bar <= bar < start_bar + bar_count:
            continue
        beat = (bar - start_bar) * _BEATS_PER_BAR + (
            int(note.slot_index) // _SLOTS_PER_BEAT
        )
        first_by_beat.setdefault(beat, note)

    return frozenset(
        (
            beat,
            (
                int(note.pitch) % 12
                - int(chord_plan[int(note.bar_index)].root_pc)
            )
            % 12,
        )
        for beat, note in first_by_beat.items()
    )


def _linear_percentile(values: list[float], percent: float) -> float:
    """Return the deterministic NumPy-style linear percentile."""

    assert values
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


@pytest.fixture(scope="module")
def fusion_benchmark_batch(
) -> dict[int, tuple[BassPerformanceNote, ...]]:
    """The fixed clean-MIDI acceptance batch used by the macro evidence gate."""

    return {
        seed: _phrase(
            style="fusion",
            seed=seed,
            activity=0.5,
            character=1.0,
        )
        for seed in _FUSION_BENCHMARK_SEEDS
    }


def _chord_relative_pitch_sentence(
    notes: tuple[BassPerformanceNote, ...],
) -> tuple[tuple[tuple[int, int], ...], ...]:
    """Encode contour independently from onset placement and rendering."""

    chord_plan = mt.progression_chords_for_bars(_CHORDS, 16)
    sentence: list[tuple[tuple[int, int], ...]] = []
    for bar in range(16):
        root_pc = int(chord_plan[bar].root_pc)
        bar_notes = sorted(
            (
                note
                for note in notes
                if int(note.bar_index) == bar
            ),
            key=lambda note: int(note.slot_index),
        )
        sentence.append(
            tuple(
                (
                    (int(note.pitch) % 12 - root_pc) % 12,
                    int(note.pitch) // 12,
                )
                for note in bar_notes
            )
        )
    return tuple(sentence)


def test_macro_plan_is_prefix_stable_and_has_explicit_form() -> None:
    four = phrase_v2._build_phrase_development_plan(
        bar_count=4,
        style="fusion",
        seed=27,
        rng=random.Random(27),
        density_bias=0.5,
    )
    eight = phrase_v2._build_phrase_development_plan(
        bar_count=8,
        style="fusion",
        seed=27,
        rng=random.Random(27),
        density_bias=0.5,
    )

    assert four == eight[:4]
    assert [bar.role for bar in eight] == [
        "anchor",
        "answer",
        "push",
        "release",
    ] * 2
    assert [bar.arc_role for bar in eight] == [
        "statement",
    ] * 4 + ["variation"] * 4


def test_fusion_macro_trades_an_inner_attack_for_a_changed_kick() -> None:
    """A full busy bar must still react when the source groove changes."""

    plan = phrase_v2._build_phrase_development_plan(
        bar_count=4,
        style="fusion",
        seed=27,
        rng=random.Random(27),
        density_bias=0.5,
    )
    offbeat_slots = (1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15)

    for bar, development in enumerate(plan):
        original = phrase_v2._style_phrase_slots(
            "fusion",
            development.role,
            kick_slots=[],
            dense=0.5,
            bar=bar,
            candidate_role=None,
            rng=random.Random(27),
            development=development,
        )
        new_kick = next(slot for slot in offbeat_slots if slot not in original)
        adapted = phrase_v2._style_phrase_slots(
            "fusion",
            development.role,
            kick_slots=[new_kick],
            dense=0.5,
            bar=bar,
            candidate_role=None,
            rng=random.Random(27),
            development=development,
        )

        assert len(adapted) == len(original)
        assert new_kick in adapted
        assert set(adapted) != set(original)
        assert len(set(adapted) - set(original)) == 1
        assert len(set(original) - set(adapted)) == 1
        assert phrase_v2._fusion_structural_slots(
            original,
            role=development.role,
        ).issubset(adapted)


def test_inserted_kick_does_not_steal_the_authored_fusion_octave_role() -> None:
    """Groove replacement changes timing without reassigning pitch intent."""

    development = phrase_v2._build_phrase_development_plan(
        bar_count=1,
        style="fusion",
        seed=0,
        rng=random.Random(0),
        density_bias=0.5,
    )[0]
    authored = phrase_v2._fusion_macro_slots(development)
    assert 7 not in authored
    authored_octave = phrase_v2._fusion_authored_octave_slot(
        authored,
        development=development,
    )
    assert authored_octave is not None
    adapted = phrase_v2._adapt_fusion_macro_to_kicks(
        set(authored),
        kick_slots=[7],
        role=development.role,
        protected_slots=(authored_octave,),
    )
    structural = tuple(
        sorted(
            phrase_v2._fusion_structural_slots(
                authored,
                role=development.role,
            )
        )
    )

    inserted_role = phrase_v2._fusion_pitch_role(
        slot=7,
        bar_slots=tuple(sorted(adapted)),
        development=development,
        authored_structural_slots=structural,
        authored_bar_slots=authored,
    )
    surviving_octaves = [
        slot
        for slot in authored
        if slot in adapted
        and phrase_v2._fusion_pitch_role(
            slot=slot,
            bar_slots=tuple(sorted(adapted)),
            development=development,
            authored_structural_slots=structural,
            authored_bar_slots=authored,
        )
        == "octave_root"
    ]

    assert inserted_role in {"colour_a", "colour_b"}
    assert surviving_octaves == [authored_octave]


def test_fusion_lead_activity_keeps_density_stable_across_changed_kicks() -> None:
    """A groove replacement must not move the producer-facing Activity knob."""

    offbeat_slots = (1, 2, 3, 5, 6, 7, 9, 10, 11, 13, 14, 15)
    for seed in range(16):
        plan = phrase_v2._build_phrase_development_plan(
            bar_count=16,
            style="fusion",
            seed=seed,
            rng=random.Random(seed),
            density_bias=1.0,
        )
        for bar, development in enumerate(plan):
            original = phrase_v2._style_phrase_slots(
                "fusion",
                development.role,
                kick_slots=[],
                dense=1.0,
                bar=bar,
                candidate_role=None,
                rng=random.Random(seed),
                development=development,
            )
            new_kick = next(
                slot for slot in offbeat_slots if slot not in original
            )
            adapted = phrase_v2._style_phrase_slots(
                "fusion",
                development.role,
                kick_slots=[new_kick],
                dense=1.0,
                bar=bar,
                candidate_role=None,
                rng=random.Random(seed),
                development=development,
            )

            assert len(adapted) == len(original)
            assert len(original) == (
                7 if development.role == "release" else 10
            )
            assert new_kick in adapted
            assert set(adapted) != set(original)


def _hostile_drum_context(bar_count: int) -> SessionAnchorContext:
    """A deliberately crowded drum map for structural-gating contracts."""

    zeros = tuple(0.0 for _ in range(16))
    crowded = tuple(1.0 for _ in range(16))
    return SessionAnchorContext(
        tempo=116,
        bar_count=bar_count,
        anchor_lane="drums",
        bar_len_sec=240.0 / 116.0,
        beat_len_sec=60.0 / 116.0,
        sixteenth_len_sec=15.0 / 116.0,
        density_per_bar=tuple(16.0 for _ in range(bar_count)),
        onsets_norm_per_bar=tuple(
            tuple(slot / 16.0 for slot in range(16))
            for _ in range(bar_count)
        ),
        gap_sec_per_bar=tuple(() for _ in range(bar_count)),
        mean_gap_sec_per_bar=tuple(0.0 for _ in range(bar_count)),
        pitch_min=35,
        pitch_max=40,
        pitch_span=5,
        syncopation_score=0.75,
        mean_density=16.0,
        slot_occupancy=tuple(crowded for _ in range(bar_count)),
        kick_slot_weight=tuple(zeros for _ in range(bar_count)),
        snare_slot_weight=tuple(crowded for _ in range(bar_count)),
        beat_phase_offset_beats=0,
        beat_phase_confidence=1.0,
        bar_start_anchor_sec=0.0,
        harmonic_root_pc_per_bar=tuple(2 for _ in range(bar_count)),
        harmonic_target_pcs_per_bar=tuple(
            (2, 5, 9) for _ in range(bar_count)
        ),
        harmonic_passing_pcs_per_bar=tuple(
            (0, 4, 7, 10) for _ in range(bar_count)
        ),
        harmonic_avoid_pcs_per_bar=tuple(
            (1, 3, 6, 8, 11) for _ in range(bar_count)
        ),
        harmonic_confidence_per_bar=tuple(1.0 for _ in range(bar_count)),
        harmonic_source_per_bar=tuple(
            "confirmed_chord_progression" for _ in range(bar_count)
        ),
    )


def test_fusion_structural_leads_survive_aggressive_groove_gating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pressure may thin detail, but not erase the written melodic sentence."""

    bar_count = 4
    context = _hostile_drum_context(bar_count)
    monkeypatch.setattr(
        phrase_v2,
        "_resolve_reference_lock",
        lambda _lock, _conditioning: (1.0, "locked"),
    )

    for seed in range(8):
        _midi, _preview, notes = generate_bass_phrase_v2(
            tempo=116,
            bar_count=bar_count,
            key="D",
            scale="natural_minor",
            bass_style="fusion",
            bass_instrument="finger_bass",
            chord_progression=list(_CHORDS),
            context=context,
            seed=seed,
            density_bias=0.5,
            expression_amount=1.0,
            bass_articulation_focus="clean",
            lock_to_groove=1.0,
            return_performance_notes=True,
        )
        rendered = {
            (int(note.bar_index), int(note.slot_index))
            for note in notes
        }
        plan = phrase_v2._build_phrase_development_plan(
            bar_count=bar_count,
            style="fusion",
            seed=seed,
            rng=random.Random(seed),
            density_bias=0.5,
        )
        omitted_inner_slots: set[tuple[int, int]] = set()
        for bar, development in enumerate(plan):
            authored = phrase_v2._fusion_macro_slots(development)
            structural = phrase_v2._fusion_structural_slots(
                authored,
                role=development.role,
            )
            assert {
                (bar, slot) for slot in structural
            }.issubset(rendered)
            omitted_inner_slots.update(
                (bar, slot)
                for slot in set(authored).difference(structural)
                if (bar, slot) not in rendered
            )
        assert omitted_inner_slots, (
            "groove protection expanded from structural notes into all detail"
        )


def test_fusion_protects_authored_and_adapted_leads_after_kick_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An early kick may lead its beat without deleting the written contour."""

    context = _hostile_drum_context(4)
    kick_rows = [list(row) for row in context.kick_slot_weight]
    kick_rows[2][5] = 1.0
    context = replace(
        context,
        kick_slot_weight=tuple(tuple(row) for row in kick_rows),
    )
    monkeypatch.setattr(
        phrase_v2,
        "_resolve_reference_lock",
        lambda _lock, _conditioning: (1.0, "locked"),
    )

    _midi, _preview, notes = generate_bass_phrase_v2(
        tempo=116,
        bar_count=4,
        key="D",
        scale="natural_minor",
        bass_style="fusion",
        bass_instrument="finger_bass",
        chord_progression=list(_CHORDS),
        context=context,
        seed=0,
        density_bias=0.5,
        expression_amount=1.0,
        bass_articulation_focus="clean",
        lock_to_groove=1.0,
        return_performance_notes=True,
    )
    rendered = {
        (int(note.bar_index), int(note.slot_index))
        for note in notes
    }

    assert (2, 5) in rendered  # adapted beat lead from the kick map
    assert (2, 7) in rendered  # authored seeded melodic lead


@pytest.mark.parametrize("style", _STYLES)
def test_same_seed_replays_the_same_macro_composition(style: str) -> None:
    """A stored seed must reproduce the written part, not only its note count."""

    first = _phrase(style=style, seed=202_607_27)
    replay = _phrase(style=style, seed=202_607_27)

    assert first == replay


@pytest.mark.parametrize("style", _STYLES)
def test_macro_development_stays_inside_confirmed_scale_and_harmony(
    style: str,
) -> None:
    """Development may be bold, but a confirmed chart remains authoritative."""

    scale_pcs = {
        (mt.key_root_pc("D") + interval) % 12
        for interval in mt.scale_intervals("natural_minor")
    }
    chord_plan = mt.progression_chords_for_bars(_CHORDS, 16)

    for seed in _SEEDS:
        notes = _phrase(
            style=style,
            seed=seed,
            activity=1.0,
            character=1.0,
        )
        assert all(int(note.pitch) % 12 in scale_pcs for note in notes)
        for note in notes:
            if int(note.slot_index) % 4 != 0:
                continue
            chord_pcs = {
                int(pc) % 12
                for pc in chord_plan[int(note.bar_index)].tone_pcs
            }
            assert int(note.pitch) % 12 in chord_pcs


def test_macro_development_preserves_distinct_style_grammars() -> None:
    """No pair of styles should inherit one shared 16-bar onset skeleton."""

    for left_style, right_style in combinations(_STYLES, 2):
        distances = [
            _jaccard_distance(
                _onsets(_phrase(style=left_style, seed=seed)),
                _onsets(_phrase(style=right_style, seed=seed)),
            )
            for seed in _SEEDS
        ]
        assert median(distances) >= 0.30, (
            f"{left_style}/{right_style} lost their rhythmic identities; "
            f"median onset distance was {median(distances):.3f}"
        )


@pytest.mark.parametrize("style", _STYLES)
def test_four_bar_arcs_have_a_peak_release_and_then_develop(
    style: str,
) -> None:
    """Each arc should push in bar 3, release in bar 4, then evolve coherently."""

    push_energy: list[int] = []
    release_energy: list[int] = []
    transition_distances: list[float] = []
    distinct_arc_counts: list[int] = []

    for seed in _SEEDS:
        notes = _phrase(style=style, seed=seed)
        # Sum over equivalent positions in all four arcs.  This makes the test
        # insensitive to a single humanised velocity while retaining the
        # large-scale statement -> answer -> peak -> release contract.
        position_energy = [
            sum(
                int(note.velocity)
                for note in notes
                if int(note.bar_index) % 4 == position
            )
            for position in range(4)
        ]
        push_energy.append(position_energy[2])
        release_energy.append(position_energy[3])

        arcs = [
            _normalized_arc_onsets(notes, arc_index)
            for arc_index in range(4)
        ]
        distinct_arc_counts.append(len(set(arcs)))
        transition_distances.extend(
            _jaccard_distance(left, right)
            for left, right in zip(arcs, arcs[1:], strict=False)
        )

    assert median(push_energy) > median(release_energy), (
        f"{style} did not create a bar-3 peak and bar-4 release"
    )
    assert median(distinct_arc_counts) >= 3, (
        f"{style} repeated one four-bar phrase instead of developing it"
    )
    assert 0.15 <= median(transition_distances) <= 0.75, (
        f"{style} arc changes were either copies or unrelated rewrites; "
        f"median distance was {median(transition_distances):.3f}"
    )


@pytest.mark.parametrize("style", _STYLES)
def test_regenerate_explores_new_macro_rhythms_not_pitch_only_variants(
    style: str,
) -> None:
    """Fresh seeds must move phrase architecture, not merely notes/velocities."""

    phrases = [_phrase(style=style, seed=seed) for seed in _SEEDS]
    signatures = {_onset_signature(notes) for notes in phrases}
    adjacent_distances = [
        _jaccard_distance(_onsets(left), _onsets(right))
        for left, right in zip(phrases, phrases[1:], strict=False)
    ]

    assert len(signatures) >= 6, (
        f"{style} produced only {len(signatures)} macro rhythms across "
        f"{len(_SEEDS)} regenerations"
    )
    assert median(adjacent_distances) >= 0.18, (
        f"{style} regeneration mostly replayed the same onset architecture"
    )


def test_fusion_activity_and_character_remain_orthogonal_at_macro_scale() -> None:
    """Activity authors rhythm; Character revoices it without density leakage."""

    character_repitch_shares: list[float] = []
    for seed in _SEEDS:
        restrained_sparse = _phrase(
            style="fusion",
            seed=seed,
            activity=-0.5,
            character=0.0,
        )
        bold_sparse = _phrase(
            style="fusion",
            seed=seed,
            activity=-0.5,
            character=1.0,
        )
        restrained_busy = _phrase(
            style="fusion",
            seed=seed,
            activity=0.5,
            character=0.0,
        )
        bold_busy = _phrase(
            style="fusion",
            seed=seed,
            activity=0.5,
            character=1.0,
        )

        assert _onset_signature(restrained_sparse) == _onset_signature(
            bold_sparse
        )
        assert _onset_signature(restrained_busy) == _onset_signature(
            bold_busy
        )
        assert _onset_signature(restrained_sparse) != _onset_signature(
            restrained_busy
        )
        assert len(restrained_sparse) < len(restrained_busy)

        restrained_pitch = {
            (bar, slot): pitch
            for bar, slot, pitch in _pitch_architecture(restrained_busy)
        }
        bold_pitch = {
            (bar, slot): pitch
            for bar, slot, pitch in _pitch_architecture(bold_busy)
        }
        shared = restrained_pitch.keys() & bold_pitch.keys()
        assert shared
        character_repitch_shares.append(
            sum(
                restrained_pitch[onset] != bold_pitch[onset]
                for onset in shared
            )
            / len(shared)
        )

    assert median(character_repitch_shares) >= 0.25


def test_fusion_regeneration_changes_chord_relative_pitch_sentence() -> None:
    """Regenerate must rewrite the line, not only reposition one contour."""

    phrases = [
        _phrase(
            style="fusion",
            seed=seed,
            activity=0.5,
            character=1.0,
        )
        for seed in range(40)
    ]
    signatures = [
        _chord_relative_pitch_sentence(notes)
        for notes in phrases
    ]
    adjacent_changes = sum(
        left != right
        for left, right in zip(signatures, signatures[1:], strict=False)
    )

    assert len(set(signatures)) >= 8
    assert adjacent_changes / (len(signatures) - 1) >= 0.75


def test_bold_fusion_remains_one_playable_voice_led_sentence(
    fusion_benchmark_batch: dict[int, tuple[BassPerformanceNote, ...]],
) -> None:
    """Character may lead, but it must not become low-root chord-tone pinball."""

    median_intervals: list[float] = []
    fifth_or_larger_rates: list[float] = []
    octave_or_larger_rates: list[float] = []
    stepwise_counts: list[int] = []

    for seed, notes in fusion_benchmark_batch.items():
        ordered = sorted(
            notes,
            key=lambda note: (
                float(note.start),
                int(note.pitch),
            ),
        )
        intervals = [
            abs(int(right.pitch) - int(left.pitch))
            for left, right in zip(ordered, ordered[1:], strict=False)
        ]
        assert intervals
        median_intervals.append(median(intervals))
        fifth_or_larger_rates.append(
            sum(interval >= 7 for interval in intervals) / len(intervals)
        )
        octave_or_larger_rates.append(
            sum(interval >= 12 for interval in intervals) / len(intervals)
        )
        stepwise_counts.append(
            sum(interval in {1, 2} for interval in intervals)
        )

        boundary_intervals: list[int] = []
        for bar in range(1, 16):
            prior = max(
                (
                    note
                    for note in ordered
                    if int(note.bar_index) == bar - 1
                ),
                key=lambda note: float(note.start),
            )
            following = min(
                (
                    note
                    for note in ordered
                    if int(note.bar_index) == bar
                ),
                key=lambda note: float(note.start),
            )
            boundary_intervals.append(
                abs(int(following.pitch) - int(prior.pitch))
            )
        assert max(boundary_intervals) <= 7, (
            f"seed {seed} restarted a bar with a disconnected "
            f"{max(boundary_intervals)}-semitone leap"
        )

        for bar in range(16):
            bar_notes = [
                note
                for note in ordered
                if int(note.bar_index) == bar
            ]
            assert all(
                not (
                    int(bar_notes[index].pitch)
                    == int(bar_notes[index - 1].pitch)
                    == int(bar_notes[index - 2].pitch)
                    == int(bar_notes[index - 3].pitch)
                    and int(bar_notes[index].slot_index)
                    - int(bar_notes[index - 3].slot_index)
                    <= 4
                )
                for index in range(3, len(bar_notes))
            ), f"seed {seed} bar {bar + 1} contains a machine-gun note run"

    assert median(median_intervals) <= 4.0
    assert max(fifth_or_larger_rates) <= 0.20
    assert max(octave_or_larger_rates) <= 0.08
    assert median(stepwise_counts) >= 6


@pytest.mark.parametrize("seed", _FUSION_BENCHMARK_SEEDS)
def test_fusion_clean_midi_retriggers_do_not_collapse_into_micro_notes(
    seed: int,
) -> None:
    """An adjacent same-pitch attack must survive a raw MIDI roundtrip."""

    midi, _preview, performance_notes = generate_bass_phrase_v2(
        tempo=116,
        bar_count=16,
        key="D",
        scale="natural_minor",
        bass_style="fusion",
        bass_instrument="finger_bass",
        chord_progression=list(_CHORDS),
        seed=seed,
        density_bias=0.5,
        expression_amount=1.0,
        bass_articulation_focus="clean",
        return_performance_notes=True,
    )
    parsed = pretty_midi.PrettyMIDI(io.BytesIO(midi))
    rendered_notes = sorted(
        parsed.instruments[0].notes,
        key=lambda note: (float(note.start), int(note.pitch)),
    )
    seconds_per_beat = 60.0 / 116.0

    assert min(
        (float(note.end) - float(note.start)) / seconds_per_beat
        for note in rendered_notes
    ) >= 0.12, f"seed {seed} serialized an accidental micro-note"

    for pitch in {int(note.pitch) for note in rendered_notes}:
        same_pitch = [
            note for note in rendered_notes if int(note.pitch) == pitch
        ]
        assert all(
            float(left.end) <= float(right.start)
            for left, right in zip(
                same_pitch,
                same_pitch[1:],
                strict=False,
            )
        ), f"seed {seed} left an overlapping pitch-{pitch} retrigger"

    for pitch in {int(note.pitch) for note in performance_notes}:
        same_pitch = [
            note
            for note in performance_notes
            if int(note.pitch) == pitch
        ]
        assert all(
            float(left.end) <= float(right.start)
            for left, right in zip(
                same_pitch,
                same_pitch[1:],
                strict=False,
            )
        ), f"seed {seed} left overlapping performance-note metadata"


@pytest.mark.parametrize("seed", _FUSION_BENCHMARK_SEEDS)
def test_fusion_four_bar_sections_restate_then_develop_the_beat_skeleton(
    seed: int,
    fusion_benchmark_batch: dict[int, tuple[BassPerformanceNote, ...]],
) -> None:
    """A/A'/B/return must be recognisable without becoming a copied loop."""

    notes = fusion_benchmark_batch[seed]
    sections = [
        _beat_skeleton_tokens(notes, start_bar=start_bar, bar_count=4)
        for start_bar in range(0, 16, 4)
    ]
    similarities = [
        _jaccard_similarity(left, right)
        for left, right in combinations(sections, 2)
    ]

    assert 0.45 <= mean(similarities) <= 0.70, (
        f"seed {seed} section skeleton mean "
        f"{mean(similarities):.3f} escaped the coherent-development window"
    )
    assert max(similarities) <= 0.80, (
        f"seed {seed} copied two section skeletons at "
        f"{max(similarities):.3f} similarity"
    )


@pytest.mark.parametrize("seed", _FUSION_BENCHMARK_SEEDS)
def test_fusion_release_bars_land_with_safe_varied_answers(
    seed: int,
    fusion_benchmark_batch: dict[int, tuple[BassPerformanceNote, ...]],
) -> None:
    """Bars 4/8/12/16 resolve safely, but do not all recite one cadence."""

    notes = fusion_benchmark_batch[seed]
    chord_plan = mt.progression_chords_for_bars(_CHORDS, 16)
    release_bars = (3, 7, 11, 15)
    landing_pcs: list[int] = []
    for bar in release_bars:
        bar_notes = [note for note in notes if int(note.bar_index) == bar]
        assert bar_notes, f"seed {seed} left release bar {bar + 1} empty"
        final_slot = max(int(note.slot_index) for note in bar_notes)
        landing_pc = min(
            int(note.pitch)
            for note in bar_notes
            if int(note.slot_index) == final_slot
        ) % 12
        chord_pcs = {
            int(pitch_class) % 12
            for pitch_class in chord_plan[bar].tone_pcs
        }
        assert landing_pc in chord_pcs, (
            f"seed {seed} release bar {bar + 1} landed outside its chord"
        )
        landing_pcs.append(landing_pc)

    assert len(set(landing_pcs)) >= 2, (
        f"seed {seed} repeated one landing pitch class in every release"
    )
    final_safe_pcs = {
        int(pitch_class) % 12
        for pitch_class in chord_plan[-1].tone_pcs
    }
    final_safe_pcs.add(mt.key_root_pc("D"))
    assert landing_pcs[-1] in final_safe_pcs


def test_fusion_regeneration_batch_has_bounded_macro_novelty(
    fusion_benchmark_batch: dict[int, tuple[BassPerformanceNote, ...]],
) -> None:
    """Twelve regenerations should be fresh siblings, not clones or rewrites."""

    phrases = [
        fusion_benchmark_batch[seed]
        for seed in _FUSION_BENCHMARK_SEEDS
    ]
    rhythm_similarities = [
        _jaccard_similarity(
            _full_rhythm_tokens(left),
            _full_rhythm_tokens(right),
        )
        for left, right in combinations(phrases, 2)
    ]
    skeleton_similarities = [
        _jaccard_similarity(
            _beat_skeleton_tokens(left),
            _beat_skeleton_tokens(right),
        )
        for left, right in combinations(phrases, 2)
    ]
    note_counts = [len(notes) for notes in phrases]
    note_count_cv = pstdev(note_counts) / mean(note_counts)
    rhythm_p50 = _linear_percentile(rhythm_similarities, 50)
    rhythm_p90 = _linear_percentile(rhythm_similarities, 90)
    skeleton_p50 = _linear_percentile(skeleton_similarities, 50)
    skeleton_p90 = _linear_percentile(skeleton_similarities, 90)

    assert 0.48 <= rhythm_p50 <= 0.60, (
        f"full-rhythm p50 was {rhythm_p50:.3f}"
    )
    assert rhythm_p90 <= 0.68, f"full-rhythm p90 was {rhythm_p90:.3f}"
    assert 0.25 <= skeleton_p50 <= 0.55, (
        f"beat-skeleton p50 was {skeleton_p50:.3f}"
    )
    assert skeleton_p90 <= 0.70, (
        f"beat-skeleton p90 was {skeleton_p90:.3f}"
    )
    assert note_count_cv <= 0.12, (
        f"note-count CV was {note_count_cv:.3f}: {note_counts}"
    )
