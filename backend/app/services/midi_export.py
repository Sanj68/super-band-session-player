"""Helpers for streaming MIDI bytes as downloads."""

from __future__ import annotations

import io
import math
import zipfile

import pretty_midi
from fastapi.responses import Response

_PITCH_BEND_RPN_SETUP = (
    (101, 0),
    (100, 0),
    (6, 12),
    (38, 0),
    (101, 127),
    (100, 127),
)


def lane_midi_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="audio/midi",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def apply_loop_phase_offset(
    data: bytes,
    *,
    tempo: int,
    bar_count: int,
    phase_offset_beats: float,
) -> bytes:
    """Rotate notes and stateful automation within the session loop.

    Notes crossing the seam are split so every event remains inside the
    declared loop. Pitch bend and CC lanes receive the state active at the cut
    on the first pass. The pitch-bend RPN setup remains at the file head, and a
    short tick guard ensures no wrapped bend/note can run before it completes.
    """
    loop_beats = float(max(1, int(bar_count)) * 4)
    phase_beats = float(phase_offset_beats) % loop_beats
    if abs(phase_beats) < 1e-12:
        return data

    try:
        midi = pretty_midi.PrettyMIDI(io.BytesIO(data))
    except Exception:
        return data

    seconds_per_beat = 60.0 / float(max(1, int(tempo)))
    loop_seconds = loop_beats * seconds_per_beat
    phase_seconds = phase_beats * seconds_per_beat
    cut_seconds = (loop_seconds - phase_seconds) % loop_seconds
    tick_seconds = seconds_per_beat / float(max(1, int(midi.resolution)))
    shifted_any = False
    for instrument in midi.instruments:
        source_notes = list(instrument.notes)
        source_bends = _canonical_pitch_bends(
            list(instrument.pitch_bends),
            loop_seconds=loop_seconds,
            tick_seconds=tick_seconds,
            note_end=max(
                (min(loop_seconds, float(note.end)) for note in source_notes),
                default=0.0,
            ),
        )
        source_control_changes = list(instrument.control_changes)
        static_setup_ids = _pitch_bend_rpn_setup_event_ids(
            source_control_changes
        )
        static_setup = [
            event
            for event in source_control_changes
            if id(event) in static_setup_ids
        ]
        musical_controls = [
            event
            for event in source_control_changes
            if id(event) not in static_setup_ids
        ]

        # RPN configuration uses six ordered ticks. Reserve one more tick for
        # seam state and one for wrapped musical events/notes.
        has_stateful_automation = bool(source_bends or musical_controls)
        setup_end = max(
            (float(event.time) for event in static_setup),
            default=-tick_seconds,
        )
        seam_state_time = (
            setup_end + tick_seconds
            if static_setup
            else 0.0
        )
        musical_floor = (
            seam_state_time + tick_seconds
            if static_setup or has_stateful_automation
            else 0.0
        )
        musical_floor = min(loop_seconds, max(0.0, musical_floor))

        rotated_notes: list[pretty_midi.Note] = []
        for note in source_notes:
            for rotated in _rotated_note_segments(
                note,
                phase_seconds=phase_seconds,
                loop_seconds=loop_seconds,
            ):
                if (
                    musical_floor > 0.0
                    and float(rotated.start) < musical_floor
                ):
                    duration = float(rotated.end) - float(rotated.start)
                    rotated.start = musical_floor + float(rotated.start)
                    rotated.end = min(
                        loop_seconds,
                        float(rotated.start) + duration,
                    )
                if float(rotated.end) > float(rotated.start):
                    rotated_notes.append(rotated)
            shifted_any = True
        instrument.notes = sorted(
            rotated_notes,
            key=lambda note: (
                float(note.start),
                int(note.pitch),
                float(note.end),
                int(note.velocity),
            )
        )
        rotated_bends: list[pretty_midi.PitchBend] = []
        if source_bends:
            seam_bend = _state_before_cut(
                source_bends,
                cut_seconds=cut_seconds,
                value_attr="pitch",
            )
            rotated_bends.append(
                pretty_midi.PitchBend(
                    pitch=max(-8192, min(8191, int(seam_bend))),
                    time=seam_state_time,
                )
            )
            for bend in source_bends:
                rotated_bends.append(
                    pretty_midi.PitchBend(
                        pitch=max(-8192, min(8191, int(bend.pitch))),
                        time=_rotated_event_time(
                            float(bend.time),
                            phase_seconds=phase_seconds,
                            loop_seconds=loop_seconds,
                            musical_floor=musical_floor,
                        ),
                    )
                )
                shifted_any = True
            rotated_bends.sort(
                key=lambda bend: (float(bend.time), int(bend.pitch))
            )
            rotated_bends = _dedupe_pitch_bends(rotated_bends)
        instrument.pitch_bends = rotated_bends

        rotated_controls = [
            pretty_midi.ControlChange(
                number=max(0, min(127, int(event.number))),
                value=max(0, min(127, int(event.value))),
                time=max(0.0, min(loop_seconds, float(event.time))),
            )
            for event in static_setup
        ]
        controls_by_number: dict[int, list[pretty_midi.ControlChange]] = {}
        for event in musical_controls:
            number = max(0, min(127, int(event.number)))
            controls_by_number.setdefault(number, []).append(event)
        for number, events in controls_by_number.items():
            seam_value = _state_before_cut(
                events,
                cut_seconds=cut_seconds,
                value_attr="value",
            )
            rotated_controls.append(
                pretty_midi.ControlChange(
                    number=number,
                    value=max(0, min(127, int(seam_value))),
                    time=seam_state_time,
                )
            )
            for event in events:
                rotated_controls.append(
                    pretty_midi.ControlChange(
                        number=number,
                        value=max(0, min(127, int(event.value))),
                        time=_rotated_event_time(
                            float(event.time),
                            phase_seconds=phase_seconds,
                            loop_seconds=loop_seconds,
                            musical_floor=musical_floor,
                        ),
                    )
                )
                shifted_any = True
        instrument.control_changes = sorted(
            _dedupe_control_changes(rotated_controls),
            key=lambda control_change: float(control_change.time)
        )

    if not shifted_any:
        return data
    buf = io.BytesIO()
    midi.write(buf)
    return buf.getvalue()


