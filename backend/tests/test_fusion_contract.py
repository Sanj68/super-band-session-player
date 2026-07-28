"""Musical contracts for the first shared Fusion rhythm-section law."""

from __future__ import annotations

from copy import deepcopy
import io

import pretty_midi
import pytest

from app.models.session import GrooveProfile
from app.services.conditioning import UnifiedConditioning
from app.services.fusion_contract import (
    FusionGrooveContract,
    build_fusion_contract,
    covenant_ids,
)
from app.services.generator import generate_bass, generate_chords, generate_drums
from app.utils import music_theory as mt


_TEMPO = 116
_BARS = 8
_CHORDS = ["Dm7", "Gm7", "Bbmaj7", "A7"]


def _source_conditioning(
    strong_slot: int,
    *,
    additional_onset_slots: tuple[int, ...] = (),
    confidence: float = 1.0,
    kick_weight: float = 0.98,
    onset_weight: float = 0.72,
) -> UnifiedConditioning:
    kicks = []
    onsets = []
    zeros = []
    for _bar in range(_BARS):
        row = [0.0] * 16
        row[strong_slot] = kick_weight
        kicks.append(tuple(row))
        onset_row = list(row)
        for slot in additional_onset_slots:
            onset_row[slot] = onset_weight
        onsets.append(tuple(onset_row))
        zeros.append(tuple(0.0 for _ in range(16)))
    return UnifiedConditioning(
        tempo=_TEMPO,
        bar_count=_BARS,
        beat_phase_offset_beats=0,
        beat_phase_confidence=1.0,
        bar_start_anchor_sec=0.0,
        beat_grid_seconds=(),
        bar_starts_seconds=(),
        sections=(),
        groove_profile=GrooveProfile(
            pocket_feel="straight",
            syncopation_score=0.7,
            density_per_bar_estimate=5.0,
            accent_strength=0.8,
            confidence=1.0,
        ),
        harmonic_bars=(),
        source_groove_resolution=16,
        source_onset_weight=tuple(onsets),
        source_kick_weight=tuple(kicks),
        source_snare_weight=tuple(zeros),
        source_slot_pressure=tuple(onsets),
        source_groove_confidence=tuple(confidence for _ in range(_BARS)),
    )


def _bar_signature(contract: FusionGrooveContract, bar: int) -> tuple[object, ...]:
    plan = contract.bar(bar)
    return (
        plan.two_bar_phase,
        plan.kick_slots,
        plan.snare_slots,
        tuple((event.slot, event.role) for event in plan.bass_events),
        tuple((event.slot, event.role) for event in plan.keys_events),
        plan.protected_melodic_rest_slots,
    )


def _midi_notes(data: bytes) -> list[pretty_midi.Note]:
    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    return [
        note
        for instrument in pm.instruments
        for note in instrument.notes
    ]


def _slot(start: float) -> tuple[int, int]:
    spb = 60.0 / _TEMPO
    absolute_slot = int(round(float(start) / (spb / 4.0)))
    return absolute_slot // 16, absolute_slot % 16


def test_contract_is_deterministic_and_round_trips_exactly() -> None:
    first = build_fusion_contract(seed=400, bar_count=16)
    second = build_fusion_contract(seed=400, bar_count=16)

    assert first == second
    assert first.contract_id == second.contract_id
    assert FusionGrooveContract.from_payload(first.to_payload()) == first


def test_contract_identity_rejects_payload_content_tampering() -> None:
    contract = build_fusion_contract(seed=400, bar_count=8)
    payload = deepcopy(contract.to_payload())
    payload["bars"][0]["hat_slots"] = (0,)

    with pytest.raises(
        ValueError,
        match="contract_id does not match",
    ):
        FusionGrooveContract.from_payload(payload)


def test_contract_rejects_false_reference_provenance() -> None:
    contract = build_fusion_contract(seed=401, bar_count=8)
    payload = contract.to_payload()
    payload["source_mode"] = "reference"

    with pytest.raises(
        ValueError,
        match="does not match the Fusion source signature",
    ):
        FusionGrooveContract.from_payload(payload)


def test_four_seeds_select_materially_different_original_covenants() -> None:
    contracts = [
        build_fusion_contract(seed=seed, bar_count=4)
        for seed in range(4)
    ]

    assert tuple(contract.covenant_id for contract in contracts) == covenant_ids()
    bass_signatures = {
        tuple(
            tuple(event.slot for event in contract.bar(bar).bass_events)
            for bar in range(2)
        )
        for contract in contracts
    }
    assert len(bass_signatures) == 4


