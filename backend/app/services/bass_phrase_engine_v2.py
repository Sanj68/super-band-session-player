"""Bass Phrase Engine v2: kick-aware phrase planning path."""

from __future__ import annotations

from dataclasses import dataclass, replace
import io
import random
from typing import Any

import pretty_midi

from app.services.bass_candidate_roles import bass_candidate_role_spec
from app.services.bass_performance import BassPerformanceNote, infer_bass_articulations
from app.services.conditioning import (
    UnifiedConditioning,
    has_source_groove,
    has_source_groove_bar,
    source_kick_weight,
    source_slot_pressure,
    source_snare_weight,
)
from app.services.bass_vocabulary.paul_chambers import (
    get_chromatic_approaches,
    get_walking_cell,
    normalize_chord_quality,
)
from app.services.session_context import (
    SessionAnchorContext,
    drum_kick_weight,
    drum_snare_weight,
    slot_pressure,
)
from app.services.style_adapter import BASS_STYLE_ADAPTER
from app.utils import music_theory as mt

_BASS_STYLES = frozenset({"supportive", "melodic", "rhythmic", "slap", "fusion"})
_BASS_INSTRUMENTS = frozenset(
    {
        "finger_bass",
        "fretless_bass",
        "upright_bass",
        "sub_bass",
        "slap_bass",
        "synth_bass",
    }
)
_BASS_PLAYERS = frozenset({"bootsy", "marcus", "pino"}) | BASS_STYLE_ADAPTER.bass_player_ids()


@dataclass(frozen=True)
class PhraseDevelopmentBar:
    """Seeded large-scale composition choices for one bar.

    The style grammar still owns the notes.  This plan makes those notes form
    a deliberate sixteen-bar argument instead of asking every bar to choose a
    fresh cell independently.
    """

    role: str
    arc_role: str
    arc_index: int
    rhythm_family: int
    melodic_code: int
    cell_variant: int
    rhythm_mutations: int
    mutation_code: int
    tone_rotation: int
    octave_slot: int
    register_lift: int
    final_turnaround: bool


_PHRASE_ROLES = ("anchor", "answer", "push", "release")
_ARC_ROLES = ("statement", "variation", "contrast", "return")


def _build_phrase_development_plan(
    *,
    bar_count: int,
    style: str,
    seed: int | None,
    rng: random.Random | object,
    density_bias: float = 0.0,
) -> tuple[PhraseDevelopmentBar, ...]:
    """Write a repeatable A / A' / B / return arc before rendering notes.

    A dedicated RNG keeps the macro composition stable when later rendering
    details consume random numbers.  Fresh generation seeds still explore new
    motif DNA, octave answers, and tone paths.
    """

    if seed is None:
        macro_seed = int(rng.randrange(1 << 30))
    else:
        macro_seed = (
            int(seed)
            ^ 0x5EED5EED
            ^ sum((index + 1) * ord(char) for index, char in enumerate(style))
        )
    macro_rng = random.Random(macro_seed)
    rhythm_family = abs(int(macro_seed)) % 8
    melodic_code = int(
        random.Random(macro_seed ^ 0x4D454C4F).randrange(3 ** 12)
    )
    base_variants = {
        role: macro_rng.randrange(2)
        for role in _PHRASE_ROLES
    }
    phrase_colour = macro_rng.randrange(7)
    phrase_direction = -1 if macro_rng.randrange(2) == 0 else 1
    plan: list[PhraseDevelopmentBar] = []

    for bar in range(max(1, int(bar_count))):
        role_index = bar % 4
        role = _PHRASE_ROLES[role_index]
        arc_index = bar // 4
        arc_role = _ARC_ROLES[arc_index % len(_ARC_ROLES)]
        section_pass = arc_index // len(_ARC_ROLES)

        if arc_role == "contrast":
            cell_variant = 1 - base_variants[role]
        else:
            cell_variant = base_variants[role]

        section_mutations = {
            "statement": 0,
            "variation": 1,
            "contrast": 2,
            "return": 1,
        }[arc_role]
        # Answer and push bars carry the local development; the opening
        # statement stays recognisable and the release deliberately thins.
        local_mutations = (0, 1, 2, 1)[role_index]
        rhythm_mutations = min(
            3,
            section_mutations + local_mutations + min(1, section_pass),
        )
        mutation_code = (
            phrase_colour
            + bar * 5
            + arc_index * 11
            + macro_rng.randrange(17)
        )
        if phrase_direction < 0:
            mutation_code = -mutation_code

        arc_tone_offset = {
            "statement": 0,
            "variation": 1,
            "contrast": 2,
            "return": -1,
        }[arc_role]
        tone_rotation = (
            phrase_colour
            + arc_tone_offset
            + (0, 1, 2, -1)[role_index]
            + section_pass
        )
        # Moving the root-octave answer is the most immediately audible way
        # to stop Fusion regenerations sharing one fixed R-C-R(oct)-C plan.
        octave_hints = (6, 7, 10, 14)
        octave_hint = octave_hints[
            (phrase_colour + role_index + arc_index)
            % len(octave_hints)
        ]
        octave_slot = octave_hint
        style_cells = _STYLE_SLOT_CELLS.get(style)
        if style_cells is not None:
            dense = max(-1.0, min(1.0, float(density_bias)))
            family = (
                "sparse"
                if dense < -0.25
                else "neutral"
                if dense < 0.25
                else "busy"
            )
            authored_cells = style_cells[family].get(
                role,
                style_cells[family]["anchor"],
            )
            authored_cell = authored_cells[
                cell_variant % len(authored_cells)
            ]
            nonzero_authored = [
                slot for slot in authored_cell if slot != 0
            ]
            if nonzero_authored:
                octave_slot = min(
                    nonzero_authored,
                    key=lambda slot: (
                        abs(slot - octave_hint),
                        -slot,
                    ),
                )
        register_lift = (
            5
            if arc_role == "contrast" and role in {"answer", "push"}
            else 5
            if arc_role == "variation" and role == "push" and phrase_colour % 2
            else 0
        )
        final_turnaround = (
            role == "release"
            and arc_index % 2 == 1
        )
        plan.append(
            PhraseDevelopmentBar(
                role=role,
                arc_role=arc_role,
                arc_index=arc_index,
                rhythm_family=rhythm_family,
                melodic_code=melodic_code,
                cell_variant=cell_variant,
                rhythm_mutations=rhythm_mutations,
                mutation_code=mutation_code,
                tone_rotation=tone_rotation,
                octave_slot=octave_slot,
                register_lift=register_lift,
                final_turnaround=final_turnaround,
            )
        )
    return tuple(plan)


def normalize_bass_style(bass_style: str | None) -> str:
    if bass_style is None:
        return "supportive"
    s = str(bass_style).strip().lower()
    return s if s in _BASS_STYLES else "supportive"


def normalize_bass_player(bass_player: str | None) -> str | None:
    if bass_player is None:
        return None
    s = str(bass_player).strip().lower()
    if not s or s in ("none", "off", "null"):
        return None
    return s if s in _BASS_PLAYERS else None


def normalize_bass_instrument(bass_instrument: str | None) -> str:
    if bass_instrument is None:
        return "finger_bass"
    s = str(bass_instrument).strip().lower()
    return s if s in _BASS_INSTRUMENTS else "finger_bass"


def bass_midi_program(bass_instrument: str, bass_style: str) -> int:
    bi = normalize_bass_instrument(bass_instrument)
    if bi == "upright_bass":
        return 32
    if bi == "fretless_bass":
        return 35
    if bi == "slap_bass":
        return 36
    if bi in ("synth_bass", "sub_bass"):
        return 38
    if bass_style == "slap":
        return 36
    return 33


def _pc_to_bass_register(pc: int, *, octave: int = 2, lo: int = 30, hi: int = 62) -> int:
    note = mt.pc_to_midi_note(pc % 12, octave)
    while note < lo:
        note += 12
    while note > hi:
        note -= 12
    return max(lo, min(hi, note))


def _bar_role(bar: int, role_span: int) -> str:
    if role_span <= 2:
        return "anchor" if bar % 2 == 0 else "answer"
    cycle = ("anchor", "push", "anchor", "release")
    return cycle[bar % len(cycle)]


def _kick_guided_slots(ctx: SessionAnchorContext, bar: int) -> list[int]:
    out: list[int] = []
    for s in range(16):
        k = drum_kick_weight(ctx, bar, s)
        if k >= 0.34:
            out.append(s)
        elif s % 4 == 0 and k >= 0.2:
            out.append(s)
    return sorted(set(out))


def _source_guided_slots(conditioning: UnifiedConditioning | None, bar: int) -> list[int]:
    if not has_source_groove_bar(conditioning, bar):
        return []
    out: list[int] = []
    for s in range(16):
        k = source_kick_weight(conditioning, bar, s)
        p = source_slot_pressure(conditioning, bar, s)
        if k >= 0.34 or (s % 4 == 0 and p >= 0.42):
            out.append(s)
        elif s % 4 != 0 and k >= 0.24 and p >= 0.32:
            out.append(s)
    # Downstream phrase grammars take only one or two anchors.  Preserve that
    # budget for the strongest kick evidence instead of whichever qualifying
    # cell happens to occur first in the bar.
    return sorted(
        set(out),
        key=lambda slot: (
            -source_kick_weight(conditioning, bar, slot),
            -source_slot_pressure(conditioning, bar, slot),
            slot,
        ),
    )


def _phrase_slots(role: str, kick_slots: list[int], *, kick_take: int = 3, extra_hits: int = 0) -> list[int]:
    if role == "anchor":
        base = [0, 8]
    elif role == "push":
        base = [0, 6, 10, 14]
    elif role == "release":
        base = [0, 8, 12]
    else:  # answer
        base = [0, 7, 12]
    if kick_slots:
        base.extend(kick_slots[: max(0, int(kick_take))])
    out = sorted(set(x for x in base if 0 <= x <= 15))
    if 0 not in out:
        out.insert(0, 0)
    max_hits = (4 if role in ("anchor", "release") else 5) + max(0, int(extra_hits))
    return out[:max_hits]


