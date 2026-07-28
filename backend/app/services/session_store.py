"""Versioned local persistence for complete Session Player sessions."""

from __future__ import annotations

import base64
import json
from dataclasses import fields
import math
from pathlib import Path
from threading import RLock
from typing import Any, Mapping, TypeVar, get_type_hints
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter

from app.models.session import SourceAnalysis
from app.utils import music_theory as mt

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_SESSIONS_FILE = _DATA_DIR / "sessions.json"
_SCHEMA_VERSION = 1
_LOCK = RLock()
_SNAPSHOT_REVISION = 0

_MIDI_FIELDS = frozenset(
    {
        "drum_bytes",
        "bass_bytes",
        "bass_performance_bytes",
        "chords_bytes",
        "lead_bytes",
    }
)
_TRANSIENT_BRIDGE_FIELDS = frozenset(
    {
        "bridge_live_overlay_active",
        "bridge_live_base_source_analysis_override",
        "bridge_live_base_key",
        "bridge_live_base_scale",
    }
)

SessionT = TypeVar("SessionT")


class SessionStoreError(RuntimeError):
    """The on-disk snapshot exists but cannot be restored safely."""


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _assert_finite_json(value: object, *, path: str = "$") -> None:
    """Reject exponent-overflow infinities anywhere in decoded JSON."""

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Non-finite JSON number at {path}")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_finite_json(item, path=f"{path}.{key}")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite_json(item, path=f"{path}[{index}]")


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
    bridge_live = bool(
        getattr(session, "bridge_live_overlay_active", False)
    )
    for item in fields(session):
        if item.name in _TRANSIENT_BRIDGE_FIELDS:
            continue
        value = getattr(session, item.name)
        if bridge_live and item.name == "source_analysis_override":
            value = getattr(
                session,
                "bridge_live_base_source_analysis_override",
                None,
            )
        elif bridge_live and item.name == "key":
            value = (
                getattr(session, "bridge_live_base_key", None)
                or value
            )
        elif bridge_live and item.name == "scale":
            value = (
                getattr(session, "bridge_live_base_scale", None)
                or value
            )
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

    # StoredSession is a standard dataclass, so its constructor does not
    # enforce annotations. Validate each supplied field strictly before
    # construction instead of accepting values such as tempo="loud".
    hints = get_type_hints(session_type)
    for key, value in values.items():
        annotation = hints.get(key)
        if annotation is not None:
            TypeAdapter(annotation).validate_python(value, strict=True)

    session = session_type(**values)
    tempo = getattr(session, "tempo")
    bar_count = getattr(session, "bar_count")
    if not 40 <= tempo <= 240:
        raise ValueError("Session tempo must be between 40 and 240 BPM")
    if not 1 <= bar_count <= 128:
        raise ValueError("Session bar_count must be between 1 and 128")
    mt.key_root_pc(str(getattr(session, "key")))
    mt.normalize_scale(str(getattr(session, "scale")))

    bounded_floats = {
        "bass_expression": (0.0, 1.0),
        "bass_phase_offset_beats": (0.0, 4.0),
        "bass_density_bias": (-1.0, 1.0),
        "reference_audio_duration_seconds": (0.0, None),
        "reference_audio_head_trim_seconds": (0.0, None),
        "groove_reference_audio_duration_seconds": (0.0, None),
        "groove_reference_audio_head_trim_seconds": (0.0, None),
    }
    for field_name, (minimum, maximum) in bounded_floats.items():
        value = float(getattr(session, field_name))
        if not math.isfinite(value) or value < minimum:
            raise ValueError(f"Invalid persisted {field_name}")
        if maximum is not None and value > maximum:
            raise ValueError(f"Invalid persisted {field_name}")
    lock = getattr(session, "bass_lock_to_groove")
    if lock is not None and (
        not math.isfinite(float(lock))
        or not 0.0 <= float(lock) <= 1.0
    ):
        raise ValueError("Invalid persisted bass_lock_to_groove")
    focus = getattr(session, "bass_articulation_focus")
    if focus not in {"natural", "clean", "ghosted", "muted", "connected"}:
        raise ValueError("Invalid persisted bass_articulation_focus")
    performance_controls = getattr(session, "bass_performance_controls", None)
    if performance_controls is not None:
        expected = {
            "ghost",
            "mute",
            "slide",
            "legato",
            "timing_humanize",
            "velocity_humanize",
        }
        if (
            not isinstance(performance_controls, dict)
            or set(performance_controls) != expected
        ):
            raise ValueError("Invalid persisted bass_performance_controls")
        for key, value in performance_controls.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or not 0.0 <= float(value) <= 1.0
            ):
                raise ValueError(
                    f"Invalid persisted bass_performance_controls.{key}"
                )
    return session