def test_statement_repeats_two_bar_identity_then_variation_develops_it() -> None:
    contract = build_fusion_contract(
        seed=0,
        bar_count=8,
        covenant_id="anticipated_tumbao",
    )

    assert _bar_signature(contract, 0)[1:] == _bar_signature(contract, 2)[1:]
    assert _bar_signature(contract, 1)[1:] == _bar_signature(contract, 3)[1:]
    assert _bar_signature(contract, 4) != _bar_signature(contract, 0)
    assert contract.bar(0).phrase_role == "statement"
    assert contract.bar(4).phrase_role == "variation"


def test_every_bar_has_interlock_sparse_keys_and_real_protected_space() -> None:
    for seed in range(4):
        contract = build_fusion_contract(seed=seed, bar_count=16)
        for plan in contract.bars:
            bass_slots = {event.slot for event in plan.bass_events}
            keys_slots = {event.slot for event in plan.keys_events}
            protected = set(plan.protected_melodic_rest_slots)

            assert set(plan.kick_slots).intersection(bass_slots)
            assert 1 <= len(keys_slots) <= 2
            assert protected
            assert not protected.intersection(bass_slots | keys_slots)
            assert not (
                bass_slots.intersection(keys_slots)
                - set(plan.intentional_ensemble_slots)
            )


def test_captured_beat_changes_a_bounded_shared_decision() -> None:
    left = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        conditioning=_source_conditioning(5),
    )
    right = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        conditioning=_source_conditioning(13),
    )

    assert left.source_mode == right.source_mode == "reference"
    assert left.source_signature != right.source_signature
    assert left.contract_id != right.contract_id
    assert all(plan.source_adapted for plan in left.bars)
    assert all(plan.source_adapted for plan in right.bars)
    assert all(5 in plan.kick_slots for plan in left.bars)
    assert all(13 in plan.kick_slots for plan in right.bars)


def test_captured_feel_evidence_change_invalidates_same_kick_pattern() -> None:
    sparse = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        conditioning=_source_conditioning(5),
    )
    busier = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        conditioning=_source_conditioning(
            5,
            additional_onset_slots=(7, 9, 11),
        ),
    )

    assert sparse.source_signature != busier.source_signature
    assert sparse.contract_id != busier.contract_id
    assert sparse.bar(0).hat_slots != busier.bar(0).hat_slots


def test_small_analysis_drift_does_not_make_same_source_decisions_stale() -> None:
    first = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        conditioning=_source_conditioning(
            5,
            additional_onset_slots=(7, 9, 11),
            kick_weight=0.98,
            onset_weight=0.72,
        ),
    )
    nudged = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        conditioning=_source_conditioning(
            5,
            additional_onset_slots=(7, 9, 11),
            kick_weight=0.93,
            onset_weight=0.68,
        ),
    )

    assert nudged == first


def test_low_confidence_source_is_reported_as_authored_and_not_consumed() -> None:
    contract = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        conditioning=_source_conditioning(5, confidence=0.44),
        covenant_id="anticipated_tumbao",
    )

    assert contract.source_mode == "authored"
    assert contract.source_signature == "authored"
    assert not any(plan.source_adapted for plan in contract.bars)


def test_source_adaptation_does_not_turn_a_keys_answer_into_a_bass_collision() -> None:
    contract = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        conditioning=_source_conditioning(3),
        covenant_id="anticipated_tumbao",
    )

    for plan in contract.bars:
        bass_slots = {event.slot for event in plan.bass_events}
        keys_slots = {event.slot for event in plan.keys_events}
        assert not bass_slots.intersection(keys_slots)


