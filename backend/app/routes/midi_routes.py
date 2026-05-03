"""MIDI utility routes."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.midi_audition import EmptyMidiBackend, MidiOutputBackend, RtMidiBackend

router = APIRouter()

_BACKEND: MidiOutputBackend = RtMidiBackend()


class MidiOutputResponseItem(BaseModel):
    id: str
    name: str


class MidiOutputsResponse(BaseModel):
    outputs: list[MidiOutputResponseItem]
    default: str | None = None
    hint: str | None = None


def set_midi_output_backend(backend: MidiOutputBackend) -> None:
    """Replace the enumeration backend for tests."""
    global _BACKEND
    _BACKEND = backend


@router.get("/outputs", response_model=MidiOutputsResponse)
def list_midi_outputs() -> MidiOutputsResponse:
    try:
        result = _BACKEND.list_outputs()
    except Exception as exc:
        result = EmptyMidiBackend(f"MIDI output enumeration failed: {exc}").list_outputs()
    return MidiOutputsResponse(
        outputs=[MidiOutputResponseItem(id=output.id, name=output.name) for output in result.outputs],
        default=result.default,
        hint=result.hint,
    )
