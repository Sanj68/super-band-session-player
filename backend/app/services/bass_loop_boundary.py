"""Universal bass loop-boundary normalization.

Fix the long-standing bass MIDI gap: regardless of generator path
(default lane, vocabulary candidate, performance overlay, splice),
the first playable note must start at 0.0 and the final bar must
contain at least one note that resolves near the loop end. The
helpers in this module are idempotent so they can run at every
egress point without compounding shifts.
"""

from __future__ import annotations

import io
from typing import Iterable

import pretty_midi

from app.models.session import LaneNote


_DEFAULT_BASS_PROGRAM = 33  # GM Electric Bass (finger)
_BASS_LO = 30
_BASS_HI = 54
_FIRST_NOTE_TOLERANCE_SLOTS = 0.25  # below this, treat first note as already at slot 0


def _root_midi_in_bass_register(root_pc: int, *, lo: int = _BASS_LO, hi: int = _BASS_HI) -> int:
    rp = int(root_pc) % 12
    pitch = rp + 36  # E2-ish baseline
    while pitch < lo:
        pitch += 12
    while pitch > hi:
        pitch -= 12
    return max(lo, min(hi, pitch))


def normalize_bass_lane_notes(
    notes: Iterable[LaneNote],
    *,
    tempo: int,
    bar_count: int,
    harmonic_root_pc: int | None = None,
    allow_delayed_entry: bool = False,
) -> list[LaneNote]:
    """Return a new list of LaneNote with a clean loop boundary.

    - Shift the earliest note to start at exactly 0.0 (unless allow_delayed_entry).
    - Clamp negative starts and any end past loop_end.
    - Drop notes that start at or past the loop end after the shift.
    - If no note exists at slot 0 of bar 0, insert a root anchor.
    - If the final bar has no note, insert a resolving root near slot 14.
    - If the final bar's latest note ends well before loop_end, extend it.
    - Dedupe overlapping same-pitch onsets.
    """
    incoming = list(notes)
    spb = 60.0 / float(max(40, min(240, int(tempo))))
    sixteenth = spb / 4.0
    bar_len = 4.0 * spb
    bars = max(1, int(bar_count))
    loop_end = float(bars) * bar_len
    cap = max(1e-4, loop_end - 1e-4)

    if not incoming:
        if allow_delayed_entry:
            return []
        pitch = _root_midi_in_bass_register(int(harmonic_root_pc) if harmonic_root_pc is not None else 0)
        return [LaneNote(pitch=pitch, start=0.0, end=min(cap, sixteenth * 4.0), velocity=92)]

    sorted_notes = sorted(incoming, key=lambda n: (float(n.start), int(n.pitch)))
    min_start = float(sorted_notes[0].start)
    shift = -min_start if (not allow_delayed_entry and min_start > 0.0) else 0.0

    out: list[LaneNote] = []
    for n in sorted_notes:
        s = float(n.start) + shift
        e = float(n.end) + shift
        if s < 0.0:
            e = max(s + sixteenth * 0.5, e)
            s = 0.0
        if s >= loop_end - 1e-6:
            continue
        if e > cap:
            e = cap
        if e <= s:
            e = min(cap, s + sixteenth * 0.5)
        out.append(
            LaneNote(
                pitch=int(n.pitch),
                start=float(s),
                end=float(e),
                velocity=int(n.velocity),
            )
        )

    if not out:
        if allow_delayed_entry:
            return []
        pitch = _root_midi_in_bass_register(int(harmonic_root_pc) if harmonic_root_pc is not None else 0)
        return [LaneNote(pitch=pitch, start=0.0, end=min(cap, sixteenth * 4.0), velocity=92)]

    out.sort(key=lambda n: (float(n.start), int(n.pitch)))

    if not allow_delayed_entry:
        first = out[0]
        first_slot_offset = float(first.start) / sixteenth if sixteenth > 0 else 0.0
        if first_slot_offset > _FIRST_NOTE_TOLERANCE_SLOTS:
            pitch_first = (
                _root_midi_in_bass_register(int(harmonic_root_pc))
                if harmonic_root_pc is not None
                else int(first.pitch)
            )
            anchor_end = min(cap, max(sixteenth * 0.5, float(first.start) - 1e-4))
            out.insert(
                0,
                LaneNote(
                    pitch=int(pitch_first),
                    start=0.0,
                    end=float(anchor_end),
                    velocity=92,
                ),
            )

    last_bar_origin = (bars - 1) * bar_len
    final_bar_notes = [n for n in out if last_bar_origin - 1e-6 <= float(n.start) < loop_end]
    if not final_bar_notes:
        target_slot = 14
        target_start = last_bar_origin + target_slot * sixteenth
        pc = (
            int(harmonic_root_pc) % 12
            if harmonic_root_pc is not None
            else int(out[0].pitch) % 12
        )
        pitch = _root_midi_in_bass_register(pc)
        out.append(
            LaneNote(
                pitch=int(pitch),
                start=float(target_start),
                end=float(min(cap, target_start + sixteenth * 2.0)),
                velocity=86,
            )
        )
    else:
        latest_idx = max(range(len(out)), key=lambda i: (float(out[i].end), float(out[i].start))) if out else -1
        if latest_idx >= 0:
            latest = out[latest_idx]
            if last_bar_origin - 1e-6 <= float(latest.start) < loop_end and float(latest.end) < loop_end - sixteenth * 1.0:
                out[latest_idx] = LaneNote(
                    pitch=int(latest.pitch),
                    start=float(latest.start),
                    end=float(cap),
                    velocity=int(latest.velocity),
                )

    out.sort(key=lambda n: (float(n.start), int(n.pitch)))
    deduped: list[LaneNote] = []
    for n in out:
        if (
            deduped
            and abs(float(n.start) - float(deduped[-1].start)) < 1e-3
            and int(n.pitch) == int(deduped[-1].pitch)
        ):
            if float(n.end) > float(deduped[-1].end):
                prev = deduped[-1]
                deduped[-1] = LaneNote(
                    pitch=int(prev.pitch),
                    start=float(prev.start),
                    end=float(n.end),
                    velocity=max(int(prev.velocity), int(n.velocity)),
                )
            continue
        deduped.append(n)

    return deduped


