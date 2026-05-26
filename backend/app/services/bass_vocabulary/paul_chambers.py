"""Paul Chambers (1935-1969) bass vocabulary module.

Source style: Miles Davis quintet/sextet bass — Kind of Blue, Workin', Steamin',
Relaxin'. Walking lines emphasising chord tones with chromatic connectors,
guide-tone (3rd / 7th) targeting at bar boundaries, and arco sustained phrases
on ballads. Pocket sits slightly behind the beat with an authoritative pull.

This module is a style head only: it returns abstract MIDI note numbers and
duration metadata. It does not render PrettyMIDI events and is not yet wired
into the candidate-pool selection in ``candidates.py``.
"""

from __future__ import annotations

from typing import Final, Literal


ChordQuality = Literal["maj7", "min7", "dom7", "dim", "half-dim"]


_CHORD_INTERVALS: Final[dict[str, tuple[int, ...]]] = {
    "maj7":     (0, 4, 7, 11),
    "min7":     (0, 3, 7, 10),
    "dom7":     (0, 4, 7, 10),
    "dim":      (0, 3, 6, 9),
    "half-dim": (0, 3, 6, 10),
}

_GUIDE_TONES: Final[dict[str, tuple[int, int]]] = {
    "maj7":     (4, 11),
    "min7":     (3, 10),
    "dom7":     (4, 10),
    "dim":      (3, 9),
    "half-dim": (3, 10),
}


PAUL_CHAMBERS_PROFILE: Final[dict[str, object]] = {
    "id": "paul_chambers",
    "name": "Paul Chambers",
    "era": "hard bop / modal (1955-1969)",
    "feel": "behind the beat, authoritative walking, melodic guide-tone emphasis",
    "tempo_range": (60, 280),
    "preferred_modes": ("dorian", "mixolydian", "minor_blues", "major", "minor"),
    "references": (
        "Miles Davis - Kind of Blue",
        "Miles Davis - Workin'",
        "Miles Davis - Steamin'",
        "Miles Davis - Relaxin'",
    ),
    "signature_devices": (
        "chromatic approach tones from above and below",
        "guide-tone targeting (3rds and 7ths) on bar boundaries",
        "tritone-substitution approaches into ii-V resolutions",
        "minor-7th leaps and ascending 4ths",
        "arco sustained tones on ballads",
    ),
}


GROOVE_FEEL: Final[dict[str, object]] = {
    "placement": "behind",
    "swing_ratio": 0.62,
    "velocity_accent_beats": [1, 3],
}


def _require_quality(chord_quality: str) -> ChordQuality:
    if chord_quality not in _CHORD_INTERVALS:
        raise ValueError(
            f"Unsupported chord_quality {chord_quality!r}; "
            f"expected one of {sorted(_CHORD_INTERVALS)}."
        )
    return chord_quality  # type: ignore[return-value]


def normalize_chord_quality(chord_quality: str) -> ChordQuality:
    """Map Session Player harmony labels into the Chambers vocabulary labels."""
    q = str(chord_quality).strip().lower().replace("_", "-")
    if q in ("major", "major7", "maj", "maj7"):
        return "maj7"
    if q in ("minor", "minor7", "min", "min7"):
        return "min7"
    if q in ("dominant", "dominant7", "dom", "dom7", "7"):
        return "dom7"
    if q in ("diminished", "dim"):
        return "dim"
    if q in ("half-diminished", "half-dim", "m7b5"):
        return "half-dim"
    return "dom7"


def get_chromatic_approaches(root_midi: int, target_midi: int) -> list[int]:
    """Return 1–2 chromatic passing tones between two MIDI pitches.

    Walking-bass convention: lead into ``target_midi`` by a half-step from
    above or below. For shorter gaps (2–3 semitones) one passing tone is
    enough; longer gaps get a two-tone approach where the final note is the
    chromatic neighbour of the target.

    Neither ``root_midi`` nor ``target_midi`` is included in the returned list.
    Equal or adjacent pitches return an empty list.
    """
    root = int(root_midi)
    target = int(target_midi)
    distance = target - root
    abs_dist = abs(distance)
    if abs_dist <= 1:
        return []
    direction = 1 if distance > 0 else -1
    if abs_dist <= 3:
        return [target - direction]
    return [target - (2 * direction), target - direction]


