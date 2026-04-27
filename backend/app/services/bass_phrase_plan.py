"""Phrase-plan engine for 4-bar bass behavior (construction only, no harmony)."""

from __future__ import annotations

from dataclasses import dataclass
import random

from app.services.session_context import SessionAnchorContext


@dataclass(frozen=True)
class PhraseBarPlan:
    role: str  # anchor | answer | push | release
    slots: tuple[int, ...]  # sixteenth slots in bar
    tone_path: tuple[int, ...]  # index path into (root, fifth, third)
    register_shift: int  # semitone shift suggestion (-12/0/+12)
    accent_push: float  # rhythmic push weight
    cadence_bias: float  # pull toward root/fifth near cadence
    rest_bias: float  # intentional space preference for this bar role
    sustain_mult: float  # articulation length scalar for this role
    allow_fill: bool  # whether extra fill slot is allowed


_STYLE_MOTIFS: dict[str, tuple[tuple[int, ...], ...]] = {
    "supportive": ((0, 8), (0, 8, 12), (0, 6, 8), (0, 8, 14)),
    "melodic": ((0, 4, 8, 12), (0, 3, 8, 11), (0, 4, 7, 12), (0, 5, 8, 13)),
    "rhythmic": ((0, 6, 10, 14), (0, 4, 10, 14), (0, 3, 8, 12), (0, 5, 8, 13)),
    "slap": ((0, 4, 8, 12, 14), (0, 3, 7, 11, 14), (0, 5, 8, 12, 14)),
    "fusion": ((0, 3, 7, 10, 14), (0, 2, 6, 10, 12), (0, 4, 7, 11, 14), (0, 3, 8, 11, 15)),
}

_STYLE_TONE_DNA: dict[str, tuple[tuple[int, ...], ...]] = {
    # 0=root, 1=fifth, 2=third (mapped later by generator).
    "supportive": ((0, 1, 0, 1), (0, 0, 1, 0), (0, 1, 2, 0)),
    "melodic": ((0, 2, 1, 2), (0, 1, 2, 1), (0, 2, 0, 1)),
    "rhythmic": ((0, 1, 0, 2), (0, 2, 1, 0), (0, 1, 2, 1)),
    "slap": ((0, 1, 0, 1, 2), (0, 2, 1, 0, 1), (0, 1, 2, 1, 0)),
    "fusion": ((0, 1, 2, 1, 0), (0, 2, 1, 2, 0), (0, 1, 2, 0, 1)),
}


def _style_seed_slots(style: str) -> tuple[int, ...]:
    motifs = _STYLE_MOTIFS.get(style, _STYLE_MOTIFS["supportive"])
    return motifs[0]


def _ensure_root(slots: tuple[int, ...]) -> tuple[int, ...]:
    out = sorted(set(int(x) for x in slots if 0 <= int(x) <= 15))
    if 0 not in out:
        out.insert(0, 0)
    return tuple(out)


def _kick_merge(
    slots: tuple[int, ...],
    *,
    context: SessionAnchorContext | None,
    bar: int,
) -> tuple[int, ...]:
    if context is None or context.anchor_lane != "drums":
        return slots
    kick_row = context.kick_slot_weight[bar] if bar < len(context.kick_slot_weight) else ()
    kick_hits = [i for i, v in enumerate(kick_row) if v >= 0.45]
    merged = sorted(set(slots).union(kick_hits[:2]))
    return _ensure_root(tuple(merged))


