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

from app.services.bass_instrument_profiles import bass_instrument_profile


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
) -> tuple[BassPerformanceNote, ...]:
    """Return copies with deterministic, style-aware articulation labels.

    This does not render articulations and must not change any musical fields.
    The midpoint (0.5) preserves the original conservative grace/ghost
    behaviour. Above the midpoint, phrase context can add connected-note
    intent or dead notes where the selected style supports them. No random
    jitter is used: identical note/style/expression inputs produce identical
    labels.
    """
    if not notes:
        return notes
    _ = source
    normalized_style = str(style or "supportive").strip().lower()
    amount = max(0.0, min(1.0, float(expression_amount)))
    instrument = bass_instrument_profile(instrument_family)
    sixteenth = 60.0 / float(max(1, tempo)) / 4.0
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
    "BassPerformanceSource",
    "BassPerformanceNote",
    "infer_bass_articulations",
    "performance_note_to_pretty_midi_note",
]
