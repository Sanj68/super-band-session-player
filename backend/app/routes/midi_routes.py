"""MIDI utility routes."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services.midi_audition import (
    AuditionPlayer,
    EmptyMidiBackend,
    MidiOutputBackend,
    RtMidiBackend,
    get_persisted_target_id,
    set_persisted_target_id,
)

router = APIRouter()

_BACKEND: MidiOutputBackend = RtMidiBackend()
_PLAYER = AuditionPlayer(_BACKEND)


class MidiOutputResponseItem(BaseModel):
    id: str
    name: str


class MidiOutputsResponse(BaseModel):
    outputs: list[MidiOutputResponseItem]
    default: str | None = None
    hint: str | None = None


class MidiAuditionStateResponse(BaseModel):
    playing: bool
    session_id: str | None = None
    mode: str | None = None
    output: str | None = None
    started_at: float | None = None
    duration_seconds: float | None = None


class MidiStopResponse(BaseModel):
    status: str


def set_midi_output_backend(backend: MidiOutputBackend) -> None:
    """Replace the enumeration backend for tests."""
    global _BACKEND
    _BACKEND = backend
    _PLAYER.set_backend(backend)


def get_audition_player() -> AuditionPlayer:
    return _PLAYER


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


@router.post("/stop", response_model=MidiStopResponse)
def stop_midi_audition() -> MidiStopResponse:
    _PLAYER.stop()
    return MidiStopResponse(status="stopped")


@router.get("/audition/state", response_model=MidiAuditionStateResponse)
def get_midi_audition_state() -> MidiAuditionStateResponse:
    return MidiAuditionStateResponse(**_PLAYER.state().to_dict())


class MidiTargetResponse(BaseModel):
    output_id: str


class MidiTargetRequest(BaseModel):
    output_id: str


@router.get("/target", response_model=MidiTargetResponse)
def get_midi_target() -> MidiTargetResponse:
    return MidiTargetResponse(output_id=get_persisted_target_id())


@router.post("/target", response_model=MidiTargetResponse)
def set_midi_target(payload: MidiTargetRequest) -> MidiTargetResponse:
    stored = set_persisted_target_id(payload.output_id)
    return MidiTargetResponse(output_id=stored)
