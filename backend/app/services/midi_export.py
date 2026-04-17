"""Helpers for streaming MIDI bytes as downloads."""

from __future__ import annotations

import io
import zipfile

from fastapi.responses import Response


def lane_midi_response(data: bytes, filename: str) -> Response:
    return Response(
        content=data,
        media_type="audio/midi",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
