"""Purposeful candidate roles inside the shared Fusion groove contract."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from app.services.bass_performance import BassPerformanceNote
from app.services.bass_phrase_engine_v2 import generate_bass_phrase_v2
from app.services.fusion_contract import build_fusion_contract
from app.utils import music_theory as mt


_TEMPO = 116
_BARS = 4
_SEED = 7919
_CHORDS = ["Dm7", "Gm7", "Bbmaj7", "A7"]


def _take(
    candidate_role: str | None,
    *,
    include_candidate_keyword: bool = True,
) -> tuple[bytes, tuple[BassPerformanceNote, ...]]:
    contract = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        covenant_id="anticipated_tumbao",
    )
    kwargs = {
        "tempo": _TEMPO,
        "bar_count": _BARS,
        "key": "D",
        "scale": "natural_minor",
        "bass_style": "fusion",
        "bass_instrument": "finger_bass",
        "chord_progression": _CHORDS,
        "seed": _SEED,
        "density_bias": 0.0,
        "expression_amount": 0.5,
        "bass_articulation_focus": "clean",
        "return_performance_notes": True,
        "fusion_contract": contract,
    }
    if include_candidate_keyword:
        kwargs["candidate_role"] = candidate_role
    midi, _preview, notes = generate_bass_phrase_v2(**kwargs)
    return midi, notes


def _by_position(
    notes: Iterable[BassPerformanceNote],
) -> dict[tuple[int, int], BassPerformanceNote]:
    return {
        (int(note.bar_index), int(note.slot_index)): note
        for note in notes
        if note.bar_index is not None and note.slot_index is not None
    }


def test_neutral_contract_render_is_unchanged_and_deterministic() -> None:
    explicit_midi, explicit = _take(None)
    default_midi, default = _take(None, include_candidate_keyword=False)
    repeated_midi, repeated = _take(None)

    assert explicit_midi == default_midi == repeated_midi
    assert explicit == default == repeated


def test_pocket_thins_only_optional_contract_material() -> None:
    _base_midi, base = _take(None)
    _pocket_midi, pocket = _take("pocket_keeper")
    contract = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        covenant_id="anticipated_tumbao",
    )
    optional = {
        (bar, event.slot)
        for bar in range(_BARS)
        for event in contract.bar(bar).bass_events
        if event.optional
    }
    required = {
        (bar, event.slot)
        for bar in range(_BARS)
        for event in contract.bar(bar).bass_events
        if not event.optional
    }

    assert set(_by_position(base)) == required | optional
    assert set(_by_position(pocket)) == required
    assert len(pocket) == len(base) - len(optional)


def test_rhythmic_role_makes_one_permissioned_move_per_bar() -> None:
    _base_midi, base = _take(None)
    rhythmic_midi, rhythmic = _take("rhythmic_alternative")
    repeated_midi, repeated = _take("rhythmic_alternative")
    base_positions = _by_position(base)
    rhythmic_positions = _by_position(rhythmic)
    contract = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        covenant_id="anticipated_tumbao",
    )

    assert rhythmic_midi == repeated_midi
    assert rhythmic == repeated
    assert len(rhythmic) == len(base)
    for bar in range(_BARS):
        before = {slot for b, slot in base_positions if b == bar}
        after = {slot for b, slot in rhythmic_positions if b == bar}
        removed = before - after
        added = after - before

        assert len(removed) == len(added) == 1
        old_slot = next(iter(removed))
        new_slot = next(iter(added))
        assert abs(new_slot - old_slot) <= 2
        assert new_slot not in contract.bar(bar).protected_melodic_rest_slots
        assert new_slot not in contract.bar(bar).snare_slots
        assert new_slot not in {
            event.slot for event in contract.bar(bar).keys_events
        }


def test_harmonic_role_keeps_rhythm_and_changes_only_safe_colour_choices() -> None:
    _base_midi, base = _take(None)
    _harmonic_midi, harmonic = _take("harmonic_alternative")
    base_positions = _by_position(base)
    harmonic_positions = _by_position(harmonic)
    progression = mt.progression_chords_for_bars(_CHORDS, _BARS)

    assert set(harmonic_positions) == set(base_positions)
    changed_colour_bars: set[int] = set()
    for position, base_note in base_positions.items():
        harmonic_note = harmonic_positions[position]
        assert harmonic_note.start == pytest.approx(base_note.start)
        assert harmonic_note.end == pytest.approx(base_note.end)
        assert harmonic_note.velocity == base_note.velocity
        if harmonic_note.pitch % 12 != base_note.pitch % 12:
            assert base_note.role in {"answer", "colour"}
            changed_colour_bars.add(position[0])
        assert harmonic_note.pitch % 12 in {
            int(pc) % 12 for pc in progression[position[0]].tone_pcs
        } or (
            harmonic_note.role == "anticipation"
            and harmonic_note.pitch % 12
            in {
                int(pc) % 12
                for pc in progression[(position[0] + 1) % _BARS].tone_pcs
            }
        )

    assert changed_colour_bars == set(range(_BARS))


def test_performance_role_keeps_notes_but_changes_bounded_shared_feel() -> None:
    _base_midi, base = _take(None)
    _performance_midi, performance = _take("performance_alternative")
    base_positions = _by_position(base)
    performance_positions = _by_position(performance)
    tick_seconds = (60.0 / _TEMPO) / 960.0

    assert set(performance_positions) == set(base_positions)
    assert {
        (position, note.pitch)
        for position, note in performance_positions.items()
    } == {
        (position, note.pitch)
        for position, note in base_positions.items()
    }
    assert any(
        note.velocity != base_positions[position].velocity
        for position, note in performance_positions.items()
    )
    assert any(
        (note.end - note.start)
        != pytest.approx(
            base_positions[position].end - base_positions[position].start
        )
        for position, note in performance_positions.items()
    )
    assert all(
        abs(note.start - base_positions[position].start)
        <= (6 * tick_seconds) + 1e-9
        for position, note in performance_positions.items()
    )
    assert all(note.end > note.start for note in performance)


@pytest.mark.parametrize(
    "candidate_role",
    [
        "pocket_keeper",
        "rhythmic_alternative",
        "harmonic_alternative",
        "performance_alternative",
    ],
)
def test_every_role_honours_protected_slots(candidate_role: str) -> None:
    _midi, notes = _take(candidate_role)
    contract = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        covenant_id="anticipated_tumbao",
    )

    assert all(
        int(note.slot_index)
        not in contract.bar(int(note.bar_index)).protected_melodic_rest_slots
        for note in notes
        if note.bar_index is not None and note.slot_index is not None
    )