def _rotated_note_segments(
    note: pretty_midi.Note,
    *,
    phase_seconds: float,
    loop_seconds: float,
) -> list[pretty_midi.Note]:
    """Rotate one note and split any duration that crosses the loop seam."""

    try:
        start = float(note.start)
        end = float(note.end)
    except (TypeError, ValueError):
        return []
    if not math.isfinite(start) or not math.isfinite(end) or end <= start:
        return []
    duration = min(float(loop_seconds), max(0.0, end - start))
    if duration <= 0.0:
        return []
    rotated_start = (max(0.0, start) + float(phase_seconds)) % float(
        loop_seconds
    )
    velocity = max(1, min(127, int(note.velocity)))
    pitch = max(0, min(127, int(note.pitch)))
    if duration >= float(loop_seconds) - 1e-12:
        return [
            pretty_midi.Note(
                velocity=velocity,
                pitch=pitch,
                start=0.0,
                end=float(loop_seconds),
            )
        ]
    rotated_end = rotated_start + duration
    if rotated_end <= float(loop_seconds) + 1e-12:
        return [
            pretty_midi.Note(
                velocity=velocity,
                pitch=pitch,
                start=rotated_start,
                end=min(float(loop_seconds), rotated_end),
            )
        ]
    overflow = rotated_end - float(loop_seconds)
    return [
        pretty_midi.Note(
            velocity=velocity,
            pitch=pitch,
            start=rotated_start,
            end=float(loop_seconds),
        ),
        pretty_midi.Note(
            velocity=velocity,
            pitch=pitch,
            start=0.0,
            end=min(float(loop_seconds), overflow),
        ),
    ]


def _rotated_event_time(
    event_time: float,
    *,
    phase_seconds: float,
    loop_seconds: float,
    musical_floor: float,
) -> float:
    if not math.isfinite(float(event_time)):
        return max(0.0, min(float(loop_seconds), float(musical_floor)))
    rotated = (
        max(0.0, min(float(loop_seconds), float(event_time)))
        + float(phase_seconds)
    ) % float(loop_seconds)
    if rotated < float(musical_floor):
        rotated += float(musical_floor)
    return max(0.0, min(float(loop_seconds), rotated))


def _state_before_cut(
    events: list[pretty_midi.PitchBend] | list[pretty_midi.ControlChange],
    *,
    cut_seconds: float,
    value_attr: str,
) -> int:
    ordered = sorted(events, key=lambda event: float(event.time))
    before = [
        event
        for event in ordered
        if float(event.time) < float(cut_seconds) - 1e-12
    ]
    chosen = before[-1] if before else ordered[-1]
    return int(getattr(chosen, value_attr))


