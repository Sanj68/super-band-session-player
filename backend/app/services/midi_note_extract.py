"""Parse note events from lane MIDI bytes for SessionState (piano-roll preview)."""

from __future__ import annotations

import io

import pretty_midi

from app.models.session import LaneNote


def extract_lane_notes(data: bytes | None, *, max_notes: int = 2000) -> list[LaneNote]:
    """Return sorted note events from MIDI bytes; empty if missing or invalid."""
    if not data:
        return []
    try:
        pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    except Exception:
        return []
    out: list[LaneNote] = []
    for inst in pm.instruments:
        for n in inst.notes:
            pitch = max(0, min(127, int(n.pitch)))
            vel = max(0, min(127, int(n.velocity)))
            start = max(0.0, float(n.start))
            end = max(0.0, float(n.end))
            if end < start:
                end = start
            out.append(
                LaneNote(
                    pitch=pitch,
                    start=start,
                    end=end,
                    velocity=vel,
                )
            )
    out.sort(key=lambda x: (x.start, x.pitch))
    if len(out) > max_notes:
        return out[:max_notes]
    return out
