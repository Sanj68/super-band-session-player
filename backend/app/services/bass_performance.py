"""Engine-internal performance-note type for v0.5 articulation work.

This is a trusted-input dataclass used by bass generators. Do NOT replace
with a Pydantic model — at the HTTP boundary, mirror it in
``app.models.session`` if/when it needs to be serialized.

Step 1 contract: this module is purely additive. No generator code reads
or writes these types yet, and ``performance_note_to_pretty_midi_note``
preserves pitch/velocity/start/end exactly with no articulation behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Final, Literal

import pretty_midi

from app.services.bass_instrument_profiles import (
    BassInstrumentProfile,
    bass_instrument_profile,
    resolve_bass_articulation_focus,
)


BassArticulation = Literal[
    "normal",
    "ghost",
    "dead",
    "slide_from",
    "slide_to",
    "hammer",
    "grace",
]

_VALID_ARTICULATIONS: Final[frozenset[str]] = frozenset(
    ("normal", "ghost", "dead", "slide_from", "slide_to", "hammer", "grace")
)

BassPerformanceSource = Literal["baseline", "phrase_v2", "candidate"]

BassArticulationFocus = Literal[
    "natural",
    "clean",
    "ghosted",
    "muted",
    "connected",
]


@dataclass(frozen=True, slots=True)
class BassPerformanceNote:
    pitch: int
    start: float
    end: float
    velocity: int
    articulation: BassArticulation = "normal"
    role: str | None = None
    bar_index: int | None = None
    slot_index: int | None = None
    source: BassPerformanceSource | None = None
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not (0 <= int(self.pitch) <= 127):
            raise ValueError(f"pitch out of MIDI range [0,127]: {self.pitch}")
        if not (1 <= int(self.velocity) <= 127):
            raise ValueError(f"velocity out of MIDI range [1,127]: {self.velocity}")
        if not (float(self.start) < float(self.end)):
            raise ValueError(
                f"start must be < end (got start={self.start}, end={self.end})"
            )
        if self.articulation not in _VALID_ARTICULATIONS:
            raise ValueError(
                f"invalid articulation {self.articulation!r}; "
                f"valid: {sorted(_VALID_ARTICULATIONS)}"
            )
        if self.confidence is not None and not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"confidence must be in [0,1]: {self.confidence}")


def performance_note_to_pretty_midi_note(note: BassPerformanceNote) -> pretty_midi.Note:
    """Convert to a vanilla ``pretty_midi.Note``.

    Step 1: articulation is *recorded but not rendered*. Every articulation
    value yields the same shape of ``pretty_midi.Note`` — the only thing
    that differs is what a future profile layer will read off the source
    note. Do not add CCs, pitch bends, or keyswitches here.
    """
    return pretty_midi.Note(
        pitch=int(note.pitch),
        velocity=int(note.velocity),
        start=float(note.start),
        end=float(note.end),
    )


def infer_bass_articulations(
    notes: tuple[BassPerformanceNote, ...],
    *,
    tempo: int,
    style: str | None = None,
    source: BassPerformanceSource | None = None,
    expression_amount: float = 0.5,
    instrument_family: str | None = None,
    bass_articulation_focus: str | None = "natural",
    ghost_amount: float | None = None,
    mute_amount: float | None = None,
    slide_amount: float | None = None,
    legato_amount: float | None = None,
) -> tuple[BassPerformanceNote, ...]:
    """Return copies with deterministic, style-aware articulation labels.

    This does not render articulations and must not change any musical fields.
    The midpoint (0.5) preserves the original conservative grace/ghost
    behaviour. Above the midpoint, phrase context can add connected-note
    intent or dead notes where the selected style supports them. No random
    jitter is used: identical note/style/expression inputs produce identical
    labels.

    ``ghost_amount``, ``mute_amount``, ``slide_amount``, and
    ``legato_amount`` opt into the phrase-aware blend planner. Each is an
    independent 0..1 amount; omitted axes resolve to zero. When all four are
    ``None``, the legacy exclusive Touch-focus behavior below is preserved
    exactly.
    """
    if not notes:
        return notes
    _ = source
    normalized_style = str(style or "supportive").strip().lower()
    amount = max(0.0, min(1.0, float(expression_amount)))
    instrument = bass_instrument_profile(instrument_family)
    sixteenth = 60.0 / float(max(1, tempo)) / 4.0
    if any(
        value is not None
        for value in (
            ghost_amount,
            mute_amount,
            slide_amount,
            legato_amount,
        )
    ):
        return _plan_phrase_articulation_blend(
            notes,
            style=normalized_style,
            instrument=instrument,
            sixteenth=sixteenth,
            ghost_amount=ghost_amount,
            mute_amount=mute_amount,
            slide_amount=slide_amount,
            legato_amount=legato_amount,
        )
    _requested_focus, effective_focus, _notice = resolve_bass_articulation_focus(
        bass_articulation_focus,
        instrument_family,
    )
    if effective_focus == "clean":
        return tuple(replace(note, articulation="normal") for note in notes)
    if effective_focus != "natural":
        return _infer_explicit_articulation_focus(
            notes,
            focus=effective_focus,
            amount=amount,
            instrument=instrument,
            sixteenth=sixteenth,
            source=source,
        )

    # ``natural`` is the compatibility rail. Keep the original inference
    # order and thresholds byte-for-byte so existing sessions do not change.
    out: list[BassPerformanceNote] = []
    ordered = tuple(sorted(enumerate(notes), key=lambda item: (item[1].start, item[1].pitch, item[1].end)))
    next_by_original_index: dict[int, BassPerformanceNote | None] = {}
    previous_by_original_index: dict[int, BassPerformanceNote | None] = {}
    for pos, (orig_idx, _note) in enumerate(ordered):
        next_by_original_index[orig_idx] = ordered[pos + 1][1] if pos + 1 < len(ordered) else None
        previous_by_original_index[orig_idx] = ordered[pos - 1][1] if pos > 0 else None

    for idx, note in enumerate(notes):
        articulation: BassArticulation = "normal"
        next_note = next_by_original_index.get(idx)
        previous_note = previous_by_original_index.get(idx)
        if (
            amount >= 0.15
            and instrument.allow_grace
            and _is_grace_note(note, next_note, sixteenth=sixteenth)
        ):
            articulation = "grace"
        elif (
            amount >= 0.25
            and instrument.allow_ghost
            and _is_ghost_note(note, sixteenth=sixteenth)
        ):
            articulation = "ghost"
        elif amount > 0.5 and instrument.allow_dead and _is_dead_note_candidate(
            note,
            style=normalized_style,
            sixteenth=sixteenth,
            expression_amount=amount,
        ):
            articulation = "dead"
        elif amount > 0.5:
            articulation = _connected_articulation(
                note,
                previous_note,
                style=normalized_style,
                sixteenth=sixteenth,
                expression_amount=amount,
                connected_bias=instrument.connected_bias,
            )
        out.append(replace(note, articulation=articulation))
    return tuple(out)


def _plan_phrase_articulation_blend(
    notes: tuple[BassPerformanceNote, ...],
    *,
    style: str,
    instrument: BassInstrumentProfile,
    sixteenth: float,
    ghost_amount: float | None,
    mute_amount: float | None,
    slide_amount: float | None,
    legato_amount: float | None,
) -> tuple[BassPerformanceNote, ...]:
    """Plan a restrained blend of punctuation and connected-note intent.

    The planner changes articulation metadata only. Existing non-normal intent
    is treated as authored and retained. Punctuation is spread across phrase
    bars and is capped as a share of eligible offbeat events, avoiding the
    "turn most of the line into ghosts" behavior of an exclusive macro focus.

    ``hammer`` is the current vocabulary's legato destination. Descending
    pull-offs deliberately remain ``slide_to``: ``pull_off`` is not added until
    the performance renderer can distinguish and audibly render it instead of
    exposing a metadata-only promise.
    """

    planned = list(notes)
    occupied = {
        index
        for index, note in enumerate(notes)
        if note.articulation != "normal"
    }

    ghost = _blend_amount(ghost_amount)
    if ghost > 0.0 and instrument.allow_ghost:
        candidates = [
            index
            for index, note in enumerate(notes)
            if index not in occupied
            and _is_blend_punctuation_candidate(
                note,
                sixteenth=sixteenth,
            )
        ]
        selected = _select_phrase_spread(
            notes,
            candidates,
            amount=ghost,
            maximum_share=0.18,
            salt=83,
        )
        for index in selected:
            planned[index] = replace(planned[index], articulation="ghost")
        occupied.update(selected)

    mute = _blend_amount(mute_amount)
    if mute > 0.0 and instrument.allow_dead:
        candidates = [
            index
            for index, note in enumerate(notes)
            if index not in occupied
            and _is_blend_punctuation_candidate(
                note,
                sixteenth=sixteenth,
            )
            and style in {"rhythmic", "slap", "fusion"}
        ]
        selected = _select_phrase_spread(
            notes,
            candidates,
            amount=mute,
            maximum_share=0.14,
            salt=97,
        )
        for index in selected:
            planned[index] = replace(planned[index], articulation="dead")
        occupied.update(selected)

    ordered = tuple(
        sorted(
            enumerate(notes),
            key=lambda item: (
                float(item[1].start),
                int(item[1].pitch),
                float(item[1].end),
            ),
        )
    )
    slide_candidates: list[int] = []
    hammer_candidates: list[int] = []
    if instrument.connected_bias > 0.0:
        for position in range(1, len(ordered)):
            index, note = ordered[position]
            if index in occupied:
                continue
            previous_note = ordered[position - 1][1]
            articulation = _explicit_connected_articulation(
                note,
                previous_note,
                sixteenth=sixteenth,
                connected_bias=instrument.connected_bias,
            )
            if articulation == "hammer":
                hammer_candidates.append(index)
            elif articulation == "slide_to":
                slide_candidates.append(index)

    slide = _blend_amount(slide_amount)
    if slide > 0.0:
        selected = _select_phrase_spread(
            notes,
            slide_candidates,
            amount=slide,
            maximum_share=0.55,
            salt=109,
        )
        for index in selected:
            planned[index] = replace(planned[index], articulation="slide_to")
        occupied.update(selected)

    legato = _blend_amount(legato_amount)
    if legato > 0.0:
        selected = _select_phrase_spread(
            notes,
            [
                index
                for index in hammer_candidates
                if index not in occupied
            ],
            amount=legato,
            maximum_share=0.65,
            salt=127,
        )
        for index in selected:
            planned[index] = replace(planned[index], articulation="hammer")

    return tuple(planned)


def _blend_amount(value: float | None) -> float:
    """Normalize one optional articulation amount to the planner's 0..1 rail."""

    if value is None:
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _is_blend_punctuation_candidate(
    note: BassPerformanceNote,
    *,
    sixteenth: float,
) -> bool:
    """Protect structural beats while admitting short offbeat punctuation."""

    slot = int(note.slot_index) if note.slot_index is not None else None
    if slot is None or slot in (0, 4, 8, 12):
        return False
    if str(note.role or "") == "anchor":
        return False
    duration = float(note.end) - float(note.start)
    return duration <= max(0.16, sixteenth * 1.5)