# A style is a phrase grammar, not a density preset.  The generic engine used
# to call ``_phrase_slots`` for every style, which made Supportive, Melodic,
# Rhythmic, Slap, and Fusion compositionally identical.  These cells keep the
# shared four-bar role rail while giving each non-supportive style its own
# onset language.  Activity selects a different cell family; the busy cells
# deliberately reposition hits instead of merely containing the neutral cell.
_STYLE_SLOT_CELLS: dict[
    str,
    dict[str, dict[str, tuple[tuple[int, ...], ...]]],
] = {
    "melodic": {
        "sparse": {
            "anchor": ((0, 6, 12), (0, 5, 11)),
            "push": ((0, 5, 10, 14), (0, 3, 9, 13)),
            "answer": ((0, 4, 10), (0, 6, 12)),
            "release": ((0, 4, 12), (0, 7, 14)),
        },
        "neutral": {
            "anchor": ((0, 4, 8, 12), (0, 3, 8, 11)),
            "push": ((0, 2, 5, 8, 11, 14), (0, 3, 6, 10, 13)),
            "answer": ((0, 4, 7, 10, 14), (0, 3, 8, 12, 15)),
            "release": ((0, 4, 8, 14), (0, 5, 10, 14)),
        },
        "busy": {
            "anchor": ((0, 2, 5, 8, 10, 13), (0, 3, 6, 9, 12, 15)),
            "push": ((0, 1, 4, 6, 9, 11, 14), (0, 2, 5, 7, 10, 12, 15)),
            "answer": ((0, 2, 6, 9, 11, 13, 15), (0, 3, 5, 8, 10, 14, 15)),
            "release": ((0, 2, 5, 8, 11, 15), (0, 3, 6, 10, 13, 15)),
        },
    },
    "rhythmic": {
        "sparse": {
            "anchor": ((0, 7, 12), (0, 6, 14)),
            "push": ((0, 3, 10, 14), (0, 6, 11, 15)),
            "answer": ((0, 5, 13), (0, 7, 14)),
            "release": ((0, 4, 14), (0, 6, 12)),
        },
        "neutral": {
            "anchor": ((0, 3, 8, 11), (0, 4, 10, 14)),
            "push": ((0, 2, 6, 10, 14), (0, 3, 7, 11, 14)),
            "answer": ((0, 5, 8, 13), (0, 6, 10, 14)),
            "release": ((0, 4, 10, 14), (0, 6, 8, 14)),
        },
        "busy": {
            "anchor": ((0, 2, 5, 9, 12, 15), (0, 3, 6, 10, 13, 15)),
            "push": ((0, 1, 4, 7, 10, 12, 15), (0, 2, 5, 8, 11, 13, 15)),
            "answer": ((0, 2, 6, 9, 11, 14, 15), (0, 3, 5, 8, 10, 13, 15)),
            "release": ((0, 2, 5, 9, 12, 15), (0, 3, 6, 10, 13, 15)),
        },
    },
    "slap": {
        "sparse": {
            "anchor": ((0, 2, 7, 14), (0, 3, 6, 13)),
            "push": ((0, 3, 7, 11, 14), (0, 2, 6, 10, 15)),
            "answer": ((0, 2, 8, 15), (0, 3, 7, 14)),
            "release": ((0, 2, 8, 14), (0, 3, 10, 15)),
        },
        "neutral": {
            "anchor": ((0, 2, 6, 8, 10, 14), (0, 3, 7, 8, 11, 15)),
            "push": ((0, 3, 5, 7, 10, 11, 14), (0, 2, 6, 9, 10, 13, 15)),
            "answer": ((0, 2, 7, 8, 11, 15), (0, 3, 6, 8, 10, 14)),
            "release": ((0, 3, 6, 8, 12, 14), (0, 2, 7, 10, 12, 15)),
        },
        "busy": {
            "anchor": ((0, 1, 3, 6, 7, 10, 11, 15), (0, 2, 4, 7, 9, 12, 14, 15)),
            "push": ((0, 2, 3, 5, 8, 10, 13, 15), (0, 1, 4, 6, 9, 11, 14, 15)),
            "answer": ((0, 1, 4, 7, 9, 11, 14, 15), (0, 2, 5, 6, 10, 12, 13, 15)),
            "release": ((0, 2, 5, 7, 10, 13, 14, 15), (0, 1, 4, 6, 9, 12, 14, 15)),
        },
    },
    "fusion": {
        "sparse": {
            "anchor": ((0, 3, 10, 14), (0, 5, 11, 15)),
            "push": ((0, 2, 7, 13, 15), (0, 3, 9, 12, 15)),
            "answer": ((0, 3, 9, 15), (0, 5, 10, 14)),
            "release": ((0, 4, 10, 15), (0, 3, 8, 14)),
        },
        "neutral": {
            "anchor": ((0, 3, 6, 9, 11, 14), (0, 2, 7, 10, 13, 15)),
            "push": ((0, 2, 5, 7, 10, 13, 15), (0, 3, 6, 8, 11, 14, 15)),
            "answer": ((0, 3, 7, 9, 12, 15), (0, 2, 6, 10, 13, 15)),
            "release": ((0, 4, 7, 10, 14, 15), (0, 3, 6, 9, 13, 15)),
        },
        "busy": {
            "anchor": ((0, 2, 5, 7, 9, 12, 14, 15), (0, 1, 4, 6, 10, 11, 13, 15)),
            "push": ((0, 1, 3, 6, 8, 10, 13, 15), (0, 2, 4, 7, 9, 11, 14, 15)),
            "answer": ((0, 3, 4, 7, 9, 11, 14, 15), (0, 1, 5, 6, 10, 12, 14, 15)),
            "release": ((0, 2, 5, 8, 10, 13, 15), (0, 1, 4, 7, 9, 12, 15)),
        },
    },
}


# Fusion's busy grammar is written as a sixteen-bar form, not sixteen
# independent bar choices.  The two repeated rows deliberately recur under a
# different chord/role, so the hook is recognisable without copying any
# same-harmony bar.  Counts follow an 8 / 8 / 8 / 5 local arc: statement,
# answer, push, then space.
_FUSION_MACRO_ROWS: tuple[tuple[int, ...], ...] = (
    # A — statement.
    (0, 2, 5, 7, 9, 12, 14, 15),
    (0, 3, 4, 7, 9, 11, 14, 15),
    (0, 1, 3, 6, 8, 10, 13, 15),
    (0, 2, 8, 13, 15),
    # A' — bounded development, including one cross-harmony hook return.
    (0, 2, 6, 8, 10, 12, 14, 15),
    (0, 2, 5, 7, 9, 12, 14, 15),
    (0, 2, 4, 6, 9, 10, 13, 15),
    (0, 3, 8, 12, 15),
    # B — contrast while retaining enough common accents to remain related.
    (0, 1, 4, 7, 10, 13, 14, 15),
    (0, 1, 3, 6, 8, 10, 13, 15),
    (0, 2, 4, 8, 11, 12, 13, 15),
    (0, 1, 8, 10, 15),
    # Return — answers B, recalls the head, and opens the final cadence.
    (0, 3, 6, 7, 10, 11, 14, 15),
    (0, 1, 3, 6, 8, 11, 14, 15),
    (0, 5, 7, 8, 11, 12, 13, 15),
    (0, 4, 10, 11, 15),
)


# Eight bounded rhythmic dialects prevent Regenerate from replaying one grid.
# Each triple remaps the three off-sixteenths inside a beat; quarter-note
# anchors never move, and the same bijection is applied to the whole part.
# That preserves the written A/A'/B/return relationships while changing the
# phrase's syncopated accent vocabulary.
_FUSION_RHYTHM_FAMILY_MAPS: tuple[
    tuple[tuple[int, int, int], ...],
    ...,
] = (
    ((1, 2, 3), (1, 2, 3), (1, 2, 3), (1, 2, 3)),
    ((3, 1, 2), (1, 3, 2), (1, 2, 3), (2, 3, 1)),
    ((1, 3, 2), (2, 1, 3), (1, 2, 3), (2, 1, 3)),
    ((2, 3, 1), (2, 1, 3), (2, 1, 3), (1, 3, 2)),
    ((3, 1, 2), (3, 2, 1), (2, 1, 3), (2, 1, 3)),
    ((2, 3, 1), (3, 2, 1), (3, 1, 2), (3, 2, 1)),
    ((2, 1, 3), (3, 1, 2), (2, 1, 3), (3, 2, 1)),
    ((1, 2, 3), (1, 2, 3), (3, 1, 2), (2, 1, 3)),
)


# Positions address the first attack of beats 2–4 in each local bar:
# ``role_index * 3 + beat_index``.  Every seed writes its own twelve-digit
# chord-tone motif; these masks develop that motif by a controlled number of
# tones per four-bar section.  Pairwise section similarity stays in the
# useful middle ground instead of becoming either a literal copy or a rewrite.
_FUSION_ARC_MELODY_SHIFTS: dict[str, frozenset[int]] = {
    "statement": frozenset(),
    "variation": frozenset({0, 4, 8, 10}),
    "contrast": frozenset({0, 1, 3, 4, 6, 8, 11}),
    "return": frozenset({1, 4, 7, 8, 10}),
}


def _fusion_macro_slots(
    development: PhraseDevelopmentBar,
) -> tuple[int, ...]:
    """Return one authored Fusion bar in its seed-stable rhythm dialect."""

    role_index = _PHRASE_ROLES.index(development.role)
    row_index = (int(development.arc_index) % 4) * 4 + role_index
    source = _FUSION_MACRO_ROWS[row_index]
    family = _FUSION_RHYTHM_FAMILY_MAPS[
        int(development.rhythm_family) % len(_FUSION_RHYTHM_FAMILY_MAPS)
    ]
    remapped: list[int] = []
    for slot in source:
        beat, within_beat = divmod(int(slot), 4)
        if within_beat == 0:
            remapped.append(int(slot))
        else:
            remapped.append(
                beat * 4 + family[beat][within_beat - 1]
            )
    return tuple(sorted(set(remapped)))


