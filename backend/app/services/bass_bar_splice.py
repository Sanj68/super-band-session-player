"""Splice regenerated bass bars into an existing bass MIDI lane."""

from __future__ import annotations

import io

import pretty_midi


def _clone_note(note: pretty_midi.Note) -> pretty_midi.Note:
    return pretty_midi.Note(
        velocity=int(note.velocity),
        pitch=int(note.pitch),
        start=float(note.start),
        end=float(note.end),
    )


def _clone_control_change(cc: pretty_midi.ControlChange) -> pretty_midi.ControlChange:
    return pretty_midi.ControlChange(
        number=int(cc.number),
        value=int(cc.value),
        time=float(cc.time),
    )


def _clone_pitch_bend(pb: pretty_midi.PitchBend) -> pretty_midi.PitchBend:
    return pretty_midi.PitchBend(
        pitch=int(pb.pitch),
        time=float(pb.time),
    )


def _clone_instrument_shell(inst: pretty_midi.Instrument) -> pretty_midi.Instrument:
    return pretty_midi.Instrument(
        program=int(inst.program),
        is_drum=bool(inst.is_drum),
        name=str(inst.name or "Bass"),
    )


def _in_range(time_seconds: float, *, start_seconds: float, end_seconds: float) -> bool:
    return start_seconds <= float(time_seconds) < end_seconds


def splice_bass_bars(
    *,
    existing_midi: bytes,
    replacement_midi: bytes,
    tempo: int,
    bar_start: int,
    bar_end: int,
) -> bytes:
    """Return MIDI with events in [bar_start, bar_end) replaced from a regenerated lane."""
    seconds_per_bar = (60.0 / float(tempo)) * 4.0
    start_seconds = float(bar_start) * seconds_per_bar
    end_seconds = float(bar_end) * seconds_per_bar

    existing_pm = pretty_midi.PrettyMIDI(io.BytesIO(existing_midi))
    replacement_pm = pretty_midi.PrettyMIDI(io.BytesIO(replacement_midi))
    out_pm = pretty_midi.PrettyMIDI(
        resolution=int(existing_pm.resolution),
        initial_tempo=float(tempo),
    )

    max_count = max(len(existing_pm.instruments), len(replacement_pm.instruments))
    for idx in range(max_count):
        source = (
            existing_pm.instruments[idx]
            if idx < len(existing_pm.instruments)
            else replacement_pm.instruments[idx]
        )
        out_inst = _clone_instrument_shell(source)

        if idx < len(existing_pm.instruments):
            existing_inst = existing_pm.instruments[idx]
            out_inst.notes.extend(
                _clone_note(n)
                for n in existing_inst.notes
                if not _in_range(n.start, start_seconds=start_seconds, end_seconds=end_seconds)
            )
            out_inst.control_changes.extend(
                _clone_control_change(cc)
                for cc in existing_inst.control_changes
                if not _in_range(cc.time, start_seconds=start_seconds, end_seconds=end_seconds)
            )
            out_inst.pitch_bends.extend(
                _clone_pitch_bend(pb)
                for pb in existing_inst.pitch_bends
                if not _in_range(pb.time, start_seconds=start_seconds, end_seconds=end_seconds)
            )

        if idx < len(replacement_pm.instruments):
            replacement_inst = replacement_pm.instruments[idx]
            out_inst.notes.extend(
                _clone_note(n)
                for n in replacement_inst.notes
                if _in_range(n.start, start_seconds=start_seconds, end_seconds=end_seconds)
            )
            out_inst.control_changes.extend(
                _clone_control_change(cc)
                for cc in replacement_inst.control_changes
                if _in_range(cc.time, start_seconds=start_seconds, end_seconds=end_seconds)
            )
            out_inst.pitch_bends.extend(
                _clone_pitch_bend(pb)
                for pb in replacement_inst.pitch_bends
                if _in_range(pb.time, start_seconds=start_seconds, end_seconds=end_seconds)
            )

        out_inst.notes.sort(key=lambda n: (n.start, n.pitch, n.end))
        out_inst.control_changes.sort(key=lambda cc: (cc.time, cc.number, cc.value))
        out_inst.pitch_bends.sort(key=lambda pb: (pb.time, pb.pitch))
        out_pm.instruments.append(out_inst)

    buf = io.BytesIO()
    out_pm.write(buf)
    return buf.getvalue()