def _select_phrase_spread(
    notes: tuple[BassPerformanceNote, ...],
    candidates: list[int],
    *,
    amount: float,
    maximum_share: float,
    salt: int,
) -> tuple[int, ...]:
    """Select an exact, bounded count while spreading choices across bars."""

    if not candidates or amount <= 0.0 or maximum_share <= 0.0:
        return ()
    # ``maximum_share`` is a hard musical ceiling. Rounding to nearest can
    # exceed it at the half-step boundary (for example 3 / 16 ghosts =
    # 18.75% against an 18% cap), so truncate the positive target instead.
    target = int(len(candidates) * maximum_share * amount)
    if target <= 0:
        target = 1
    target = min(len(candidates), target)

    remaining = list(dict.fromkeys(candidates))
    selected: list[int] = []
    selected_per_bar: dict[int, int] = {}
    role_priority = {
        "answer": 0,
        "release": 1,
        "push": 2,
        "anchor": 3,
    }
    while remaining and len(selected) < target:
        index = min(
            remaining,
            key=lambda candidate: (
                selected_per_bar.get(
                    int(notes[candidate].bar_index or 0),
                    0,
                ),
                role_priority.get(str(notes[candidate].role or ""), 2),
                _deterministic_unit(notes[candidate], salt=salt),
                float(notes[candidate].start),
                candidate,
            ),
        )
        remaining.remove(index)
        selected.append(index)
        bar = int(notes[index].bar_index or 0)
        selected_per_bar[bar] = selected_per_bar.get(bar, 0) + 1
    return tuple(selected)