def _canonical_pitch_bends(
    events: list[pretty_midi.PitchBend],
    *,
    loop_seconds: float,
    tick_seconds: float,
    note_end: float,
) -> list[pretty_midi.PitchBend]:
    """Return bounded bends with an ordered center reset inside the loop."""

    canonical: list[pretty_midi.PitchBend] = []
    for event in events:
        try:
            event_time = float(event.time)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(event_time):
            continue
        canonical.append(
            pretty_midi.PitchBend(
                pitch=max(-8192, min(8191, int(event.pitch))),
                time=max(0.0, min(float(loop_seconds), event_time)),
            )
        )
    canonical.sort(key=lambda event: (float(event.time), int(event.pitch)))
    terminal_guard = max(
        0.0,
        float(loop_seconds) - max(1e-9, float(tick_seconds)),
    )
    canonical = [
        event
        for event in canonical
        if not (
            int(event.pitch) != 0
            and float(event.time) > terminal_guard + 1e-12
        )
    ]
    if canonical and int(canonical[-1].pitch) != 0:
        reset_time = max(
            float(canonical[-1].time) + max(1e-9, float(tick_seconds)),
            max(0.0, min(float(loop_seconds), float(note_end))),
        )
        if reset_time <= float(loop_seconds):
            canonical.append(
                pretty_midi.PitchBend(pitch=0, time=reset_time)
            )
        else:
            canonical.pop()
    return canonical


def _dedupe_pitch_bends(
    events: list[pretty_midi.PitchBend],
) -> list[pretty_midi.PitchBend]:
    deduped: list[pretty_midi.PitchBend] = []
    for event in events:
        if (
            deduped
            and abs(float(event.time) - float(deduped[-1].time)) < 1e-12
            and int(event.pitch) == int(deduped[-1].pitch)
        ):
            continue
        deduped.append(event)
    return deduped


def _dedupe_control_changes(
    events: list[pretty_midi.ControlChange],
) -> list[pretty_midi.ControlChange]:
    ordered = sorted(
        events,
        key=lambda event: (
            float(event.time),
            int(event.number),
            int(event.value),
        ),
    )
    deduped: list[pretty_midi.ControlChange] = []
    for event in ordered:
        if (
            deduped
            and abs(float(event.time) - float(deduped[-1].time)) < 1e-12
            and int(event.number) == int(deduped[-1].number)
            and int(event.value) == int(deduped[-1].value)
        ):
            continue
        deduped.append(event)
    return deduped


def _pitch_bend_rpn_setup_event_ids(
    events: list[pretty_midi.ControlChange],
) -> set[int]:
    """Identify the renderer's ordered RPN 0,0 bend-range configuration."""

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


def merge_lane_midis(*, tempo: int, lanes: dict[str, bytes | None]) -> bytes:
    parsed_lanes: list[tuple[str, pretty_midi.PrettyMIDI]] = []
    resolution = 220
    for lane_name, data in lanes.items():
        if not data:
            continue
        lane_pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
        parsed_lanes.append((lane_name, lane_pm))
        resolution = max(resolution, int(lane_pm.resolution))

    # Retain the highest source PPQ so sub-5ms legato overlaps and slide curves
    # are not coarsened merely by asking for a combined session export.
    merged = pretty_midi.PrettyMIDI(
        initial_tempo=float(tempo),
        resolution=resolution,
    )
    for lane_name, lane_pm in parsed_lanes:
        for inst in lane_pm.instruments:
            out = pretty_midi.Instrument(
                program=inst.program,
                is_drum=inst.is_drum,
                name=inst.name or lane_name.title(),
            )
            out.notes = [
                pretty_midi.Note(
                    velocity=int(n.velocity),
                    pitch=int(n.pitch),
                    start=float(n.start),
                    end=float(n.end),
                )
                for n in inst.notes
            ]
            out.pitch_bends = list(inst.pitch_bends)
            out.control_changes = list(inst.control_changes)
            merged.instruments.append(out)
    buf = io.BytesIO()
    merged.write(buf)
    return buf.getvalue()


def zip_all_lanes(
    *,
    session_id: str,
    drums: bytes,
    bass: bytes,
    bass_mode: str,
    chords: bytes,
    lead: bytes,
) -> Response:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{session_id}_drums.mid", drums)
        zf.writestr(f"{session_id}_bass_{bass_mode}.mid", bass)
        zf.writestr(f"{session_id}_chords.mid", chords)
        zf.writestr(f"{session_id}_lead.mid", lead)
    filename = f"{session_id}_super_band_lanes.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
