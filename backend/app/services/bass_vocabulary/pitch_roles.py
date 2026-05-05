"""Pitch-role resolution for abstract bass vocabulary templates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.services.bass_vocabulary.templates import BassVocabularyTemplate


SUPPORTED_PITCH_ROLES: Final[set[str]] = {
    "root",
    "octave",
    "fifth",
    "minor3",
    "major3",
    "flat7",
    "major7",
    "fourth",
    "chromatic_below_root",
    "chromatic_above_root",
    "ghost",
    "dead",
    "rest",
}

_ROLE_INTERVALS: Final[dict[str, int]] = {
    "root": 0,
    "octave": 12,
    "fifth": 7,
    "minor3": 3,
    "major3": 4,
    "flat7": 10,
    "major7": 11,
    "fourth": 5,
    "chromatic_below_root": -1,
    "chromatic_above_root": 1,
}

_SILENT_ROLES: Final[set[str]] = {"ghost", "dead", "rest"}


@dataclass(frozen=True)
class BassVocabularyNoteEvent:
    bar_index: int
    slot: int
    pitch_role: str
    midi_pitch: int | None
    articulation: str
    duration_slots: int


def resolve_pitch_role(role: str, root_midi: int, chord_quality: str = "minor") -> int | None:
    """Resolve a pitch role to MIDI pitch, returning None for non-pitched roles."""
    if role in _SILENT_ROLES:
        return None
    if role not in _ROLE_INTERVALS:
        raise ValueError(f"Unsupported pitch role: {role}")
    return int(root_midi) + _ROLE_INTERVALS[role]


def _duration_slots(slots: tuple[int, ...], index: int) -> int:
    slot = slots[index]
    next_slot = slots[index + 1] if index + 1 < len(slots) else 16
    return max(1, next_slot - slot)


def _articulation_for_role(role: str) -> str:
    if role == "rest":
        return "rest"
    if role == "ghost":
        return "ghost"
    if role == "dead":
        return "dead"
    return "note"


def template_to_note_events(
    template: BassVocabularyTemplate,
    root_midi: int,
    chord_quality: str = "minor",
    bar_index: int = 0,
) -> list[BassVocabularyNoteEvent]:
    """Convert a template into deterministic abstract note-event descriptions."""
    if len(template.slots) != len(template.pitch_roles):
        raise ValueError(f"Template {template.id} has mismatched slots and pitch roles")

    events: list[BassVocabularyNoteEvent] = []
    for index, (slot, role) in enumerate(zip(template.slots, template.pitch_roles, strict=True)):
        if role not in SUPPORTED_PITCH_ROLES:
            raise ValueError(f"Template {template.id} uses unsupported pitch role: {role}")
        if role == "rest":
            continue
        events.append(
            BassVocabularyNoteEvent(
                bar_index=int(bar_index),
                slot=int(slot),
                pitch_role=role,
                midi_pitch=resolve_pitch_role(role, root_midi, chord_quality),
                articulation=_articulation_for_role(role),
                duration_slots=_duration_slots(template.slots, index),
            )
        )
    return events
