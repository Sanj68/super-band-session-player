"""Helpers for streaming MIDI bytes as downloads."""

from __future__ import annotations

import io
import zipfile

import pretty_midi
from fastapi.responses import Response


def lane_midi_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="audio/midi",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def merge_lane_midis(*, tempo: int, lanes: dict[str, bytes | None]) -> bytes:
    merged = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    for lane_name, data in lanes.items():
        if not data:
            continue
        lane_pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
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
    chords: bytes,
    lead: bytes,
) -> Response:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{session_id}_drums.mid", drums)
        zf.writestr(f"{session_id}_bass.mid", bass)
        zf.writestr(f"{session_id}_chords.mid", chords)
        zf.writestr(f"{session_id}_lead.mid", lead)
    filename = f"{session_id}_super_band_lanes.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