def save_sessions(sessions: Mapping[str, object]) -> int:
    """Atomically replace the local snapshot and return its monotonic revision.

    Snapshot capture deliberately happens while holding the same lock as the
    write.  If two request middlewares finish concurrently, the later writer
    therefore serializes the current mapping after the earlier writer instead
    of capturing stale state first and overwriting a newer snapshot later.
    """

    global _SNAPSHOT_REVISION

    with _LOCK:
        revision = _SNAPSHOT_REVISION + 1
        document = {
            "schema_version": _SCHEMA_VERSION,
            "snapshot_revision": revision,
            "sessions": [
                _session_to_payload(session)
                for session in sessions.values()
            ],
        }
        try:
            raw = json.dumps(
                document,
                indent=2,
                sort_keys=True,
                allow_nan=False,
            ) + "\n"
        except (TypeError, ValueError) as exc:
            raise SessionStoreError(
                f"Session snapshot contains non-JSON state: {exc}"
            ) from exc
        tmp = _SESSIONS_FILE.with_name(
            f".{_SESSIONS_FILE.name}.{uuid4().hex}.tmp"
        )
        try:
            _SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(raw, encoding="utf-8")
            tmp.replace(_SESSIONS_FILE)
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        _SNAPSHOT_REVISION = revision
        return revision


def load_sessions(session_type: type[SessionT]) -> dict[str, SessionT]:
    """Load sessions in stored order, failing closed on a corrupt snapshot."""

    global _SNAPSHOT_REVISION

    with _LOCK:
        if not _SESSIONS_FILE.exists():
            return {}
        try:
            document = json.loads(
                _SESSIONS_FILE.read_text(encoding="utf-8"),
                parse_constant=_reject_json_constant,
            )
            _assert_finite_json(document)
        except (
            json.JSONDecodeError,
            OSError,
            ValueError,
            UnicodeError,
            RecursionError,
        ) as exc:
            raise SessionStoreError(f"Could not read session snapshot: {exc}") from exc

        if not isinstance(document, dict) or document.get("schema_version") != _SCHEMA_VERSION:
            raise SessionStoreError("Unsupported session snapshot schema")
        revision = document.get("snapshot_revision", 0)
        if (
            isinstance(revision, bool)
            or not isinstance(revision, int)
            or revision < 0
        ):
            raise SessionStoreError("Invalid session snapshot revision")
        rows = document.get("sessions")
        if not isinstance(rows, list):
            raise SessionStoreError("Session snapshot has no sessions list")

        restored: dict[str, SessionT] = {}
        for index, row in enumerate(rows):
            try:
                session = _payload_to_session(row, session_type)
                session_id = str(getattr(session, "id")).strip()
            except (TypeError, ValueError) as exc:
                raise SessionStoreError(
                    f"Invalid session at index {index}: {exc}"
                ) from exc
            if not session_id:
                raise SessionStoreError(f"Session at index {index} has no id")
            if session_id in restored:
                raise SessionStoreError(f"Duplicate session id: {session_id}")
            restored[session_id] = session
        _SNAPSHOT_REVISION = max(_SNAPSHOT_REVISION, revision)
        return restored
