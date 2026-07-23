"""Explicit, engine-independent bass turnaround shaping."""

from __future__ import annotations

import io

import pretty_midi


def _nearest_pitch_for_pc(pc: int, *, near: int, lo: int = 30, hi: int = 62) -> int:
    candidates = [pitch for pitch in range(lo, hi + 1) if pitch % 12 == int(pc) % 12]
    return min(candidates, key=lambda pitch: (abs(pitch - near), pitch))


def apply_bass_turnaround(
    midi_bytes: bytes,
    *,
    tempo: int,
    bar_index: int,
    next_root_pc: int,
) -> bytes:
    """Replace the selected bar's final beat with a chromatic lead-in.

    The three-note approach lands one semitone from the following bar's root;
    the following bar itself remains untouched and supplies the resolution.
    """

    pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
    if not pm.instruments:
        return midi_bytes

    inst = next((candidate for candidate in pm.instruments if not candidate.is_drum), pm.instruments[0])
    spb = 60.0 / float(max(1, tempo))
    bar_start = float(max(0, bar_index)) * 4.0 * spb
    final_beat = bar_start + 3.0 * spb
    bar_end = bar_start + 4.0 * spb

    earlier = [note for note in inst.notes if note.start < final_beat or note.start >= bar_end]
    prior = [note for note in earlier if bar_start <= note.start < final_beat]
    near = int(prior[-1].pitch) if prior else 40
    target = _nearest_pitch_for_pc(next_root_pc, near=near)

    # Approach from the side that needs the smaller leap from the preceding
    # phrase, then stop just before the next bar so its root can resolve cleanly.
    ascending = abs((target - 3) - near) <= abs((target + 3) - near)
    pitches = (target - 3, target - 2, target - 1) if ascending else (target + 3, target + 2, target + 1)
    starts = (final_beat, final_beat + 0.5 * spb, final_beat + 0.75 * spb)
    durations = (0.42 * spb, 0.18 * spb, 0.18 * spb)
    velocities = (82, 86, 91)

    for pitch, start, duration, velocity in zip(pitches, starts, durations, velocities, strict=True):
        pitch = max(30, min(62, int(pitch)))
        end = min(bar_end - 1.0e-4, start + duration)
        if end > start:
            earlier.append(
                pretty_midi.Note(
                    velocity=velocity,
                    pitch=pitch,
                    start=start,
                    end=end,
                )
            )

    inst.notes = sorted(earlier, key=lambda note: (note.start, note.pitch, note.end))
    out = io.BytesIO()
    pm.write(out)
    return out.getvalue()