def normalize_bass_loop_bytes(
    midi_bytes: bytes | None,
    *,
    tempo: int,
    bar_count: int,
    harmonic_root_pc: int | None = None,
    allow_delayed_entry: bool = False,
) -> bytes:
    """Parse, normalize, and re-serialize bass MIDI bytes.

    Idempotent: repeated calls do not compound shifts. Returns the
    original bytes unchanged if parsing fails (the harness should not
    poison MIDI on a corrupt input).
    """
    if not midi_bytes:
        return midi_bytes or b""
    try:
        pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
    except Exception:
        return midi_bytes
    program = _DEFAULT_BASS_PROGRAM
    name = "Bass"
    bass_inst = None
    for inst in pm.instruments:
        if not inst.is_drum:
            bass_inst = inst
            program = int(inst.program)
            name = inst.name or name
            break
    raw_notes: list[LaneNote] = []
    if bass_inst is not None:
        for n in bass_inst.notes:
            raw_notes.append(
                LaneNote(
                    pitch=int(n.pitch),
                    start=float(n.start),
                    end=float(n.end),
                    velocity=int(n.velocity),
                )
            )
    normalized = normalize_bass_lane_notes(
        raw_notes,
        tempo=tempo,
        bar_count=bar_count,
        harmonic_root_pc=harmonic_root_pc,
        allow_delayed_entry=allow_delayed_entry,
    )
    out_pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    out_inst = pretty_midi.Instrument(program=program, name=name)
    for n in normalized:
        out_inst.notes.append(
            pretty_midi.Note(
                velocity=int(n.velocity),
                pitch=int(n.pitch),
                start=float(n.start),
                end=float(n.end),
            )
        )
    out_pm.instruments.append(out_inst)
    buf = io.BytesIO()
    out_pm.write(buf)
    return buf.getvalue()