def _infer_explicit_articulation_focus(
    notes: tuple[BassPerformanceNote, ...],
    *,
    focus: str,
    amount: float,
    instrument: BassInstrumentProfile,
    sixteenth: float,
    source: BassPerformanceSource | None,
) -> tuple[BassPerformanceNote, ...]:
    """Apply one producer-selected articulation family deterministically.

    Explicit focus is intentionally narrower than ``natural``: it suppresses
    every other special articulation. The selected family only touches
    musically eligible notes, and expression remains the amount control.
    """

    normal = [replace(note, articulation="normal") for note in notes]
    if amount <= 0.0:
        return tuple(normal)

    ordered = tuple(
        sorted(
            enumerate(notes),
            key=lambda item: (item[1].start, item[1].pitch, item[1].end),
        )
    )
    next_by_original_index: dict[int, BassPerformanceNote | None] = {}
    previous_by_original_index: dict[int, BassPerformanceNote | None] = {}
    for pos, (orig_idx, _note) in enumerate(ordered):
        next_by_original_index[orig_idx] = (
            ordered[pos + 1][1] if pos + 1 < len(ordered) else None
        )
        previous_by_original_index[orig_idx] = (
            ordered[pos - 1][1] if pos > 0 else None
        )

    candidates: list[tuple[int, BassArticulation]] = []
    for idx, note in enumerate(notes):
        if focus in {"ghosted", "muted"}:
            if _is_explicit_punctuation_candidate(note, sixteenth=sixteenth):
                candidates.append(
                    (idx, "ghost" if focus == "ghosted" else "dead")
                )
            continue

        if focus == "connected":
            next_note = next_by_original_index.get(idx)
            previous_note = previous_by_original_index.get(idx)
            if instrument.allow_grace and _is_grace_note(
                note,
                next_note,
                sixteenth=sixteenth,
            ):
                candidates.append((idx, "grace"))
                continue
            connected = _explicit_connected_articulation(
                note,
                previous_note,
                sixteenth=sixteenth,
                connected_bias=instrument.connected_bias,
            )
            if connected != "normal":
                candidates.append((idx, connected))

    if (
        focus == "connected"
        and not candidates
        and source in {"baseline", "phrase_v2"}
    ):
        # Generated bass lines can leave their closest moving pair just
        # outside the conservative natural-inference window. Honour an
        # explicit Connected request by choosing only the nearest viable
        # destination under one beat. This adds one bounded legato gesture
        # without relabelling every sparse transition or changing any
        # generated note field.
        fallback_candidates: list[
            tuple[float, int, float, int, BassArticulation]
        ] = []
        for position in range(1, len(ordered)):
            idx, note = ordered[position]
            previous_note = ordered[position - 1][1]
            articulation = _explicit_connected_articulation(
                note,
                previous_note,
                sixteenth=sixteenth,
                connected_bias=instrument.connected_bias,
                max_gap_sixteenths=4.0,
            )
            if articulation == "normal":
                continue
            gap = float(note.start) - float(previous_note.end)
            distance = abs(int(note.pitch) - int(previous_note.pitch))
            fallback_candidates.append(
                (gap, distance, float(note.start), idx, articulation)
            )
        if fallback_candidates:
            _gap, _distance, _start, idx, articulation = min(
                fallback_candidates
            )
            candidates.append((idx, articulation))

    if not candidates:
        return tuple(normal)

    rate = {
        "ghosted": 0.72,
        "muted": 0.58,
        "connected": 0.64,
    }.get(focus, 0.0)
    threshold = max(0.0, min(1.0, rate * amount))
    selected: list[tuple[int, BassArticulation]] = [
        (idx, articulation)
        for idx, articulation in candidates
        if _deterministic_unit(notes[idx], salt=73) < threshold
    ]
    # An explicit selection should be audible at the natural midpoint whenever
    # the phrase contains a valid target. Keep it bounded to one target when
    # the deterministic sampler would otherwise choose none.
    if not selected and threshold > 0.0:
        selected.append(candidates[0])
    for idx, articulation in selected:
        normal[idx] = replace(normal[idx], articulation=articulation)
    return tuple(normal)


