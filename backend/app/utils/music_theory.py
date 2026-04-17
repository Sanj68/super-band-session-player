"""Pitch-class and scale helpers for rule-based generation."""

from __future__ import annotations

from typing import Final

# Pitch class (0=C) for each key name
KEY_TO_PC: Final[dict[str, int]] = {
    "C": 0,
    "C#": 1,
    "Db": 1,
    "D": 2,
    "D#": 3,
    "Eb": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "Gb": 6,
    "G": 7,
    "G#": 8,
    "Ab": 8,
    "A": 9,
    "A#": 10,
    "Bb": 10,
    "B": 11,
}

# Intervals in semitones from the tonic for each diatonic degree (1–7), by scale type
_SCALE_INTERVALS: Final[dict[str, list[int]]] = {
    "major": [0, 2, 4, 5, 7, 9, 11],
    "natural_minor": [0, 2, 3, 5, 7, 8, 10],
    "harmonic_minor": [0, 2, 3, 5, 7, 8, 11],
    "melodic_minor": [0, 2, 3, 5, 7, 9, 11],
    "dorian": [0, 2, 3, 5, 7, 9, 10],
    "mixolydian": [0, 2, 4, 5, 7, 9, 10],
    "pentatonic_major": [0, 2, 4, 7, 9],
    "pentatonic_minor": [0, 3, 5, 7, 10],
    "blues": [0, 3, 5, 6, 7, 10],
}

# Default to major if unknown
_FALLBACK_SCALE = "major"


def normalize_key(key: str) -> str:
    k = key.strip()
    if not k:
        raise ValueError("key must be non-empty")
    if k[0].islower():
        k = k[0].upper() + k[1:]
    return k


def key_root_pc(key: str) -> int:
    k = normalize_key(key)
    if k not in KEY_TO_PC:
        raise ValueError(f"Unknown key: {key!r}. Use e.g. C, F#, Bb.")
    return KEY_TO_PC[k]


def scale_intervals(scale: str) -> list[int]:
    s = scale.strip().lower().replace(" ", "_")
    if s not in _SCALE_INTERVALS:
        return list(_SCALE_INTERVALS[_FALLBACK_SCALE])
    return list(_SCALE_INTERVALS[s])


def pc_to_midi_note(pc: int, octave: int) -> int:
    """Map pitch class and octave to MIDI note number (C4 = 60)."""
    pc = pc % 12
    return 12 * (octave + 1) + pc


def degree_root_pc(key_pc: int, scale: str, degree_one_indexed: int) -> int:
    """
    Root pitch class for a diatonic degree (1 = tonic, 2 = second, ...).
    For pentatonic/blues, degrees wrap using the length of the scale.
    """
    intervals = scale_intervals(scale)
    idx = (degree_one_indexed - 1) % len(intervals)
    return (key_pc + intervals[idx]) % 12


def bass_root_midi(key: str, scale: str, degree_one_indexed: int, octave: int = 2) -> int:
    """MIDI note for the bass root of a scale degree (typical bass octave)."""
    kpc = key_root_pc(key)
    rpc = degree_root_pc(kpc, scale, degree_one_indexed)
    return pc_to_midi_note(rpc, octave)


def chord_tones_midi(
    key: str,
    scale: str,
    degree_one_indexed: int,
    octave: int,
    *,
    seventh: bool = False,
) -> list[int]:
    """MIDI note numbers for a close-position triad (or seventh) in given octave."""
    kpc = key_root_pc(key)
    intervals = scale_intervals(scale)
    n = len(intervals)
    i = (degree_one_indexed - 1) % n
    root_pc = (kpc + intervals[i]) % 12
    third_pc = (kpc + intervals[(i + 2) % n]) % 12
    fifth_pc = (kpc + intervals[(i + 4) % n]) % 12

    root_m = pc_to_midi_note(root_pc, octave)
    third_m = pc_to_midi_note(third_pc, octave)
    fifth_m = pc_to_midi_note(fifth_pc, octave)
    if third_m <= root_m:
        third_m += 12
    if fifth_m <= third_m:
        fifth_m += 12

    notes = [root_m, third_m, fifth_m]
    if seventh:
        seventh_pc = (kpc + intervals[(i + 6) % n]) % 12
        seventh_m = pc_to_midi_note(seventh_pc, octave)
        while seventh_m <= fifth_m:
            seventh_m += 12
        notes.append(seventh_m)
    return notes


def progression_degrees_for_bars(bar_count: int, scale: str) -> list[int]:
    """
    One Roman degree per bar (1-based), cycling I–IV–V–I for 7-note scales;
    simpler 1–3–4–1 pattern for pentatonic/blues.
    """
    intervals = scale_intervals(scale)
    if len(intervals) >= 7:
        pattern = [1, 4, 5, 1]
    else:
        pattern = [1, 3, 4, 1]
    return [pattern[b % len(pattern)] for b in range(bar_count)]


def describe_scale(scale: str) -> str:
    s = scale.strip().lower().replace(" ", "_")
    if s in _SCALE_INTERVALS:
        return s
    return _FALLBACK_SCALE
