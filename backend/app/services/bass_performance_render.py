"""v0.5 Step 4: pure Performance MIDI renderer.

Reads ``BassPerformanceNote`` articulation metadata and emits a standalone
MIDI byte blob with generic articulation shaping (velocity scaling and
duration shortening). No CCs, no pitch bends, no keyswitches — those
belong in the v0.7 profile renderer.

This module is purely additive. Nothing else in the codebase imports it
yet; the clean MIDI path is unchanged by construction.
"""

from __future__ import annotations

import io

import pretty_midi

from app.services.bass_performance import BassPerformanceNote


_GHOST_VEL_SCALE = 0.55
_GHOST_VEL_MIN = 12
_GHOST_VEL_MAX = 48
_GHOST_DUR_FRAC = 0.5

_GRACE_VEL_SCALE = 0.7
_GRACE_VEL_MIN = 20
_GRACE_VEL_MAX = 64
_GRACE_DUR_FRAC = 0.4
_GRACE_TARGET_GAP = 0.005

_MIN_NOTE_DURATION = 0.001


def render_performance_bass_midi(
    notes: tuple[BassPerformanceNote, ...],
    *,
    tempo: int,
    program: int,
) -> bytes:
    """Render performance notes to MIDI bytes with generic shaping.

    The output is a single-instrument MIDI file at the given tempo. Notes
    are sorted by ``(start, pitch, end)`` so output is deterministic for a
    given input. Articulations not yet rendered (``dead``, ``slide_from``,
    ``slide_to``, ``hammer``) pass through with no shaping.
    """
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    inst = pretty_midi.Instrument(
        program=int(program),
        is_drum=False,
        name="Bass (Performance)",
    )
    pm.instruments.append(inst)

    if not notes:
        buf = io.BytesIO()
        pm.write(buf)
        return buf.getvalue()

    sixteenth = 60.0 / float(max(1, int(tempo))) / 4.0
    ordered = tuple(
        sorted(
            notes,
            key=lambda n: (float(n.start), int(n.pitch), float(n.end)),
        )
    )

    for idx, note in enumerate(ordered):
        next_note = ordered[idx + 1] if idx + 1 < len(ordered) else None
        rendered = _shape_note(note, next_note=next_note, sixteenth=sixteenth)
        inst.notes.append(rendered)

    buf = io.BytesIO()
    pm.write(buf)
    return buf.getvalue()


def _shape_note(
    note: BassPerformanceNote,
    *,
    next_note: BassPerformanceNote | None,
    sixteenth: float,
) -> pretty_midi.Note:
    pitch = int(note.pitch)
    velocity = int(note.velocity)
    start = float(note.start)
    end = float(note.end)

    if note.articulation == "ghost":
        velocity = _clamp(round(velocity * _GHOST_VEL_SCALE), _GHOST_VEL_MIN, _GHOST_VEL_MAX)
        max_dur = sixteenth * _GHOST_DUR_FRAC
        end = _shorten_to(start, end, max_dur)
    elif note.articulation == "grace":
        velocity = _clamp(round(velocity * _GRACE_VEL_SCALE), _GRACE_VEL_MIN, _GRACE_VEL_MAX)
        max_dur = sixteenth * _GRACE_DUR_FRAC
        end = _shorten_to(start, end, max_dur)
        if next_note is not None:
            target_cap = float(next_note.start) - _GRACE_TARGET_GAP
            if target_cap > start + _MIN_NOTE_DURATION and end > target_cap:
                end = target_cap
    # normal / dead / slide_from / slide_to / hammer: pass through.

    return pretty_midi.Note(
        pitch=pitch,
        velocity=velocity,
        start=start,
        end=end,
    )


def _clamp(value: int, lo: int, hi: int) -> int:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return int(value)


def _shorten_to(start: float, end: float, max_dur: float) -> float:
    capped_end = start + max(_MIN_NOTE_DURATION, float(max_dur))
    return min(end, capped_end)


__all__ = ["render_performance_bass_midi"]