def _bar_pattern_intervals(
    quality: ChordQuality, bar_position: int
) -> tuple[int, int, int, int]:
    """Four intervals-from-root for one bar of walking bass.

    The pattern cycles every 4 bars to mirror Chambers' phrase variation.
    Beat 1 is always the root; beats 2–3 land on chord tones; beat 4 is a
    chord tone or a stepwise neighbour leading into the next bar.
    """
    pos = int(bar_position) % 4
    if quality == "min7":
        if pos == 0:
            return (0, 3, 7, 10)
        if pos == 1:
            return (0, 7, 10, 12)
        if pos == 2:
            return (0, 3, 7, 11)
        return (0, 10, 7, 3)
    if quality == "maj7":
        if pos == 0:
            return (0, 4, 7, 11)
        if pos == 1:
            return (0, 7, 11, 12)
        if pos == 2:
            return (0, 4, 7, 9)
        return (0, 11, 7, 4)
    if quality == "dom7":
        if pos == 0:
            return (0, 4, 7, 10)
        if pos == 1:
            return (0, 7, 10, 12)
        if pos == 2:
            return (0, 4, 7, 9)
        return (0, 10, 7, 4)
    if quality == "dim":
        if pos == 0:
            return (0, 3, 6, 9)
        if pos == 1:
            return (0, 6, 9, 12)
        if pos == 2:
            return (0, 3, 6, 8)
        return (0, 9, 6, 3)
    # half-dim
    if pos == 0:
        return (0, 3, 6, 10)
    if pos == 1:
        return (0, 6, 10, 12)
    if pos == 2:
        return (0, 3, 6, 8)
    return (0, 10, 6, 3)


def get_walking_cell(
    chord_root: int, chord_quality: str, bar_position: int
) -> list[int]:
    """Return 4 MIDI notes for one bar of Paul Chambers-style walking bass.

    Args:
        chord_root: MIDI pitch of the chord root in the bass register
            (typically 28–52).
        chord_quality: One of ``'maj7' | 'min7' | 'dom7' | 'dim' | 'half-dim'``.
        bar_position: Position in a multi-bar phrase. The pattern cycles every
            four bars; bar 0 is a pure chord-tone walk, bar 3 is a turnaround.

    Returns four MIDI note numbers, one per quarter note in 4/4.
    """
    quality = _require_quality(chord_quality)
    intervals = _bar_pattern_intervals(quality, bar_position)
    root = int(chord_root)
    return [root + iv for iv in intervals]


def get_arco_phrase(
    chord_root: int, chord_quality: str, bars: int
) -> list[tuple[int, float]]:
    """Return ``(midi_note, duration_beats)`` pairs for a bowed phrase.

    Phrase structure cycles every 4 bars:

    * bar 0 — whole-note root anchor
    * bar 1 — guide tones (3rd then 7th) as two half-notes
    * bar 2 — 5th sustain then root, as two half-notes
    * bar 3 — whole-note 7th, drifting back to root in the next phrase

    Each bar's durations sum to 4.0 beats. ``bars`` is clamped to ``>= 1``.
    """
    quality = _require_quality(chord_quality)
    n = max(1, int(bars))
    intervals = _CHORD_INTERVALS[quality]
    fifth = intervals[2] if len(intervals) >= 3 else 7
    third, seventh = _GUIDE_TONES[quality]
    root = int(chord_root)
    out: list[tuple[int, float]] = []
    for i in range(n):
        pos = i % 4
        if pos == 0:
            out.append((root, 4.0))
        elif pos == 1:
            out.append((root + third, 2.0))
            out.append((root + seventh, 2.0))
        elif pos == 2:
            out.append((root + fifth, 2.0))
            out.append((root, 2.0))
        else:
            out.append((root + seventh, 4.0))
    return out


__all__ = [
    "ChordQuality",
    "GROOVE_FEEL",
    "PAUL_CHAMBERS_PROFILE",
    "get_arco_phrase",
    "get_chromatic_approaches",
    "get_walking_cell",
    "normalize_chord_quality",
]
