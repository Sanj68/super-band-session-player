"""Focused contracts for Fusion's source-groove pocket lock.

The reference must edit the authored phrase only when the lock is engaged.
At full lock it should trade attacks toward the strongest useful kick
anchors, while bars without trustworthy groove evidence retain their
authored onset signature.
"""

from __future__ import annotations

from dataclasses import replace

from app.models.session import GrooveProfile
from app.services.bass_performance import BassPerformanceNote
from app.services.bass_phrase_engine_v2 import generate_bass_phrase_v2
from app.services.bass_quality import count_unsupported_structural_notes
from app.services.conditioning import ConditioningHarmonicBar, UnifiedConditioning


_BARS = 4
_SEED = 42
_CHORDS = ("F", "Gm", "Dm", "Bb")
_WEAK_KICK_SLOTS = (1, 2, 3, 1)
_STRONG_KICK_SLOTS = (11, 13, 11, 11)


def _kick_rows() -> tuple[tuple[float, ...], ...]:
    rows: list[tuple[float, ...]] = []
    for weak_slot, strong_slot in zip(
        _WEAK_KICK_SLOTS,
        _STRONG_KICK_SLOTS,
        strict=True,
    ):
        row = [0.0] * 16
        row[weak_slot] = 0.36
        row[strong_slot] = 0.98
        rows.append(tuple(row))
    return tuple(rows)


def _conditioning(
    *,
    kick_rows: tuple[tuple[float, ...], ...] | None = None,
    confidence: tuple[float, ...] = (1.0, 1.0, 1.0, 1.0),
) -> UnifiedConditioning:
    kicks = kick_rows if kick_rows is not None else _kick_rows()
    zeros = tuple(tuple(0.0 for _ in range(16)) for _ in range(_BARS))
    return UnifiedConditioning(
        tempo=116,
        bar_count=_BARS,
        beat_phase_offset_beats=0,
        beat_phase_confidence=1.0,
        bar_start_anchor_sec=0.0,
        beat_grid_seconds=(),
        bar_starts_seconds=(),
        sections=(),
        groove_profile=GrooveProfile(
            pocket_feel="straight",
            syncopation_score=0.6,
            density_per_bar_estimate=6.0,
            accent_strength=0.8,
            confidence=1.0,
        ),
        harmonic_bars=(),
        tempo_confidence=1.0,
        bar_start_confidence=1.0,
        source_groove_resolution=16,
        source_onset_weight=kicks,
        source_kick_weight=kicks,
        source_snare_weight=zeros,
        # Pressure follows the kick here so the fixture asks only about
        # anchor selection; it does not introduce a competing snare gate.
        source_slot_pressure=kicks,
        source_groove_confidence=confidence,
    )


def _fusion(
    *,
    conditioning: UnifiedConditioning | None,
    lock: float,
) -> tuple[BassPerformanceNote, ...]:
    _midi, _preview, notes = generate_bass_phrase_v2(
        tempo=116,
        bar_count=_BARS,
        key="D",
        scale="natural_minor",
        bass_style="fusion",
        bass_instrument="finger_bass",
        chord_progression=list(_CHORDS),
        conditioning=conditioning,
        seed=_SEED,
        lock_to_groove=lock,
        density_bias=0.5,
        expression_amount=1.0,
        bass_articulation_focus="clean",
        return_performance_notes=True,
    )
    return notes


def _attack_signature(
    notes: tuple[BassPerformanceNote, ...],
    *,
    bars: tuple[int, ...] | None = None,
) -> frozenset[tuple[int, int]]:
    included = set(range(_BARS)) if bars is None else set(bars)
    return frozenset(
        (int(note.bar_index), int(note.slot_index))
        for note in notes
        if note.bar_index is not None
        and note.slot_index is not None
        and int(note.bar_index) in included
    )


def _strong_kick_attack_share(
    notes: tuple[BassPerformanceNote, ...],
    kick_rows: tuple[tuple[float, ...], ...],
) -> float:
    attacks = _attack_signature(notes)
    assert attacks
    strong = sum(
        kick_rows[bar][slot] >= 0.8
        for bar, slot in attacks
    )
    return strong / len(attacks)


def test_fusion_lock_zero_preserves_no_source_authored_attack_signature() -> None:
    authored = _fusion(conditioning=None, lock=0.0)
    unlocked_reference = _fusion(
        conditioning=_conditioning(),
        lock=0.0,
    )

    assert _attack_signature(unlocked_reference) == _attack_signature(authored)