def _is_explicit_punctuation_candidate(
    note: BassPerformanceNote,
    *,
    sixteenth: float,
) -> bool:
    """Return whether a note can credibly become a ghost/dead punctuation."""

    slot = int(note.slot_index) if note.slot_index is not None else None
    if slot is None or slot in (0, 4, 8, 12):
        return False
    if str(note.role or "") == "anchor":
        return False
    duration = float(note.end) - float(note.start)
    return duration <= max(0.16, sixteenth * 1.5)


def _explicit_connected_articulation(
    note: BassPerformanceNote,
    previous_note: BassPerformanceNote | None,
    *,
    sixteenth: float,
    connected_bias: float,
    max_gap_sixteenths: float = 0.8,
) -> BassArticulation:
    """Classify a close destination as hammer or slide intent."""

    if previous_note is None or connected_bias <= 0.0:
        return "normal"
    gap = float(note.start) - float(previous_note.end)
    if gap < -0.01 or gap > max(
        0.10,
        sixteenth * max(0.0, float(max_gap_sixteenths)),
    ):
        return "normal"
    interval = int(note.pitch) - int(previous_note.pitch)
    distance = abs(interval)
    if not 1 <= distance <= 7:
        return "normal"
    if interval > 0 and distance <= 2:
        return "hammer"
    return "slide_to"