def _make_cell(base_slots: tuple[int, ...], rng: random.Random, *, style: str) -> tuple[PhraseBarPlan, ...]:
    motifs = _STYLE_MOTIFS.get(style, _STYLE_MOTIFS["supportive"])
    tone_dna = _STYLE_TONE_DNA.get(style, _STYLE_TONE_DNA["supportive"])
    anchor_slots = _ensure_root(motifs[rng.randrange(len(motifs))] if motifs else base_slots)
    answer_slots = _ensure_root(motifs[rng.randrange(len(motifs))] if motifs else base_slots)
    push_slots = _ensure_root(motifs[rng.randrange(len(motifs))] if motifs else base_slots)
    release_slots = _ensure_root((0, 8, 14) if style != "slap" else (0, 8, 12, 14))

    anchor_tones_seed = tone_dna[rng.randrange(len(tone_dna))]
    answer_tones_seed = tone_dna[rng.randrange(len(tone_dna))]
    push_tones_seed = tone_dna[rng.randrange(len(tone_dna))]

    # Bar 1: motif / anchor
    b1_slots = anchor_slots
    b1_tones = anchor_tones_seed
    b1 = PhraseBarPlan(
        role="anchor",
        slots=b1_slots,
        tone_path=b1_tones[: len(b1_slots)],
        register_shift=0,
        accent_push=0.1,
        cadence_bias=0.2,
        rest_bias=0.12,
        sustain_mult=1.04,
        allow_fill=False,
    )

    # Bar 2: answer / variation (modify motif, not random new template)
    b2_shift = 1 if rng.random() < 0.55 else -1
    b2_slots = tuple(min(15, max(0, s + b2_shift)) for s in answer_slots)
    b2_slots = _ensure_root(tuple(sorted(set(b2_slots + (() if 12 in b2_slots else (12,))))))
    b2_tones = tuple((x + 1) % 3 for x in answer_tones_seed)
    b2 = PhraseBarPlan(
        role="answer",
        slots=b2_slots,
        tone_path=b2_tones[: len(b2_slots)],
        register_shift=0,
        accent_push=0.22,
        cadence_bias=0.25,
        rest_bias=0.2,
        sustain_mult=0.98,
        allow_fill=False,
    )

    # Bar 3: push / development (denser syncopation + contour lift)
    dev_extra = (11, 14) if rng.random() < 0.6 else (10, 13)
    b3_slots = _ensure_root(tuple(sorted(set(push_slots + dev_extra))))
    b3_tones = tuple((push_tones_seed[i % len(push_tones_seed)] + (1 if i % 3 == 0 else 0)) % 3 for i in range(max(len(b3_slots), 1)))
    b3 = PhraseBarPlan(
        role="push",
        slots=b3_slots,
        tone_path=b3_tones[: len(b3_slots)],
        register_shift=12 if rng.random() < 0.35 else 0,
        accent_push=0.45,
        cadence_bias=0.35,
        rest_bias=0.08,
        sustain_mult=0.9,
        allow_fill=True,
    )

    # Bar 4: cadence / release (fewer events, cadence landing)
    b4_slots = release_slots
    b4_tones = (0, 1, 0)
    b4 = PhraseBarPlan(
        role="release",
        slots=b4_slots,
        tone_path=b4_tones,
        register_shift=0,
        accent_push=0.08,
        cadence_bias=0.82,
        rest_bias=0.36,
        sustain_mult=1.1,
        allow_fill=False,
    )
    return (b1, b2, b3, b4)


def build_phrase_plan(
    *,
    bar_count: int,
    style: str,
    salt: int,
    context: SessionAnchorContext | None,
) -> list[PhraseBarPlan]:
    rng = random.Random(int(salt) * 7919 + max(1, bar_count) * 104729)
    base_slots = _style_seed_slots(style)
    out: list[PhraseBarPlan] = []
    for cell_start in range(0, max(1, bar_count), 4):
        cell = _make_cell(base_slots, rng, style=style)
        for i, bar_plan in enumerate(cell):
            bar = cell_start + i
            if bar >= bar_count:
                break
            slots = _kick_merge(bar_plan.slots, context=context, bar=bar)
            out.append(
                PhraseBarPlan(
                    role=bar_plan.role,
                    slots=slots,
                    tone_path=bar_plan.tone_path[: len(slots)] or (0,),
                    register_shift=bar_plan.register_shift,
                    accent_push=bar_plan.accent_push,
                    cadence_bias=bar_plan.cadence_bias,
                    rest_bias=bar_plan.rest_bias,
                    sustain_mult=bar_plan.sustain_mult,
                    allow_fill=bar_plan.allow_fill,
                )
            )
    return out
