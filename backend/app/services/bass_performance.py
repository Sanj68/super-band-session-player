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
) -> tuple[BassPerformanceNote, ...]:
    """Return copies with conservative metadata-only articulation labels.

    This does not render articulations and must not change any musical fields.
    Step 3 only infers ``grace`` and ``ghost``; all other notes remain normal.
    """
    if not notes:
        return notes
    _ = style, source
    sixteenth = 60.0 / float(max(1, tempo)) / 4.0
    out: list[BassPerformanceNote] = []
    ordered = tuple(sorted(enumerate(notes), key=lambda item: (item[1].start, item[1].pitch, item[1].end)))
    next_by_original_index: dict[int, BassPerformanceNote | None] = {}
    for pos, (orig_idx, _note) in enumerate(ordered):
        next_by_original_index[orig_idx] = ordered[pos + 1][1] if pos + 1 < len(ordered) else None

    for idx, note in enumerate(notes):
        articulation: BassArticulation = "normal"
        dur = float(note.end) - float(note.start)
        next_note = next_by_original_index.get(idx)
        if _is_grace_note(note, next_note, sixteenth=sixteenth):
            articulation = "grace"
        elif _is_ghost_note(note, sixteenth=sixteenth):
            articulation = "ghost"
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


__all__ = [
    "BassArticulation",
    "BassPerformanceSource",
    "BassPerformanceNote",
    "infer_bass_articulations",
    "performance_note_to_pretty_midi_note",
]
