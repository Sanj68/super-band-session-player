"""Versioned local persistence for complete Session Player sessions."""

from __future__ import annotations

import base64
import json
from dataclasses import fields
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, TypeVar

from pydantic import BaseModel

from app.models.session import SourceAnalysis

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_SESSIONS_FILE = _DATA_DIR / "sessions.json"
_SCHEMA_VERSION = 1
_LOCK = RLock()

_MIDI_FIELDS = frozenset(
    {
        "drum_bytes",
        "bass_bytes",
        "bass_performance_bytes",
        "chords_bytes",
        "lead_bytes",
    }
)

SessionT = TypeVar("SessionT")


class SessionStoreError(RuntimeError):
    """The on-disk snapshot exists but cannot be restored safely."""


def _encode_bytes(value: bytes | None) -> str | None:
    if value is None:
        return None
    return base64.b64encode(value).decode("ascii")


def _decode_bytes(value: object) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("MIDI payload must be base64 text or null")
    return base64.b64decode(value.encode("ascii"), validate=True)


def _session_to_payload(session: object) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for item in fields(session):
        value = getattr(session, item.name)
        if item.name in _MIDI_FIELDS:
            payload[item.name] = _encode_bytes(value)
        elif isinstance(value, BaseModel):
            payload[item.name] = value.model_dump(mode="json")
        else:
            payload[item.name] = value
    return payload


def _payload_to_session(payload: object, session_type: type[SessionT]) -> SessionT:
    if not isinstance(payload, dict):
        raise ValueError("Session payload must be an object")

    allowed = {item.name for item in fields(session_type)}
    values = {key: value for key, value in payload.items() if key in allowed}
    for field_name in _MIDI_FIELDS:
        if field_name in values:
            values[field_name] = _decode_bytes(values[field_name])

    source = values.get("source_analysis_override")
    if source is not None:
        values["source_analysis_override"] = SourceAnalysis.model_validate(source)
    groove_source = values.get("groove_reference_analysis_override")
    if groove_source is not None:
        values["groove_reference_analysis_override"] = SourceAnalysis.model_validate(groove_source)

    return session_type(**values)


def save_sessions(sessions: Mapping[str, object]) -> None:
    """Atomically replace the local snapshot while preserving creation order."""

    document = {
        "schema_version": _SCHEMA_VERSION,
        "sessions": [_session_to_payload(session) for session in sessions.values()],
    }
    raw = json.dumps(document, indent=2, sort_keys=True) + "\n"
    with _LOCK:
        _SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _SESSIONS_FILE.with_suffix(".json.tmp")
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(_SESSIONS_FILE)


def load_sessions(session_type: type[SessionT]) -> dict[str, SessionT]:
    """Load sessions in stored order, failing closed on a corrupt snapshot."""

    with _LOCK:
        if not _SESSIONS_FILE.exists():
            return {}
        try:
            document = json.loads(_SESSIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise SessionStoreError(f"Could not read session snapshot: {exc}") from exc

    if not isinstance(document, dict) or document.get("schema_version") != _SCHEMA_VERSION:
        raise SessionStoreError("Unsupported session snapshot schema")
    rows = document.get("sessions")
    if not isinstance(rows, list):
        raise SessionStoreError("Session snapshot has no sessions list")

    restored: dict[str, SessionT] = {}
    for index, row in enumerate(rows):
        try:
            session = _payload_to_session(row, session_type)
            session_id = str(getattr(session, "id")).strip()
        except (TypeError, ValueError) as exc:
            raise SessionStoreError(f"Invalid session at index {index}: {exc}") from exc
        if not session_id:
            raise SessionStoreError(f"Session at index {index} has no id")
        if session_id in restored:
            raise SessionStoreError(f"Duplicate session id: {session_id}")
        restored[session_id] = session
    return restored
