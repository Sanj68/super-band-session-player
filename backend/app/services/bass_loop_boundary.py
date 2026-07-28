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
import math
from typing import Iterable

import pretty_midi

from app.models.session import LaneNote


_DEFAULT_BASS_PROGRAM = 33  # GM Electric Bass (finger)
_BASS_LO = 30
_BASS_HI = 54
_FIRST_NOTE_TOLERANCE_SLOTS = 0.25  # below this, treat first note as already at slot 0
_MONOPHONIC_GAP_SECONDS = 1e-4
_MAX_CONNECTED_OVERLAP_SECONDS = 0.005
_MIN_NOTE_SECONDS = 0.025
_MIN_NOTE_SIXTEENTH_FRACTION = 0.25
_MAX_MIN_NOTE_SECONDS = 0.045
_PITCH_BEND_RPN_SETUP = (
    (101, 0),
    (100, 0),
    (6, 12),
    (38, 0),
    (101, 127),
    (100, 127),
)


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
    preserve_connected_overlap: bool = False,
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
    end_cap = loop_end
    start_cap = max(1e-4, loop_end - 1e-4)
    min_note_duration = max(
        _MIN_NOTE_SECONDS,
        min(_MAX_MIN_NOTE_SECONDS, sixteenth * _MIN_NOTE_SIXTEENTH_FRACTION),
    )

    if not incoming:
        if allow_delayed_entry:
            return []
        pitch = _root_midi_in_bass_register(int(harmonic_root_pc) if harmonic_root_pc is not None else 0)
        return [LaneNote(pitch=pitch, start=0.0, end=min(end_cap, sixteenth * 4.0), velocity=92)]

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
        if e > end_cap:
            e = end_cap
        if e <= s:
            e = min(end_cap, s + max(min_note_duration, sixteenth * 0.5))
        if e - s < min_note_duration:
            e = min(end_cap, s + min_note_duration)
        if e - s < min_note_duration - 1e-9:
            continue
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
        return [LaneNote(pitch=pitch, start=0.0, end=min(end_cap, sixteenth * 4.0), velocity=92)]

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
            anchor_end = min(end_cap, max(sixteenth * 0.5, float(first.start) - 1e-4))
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
                end=float(min(end_cap, target_start + sixteenth * 2.0)),
                velocity=86,
            )
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

    monophonic: list[LaneNote] = []
    for n in deduped:
        current = LaneNote(
            pitch=int(n.pitch),
            start=float(n.start),
            end=float(min(end_cap, max(float(n.end), float(n.start) + min_note_duration))),
            velocity=int(n.velocity),
        )
        if float(current.end) - float(current.start) < min_note_duration - 1e-9:
            continue

        merged_with_previous = False
        while monophonic:
            previous = monophonic[-1]
            onset_gap = float(current.start) - float(previous.start)
            if int(current.pitch) == int(previous.pitch) and onset_gap < min_note_duration:
                monophonic[-1] = LaneNote(
                    pitch=int(previous.pitch),
                    start=float(previous.start),
                    end=float(min(end_cap, max(float(previous.end), float(current.end)))),
                    velocity=max(int(previous.velocity), int(current.velocity)),
                )
                merged_with_previous = True
                break

            if float(previous.end) <= float(current.start) - _MONOPHONIC_GAP_SECONDS:
                break
            overlap = float(previous.end) - float(current.start)
            if (
                preserve_connected_overlap
                and int(previous.pitch) != int(current.pitch)
                and 0.0 < overlap <= _MAX_CONNECTED_OVERLAP_SECONDS + 1e-9
            ):
                # Performance hammer/legato intent uses a deliberately tiny
                # different-pitch overlap. Preserve only this bounded window;
                # ordinary or excessive polyphony still follows the strict
                # monophonic trim below.
                break
            trimmed_end = float(current.start) - _MONOPHONIC_GAP_SECONDS
            if trimmed_end - float(previous.start) >= min_note_duration:
                monophonic[-1] = LaneNote(
                    pitch=int(previous.pitch),
                    start=float(previous.start),
                    end=float(trimmed_end),
                    velocity=int(previous.velocity),
                )
                break
            monophonic.pop()

        if merged_with_previous:
            continue
        monophonic.append(current)

    if not monophonic:
        if allow_delayed_entry:
            return []
        pitch = _root_midi_in_bass_register(int(harmonic_root_pc) if harmonic_root_pc is not None else 0)
        return [LaneNote(pitch=pitch, start=0.0, end=min(end_cap, sixteenth * 4.0), velocity=92)]

    # Make the file itself exactly loop-length without adding a dummy event:
    # the final playable note releases on the boundary.
    final_idx = max(range(len(monophonic)), key=lambda i: float(monophonic[i].start))
    final_note = monophonic[final_idx]
    if last_bar_origin - 1e-6 <= float(final_note.start) < start_cap:
        monophonic[final_idx] = LaneNote(
            pitch=int(final_note.pitch),
            start=float(final_note.start),
            end=float(end_cap),
            velocity=int(final_note.velocity),
        )

    return sorted(monophonic, key=lambda n: (float(n.start), int(n.pitch)))


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
    resolution = max(1, int(pm.resolution))
    bass_inst = None
    for inst in pm.instruments:
        if not inst.is_drum:
            bass_inst = inst
            program = int(inst.program)
            name = inst.name or name
            break
    source_pitch_bends = list(bass_inst.pitch_bends) if bass_inst is not None else []
    source_control_changes = (
        list(bass_inst.control_changes) if bass_inst is not None else []
    )
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
        # Articulation metadata is not present in a generic SMF. The dedicated
        # performance lane name is the egress contract that distinguishes an
        # intentional hammer/legato overlap from accidental clean-lane
        # polyphony.
        preserve_connected_overlap=name.strip().casefold() == "bass (performance)",
    )
    out_pm = pretty_midi.PrettyMIDI(
        initial_tempo=float(tempo),
        resolution=resolution,
    )
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
    raw_min_start = min(
        (float(note.start) for note in raw_notes),
        default=0.0,
    )
    automation_shift = (
        -raw_min_start
        if not allow_delayed_entry and raw_min_start > 0.0
        else 0.0
    )
    loop_seconds = float(max(1, int(bar_count))) * (
        4.0 * 60.0 / float(max(40, min(240, int(tempo))))
    )
    out_inst.control_changes.extend(
        _normalized_control_changes(
            source_control_changes,
            time_shift=automation_shift,
            loop_seconds=loop_seconds,
        )
    )
    out_inst.pitch_bends.extend(
        _normalized_pitch_bends(
            source_pitch_bends,
            time_shift=automation_shift,
            loop_seconds=loop_seconds,
            note_end=max(
                (float(note.end) for note in normalized),
                default=0.0,
            ),
            tick_seconds=(
                60.0
                / float(max(40, min(240, int(tempo))))
                / float(resolution)
            ),
        )
    )
    out_pm.instruments.append(out_inst)
    buf = io.BytesIO()
    out_pm.write(buf)
    return buf.getvalue()