def test_fusion_full_lock_adds_the_strongest_non_downbeat_kick_anchors() -> None:
    authored = _fusion(conditioning=None, lock=0.0)
    locked = _fusion(
        conditioning=_conditioning(),
        lock=1.0,
    )
    authored_attacks = _attack_signature(authored)
    locked_attacks = _attack_signature(locked)
    expected_anchors = frozenset(enumerate(_STRONG_KICK_SLOTS))

    assert expected_anchors.isdisjoint(authored_attacks), (
        "fixture no longer isolates newly introduced groove anchors"
    )
    assert expected_anchors.issubset(locked_attacks - authored_attacks), (
        "full lock must add the strongest kick anchor in every conditioned "
        "bar, not merely remove authored attacks"
    )


def test_fusion_full_lock_improves_strong_kick_affinity() -> None:
    kicks = _kick_rows()
    authored = _fusion(conditioning=None, lock=0.0)
    unlocked_reference = _fusion(
        conditioning=_conditioning(kick_rows=kicks),
        lock=0.0,
    )
    locked = _fusion(
        conditioning=_conditioning(kick_rows=kicks),
        lock=1.0,
    )
    locked_affinity = _strong_kick_attack_share(locked, kicks)

    assert locked_affinity > _strong_kick_attack_share(authored, kicks)
    assert locked_affinity > _strong_kick_attack_share(
        unlocked_reference,
        kicks,
    )


def test_fusion_does_not_condition_zero_confidence_or_zero_map_bars() -> None:
    authored = _fusion(conditioning=None, lock=0.0)
    kicks = list(_kick_rows())
    kicks[2] = tuple(0.0 for _ in range(16))
    partial_reference = _conditioning(
        kick_rows=tuple(kicks),
        confidence=(1.0, 0.0, 1.0, 1.0),
    )
    locked = _fusion(
        conditioning=partial_reference,
        lock=1.0,
    )

    # Bar 1 contains a tempting kick map but explicitly has no confidence;
    # bar 2 claims confidence but has no map. Neither is groove evidence.
    unconditioned_bars = (1, 2)
    assert _attack_signature(
        locked,
        bars=unconditioned_bars,
    ) == _attack_signature(
        authored,
        bars=unconditioned_bars,
    )

    # The neighboring trusted bars prove the lock was active, rather than
    # the whole fixture silently falling back to the authored phrase.
    trusted_anchors = {
        (bar, _STRONG_KICK_SLOTS[bar])
        for bar in (0, 3)
    }
    assert trusted_anchors.issubset(
        _attack_signature(locked) - _attack_signature(authored)
    )


def test_early_groove_anchor_does_not_become_a_dangling_chord_approach() -> None:
    """An answer ending before slot 15 must remain on the confirmed chord."""

    harmonic_bars = (
        ConditioningHarmonicBar(
            bar_index=0,
            root_pc=5,
            target_pcs=(5, 9, 0),
            passing_pcs=(),
            avoid_pcs=(1, 3, 6, 8, 10, 11),
            confidence=1.0,
            source="confirmed_chord_progression",
        ),
        ConditioningHarmonicBar(
            bar_index=1,
            root_pc=7,
            target_pcs=(7, 10, 2),
            passing_pcs=(),
            avoid_pcs=(0, 1, 3, 4, 6, 8, 9, 11),
            confidence=1.0,
            source="confirmed_chord_progression",
        ),
        ConditioningHarmonicBar(
            bar_index=2,
            root_pc=2,
            target_pcs=(2, 5, 9),
            passing_pcs=(),
            avoid_pcs=(0, 1, 3, 4, 6, 7, 8, 10, 11),
            confidence=1.0,
            source="confirmed_chord_progression",
        ),
        ConditioningHarmonicBar(
            bar_index=3,
            root_pc=10,
            target_pcs=(10, 2, 5),
            passing_pcs=(),
            avoid_pcs=(0, 1, 3, 4, 6, 7, 8, 9, 11),
            confidence=1.0,
            source="confirmed_chord_progression",
        ),
    )
    conditioning = replace(
        _conditioning(),
        harmonic_bars=harmonic_bars,
    )
    notes = _fusion(conditioning=conditioning, lock=1.0)

    assert count_unsupported_structural_notes(
        list(notes),
        tempo=116,
        conditioning=conditioning,
        style="fusion",
    ) == 0