def test_drums_bass_and_keys_obey_one_contract_and_confirmed_harmony() -> None:
    contract = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        covenant_id="anticipated_tumbao",
    )
    drum_bytes, _drum_preview = generate_drums(
        tempo=_TEMPO,
        bar_count=_BARS,
        drum_style="funk",
        fusion_contract=contract,
    )
    bass_bytes, _bass_preview, bass_notes = generate_bass(
        tempo=_TEMPO,
        bar_count=_BARS,
        key="D",
        scale="natural_minor",
        bass_style="fusion",
        bass_engine="phrase_v2",
        bass_instrument="finger_bass",
        chord_progression=_CHORDS,
        seed=0,
        return_performance_notes=True,
        fusion_contract=contract,
    )
    chord_bytes, _chord_preview = generate_chords(
        tempo=_TEMPO,
        bar_count=_BARS,
        key="D",
        scale="natural_minor",
        chord_style="wide",
        chord_instrument="rhodes",
        chord_progression=_CHORDS,
        fusion_contract=contract,
    )

    kicks = {
        _slot(note.start)
        for note in _midi_notes(drum_bytes)
        if note.pitch == 36
    }
    expected_kicks = {
        (bar, slot)
        for bar in range(_BARS)
        for slot in contract.bar(bar).kick_slots
    }
    assert kicks == expected_kicks

    bass_onsets = {
        (int(note.bar_index), int(note.slot_index))
        for note in bass_notes
    }
    expected_bass = {
        (bar, event.slot)
        for bar in range(_BARS)
        for event in contract.bar(bar).bass_events
    }
    assert bass_onsets == expected_bass

    chord_onsets = {_slot(note.start) for note in _midi_notes(chord_bytes)}
    expected_keys = {
        (bar, event.slot)
        for bar in range(_BARS)
        for event in contract.bar(bar).keys_events
    }
    assert chord_onsets == expected_keys

    parsed_bass = _midi_notes(bass_bytes)
    progression = mt.progression_chords_for_bars(_CHORDS, _BARS)
    for note in parsed_bass:
        bar, slot = _slot(note.start)
        pc = int(note.pitch) % 12
        if slot >= 14 and pc in set(progression[(bar + 1) % _BARS].tone_pcs):
            continue
        assert pc in set(progression[bar].tone_pcs)


def _contract_bass_signature(
    contract: FusionGrooveContract,
    *,
    style: str,
    activity: float,
) -> tuple[tuple[object, ...], ...]:
    _midi, _preview, notes = generate_bass(
        tempo=_TEMPO,
        bar_count=_BARS,
        key="D",
        scale="natural_minor",
        bass_style=style,
        bass_engine="phrase_v2",
        bass_instrument="finger_bass",
        chord_progression=_CHORDS,
        density_bias=activity,
        seed=contract.seed,
        return_performance_notes=True,
        fusion_contract=contract,
    )
    return tuple(
        (
            int(note.bar_index),
            int(note.slot_index),
            int(note.pitch),
            round(float(note.end) - float(note.start), 5),
            int(note.velocity),
        )
        for note in notes
    )


def test_five_fusion_activity_gears_are_five_distinct_permissioned_views() -> None:
    contract = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        covenant_id="anticipated_tumbao",
    )
    signatures = {
        activity: _contract_bass_signature(
            contract,
            style="supportive",
            activity=activity,
        )
        for activity in (-1.0, -0.5, 0.0, 0.5, 1.0)
    }

    assert len(set(signatures.values())) == 5
    assert len(signatures[-1.0]) < len(signatures[-0.5])
    assert len(signatures[-0.5]) <= len(signatures[0.0])
    assert len(signatures[1.0]) > len(signatures[0.5])
    for signature in signatures.values():
        for bar, slot, _pitch, _duration, _velocity in signature:
            plan = contract.bar(int(bar))
            assert int(slot) not in plan.protected_melodic_rest_slots
            assert (
                int(slot) not in {event.slot for event in plan.keys_events}
                or int(slot) in plan.intentional_ensemble_slots
            )


def test_five_fusion_bass_styles_are_audibly_distinct_contract_adapters() -> None:
    contract = build_fusion_contract(
        seed=0,
        bar_count=_BARS,
        covenant_id="anticipated_tumbao",
    )
    signatures = {
        style: _contract_bass_signature(
            contract,
            style=style,
            activity=0.0,
        )
        for style in (
            "supportive",
            "melodic",
            "rhythmic",
            "slap",
            "fusion",
        )
    }

    assert len(set(signatures.values())) == 5
    for signature in signatures.values():
        for bar, slot, _pitch, _duration, _velocity in signature:
            plan = contract.bar(int(bar))
            assert int(slot) not in plan.protected_melodic_rest_slots
            assert (
                int(slot) not in {event.slot for event in plan.keys_events}
                or int(slot) in plan.intentional_ensemble_slots
            )