def _is_grace_note(
    note: BassPerformanceNote,
    next_note: BassPerformanceNote | None,
    *,
    sixteenth: float,
) -> bool:
    if next_note is None:
        return False
    dur = float(note.end) - float(note.start)
    gap = float(next_note.start) - float(note.end)
    pitch_distance = abs(int(next_note.pitch) - int(note.pitch))
    return (
        dur <= min(0.09, sixteenth * 0.5)
        and 0.0 <= gap <= min(0.12, sixteenth * 0.85)
        and 1 <= pitch_distance <= 3
        and int(note.velocity) < int(next_note.velocity)
    )


def _is_ghost_note(note: BassPerformanceNote, *, sixteenth: float) -> bool:
    dur = float(note.end) - float(note.start)
    slot = int(note.slot_index) if note.slot_index is not None else None
    role = str(note.role or "")
    strong_anchor = role == "anchor" and slot in (0, 8)
    structural_beat = slot in (0, 4, 8, 12)
    velocity = int(note.velocity)
    if strong_anchor:
        return False
    return velocity <= 62 and dur <= max(0.08, sixteenth * 0.95) and not (structural_beat and velocity > 52)


def _is_dead_note_candidate(
    note: BassPerformanceNote,
    *,
    style: str,
    sixteenth: float,
    expression_amount: float,
) -> bool:
    """Choose muted rhythmic punctuation only in styles that support it."""

    style_ceiling = {
        "rhythmic": 0.34,
        "slap": 0.46,
        "fusion": 0.24,
    }.get(style, 0.0)
    if style_ceiling <= 0.0:
        return False
    slot = int(note.slot_index) if note.slot_index is not None else None
    if slot is None or slot in (0, 4, 8, 12):
        return False
    if str(note.role or "") == "anchor":
        return False
    duration = float(note.end) - float(note.start)
    if duration > max(0.12, sixteenth * 1.35):
        return False
    strength = (expression_amount - 0.5) * 2.0
    return _deterministic_unit(note, salt=17) < style_ceiling * strength


def _connected_articulation(
    note: BassPerformanceNote,
    previous_note: BassPerformanceNote | None,
    *,
    style: str,
    sixteenth: float,
    expression_amount: float,
    connected_bias: float,
) -> BassArticulation:
    """Infer believable connected-note intent from an adjacent pitch pair."""

    style_ceiling = {
        "supportive": 0.12,
        "melodic": 0.42,
        "rhythmic": 0.08,
        "slap": 0.10,
        "fusion": 0.48,
    }.get(style, 0.0)
    style_ceiling *= max(0.0, float(connected_bias))
    if previous_note is None or style_ceiling <= 0.0:
        return "normal"
    gap = float(note.start) - float(previous_note.end)
    if gap < -0.01 or gap > max(0.10, sixteenth * 0.8):
        return "normal"
    interval = int(note.pitch) - int(previous_note.pitch)
    distance = abs(interval)
    if not 1 <= distance <= 7:
        return "normal"
    strength = (expression_amount - 0.5) * 2.0
    if _deterministic_unit(note, salt=41) >= style_ceiling * strength:
        return "normal"
    if interval > 0 and distance <= 2:
        return "hammer"
    return "slide_to"


def _deterministic_unit(note: BassPerformanceNote, *, salt: int) -> float:
    """Stable 0..1 selector derived from musical position, never RNG."""

    bar = int(note.bar_index or 0)
    slot = int(note.slot_index or 0)
    pitch = int(note.pitch)
    mixed = (bar * 1009 + slot * 131 + pitch * 17 + int(salt) * 53) & 0xFFFF
    return mixed / 65535.0


__all__ = [
    "BassArticulation",
    "BassArticulationFocus",
    "BassPerformanceSource",
    "BassPerformanceNote",
    "infer_bass_articulations",
    "performance_note_to_pretty_midi_note",
]
