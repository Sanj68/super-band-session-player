"""Engine-internal performance-note type for v0.5 articulation work.

This is a trusted-input dataclass used by bass generators. Do NOT replace
with a Pydantic model — at the HTTP boundary, mirror it in
``app.models.session`` if/when it needs to be serialized.

Step 1 contract: this module is purely additive. No generator code reads
or writes these types yet, and ``performance_note_to_pretty_midi_note``
preserves pitch/velocity/start/end exactly with no articulation behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
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


__all__ = [
    "BassArticulation",
    "BassPerformanceSource",
    "BassPerformanceNote",
    "performance_note_to_pretty_midi_note",
]