def _fusion_structural_slots(
    slots: set[int] | list[int] | tuple[int, ...],
    *,
    role: str,
) -> set[int]:
    """Return the authored attacks that carry a Fusion bar's sentence."""

    ordered = sorted({int(slot) for slot in slots})
    if not ordered:
        return set()
    beat_leads = {
        min(slot for slot in ordered if slot // 4 == beat)
        for beat in {slot // 4 for slot in ordered}
    }
    protected = {0, *beat_leads}
    if role == "release":
        protected.add(max(ordered))
    return protected


def _fusion_authored_octave_slot(
    slots: set[int] | list[int] | tuple[int, ...],
    *,
    development: PhraseDevelopmentBar,
) -> int | None:
    """Locate the authored inner answer that carries Fusion's octave pop."""

    ordered = sorted({int(slot) for slot in slots})
    structural = _fusion_structural_slots(
        ordered,
        role=development.role,
    )
    candidates = [
        slot
        for slot in ordered
        if slot != 0 and slot not in structural
    ]
    if not candidates:
        return None
    if int(development.octave_slot) in candidates:
        return int(development.octave_slot)
    return min(
        candidates,
        key=lambda candidate: (
            abs(candidate - int(development.octave_slot)),
            -candidate,
        ),
    )


def _adapt_fusion_macro_to_kicks(
    slots: set[int],
    *,
    kick_slots: list[int],
    role: str,
    protected_slots: tuple[int, ...] = (),
) -> set[int]:
    """Trade inner Fusion attacks for the supplied new groove anchors.

    Busy macro rows already sit at their hit ceiling, so unioning kick slots
    would later discard every groove addition. A replacement keeps the
    authored density/form while making a changed drum feel materially alter
    each bar. Beat leads and the release cadence stay protected because they
    carry the melodic sentence.
    """

    adapted = set(slots)
    authored_structural = _fusion_structural_slots(
        adapted,
        role=role,
    )
    inserted: set[int] = set()
    for kick_slot in (int(slot) for slot in kick_slots):
        if kick_slot in adapted:
            inserted.add(kick_slot)
            continue

        structural = (
            authored_structural
            | _fusion_structural_slots(adapted, role=role)
        )
        protected = (
            structural
            | inserted
            | {int(slot) for slot in protected_slots}
        )
        removable = [
            slot for slot in sorted(adapted)
            if slot not in protected
        ]
        # A constrained release may have only one inner answer. In that case
        # the changed kick is more important than retaining the octave pop;
        # structural leads and prior groove anchors remain inviolable.
        if not removable:
            removable = [
                slot for slot in sorted(adapted)
                if slot not in structural and slot not in inserted
            ]
        if not removable:
            break
        source = min(
            removable,
            key=lambda slot: (
                0 if slot // 4 == kick_slot // 4 else 1,
                abs(slot - kick_slot),
                slot,
            ),
        )
        adapted.remove(source)
        adapted.add(kick_slot)
        inserted.add(kick_slot)
    return adapted


def _develop_phrase_slots(
    slots: list[int] | tuple[int, ...],
    *,
    style: str,
    dense: float,
    development: PhraseDevelopmentBar | None,
    protected_slots: tuple[int, ...] = (),
) -> list[int]:
    """Apply bounded A/A'/B/return edits to an authored style cell.

    This deliberately preserves most of the original rhythm.  The benchmark
    already shows Session Player's onset variation in ACE's useful range; the
    job here is to make that repetition intentional and to stop exact
    four-bar copies, not to randomise every attack.
    """

    ordered = sorted({int(slot) for slot in slots if 0 <= int(slot) <= 15})
    if 0 not in ordered:
        ordered.insert(0, 0)
    if development is None or len(ordered) <= 1 or dense <= -0.75:
        return ordered

    protected = {0, *(int(slot) for slot in protected_slots)}
    mutation_count = min(
        max(0, int(development.rhythm_mutations)),
        max(0, len(ordered) - 1),
    )
    code = abs(int(development.mutation_code))
    direction = -1 if int(development.mutation_code) < 0 else 1
    shift_sizes = {
        "supportive": (2, 1, 2),
        "melodic": (1, 2, 1),
        "rhythmic": (1, 2, 1),
        "slap": (1, 1, 2),
        "fusion": (1, 2, 1),
    }.get(style, (1, 2, 1))

    # A release is authored as space, not as another full cell with a softer
    # velocity. Retain the one, one structural/kick anchor, and a late cadence.
    if development.role == "release":
        target_hits = max(
            2,
            len(ordered) - (
                2 if dense >= 0.25 and len(ordered) >= 5 else 1
            ),
        )
        late_slot = (
            15
            if style in {"fusion", "slap"}
            or (
                development.final_turnaround
                and style in {"melodic", "rhythmic"}
            )
            else 14
        )
        selected = {0, late_slot}
        protected_nonzero = [
            slot for slot in ordered if slot in protected and slot != 0
        ]
        if protected_nonzero:
            selected.add(protected_nonzero[code % len(protected_nonzero)])
        inner = [
            slot
            for slot in ordered
            if slot not in selected and slot != 0
        ]
        # Even spacing retains the phrase's identity better than simply
        # keeping the first N attacks.
        while len(selected) < target_hits and inner:
            index = (
                code + len(selected) * max(1, len(inner) // 2)
            ) % len(inner)
            selected.add(inner.pop(index))
        if len(selected) > target_hits:
            required = {0, late_slot}
            extras = [
                slot
                for slot in sorted(selected)
                if slot not in required
            ]
            selected = required | set(extras[: target_hits - len(required)])
        return sorted(selected)

    developed = set(ordered)
    mutable = [
        slot
        for slot in ordered
        if slot not in protected and slot != 0
    ]
    for edit_index in range(mutation_count):
        if not mutable:
            break
        source_index = (code + edit_index * 3) % len(mutable)
        source = mutable.pop(source_index)
        shift = shift_sizes[
            (code + edit_index) % len(shift_sizes)
        ]
        signed_shift = direction * shift * (
            -1 if edit_index % 2 else 1
        )
        candidate = source + signed_shift
        alternatives = (
            candidate,
            source - signed_shift,
            source + direction,
            source - direction,
        )
        replacement = next(
            (
                slot
                for slot in alternatives
                if 1 <= slot <= 15
                and slot not in developed
                and slot not in protected
            ),
            None,
        )
        if replacement is None:
            continue
        developed.remove(source)
        developed.add(replacement)

    if development.role == "push" and dense >= -0.25:
        push_cells = {
            "supportive": (3, 6, 11, 14),
            "melodic": (2, 5, 11, 15),
            "rhythmic": (3, 7, 10, 14),
            "slap": (2, 5, 10, 15),
            "fusion": (1, 5, 10, 14),
        }
        candidates = push_cells.get(style, push_cells["supportive"])
        extra = next(
            (
                candidates[(code + offset) % len(candidates)]
                for offset in range(len(candidates))
                if candidates[(code + offset) % len(candidates)]
                not in developed
            ),
            None,
        )
        if extra is not None:
            removable = [
                slot
                for slot in sorted(developed)
                if slot not in protected and slot not in {0, extra}
            ]
            if removable:
                developed.remove(removable[code % len(removable)])
            developed.add(extra)

    return sorted(developed)


def _style_phrase_slots(
    style: str,
    role: str,
    *,
    kick_slots: list[int],
    dense: float,
    bar: int,
    candidate_role: str | None,
    rng: random.Random | object,
    development: PhraseDevelopmentBar | None = None,
) -> list[int]:
    """Choose a seeded style cell, then apply a bounded candidate-role edit.

    Candidate roles are comparison lenses, not replacement phrase grammars.
    The selected style cell therefore remains the source material: Pocket
    thins that cell, Rhythmic swaps in one syncopation, and the Harmonic /
    Performance roles leave its onset frame intact for their later pitch and
    performance edits.
    """

    cells = _STYLE_SLOT_CELLS.get(style)
    if cells is None:
        return _phrase_slots(role, kick_slots)

    if dense <= -0.75:
        return [0]
    activity_tier = (
        1
        if dense <= -0.25
        else 2
        if dense < 0.25
        else 3
        if dense < 0.75
        else 4
    )
    family = (
        "sparse"
        if activity_tier == 1
        else "neutral"
        if activity_tier == 2
        else "busy"
    )
    role_cells = cells[family].get(role, cells[family]["anchor"])
    uses_fusion_macro = (
        style == "fusion"
        and family == "busy"
        and development is not None
    )
    # Mix a deterministic bar phase with the generation RNG. Fresh Generate
    # calls therefore produce new phrases, while a stored seed is repeatable.
    if uses_fusion_macro:
        cell = _fusion_macro_slots(development)
    elif development is None:
        cell_index = (
            bar + int(rng.randrange(len(role_cells)))
        ) % len(role_cells)
        cell = role_cells[cell_index]
    else:
        cell_index = int(development.cell_variant) % len(role_cells)
        cell = role_cells[cell_index]
    slots = set(cell)

    # Style remains the lead author. Groove evidence may contribute a couple
    # of anchors, but cannot collapse every style back into the same kick copy.
    kick_take = 1 if style == "melodic" else 2
    useful_kicks = [
        slot
        for slot in kick_slots
        if slot != 0 and (
            slot % 4 != 0
            or style in {"rhythmic", "fusion"}
        )
    ]
    if uses_fusion_macro:
        authored_octave_slot = _fusion_authored_octave_slot(
            cell,
            development=development,
        )
        slots = _adapt_fusion_macro_to_kicks(
            slots,
            kick_slots=useful_kicks[:kick_take],
            role=role,
            protected_slots=(
                (authored_octave_slot,)
                if authored_octave_slot is not None
                else ()
            ),
        )
    else:
        slots.update(useful_kicks[:kick_take])
        slots = set(
            _develop_phrase_slots(
                sorted(slots),
                style=style,
                dense=dense,
                development=development,
                protected_slots=tuple(useful_kicks[:1]),
            )
        )

    max_hits = {
        "melodic": 4 if family == "sparse" else 6 if family == "neutral" else 7,
        "rhythmic": 4 if family == "sparse" else 5 if family == "neutral" else 7,
        "slap": 5 if family == "sparse" else 7 if family == "neutral" else 8,
        "fusion": 5 if family == "sparse" else 7 if family == "neutral" else 8,
    }[style]

    if activity_tier == 4:
        # Lead Activity is a developed phrase, not Busy with the same ceiling.
        # Retire one authored inner attack, then add three style-specific
        # sixteenth-note answers. This makes the top two producer settings
        # audibly different in both density and placement.
        development_cells = {
            "melodic": ((1, 6, 13, 15), (2, 5, 11, 14)),
            "rhythmic": ((1, 5, 10, 14), (2, 6, 9, 15)),
            "slap": ((1, 4, 9, 13), (2, 5, 10, 14)),
            "fusion": ((1, 4, 8, 13), (2, 5, 10, 14)),
        }
        lead_development = development_cells[style][bar % 2]
        removable = [
            slot
            for slot in cell
            if slot != 0 and slot % 4 == 0 and slot not in useful_kicks[:1]
        ]
        if not removable:
            removable = [
                slot
                for slot in cell[1:-1]
                if slot not in useful_kicks[:1]
            ]
        if removable:
            slots.discard(removable[bar % len(removable)])
        for slot in lead_development:
            if slot not in slots:
                slots.add(slot)
            if len(slots) >= len(cell) + 2:
                break
        max_hits += 1 if style == "melodic" else 2
        if uses_fusion_macro:
            # Lead is exactly two attacks beyond the authored macro row,
            # independent of whether a source kick collided with one of the
            # development answers. Fill deterministically so changing the
            # groove changes placement, not the Activity control's density.
            target_hits = min(max_hits, len(cell) + 2)
            fill_order = (
                1, 3, 5, 7, 9, 11, 13, 15,
                2, 6, 10, 14, 4, 8, 12,
            )
            for slot in fill_order:
                if len(slots) >= target_hits:
                    break
                slots.add(slot)
            if len(slots) > target_hits:
                protected = (
                    _fusion_structural_slots(cell, role=role)
                    | set(useful_kicks[:kick_take])
                )
                removable = [
                    slot
                    for slot in sorted(slots, reverse=True)
                    if slot not in protected
                ]
                while len(slots) > target_hits and removable:
                    slots.remove(removable.pop(0))

    if candidate_role == "pocket_keeper":
        # A high Activity setting can keep Pocket in the busy family even
        # after its density delta. Explicitly thin the authored style cell so
        # the role remains a purposeful alternative instead of saturating at
        # the same eight-hit ceiling as every other role.
        target_hits = max(3, min(len(cell), max_hits - 2))
        selected = {0}
        if useful_kicks:
            selected.add(useful_kicks[0])
        if target_hits > 1:
            last = len(cell) - 1
            even_indexes = {
                int(round(index * last / (target_hits - 1)))
                for index in range(target_hits)
            }
            selected.update(cell[index] for index in sorted(even_indexes))
        for slot in cell:
            if len(selected) >= target_hits:
                break
            selected.add(slot)
        if len(selected) > target_hits:
            removable = [
                slot
                for slot in sorted(selected, reverse=True)
                if slot != 0 and slot not in useful_kicks[:1]
            ]
            while len(selected) > target_hits and removable:
                selected.remove(removable.pop(0))
        return sorted(selected)

    if candidate_role == "rhythmic_alternative":
        # At high Activity the style cell already reaches its hit ceiling, so
        # merely adding sync slots is a no-op. Swap exactly one authored inner
        # attack for a role-specific syncopation while retaining the rest of
        # the selected style grammar.
        sync_candidates = (
            (3, 7, 10, 14)
            if bar % 2 == 0
            else (2, 6, 11, 14)
        )
        injected = next((slot for slot in sync_candidates if slot not in slots), None)
        if injected is not None:
            slots.add(injected)
            if len(slots) > max_hits:
                removable = [
                    slot
                    for slot in reversed(cell)
                    if (
                        slot in slots
                        and slot != 0
                        and slot != injected
                        and slot % 4 == 0
                    )
                ]
                if not removable:
                    removable = [
                        slot
                        for slot in reversed(cell)
                        if slot in slots and slot != 0 and slot != injected
                    ]
                if removable:
                    slots.remove(removable[0])

    ordered = sorted(x for x in slots if 0 <= x <= 15)
    if 0 not in ordered:
        ordered.insert(0, 0)
    if len(ordered) <= max_hits:
        return ordered

    # Retain the grammar's own accents before any merged kick extras.
    own = [slot for slot in cell if slot in ordered]
    merged = [slot for slot in ordered if slot not in own]
    return sorted((own + merged)[:max_hits])


def _nearest_register_pitch(
    pc: int,
    center: int,
    *,
    lo: int = 30,
    hi: int = 67,
) -> int:
    options = [
        _pc_to_bass_register(pc, octave=octave, lo=lo, hi=hi)
        for octave in (1, 2, 3, 4)
    ]
    return min(options, key=lambda pitch: (abs(pitch - center), pitch))


def _style_register_bounds(
    instrument_family: str,
    *,
    style: str,
) -> tuple[int, int]:
    """Return a credible composition register for one bass family.

    Renderers may expose a wider playable range, but Phrase-v2 should not use
    that whole range as ordinary phrasing space.  In particular an upright
    Fusion line should feel like an assertive bassist, not alternate between
    the bottom and top of the keyboard.
    """

    if instrument_family == "upright_bass":
        return 32, 58
    if instrument_family in {"sub_bass", "synth_bass"}:
        return 28, 52
    if instrument_family == "fretless_bass":
        return 32, 62
    if style == "slap":
        return 30, 64
    return 30, 62


def _fusion_pitch_role(
    *,
    slot: int,
    bar_slots: tuple[int, ...],
    development: PhraseDevelopmentBar | None,
    authored_structural_slots: tuple[int, ...] = (),
    authored_bar_slots: tuple[int, ...] = (),
) -> str:
    """Return a slot-stable Fusion role unaffected by inserted kick hits."""

    if slot == 0:
        return "root"
    structural_slots = {
        int(candidate)
        for candidate in authored_structural_slots
        if int(candidate) in bar_slots
    }
    if not structural_slots:
        structural_slots = _fusion_structural_slots(
            bar_slots,
            role=development.role if development is not None else "",
        )
    cadence_slot = (
        max(structural_slots)
        if structural_slots
        else max(bar_slots) if bar_slots else None
    )
    if (
        development is not None
        and development.role == "release"
        and cadence_slot is not None
        and slot == cadence_slot
    ):
        return "cadence"
    if development is not None:
        surviving_authored = [
            int(candidate)
            for candidate in (
                authored_bar_slots if authored_bar_slots else bar_slots
            )
            if int(candidate) != 0 and int(candidate) in bar_slots
        ]
        # Keep the first attack in each beat available to carry the seeded
        # melodic skeleton. The octave pop still lands on the nearest inner
        # authored answer. A newly inserted kick therefore changes the groove
        # without stealing the macro's octave role.
        octave_candidates = [
            candidate
            for candidate in surviving_authored
            if candidate not in structural_slots
        ]
        if not authored_bar_slots:
            octave_candidates = octave_candidates or surviving_authored
        octave_slot = (
            development.octave_slot
            if development.octave_slot in octave_candidates
            else min(
                octave_candidates,
                key=lambda candidate: (
                    abs(candidate - development.octave_slot),
                    -candidate,
                ),
            )
            if octave_candidates
            else None
        )
        if octave_slot is not None and slot == octave_slot:
            return "octave_root"
    rotation = development.tone_rotation if development is not None else 0
    return (
        "colour_a"
        if (slot // 2 + rotation) % 2 == 0
        else "colour_b"
    )


def _nearest_pc_to_target(
    pitch_classes: list[int],
    target_pc: int,
) -> int:
    return min(
        (int(pc) % 12 for pc in pitch_classes),
        key=lambda pc: (
            min((pc - target_pc) % 12, (target_pc - pc) % 12),
            pc,
        ),
    )


def _pick_style_pitch(
    style: str,
    *,
    slot: int,
    event_index: int,
    role: str,
    root_pc: int,
    stable_pcs: list[int],
    passing_pcs: list[int],
    avoid_pcs: list[int],
    conf: float,
    previous_pitch: int | None,
    expression_amount: float,
    density_bias: float,
    candidate_role: str | None,
    scale_pcs: set[int],
    instrument_family: str,
    confirmed_chart: bool,
    rng: random.Random | object,
    development: PhraseDevelopmentBar | None = None,
    bar_slots: tuple[int, ...] = (),
    next_root_pc: int | None = None,
    authored_structural_slots: tuple[int, ...] = (),
    authored_bar_slots: tuple[int, ...] = (),
) -> int:
    """Map one style cell into a characteristic, harmonically safe contour."""

    if style in {"supportive", "rhythmic"} or candidate_role is not None:
        return _pick_pitch(
            slot,
            role,
            root_pc=root_pc,
            stable_pcs=stable_pcs,
            passing_pcs=passing_pcs,
            avoid_pcs=avoid_pcs,
            conf=conf,
            candidate_role=candidate_role,
            previous_pitch=previous_pitch,
            scale_pcs=scale_pcs,
            rng=rng,
        )

    register_lo, register_hi = _style_register_bounds(
        instrument_family,
        style=style,
    )
    character = max(0.0, min(1.0, float(expression_amount)))
    character_tier = min(4, int(character * 4.0 + 1.0e-9))
    root_pitch = _nearest_register_pitch(
        root_pc,
        40,
        lo=register_lo,
        hi=min(register_hi, 55),
    )
    if slot == 0:
        if (
            style == "fusion"
            and character_tier >= 3
            and development is not None
        ):
            # A four-bar Fusion sentence proposes a hand position by function:
            # low statement, lifted answer, pushed peak, mid-register release.
            # Boundary voice-leading may select the adjacent root octave when
            # that avoids a disconnected jump. Resetting every bar to one low
            # root made the old Bold path climb and crash at all 15 boundaries.
            role_center = {
                "anchor": root_pitch,
                "answer": root_pitch + 12,
                "push": root_pitch + 19,
                "release": root_pitch + 12,
            }.get(development.role, root_pitch)
            role_pitch = _nearest_register_pitch(
                root_pc,
                role_center,
                lo=register_lo,
                hi=register_hi,
            )
            if (
                previous_pitch is not None
                and abs(role_pitch - previous_pitch) > 7
            ):
                role_pitch = _nearest_register_pitch(
                    root_pc,
                    previous_pitch,
                    lo=register_lo,
                    hi=register_hi,
                )
            return role_pitch
        if (
            style == "fusion"
            and previous_pitch is not None
            and character_tier >= 2
        ):
            return _nearest_register_pitch(
                root_pc,
                previous_pitch,
                lo=register_lo,
                hi=register_hi,
            )
        return root_pitch

    avoid = set(avoid_pcs)
    safe_stable = [
        int(pc) % 12
        for pc in stable_pcs
        if int(pc) % 12 not in avoid
    ]
    safe_passing = [
        int(pc) % 12
        for pc in passing_pcs
        if int(pc) % 12 not in avoid and int(pc) % 12 in scale_pcs
    ]
    color = [pc for pc in safe_stable if pc != root_pc] or [root_pc]
    activity = max(-1.0, min(1.0, float(density_bias)))

    if style == "slap":
        # Low thumb statements alternate with guaranteed octave pops. Quiet
        # in-between events remain root/fifth candidates for ghost shaping.
        if event_index % 3 == 2 or slot in (6, 7, 14, 15):
            octave_root = root_pitch + 12
            if octave_root <= register_hi:
                return octave_root
        if event_index % 2 == 0 and color:
            return _nearest_register_pitch(
                color[(event_index // 2) % len(color)],
                previous_pitch if previous_pitch is not None else root_pitch,
                lo=register_lo,
                hi=min(register_hi, 58),
            )
        return root_pitch

    if style == "melodic":
        pool = list(color)
        if safe_passing and character + max(0.0, activity) >= 0.85 and slot % 4 != 0:
            pool.extend(safe_passing)
        target_pc = pool[(event_index + (1 if role == "push" else 0)) % len(pool)]
        center = previous_pitch if previous_pitch is not None else root_pitch
        return _nearest_register_pitch(
            target_pc,
            center,
            lo=max(32, register_lo),
            hi=register_hi,
        )

    # Fusion is a bass-lead voice. Its pitch intent is attached to authored
    # sixteenth slots rather than event indexes, so a kick overlay or gated
    # rest cannot revoice every subsequent note in the bar.
    fusion_role = _fusion_pitch_role(
        slot=slot,
        bar_slots=bar_slots,
        development=development,
        authored_structural_slots=authored_structural_slots,
        authored_bar_slots=authored_bar_slots,
    )
    tone_rotation = (
        development.tone_rotation
        if development is not None
        else 0
    )
    register_lift = (
        development.register_lift
        if development is not None
        else 0
    )
    if fusion_role == "cadence":
        cadence_pool = safe_stable or [root_pc]
        ordinary_target_pc = _nearest_pc_to_target(
            cadence_pool,
            next_root_pc if next_root_pc is not None else root_pc,
        )
        target_pc = ordinary_target_pc
        if development is not None and development.final_turnaround:
            # The midpoint/final turnaround answers the ordinary bar-4/12
            # approach with another safe chord tone.  This makes the four
            # releases develop instead of landing on one identical pitch
            # class every time.
            alternatives = [
                pc for pc in cadence_pool
                if int(pc) % 12 != int(ordinary_target_pc) % 12
            ]
            if alternatives:
                target_pc = alternatives[
                    (
                        abs(int(development.mutation_code))
                        + int(development.tone_rotation)
                    )
                    % len(alternatives)
                ]
        center = (
            root_pitch
            if development is None or not development.final_turnaround
            else root_pitch - 5
        )
        cadence_pitch = _nearest_register_pitch(
            target_pc,
            center,
            lo=register_lo,
            hi=min(register_hi, root_pitch + 12),
        )
        terminal_return = bool(
            development is not None
            and development.arc_role == "return"
            and development.final_turnaround
        )
        if (
            previous_pitch is not None
            and abs(cadence_pitch - previous_pitch) > 7
            and not terminal_return
        ):
            cadence_pitch = _nearest_register_pitch(
                target_pc,
                previous_pitch,
                lo=register_lo,
                hi=register_hi,
            )
        return cadence_pitch

    # A confirmed chart keeps ordinary phrase attacks on its chord tones;
    # passing tones belong to explicitly modelled transitions, not the
    # structural pitch pool.
    if character_tier <= 1:
        # The first two Character gears both stay in the low hand position,
        # but Subtle travels through the full chord while Restrained mostly
        # alternates root and one stable colour.
        if character_tier == 0:
            target_pc = (
                color[tone_rotation % len(color)]
                if fusion_role in {"colour_b", "octave_root"}
                else root_pc
            )
            contour_hi = min(register_hi, root_pitch + 10)
        else:
            restrained_pool = [root_pc, *color]
            target_pc = restrained_pool[
                (
                    slot // 2
                    + tone_rotation
                    + (1 if role == "push" else 0)
                )
                % len(restrained_pool)
            ]
            contour_hi = min(register_hi, root_pitch + 14)
        return _nearest_register_pitch(
            target_pc,
            previous_pitch if previous_pitch is not None else root_pitch,
            lo=max(32, register_lo),
            hi=contour_hi,
        )

    if fusion_role == "octave_root":
        octave_root = _nearest_register_pitch(
            root_pc,
            root_pitch + 12,
            lo=register_lo,
            hi=register_hi,
        )
        if (
            previous_pitch is None
            or abs(octave_root - previous_pitch)
            <= (12 if confirmed_chart else 17)
        ):
            return octave_root
    pool = list(color)
    if (
        not confirmed_chart
        and safe_passing
        and slot % 4 != 0
        and event_index % 4 == 1
        and (character_tier >= 2 or activity >= 0.25)
    ):
        pool.extend(safe_passing)
    beat = int(slot) // 4
    if authored_structural_slots:
        first_attack_in_beat = int(slot) in {
            int(candidate) for candidate in authored_structural_slots
        }
    else:
        beat_slots = [
            int(candidate)
            for candidate in bar_slots
            if int(candidate) // 4 == beat
        ]
        first_attack_in_beat = (
            bool(beat_slots) and int(slot) == min(beat_slots)
        )
    if (
        character_tier >= 2
        and first_attack_in_beat
        and development is not None
    ):
        # Regeneration needs new melodic sentences, not merely new attack
        # positions.  Revoice the structural lead of beats 2–4 from a seeded
        # chord-tone digit while leaving the one, octave answer, and cadence
        # under their explicit roles.  The code belongs to the pre-render
        # macro plan, so timing/detail RNG cannot alter this contour.
        structural_pool = [root_pc, *color]
        digit_place = max(0, beat - 1)
        melodic_position = (
            _PHRASE_ROLES.index(development.role) * 3
            + digit_place
        )
        melodic_digit = (
            abs(int(development.melodic_code))
            // (len(structural_pool) ** melodic_position)
        ) % len(structural_pool)
        arc_shift = int(
            melodic_position
            in _FUSION_ARC_MELODY_SHIFTS.get(
                development.arc_role,
                frozenset(),
            )
        )
        target_pc = structural_pool[
            (melodic_digit + arc_shift) % len(structural_pool)
        ]
    else:
        colour_offset = (
            1
            if character_tier == 3 and fusion_role == "colour_a"
            else 2
            if character_tier == 4 and fusion_role == "colour_b"
            else 0
            if fusion_role == "colour_a"
            else 1
        )
        target_pc = pool[
            (
                slot // 2
                + tone_rotation
                + colour_offset
                + (2 if role == "push" else 0)
            )
            % len(pool)
        ]
    # Bold Character changes melodic targets, but it must remain one playable
    # hand movement. Voice-lead ordinary attacks from the preceding note and
    # reserve the explicit octave role for the one authored pop. A small,
    # once-per-bar lift gives the contrast section shape without repeatedly
    # teleporting between the bottom and top of the instrument.
    center = (
        previous_pitch
        if previous_pitch is not None
        else root_pitch + 5
    )
    if (
        character_tier >= 3
        and register_lift > 0
        and first_attack_in_beat
        and beat == 1
    ):
        center += min(5, int(register_lift))
    pitch = _nearest_register_pitch(
        target_pc,
        center,
        lo=max(32, register_lo),
        hi=register_hi,
    )
    if (
        confirmed_chart
        and previous_pitch is not None
        and abs(pitch - previous_pitch) > 12
    ):
        pitch = _nearest_register_pitch(
            target_pc,
            previous_pitch,
            lo=max(32, register_lo),
            hi=register_hi,
        )
    return pitch


def _apply_candidate_role_to_style_pitch(
    style_pitch: int,
    *,
    style: str,
    candidate_role: str | None,
    slot: int,
    event_index: int,
    root_pc: int,
    stable_pcs: list[int],
    avoid_pcs: list[int],
    previous_pitch: int | None,
    scale_pcs: set[int],
    instrument_family: str,
) -> int:
    """Modify a composed style pitch without replacing the style grammar."""

    if candidate_role in (None, "performance_alternative"):
        return int(style_pitch)

    register_lo, register_hi = _style_register_bounds(
        instrument_family,
        style=style,
    )
    root_pitch = _nearest_register_pitch(
        root_pc,
        int(style_pitch),
        lo=register_lo,
        hi=register_hi,
    )

    if candidate_role == "pocket_keeper":
        # Root the strong grid while retaining the style's offbeat colour and
        # signature octave-pop events.
        return root_pitch if slot % 4 == 0 else int(style_pitch)

    if candidate_role == "rhythmic_alternative":
        # Extra syncopations read more clearly when most are root-led, but do
        # not erase the style's octave gesture and coloured counterline.
        if slot % 4 != 0 and event_index % 4 == 1:
            return root_pitch
        return int(style_pitch)

    if candidate_role != "harmonic_alternative":
        return int(style_pitch)

    # Keep Fusion/Slap's defining octave answer. All other non-downbeat
    # attacks voice-lead through safe chord colour in the selected style's
    # playable register.
    if (
        style in {"fusion", "slap"}
        and event_index % 3 == 2
        and int(style_pitch) % 12 == root_pc % 12
    ):
        return int(style_pitch)
    safe_colours = [
        int(pc) % 12
        for pc in stable_pcs
        if int(pc) % 12 != root_pc % 12
        and int(pc) % 12 not in set(avoid_pcs)
        and int(pc) % 12 in scale_pcs
    ]
    if slot == 0 or not safe_colours:
        return int(style_pitch)
    target_pc = safe_colours[(event_index + 1) % len(safe_colours)]
    center = previous_pitch if previous_pitch is not None else int(style_pitch)
    return _nearest_register_pitch(
        target_pc,
        center,
        lo=register_lo,
        hi=register_hi,
    )


# ---- v0.3b reference-aware groove (BUILD_NOTES §6) -------------------------

# Evidence threshold below which we refuse to claim a reference lock —
# the v0.3a validation pack's low-confidence gate (0.45), validated there:
# wrong analyser answers self-report below it.
_REFERENCE_EVIDENCE_THRESHOLD = 0.45


def _resolve_reference_lock(
    lock_to_groove: float | None,
    conditioning: UnifiedConditioning | None,
) -> tuple[float, str]:
    """Resolve the lock-to-groove knob against available evidence.

    Returns (lock, state) where state is one of:
      "locked" — source groove present, evidence strong, lock > 0
      "off"    — evidence fine but the user dialled lock to 0
      "thin"   — source present but analysis confidence under the v0.3a
                 gate: fall back to the standard pocket, claim nothing
      "none"   — no reference source at all
    """
    if not has_source_groove(conditioning):
        return 0.0, "none"
    assert conditioning is not None
    evidence = 0.5 * (
        float(conditioning.tempo_confidence) + float(conditioning.beat_phase_confidence)
    )
    # Live bridge frames carry exact MIDI-derived groove with per-bar
    # confidence but no audio-analysis tempo/phase confidence — accept
    # whichever evidence channel is stronger.
    rows = [
        max(0.0, min(1.0, float(value)))
        for value in (conditioning.source_groove_confidence or ())
    ]
    if rows:
        expected = max(1, int(conditioning.bar_count))
        padded = rows[:expected] + [0.0] * max(0, expected - len(rows))
        evidence = sum(padded) / expected
    if evidence < _REFERENCE_EVIDENCE_THRESHOLD:
        return 0.0, "thin"
    lock = 0.5 if lock_to_groove is None else max(0.0, min(1.0, float(lock_to_groove)))
    return lock, ("locked" if lock > 0.0 else "off")


def _space_score(
    context: SessionAnchorContext | None,
    conditioning: UnifiedConditioning | None,
    bar: int,
    slot: int,
) -> float | None:
    """Per-slot space score: ``1 - snare - 0.5*pressure + kick`` (spec §6).

    Pocket GATING, not mimicking: high score = room for the bass (kick
    adjacency, no snare, low pressure). None when there is no rhythm
    evidence at all.
    """
    if has_source_groove_bar(conditioning, bar):
        return (
            1.0
            - source_snare_weight(conditioning, bar, slot)
            - (0.5 * source_slot_pressure(conditioning, bar, slot))
            + source_kick_weight(conditioning, bar, slot)
        )
    if context is not None and context.anchor_lane == "drums":
        return (
            1.0
            - drum_snare_weight(context, bar, slot)
            - (0.5 * slot_pressure(context, bar, slot))
            + drum_kick_weight(context, bar, slot)
        )
    return None


def _profile_float(profile: dict[str, Any], key: str, fallback: float) -> float:
    raw = profile.get(key)
    if isinstance(raw, int | float):
        return float(raw)
    return fallback


def _profile_int(profile: dict[str, Any], key: str, fallback: int) -> int:
    raw = profile.get(key)
    if isinstance(raw, int):
        return int(raw)
    return fallback


def _pino_phrase_slots(
    role: str,
    kick_slots: list[int],
    *,
    rng: random.Random | object,
    profile: dict[str, Any],
) -> list[int]:
    if role == "push":
        base = [0, 7, 12]
    elif role == "answer":
        base = [0, 10]
    elif role == "release":
        base = [0, 12] if rng.random() > _profile_float(profile, "rest_preference", 0.5) else [0]
    else:
        base = [0, 8]
    for slot in kick_slots:
        if slot != 0 and slot % 4 != 0 and rng.random() < _profile_float(profile, "offbeat_bias", 0.32):
            base.append(slot)
    out = sorted(set(x for x in base if 0 <= x <= 15))
    if 0 not in out:
        out.insert(0, 0)
    return out[: max(1, _profile_int(profile, "density_ceiling", 3))]


def _pick_pino_pitch(
    slot: int,
    role: str,
    *,
    root_pc: int,
    stable_pcs: list[int],
    passing_pcs: list[int],
    avoid_pcs: list[int],
    previous_pitch: int | None,
    profile: dict[str, Any],
    rng: random.Random | object,
) -> int:
    lo = _profile_int(profile, "register_min", 34)
    hi = _profile_int(profile, "register_max", 55)
    root_pitch = _pc_to_bass_register(root_pc, octave=2, lo=lo, hi=hi)
    if slot == 0 or role == "anchor":
        return root_pitch

    clean_stable = [pc for pc in stable_pcs if pc not in set(avoid_pcs)]
    if passing_pcs and slot % 4 != 0 and rng.random() < 0.28:
        target_pc = rng.choice(passing_pcs)
    else:
        target_pool = clean_stable or [root_pc]
        thirds_or_sevenths = [pc for pc in target_pool if (pc - root_pc) % 12 in (3, 4, 10, 11)]
        if thirds_or_sevenths and rng.random() < 0.62:
            target_pc = rng.choice(thirds_or_sevenths)
        else:
            target_pc = rng.choice(target_pool)

    center = previous_pitch if previous_pitch is not None else root_pitch
    pitch = _pc_to_bass_register(target_pc, octave=2, lo=lo, hi=hi)
    while abs(pitch - center) > 6 and pitch - 12 >= lo:
        pitch -= 12
    while abs(pitch - center) > 6 and pitch + 12 <= hi:
        pitch += 12
    if pitch % 12 in set(avoid_pcs):
        return root_pitch
    return pitch


def _harmonic_bar_plan(
    bar: int,
    *,
    key: str,
    scale: str,
    context: SessionAnchorContext | None,
    conditioning: UnifiedConditioning | None = None,
    chord_progression: list[str] | None = None,
) -> tuple[int, list[int], list[int], list[int], float]:
    harm = conditioning.harmonic_bar(bar) if conditioning is not None else None
    if harm is not None and str(harm.source) == "confirmed_chord_progression":
        return (
            int(harm.root_pc),
            [int(x) for x in harm.target_pcs],
            [int(x) for x in harm.passing_pcs],
            [int(x) for x in harm.avoid_pcs],
            float(harm.confidence),
        )
    if chord_progression:
        chord = mt.progression_chords_for_bars(chord_progression, bar + 1)[bar]
        stable = sorted({int(pc) % 12 for pc in chord.tone_pcs})
        scale_pcs = {
            (mt.key_root_pc(key) + int(interval)) % 12
            for interval in mt.scale_intervals(scale)
        }
        passing = sorted(scale_pcs.difference(stable))
        allowed = set(stable).union(passing)
        return (
            int(chord.root_pc) % 12,
            stable,
            passing,
            [pc for pc in range(12) if pc not in allowed],
            1.0,
        )
    if context is not None and bar < len(context.harmonic_target_pcs_per_bar):
        root = int(context.harmonic_root_pc_per_bar[bar])
        stable = [int(x) for x in context.harmonic_target_pcs_per_bar[bar]]
        passing = [int(x) for x in context.harmonic_passing_pcs_per_bar[bar]]
        avoid = [int(x) for x in context.harmonic_avoid_pcs_per_bar[bar]]
        conf = float(context.harmonic_confidence_per_bar[bar]) if bar < len(context.harmonic_confidence_per_bar) else 0.2
        return root, stable, passing, avoid, conf
    if harm is not None:
        return (
            int(harm.root_pc),
            [int(x) for x in harm.target_pcs],
            [int(x) for x in harm.passing_pcs],
            [int(x) for x in harm.avoid_pcs],
            float(harm.confidence),
        )

    key_pc = mt.key_root_pc(key)
    intervals = mt.scale_intervals(scale)
    root = key_pc
    stable = [key_pc, (key_pc + intervals[2 % len(intervals)]) % 12, (key_pc + intervals[4 % len(intervals)]) % 12]
    passing = [(key_pc + x) % 12 for x in intervals if ((key_pc + x) % 12) not in stable]
    avoid = [pc for pc in range(12) if pc not in [(key_pc + x) % 12 for x in intervals]]
    return root, stable, passing, avoid, 0.2


def _quality_from_targets(root_pc: int, stable_pcs: list[int]) -> str:
    intervals = {(int(pc) - int(root_pc)) % 12 for pc in stable_pcs}
    if 4 in intervals and 10 in intervals:
        return "dominant"
    if 4 in intervals:
        return "major"
    if 3 in intervals:
        return "minor"
    return "dominant"


def _pick_pitch(
    slot: int,
    role: str,
    *,
    root_pc: int,
    stable_pcs: list[int],
    passing_pcs: list[int],
    avoid_pcs: list[int],
    conf: float,
    candidate_role: str | None = None,
    previous_pitch: int | None = None,
    scale_pcs: set[int] | None = None,
    rng: random.Random | object = random,
) -> int:
    strong = slot % 4 == 0
    safe_stable = [
        pc
        for pc in stable_pcs
        if pc not in set(avoid_pcs)
        and (scale_pcs is None or pc in scale_pcs)
    ]
    safe_passing = [
        pc
        for pc in passing_pcs
        if pc not in set(avoid_pcs)
        and (scale_pcs is None or pc in scale_pcs)
    ]
    if candidate_role == "harmonic_alternative" and slot != 0:
        colored = [pc for pc in safe_stable if pc != root_pc]
        if colored:
            target_pc = rng.choice(colored)
            center = previous_pitch if previous_pitch is not None else _pc_to_bass_register(root_pc, octave=2)
            options = [
                _pc_to_bass_register(target_pc, octave=octave, lo=30, hi=62)
                for octave in (1, 2, 3)
            ]
            return min(options, key=lambda pitch: (abs(pitch - center), pitch))
    if strong or role == "anchor":
        return _pc_to_bass_register(root_pc, octave=2)
    if candidate_role == "rhythmic_alternative" and rng.random() < 0.68:
        return _pc_to_bass_register(root_pc, octave=2)
    if safe_passing and rng.random() < min(0.5, 0.15 + 0.5 * conf):
        return _pc_to_bass_register(rng.choice(safe_passing), octave=2)
    pick_pc = rng.choice(safe_stable or [root_pc])
    return _pc_to_bass_register(pick_pc, octave=2)


def generate_bass_phrase_v2(
    *,
    tempo: int,
    bar_count: int,
    key: str,
    scale: str,
    bass_style: str | None = None,
    bass_instrument: str | None = None,
    bass_player: str | None = None,
    chord_progression: list[str] | None = None,
    session_preset: str | None = None,
    context: SessionAnchorContext | None = None,
    conditioning: UnifiedConditioning | None = None,
    seed: int | None = None,
    return_performance_notes: bool = False,
    lock_to_groove: float | None = None,
    density_bias: float = 0.0,
    expression_amount: float = 0.5,
    bass_articulation_focus: str | None = "natural",
    ghost_amount: float | None = None,
    mute_amount: float | None = None,
    slide_amount: float | None = None,
    legato_amount: float | None = None,
    candidate_role: str | None = None,
) -> tuple[bytes, str] | tuple[bytes, str, tuple[BassPerformanceNote, ...]]:
    rng = random.Random(seed) if seed is not None else random
    style = normalize_bass_style(bass_style)
    player = normalize_bass_player(bass_player)
    bi = normalize_bass_instrument(bass_instrument)
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    inst = pretty_midi.Instrument(program=bass_midi_program(bi, style), name="Bass")
    spb = 60.0 / float(tempo)
    sixteenth = spb / 4.0
    bar_anchor = float(context.bar_start_anchor_sec) if context is not None else 0.0
    role_span = 4 if bar_count >= 4 else 2
    perf_notes: list[BassPerformanceNote] = []
    player_persona = BASS_STYLE_ADAPTER.bass_persona(player) if player is not None else None
    player_profile_raw = player_persona.get("profile") if player_persona is not None else None
    player_profile: dict[str, Any] = dict(player_profile_raw) if isinstance(player_profile_raw, dict) else {}
    candidate_role_spec = bass_candidate_role_spec(candidate_role)

    if player == "paul_chambers":
        harmonic = [
            _harmonic_bar_plan(
                bar,
                key=key,
                scale=scale,
                context=context,
                conditioning=conditioning,
                chord_progression=chord_progression,
            )
            for bar in range(max(1, bar_count))
        ]
        for bar, (root_pc, stable_pcs, _passing_pcs, _avoid_pcs, _conf) in enumerate(harmonic):
            role = _bar_role(bar, role_span)
            root_pitch = _pc_to_bass_register(root_pc, octave=2, lo=31, hi=50)
            quality = normalize_chord_quality(_quality_from_targets(root_pc, stable_pcs))
            cell = get_walking_cell(root_pitch, quality, bar)
            next_root_pc = harmonic[(bar + 1) % len(harmonic)][0]
            next_root = _pc_to_bass_register(next_root_pc, octave=2, lo=31, hi=57)
            while abs(next_root - cell[2]) > 7 and next_root + 12 <= 57:
                next_root += 12
            while abs(next_root - cell[2]) > 7 and next_root - 12 >= 31:
                next_root -= 12
            approaches = get_chromatic_approaches(cell[2], next_root)
            if approaches:
                cell[3] = approaches[-1]
            bar_t0 = bar_anchor + bar * 4.0 * spb
            bar_t1 = bar_anchor + (bar + 1) * 4.0 * spb
            for beat_idx, pitch in enumerate(cell):
                slot = beat_idx * 4
                start = max(
                    bar_t0,
                    bar_t0 + beat_idx * spb + spb * 0.02 + (spb * 0.006 if beat_idx in (1, 3) else 0.0)
                    + rng.uniform(-0.002, 0.004) * spb,
                )
                end = min(bar_t1 - 1e-4, start + spb * rng.uniform(0.82, 0.9))
                if end <= start:
                    continue
                vel = max(64, min(106, (92, 82, 88, 78)[beat_idx] + rng.randint(-4, 4)))
                inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=start, end=end))
                if return_performance_notes:
                    perf_notes.append(
                        BassPerformanceNote(
                            pitch=int(pitch),
                            velocity=int(vel),
                            start=float(start),
                            end=float(end),
                            articulation="normal",
                            role=str(role),
                            bar_index=int(bar),
                            slot_index=int(slot),
                            source="phrase_v2",
                            confidence=None,
                        )
                    )

        pm.instruments.append(inst)
        buf = io.BytesIO()
        pm.write(buf)
        preview = (
            f"Bass [phrase_v2, {bi}, {style}, paul_chambers]: "
            f"{mt.normalize_key(key)} {mt.describe_scale(scale)}, {bar_count} bar(s), {tempo} BPM — "
            "quarter-note walking, strong-beat chord targets, and chromatic beat-4 approaches"
            + (", constrained by live harmonic context." if conditioning is not None and conditioning.harmonic_bars else ".")
        )
        if return_performance_notes:
            perf_notes = list(
                infer_bass_articulations(
                    tuple(perf_notes),
                    tempo=tempo,
                    style=style,
                    source="phrase_v2",
                    expression_amount=expression_amount,
                    instrument_family=bi,
                    bass_articulation_focus=bass_articulation_focus,
                    ghost_amount=ghost_amount,
                    mute_amount=mute_amount,
                    slide_amount=slide_amount,
                    legato_amount=legato_amount,
                )
            )
            return buf.getvalue(), preview, tuple(perf_notes)
        return buf.getvalue(), preview

    if player == "pino":
        previous_pitch: int | None = None
        for bar in range(max(1, bar_count)):
            role = _bar_role(bar, role_span)
            kick_slots = _kick_guided_slots(context, bar) if context is not None and context.anchor_lane == "drums" else []
            live_slots = _source_guided_slots(conditioning, bar)
            if live_slots and not kick_slots:
                kick_slots = live_slots
            elif live_slots:
                kick_slots = sorted(set(kick_slots).union(live_slots[:2]))
            slots = _pino_phrase_slots(role, kick_slots, rng=rng, profile=player_profile)
            root_pc, stable_pcs, passing_pcs, avoid_pcs, _conf = _harmonic_bar_plan(
                bar,
                key=key,
                scale=scale,
                context=context,
                conditioning=conditioning,
                chord_progression=chord_progression,
            )
            bar_t0 = bar_anchor + bar * 4.0 * spb
            bar_t1 = bar_anchor + (bar + 1) * 4.0 * spb

            for slot_index, slot in enumerate(slots):
                live_pressure = source_slot_pressure(conditioning, bar, slot) if has_source_groove(conditioning) else 0.0
                live_kick = source_kick_weight(conditioning, bar, slot) if has_source_groove(conditioning) else 0.0
                if live_pressure > 0.72 and live_kick < 0.18 and slot % 4 != 0:
                    continue
                pitch = _pick_pino_pitch(
                    slot,
                    role,
                    root_pc=root_pc,
                    stable_pcs=stable_pcs,
                    passing_pcs=passing_pcs,
                    avoid_pcs=avoid_pcs,
                    previous_pitch=previous_pitch,
                    profile=player_profile,
                    rng=rng,
                )
                behind = spb * 0.028
                if live_kick > 0.0:
                    behind += sixteenth * 0.025 * min(1.0, live_kick)
                start = bar_t0 + slot * sixteenth + behind + rng.uniform(-0.002, 0.006) * spb
                if slot == 0 and len(slots) == 1:
                    dur = spb * 2.75
                elif slot % 8 == 0:
                    dur = spb * 1.72
                elif slot % 4 == 0:
                    dur = spb * 1.18
                else:
                    dur = spb * 0.78
                dur *= _profile_float(player_profile, "articulation_length_bias", 1.24)
                next_slot = slots[slot_index + 1] if slot_index + 1 < len(slots) else None
                next_slot_start = bar_t0 + next_slot * sixteenth if next_slot is not None else bar_t1
                end = min(bar_t1 - 1e-4, next_slot_start - 1e-4, start + dur)
                if end <= start:
                    continue
                vel_base = 84 if slot == 0 else 72
                if role == "push":
                    vel_base += 3
                elif role == "release":
                    vel_base -= 4
                if live_kick > 0.0:
                    vel_base += int(round(5.0 * min(1.0, live_kick)))
                final_vel = max(54, min(100, vel_base + rng.randint(-4, 4)))
                inst.notes.append(
                    pretty_midi.Note(
                        velocity=final_vel,
                        pitch=pitch,
                        start=start,
                        end=end,
                    )
                )
                previous_pitch = pitch
                if return_performance_notes:
                    perf_notes.append(
                        BassPerformanceNote(
                            pitch=int(pitch),
                            velocity=int(final_vel),
                            start=float(start),
                            end=float(end),
                            articulation="normal",
                            role=str(role),
                            bar_index=int(bar),
                            slot_index=int(slot),
                            source="phrase_v2",
                            confidence=None,
                        )
                    )

        pm.instruments.append(inst)
        buf = io.BytesIO()
        pm.write(buf)
        preview = (
            f"Bass [phrase_v2, {bi}, {style}, pino]: "
            f"{mt.normalize_key(key)} {mt.describe_scale(scale)}, {bar_count} bar(s), {tempo} BPM — "
            "laid-back neo-soul pocket, warm sustained chord-tone targets, and rare-groove space"
            + (", conditioned by live source groove." if has_source_groove(conditioning) else ".")
        )
        if return_performance_notes:
            perf_notes = list(
                infer_bass_articulations(
                    tuple(perf_notes),
                    tempo=tempo,
                    style=style,
                    source="phrase_v2",
                    expression_amount=expression_amount,
                    instrument_family=bi,
                    bass_articulation_focus=bass_articulation_focus,
                    ghost_amount=ghost_amount,
                    mute_amount=mute_amount,
                    slide_amount=slide_amount,
                    legato_amount=legato_amount,
                )
            )
            return buf.getvalue(), preview, tuple(perf_notes)
        return buf.getvalue(), preview

    phrase_development = _build_phrase_development_plan(
        bar_count=bar_count,
        style=style,
        seed=seed,
        rng=rng,
        density_bias=density_bias,
    )

    # v0.3b: resolve the reference lock once for the whole part. lock drives
    # kick gravitation, restraint, and pressure-aversion from ONE knob.
    lock, lock_state = _resolve_reference_lock(lock_to_groove, conditioning)
    # Precompute the harmonic plan so every bar can resolve INTO the next
    # chord (spec: "resolve into the next chord, every bar").
    harmonic_plan = [
        _harmonic_bar_plan(
            b,
            key=key,
            scale=scale,
            context=context,
            conditioning=conditioning,
            chord_progression=chord_progression,
        )
        for b in range(max(1, bar_count))
    ]
    confirmed_scale_pcs = {
        (mt.key_root_pc(key) + interval) % 12 for interval in mt.scale_intervals(scale)
    }
    previous_pitch: int | None = None
    previous_previous_pitch: int | None = None
    previous_bar_index: int | None = None
    previous_previous_bar_index: int | None = None
    previous_slot_index: int | None = None
    previous_previous_slot_index: int | None = None
    last_fusion_note_index_by_pitch: dict[int, int] = {}

    for bar in range(max(1, bar_count)):
        development = phrase_development[bar]
        role = development.role
        context_kick_slots = (
            _kick_guided_slots(context, bar)
            if context is not None and context.anchor_lane == "drums"
            else []
        )
        bar_source_groove = has_source_groove_bar(conditioning, bar)
        live_slots = (
            _source_guided_slots(conditioning, bar)
            if lock_state == "locked" and bar_source_groove
            else []
        )
        # The reference is an actual lock knob, not a hidden always-on style
        # adapter.  Off contributes no source attacks; half lock trades one
        # anchor per bar; full lock may trade two.  Keep the strength ranking
        # ahead of context slots rather than sorting it back into chronology.
        live_anchor_take = (
            max(0, min(2, int((lock * 2.0) + 0.5)))
            if lock_state == "locked"
            else 0
        )
        source_anchor_slots = [
            slot for slot in live_slots
            if slot != 0
        ][:live_anchor_take]
        kick_slots = list(
            dict.fromkeys([*source_anchor_slots, *context_kick_slots])
        )
        bar_lock_active = (
            lock_state == "locked"
            and (
                bar_source_groove
                or (
                    context is not None
                    and context.anchor_lane == "drums"
                )
            )
        )
        role_density_delta = candidate_role_spec.density_delta if candidate_role_spec is not None else 0.0
        dense = float(max(-1.0, min(1.0, density_bias + role_density_delta)))
        if candidate_role == "rhythmic_alternative":
            sync_slots = (3, 7, 10, 14) if bar % 2 == 0 else (2, 6, 11, 14)
            kick_slots = list(dict.fromkeys([*kick_slots, *sync_slots]))
        if style == "supportive":
            # Preserve the legacy pocket path exactly. Non-supportive styles
            # use the grammar rail below.
            if lock_state == "locked":
                # kick_lock_mult: higher lock pulls more kick-adjacent slots
                # into the phrase (and allows one extra hit at full glue).
                slots = _phrase_slots(
                    role,
                    kick_slots,
                    kick_take=(
                        3
                        + (1 if lock >= 0.7 else 0)
                        + (1 if dense >= 0.25 else 0)
                        - (1 if candidate_role == "pocket_keeper" else 0)
                    ),
                    extra_hits=(
                        (1 if lock >= 0.75 else 0)
                        + (1 if dense >= 0.25 else 0)
                        + (1 if candidate_role == "rhythmic_alternative" else 0)
                    ),
                )
            else:
                slots = _phrase_slots(
                    role,
                    kick_slots,
                    kick_take=3 + (1 if dense >= 0.25 else 0),
                    extra_hits=1 if dense >= 0.25 else 0,
                )
            if dense >= 0.25:
                activity_take = 2 if dense >= 0.75 else 1
                activity_slots = (
                    (3, 11, 14, 6, 10, 2, 7, 13, 15, 5, 9)
                    if bar % 2 == 0
                    else (2, 10, 14, 5, 11, 3, 7, 13, 15, 6, 9)
                )
                # Busy is a development pass, not the neutral cell plus
                # ornaments. Retire one inner statement and replace it with
                # a different syncopation before adding the extra attack(s).
                retire = [
                    slot
                    for slot in slots
                    if slot != 0 and slot not in activity_slots[:2]
                ]
                if retire:
                    slots.remove(retire[(bar + len(slots)) % len(retire)])
                extras = [
                    slot
                    for slot in activity_slots
                    if slot not in slots
                ][: activity_take + 1]
                slots = sorted(set(slots).union(extras))
            # "sparser": probabilistically thin everything but the one.
            if dense <= -0.75:
                slots = [s for s in slots if s == 0]
            elif dense <= -0.25:
                slots = [
                    s
                    for s in slots
                    if s == 0 or rng.random() > (-dense * 0.55)
                ]
        else:
            slots = _style_phrase_slots(
                style,
                role,
                kick_slots=kick_slots,
                dense=dense,
                bar=bar,
                candidate_role=candidate_role,
                rng=rng,
                development=development,
            )
        if style == "supportive":
            slots = _develop_phrase_slots(
                slots,
                style=style,
                dense=dense,
                development=development,
                protected_slots=tuple(kick_slots[:1]),
            )
            # A Supportive quarter-grid root sustains beyond one sixteenth.
            # Do not let a macro edit place a normal retrigger immediately
            # after it: overlapping same-pitch MIDI note-ons serialize as a
            # false ultra-short note. Other adjacent syncopations remain
            # available because their written lengths already leave space.
            slot_set = set(slots)
            slots = [
                slot
                for slot in slots
                if not (int(slot) % 4 == 1 and int(slot) - 1 in slot_set)
            ]
        fusion_authored_bar_slots: tuple[int, ...] = ()
        fusion_authored_structural_slots: set[int] = set()
        fusion_gating_protected_slots: set[int] = set()
        if style == "fusion" and dense >= 0.25:
            fusion_authored_bar_slots = _fusion_macro_slots(development)
            fusion_authored_structural_slots = _fusion_structural_slots(
                fusion_authored_bar_slots,
                role=role,
            )
            # Preserve the written melodic leads and the adapted bar's first
            # attacks. A groove insertion may arrive earlier in a beat, but
            # it must neither steal the seeded contour nor leave that beat
            # vulnerable to pressure-gating.
            fusion_gating_protected_slots = (
                fusion_authored_structural_slots
                | _fusion_structural_slots(slots, role=role)
                | (set(source_anchor_slots) & set(slots))
            )
        root_pc, stable_pcs, passing_pcs, avoid_pcs, conf = harmonic_plan[bar]
        confirmed_harmonic = conditioning.harmonic_bar(bar) if conditioning is not None else None
        confirmed_chart = bool(chord_progression) or (
            confirmed_harmonic is not None
            and str(confirmed_harmonic.source) == "confirmed_chord_progression"
        )
        if (
            str(bass_style or "supportive") == "supportive"
            and confirmed_chart
        ):
            # A confirmed chart is stronger evidence than global scale
            # membership. Supportive lines use chord tones here; the explicit
            # late-bar approach below remains the controlled source of color.
            passing_pcs = []
        next_root_pc = harmonic_plan[(bar + 1) % len(harmonic_plan)][0]
        bar_t0 = bar_anchor + bar * 4.0 * spb
        bar_t1 = bar_anchor + (bar + 1) * 4.0 * spb

        for event_index, slot in enumerate(slots):
            live_pressure = (
                source_slot_pressure(conditioning, bar, slot)
                if bar_source_groove
                else 0.0
            )
            live_kick = (
                source_kick_weight(conditioning, bar, slot)
                if bar_source_groove
                else 0.0
            )
            if slot not in fusion_gating_protected_slots:
                if bar_lock_active and slot != 0:
                    # Pocket gating via the one space score (spec §6): higher
                    # lock = stricter pressure-aversion and firmer restraint.
                    # Fusion macro beat leads are exempt because they carry
                    # the phrase's melodic sentence; inner attacks still make
                    # room for the source.
                    space = _space_score(context, conditioning, bar, slot)
                    if space is not None:
                        space_threshold = 0.42 + (0.28 * lock)
                        restraint = (0.50 + (0.50 * lock)) * (1.0 - 0.4 * max(0.0, dense))
                        if space < space_threshold and rng.random() < restraint:
                            continue
                else:
                    if context is not None:
                        pressure = slot_pressure(context, bar, slot)
                        kick = drum_kick_weight(context, bar, slot) if context.anchor_lane == "drums" else 0.0
                        # Rest-space rule: avoid busy non-kick slots.
                        if pressure > 0.72 and kick < 0.18 and slot % 4 != 0 and rng.random() < 0.45:
                            continue
            # Compose from the selected Style first. Controlled candidate
            # roles then edit one dimension of that result; they must never
            # route Fusion, Slap, or Melodic back through the generic picker.
            style_pitch = _pick_style_pitch(
                style,
                slot=slot,
                event_index=event_index,
                role=role,
                root_pc=root_pc,
                stable_pcs=stable_pcs,
                passing_pcs=passing_pcs,
                avoid_pcs=avoid_pcs,
                conf=conf,
                candidate_role=(
                    candidate_role
                    if style in {"supportive", "rhythmic"}
                    else None
                ),
                previous_pitch=previous_pitch,
                expression_amount=expression_amount,
                density_bias=dense,
                scale_pcs=confirmed_scale_pcs,
                instrument_family=bi,
                confirmed_chart=confirmed_chart,
                rng=rng,
                development=development,
                bar_slots=tuple(slots),
                next_root_pc=next_root_pc,
                authored_structural_slots=tuple(
                    sorted(fusion_authored_structural_slots)
                ),
                authored_bar_slots=fusion_authored_bar_slots,
            )
            pitch = _apply_candidate_role_to_style_pitch(
                style_pitch,
                style=style,
                candidate_role=(
                    candidate_role
                    if style not in {"supportive", "rhythmic"}
                    else None
                ),
                slot=slot,
                event_index=event_index,
                root_pc=root_pc,
                stable_pcs=stable_pcs,
                avoid_pcs=avoid_pcs,
                previous_pitch=previous_pitch,
                scale_pcs=confirmed_scale_pcs,
                instrument_family=bi,
            )
            # Resolve into the next chord. With a confirmed chart, answer bars
            # may use one directed *scale-safe* neighbour of the next root;
            # release bars retain their tested current-chord cadence. Without
            # a confirmed chart, retain the guarded chromatic vocabulary.
            if (
                role in ("release", "answer")
                and slot == slots[-1]
                and slot >= 10
                and conf >= 0.3
                and next_root_pc != root_pc
            ):
                if (
                    confirmed_chart
                    and role == "answer"
                    and style == "fusion"
                    and slot == 15
                ):
                    register_lo, register_hi = _style_register_bounds(
                        bi,
                        style=style,
                    )
                    next_root = _nearest_register_pitch(
                        next_root_pc,
                        57,
                        lo=register_lo,
                        hi=register_hi,
                    )
                    neighbour_pcs = [
                        int(pc) % 12
                        for pc in confirmed_scale_pcs
                        if int(pc) % 12 not in set(avoid_pcs)
                        and 0
                        < min(
                            (int(pc) - next_root_pc) % 12,
                            (next_root_pc - int(pc)) % 12,
                        )
                        <= 2
                    ]
                    approach_pitches = sorted(
                        {
                            _pc_to_bass_register(
                                approach_pc,
                                octave=octave,
                                lo=register_lo,
                                hi=register_hi,
                            )
                            for approach_pc in neighbour_pcs
                            for octave in (1, 2, 3, 4)
                        }
                    )
                    approach_origin = (
                        previous_pitch
                        if previous_pitch is not None
                        else pitch
                    )
                    playable_approaches = [
                        candidate
                        for candidate in approach_pitches
                        if abs(candidate - approach_origin) <= 7
                    ]
                    if playable_approaches:
                        pitch = min(
                            playable_approaches,
                            key=lambda candidate: (
                                abs(candidate - approach_origin),
                                abs(candidate - next_root),
                                candidate,
                            ),
                        )
                elif not confirmed_chart:
                    next_root = _pc_to_bass_register(
                        next_root_pc,
                        octave=2,
                        lo=30,
                        hi=62,
                    )
                    while (
                        abs(next_root - pitch) > 7
                        and next_root + 12 <= 62
                    ):
                        next_root += 12
                    while (
                        abs(next_root - pitch) > 7
                        and next_root - 12 >= 30
                    ):
                        next_root -= 12
                    approaches = get_chromatic_approaches(
                        pitch,
                        next_root,
                    )
                    if approaches:
                        approach_pitch = int(approaches[-1])
                        if approach_pitch % 12 in confirmed_scale_pcs:
                            pitch = approach_pitch
            if (
                style == "fusion"
                and slot != 0
                and previous_pitch is not None
                and previous_previous_pitch is not None
                and int(pitch)
                == int(previous_pitch)
                == int(previous_previous_pitch)
                and slot not in fusion_gating_protected_slots
                and previous_bar_index == bar
                and previous_previous_bar_index == bar
                and previous_slot_index is not None
                and previous_previous_slot_index is not None
                and int(previous_previous_slot_index)
                < int(previous_slot_index)
                < int(slot)
                and int(slot)
                - int(previous_previous_slot_index)
                <= 3
            ):
                # A third identical inner attack reads as a stuck sequencer.
                # Preserve authored structural tones, but turn an unprotected
                # local repeat into the nearest alternative chord tone.
                register_lo, register_hi = _style_register_bounds(
                    bi,
                    style=style,
                )
                alternate_pcs = sorted(
                    {
                        int(pc) % 12
                        for pc in [root_pc, *stable_pcs]
                        if int(pc) % 12 in confirmed_scale_pcs
                        and int(pc) % 12 not in set(avoid_pcs)
                        and int(pc) % 12 != int(pitch) % 12
                    }
                )
                alternate_pitches = [
                    _nearest_register_pitch(
                        alternate_pc,
                        previous_pitch,
                        lo=register_lo,
                        hi=register_hi,
                    )
                    for alternate_pc in alternate_pcs
                ]
                playable_alternates = [
                    candidate
                    for candidate in alternate_pitches
                    if abs(candidate - previous_pitch) <= 7
                ]
                if playable_alternates:
                    pitch = min(
                        playable_alternates,
                        key=lambda candidate: (
                            abs(candidate - previous_pitch),
                            candidate,
                        ),
                    )
            start = bar_t0 + slot * sixteenth
            if bar_lock_active:
                # Full lock means sample-accurate sixteenth placement. The
                # previous implementation added only-positive "glue" and
                # humanisation here, so even lock=1 deliberately played late.
                # As the knob approaches 1, collapse both nudges to zero.
                timing_freedom = 1.0 - lock
                if context is not None and context.anchor_lane == "drums":
                    start += (
                        sixteenth
                        * 0.05
                        * drum_kick_weight(context, bar, slot)
                        * timing_freedom
                    )
                if live_kick > 0.0:
                    glue = 0.02 + (0.03 * lock)
                    start += sixteenth * glue * min(1.0, live_kick) * timing_freedom
                start += rng.uniform(-0.004, 0.004) * spb * timing_freedom
            else:
                if context is not None and context.anchor_lane == "drums":
                    start += sixteenth * 0.05 * drum_kick_weight(context, bar, slot)
                if live_kick > 0.0:
                    start += sixteenth * 0.035 * min(1.0, live_kick)
                start += rng.uniform(0.0, 0.008) * spb
            if style == "melodic":
                dur = sixteenth * (2.4 if slot % 4 == 0 else 1.55)
            elif style == "slap":
                dur = sixteenth * (0.82 if event_index % 3 == 2 else 0.56)
            elif style == "fusion":
                dur = sixteenth * (1.05 if slot % 4 == 0 else 0.68)
            elif style == "rhythmic":
                dur = sixteenth * (1.0 if slot % 4 == 0 else 0.64)
            else:
                dur = sixteenth * (1.2 if slot % 4 == 0 else 0.85)
            if role == "release":
                dur *= 0.9
            if candidate_role == "pocket_keeper":
                dur *= 1.18 if slot % 4 == 0 else 1.0
            elif candidate_role == "rhythmic_alternative":
                dur *= 0.72 if slot % 4 != 0 else 0.88
            elif candidate_role == "performance_alternative":
                if slot % 4 != 0:
                    start += spb * 0.018
                    dur *= 0.56
                else:
                    dur *= 1.24
            end = min(bar_t1 - 1e-4, start + dur)
            if end <= start:
                continue
            if style == "fusion":
                # Re-articulating one pitch requires the prior note-off to
                # precede the new note-on. Otherwise a standards-compliant
                # MIDI roundtrip can collapse the second attack into a
                # 2–10 ms blip when it encounters the first note-off.
                prior_index = last_fusion_note_index_by_pitch.get(
                    int(pitch)
                )
                if prior_index is not None:
                    previous_note = inst.notes[prior_index]
                    if (
                        float(previous_note.start)
                        < float(start)
                        < float(previous_note.end)
                    ):
                        previous_note.end = float(start)
                        if (
                            return_performance_notes
                            and prior_index < len(perf_notes)
                        ):
                            perf_notes[prior_index] = replace(
                                perf_notes[prior_index],
                                end=float(start),
                            )
                last_fusion_note_index_by_pitch[int(pitch)] = len(inst.notes)
            vel = 92 if slot % 4 == 0 else 78
            if style == "slap":
                vel = 106 if event_index % 3 == 2 else 98 if slot % 4 == 0 else 60
            elif style == "fusion":
                vel = 99 if event_index in (0, 2) else 86 if slot % 4 else 94
            elif style == "melodic":
                vel = 88 if slot % 4 == 0 else 78
            if live_kick > 0.0:
                # kick accenting scales with the lock (0.5 reproduces the
                # previous fixed +10)
                accent = (6.0 + (8.0 * lock)) if bar_lock_active else 10.0
                vel += int(round(accent * min(1.0, live_kick)))
            if (
                style == "fusion"
                and bar_lock_active
                and bar_source_groove
                and slot != 0
                and live_kick < 0.18
            ):
                # The melodic sentence may deliberately cross a quiet cell,
                # but it must read as an answer around the drum accents, not
                # compete with them at the same dynamic weight.
                vel -= int(
                    round(
                        (10.0 + (8.0 * lock))
                        * (1.0 - max(0.0, live_kick))
                    )
                )
            if role == "push":
                vel += 4
            elif role == "release":
                vel -= 5
            if candidate_role == "rhythmic_alternative" and slot % 4 != 0:
                vel += 5
            elif candidate_role == "performance_alternative":
                if slot % 4 != 0:
                    vel -= 17 if slot % 8 in (2, 6) else 10
                elif slot in (0, 8):
                    vel += 5
            final_vel = max(54, min(112, vel + rng.randint(-6, 6)))
            inst.notes.append(
                pretty_midi.Note(
                    velocity=final_vel,
                    pitch=pitch,
                    start=start,
                    end=end,
                )
            )
            if return_performance_notes:
                perf_notes.append(
                    BassPerformanceNote(
                        pitch=int(pitch),
                        velocity=int(final_vel),
                        start=float(start),
                        end=float(end),
                        articulation="normal",
                        role=str(role),
                        bar_index=int(bar),
                        slot_index=int(slot),
                        source="phrase_v2",
                        confidence=None,
                    )
                )
            previous_previous_pitch = previous_pitch
            previous_previous_bar_index = previous_bar_index
            previous_previous_slot_index = previous_slot_index
            previous_pitch = int(pitch)
            previous_bar_index = int(bar)
            previous_slot_index = int(slot)

    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    if lock_state == "locked":
        covered_bars = sum(
            has_source_groove_bar(conditioning, bar)
            for bar in range(max(1, bar_count))
        )
        coverage_clause = (
            ""
            if covered_bars >= max(1, bar_count)
            else f"; {covered_bars}/{max(1, bar_count)} bars with usable evidence"
        )
        groove_clause = (
            f", locked to the reference groove (lock {lock:.2f}"
            f"{coverage_clause})."
        )
    elif lock_state == "off":
        groove_clause = ", reference groove available but lock dialled to 0."
    elif lock_state == "thin":
        # Confidence-gated honesty (spec §6): never claim a lock the
        # analysis can't support.
        groove_clause = ", standard pocket — reference evidence too thin to claim a groove lock."
    else:
        groove_clause = "."
    style_clause = {
        "supportive": "root-led pocket statements and deliberate space",
        "melodic": "connected chord-tone counterlines and shaped answers",
        "rhythmic": "repeating syncopated cells with short root-led attacks",
        "slap": "thumb-and-pop octave cells, ghosted gaps, and funk accents",
        "fusion": "bass-lead counterlines, octave pops, coloured runs, and syncopated fills",
    }[style]
    preview = (
        f"Bass [phrase_v2, {bi}, {style}{', ' + player if player else ''}]: "
        f"{mt.normalize_key(key)} {mt.describe_scale(scale)}, {bar_count} bar(s), {tempo} BPM — "
        + style_clause
        + (
            "; seeded A/A'/B/return phrase development, kick-aware roles, "
            "rest-space gating, and bar-level harmonic targets"
        )
        + groove_clause
    )
    if candidate_role_spec is not None:
        preview += f" Role: {candidate_role_spec.label} — {candidate_role_spec.description}"
    if return_performance_notes:
        perf_notes = list(
            infer_bass_articulations(
                tuple(perf_notes),
                tempo=tempo,
                style=style,
                source="phrase_v2",
                expression_amount=expression_amount,
                instrument_family=bi,
                bass_articulation_focus=bass_articulation_focus,
                ghost_amount=ghost_amount,
                mute_amount=mute_amount,
                slide_amount=slide_amount,
                legato_amount=legato_amount,
            )
        )
        return buf.getvalue(), preview, tuple(perf_notes)
    return buf.getvalue(), preview