def _safe_event_time(
    value: float,
    *,
    time_shift: float,
    loop_seconds: float,
) -> float | None:
    try:
        shifted = float(value) + float(time_shift)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(shifted):
        return None
    return max(0.0, min(float(loop_seconds), shifted))


def _normalized_control_changes(
    events: list[pretty_midi.ControlChange],
    *,
    time_shift: float,
    loop_seconds: float,
) -> list[pretty_midi.ControlChange]:
    normalized: list[pretty_midi.ControlChange] = []
    static_setup_ids = _pitch_bend_rpn_setup_event_ids(events)
    for event in events:
        event_time = _safe_event_time(
            float(event.time),
            # RPN pitch-bend sensitivity is channel configuration, not musical
            # automation. Keep its ordered setup at the file head while note-
            # attached CC automation follows any loop-normalization shift.
            time_shift=0.0 if id(event) in static_setup_ids else time_shift,
            loop_seconds=loop_seconds,
        )
        if event_time is None:
            continue
        normalized.append(
            pretty_midi.ControlChange(
                number=max(0, min(127, int(event.number))),
                value=max(0, min(127, int(event.value))),
                time=event_time,
            )
        )
    return sorted(
        normalized,
        key=lambda event: float(event.time),
    )


def _pitch_bend_rpn_setup_event_ids(
    events: list[pretty_midi.ControlChange],
) -> set[int]:
    ordered = sorted(events, key=lambda event: float(event.time))
    signature_size = len(_PITCH_BEND_RPN_SETUP)
    for start in range(0, len(ordered) - signature_size + 1):
        window = ordered[start : start + signature_size]
        signature = tuple(
            (int(event.number), int(event.value))
            for event in window
        )
        if signature == _PITCH_BEND_RPN_SETUP:
            return {id(event) for event in window}
    return set()


def _normalized_pitch_bends(
    events: list[pretty_midi.PitchBend],
    *,
    time_shift: float,
    loop_seconds: float,
    note_end: float,
    tick_seconds: float,
) -> list[pretty_midi.PitchBend]:
    normalized: list[pretty_midi.PitchBend] = []
    for event in events:
        event_time = _safe_event_time(
            float(event.time),
            time_shift=time_shift,
            loop_seconds=loop_seconds,
        )
        if event_time is None:
            continue
        normalized.append(
            pretty_midi.PitchBend(
                pitch=max(-8192, min(8191, int(event.pitch))),
                time=event_time,
            )
        )
    normalized.sort(key=lambda event: (float(event.time), int(event.pitch)))

    # A nonzero bend on the terminal tick cannot be followed by an in-range
    # reset. Drop that inaudible boundary-only state instead of appending a
    # coincident zero: PrettyMIDI sorts same-tick pitch wheels by value, which
    # can otherwise leave a positive bend last and make normalization grow on
    # every pass.
    terminal_guard = max(0.0, float(loop_seconds) - max(1e-9, float(tick_seconds)))
    normalized = [
        event
        for event in normalized
        if not (
            int(event.pitch) != 0
            and float(event.time) > terminal_guard + 1e-12
        )
    ]
    if normalized and int(normalized[-1].pitch) != 0:
        reset_time = max(
            float(normalized[-1].time) + max(1e-9, float(tick_seconds)),
            max(0.0, min(float(loop_seconds), float(note_end))),
        )
        reset_time = min(float(loop_seconds), reset_time)
        if reset_time <= float(normalized[-1].time):
            # Defensive fallback for a pathological sub-tick loop: discard the
            # terminal nonzero state rather than serialize a stuck bend.
            normalized.pop()
            return normalized
        normalized.append(
            pretty_midi.PitchBend(
                pitch=0,
                time=max(0.0, reset_time),
            )
        )
    return normalized
