"""Recoverable local history for the Session Player Bass plugin.

History is deliberately separate from candidate runs. A plugin idea may come
from a direct regeneration, a natural-language edit, or a promoted candidate;
the producer must be able to recover the exact playable MIDI in every case.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_HISTORY_FILE = _DATA_DIR / "bass_history.json"
_SCHEMA_VERSION = 1
_MAX_UNKEPT_PER_SESSION = 48
_LOCK = RLock()

_RESTORED_FIELDS = (
    "bass_style",
    "bass_instrument",
    "bass_player",
    "bass_engine",
    "bass_lock_to_groove",
    "bass_expression",
    "bass_density_bias",
    "bass_seed",
    "bass_preview",
    "bass_locked",
    "current_bass_candidate_run_id",
    "current_bass_candidate_take_id",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_bytes(value: bytes | None) -> str | None:
    return None if value is None else base64.b64encode(value).decode("ascii")


def _decode_bytes(value: object) -> bytes | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Stored bass MIDI must be base64 text or null")
    return base64.b64decode(value.encode("ascii"), validate=True)


def _context_payload(session: object) -> dict[str, Any]:
    return {
        "tempo": int(getattr(session, "tempo")),
        "key": str(getattr(session, "key")),
        "scale": str(getattr(session, "scale")),
        "bar_count": int(getattr(session, "bar_count")),
        "chord_progression": getattr(session, "chord_progression"),
        "reference_audio_path": getattr(session, "reference_audio_path"),
        "groove_reference_audio_path": getattr(
            session, "groove_reference_audio_path"
        ),
    }


def _stable_hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def context_fingerprint(session: object) -> str:
    """Identify the musical context in which an idea is safe to recall."""

    return _stable_hash(_context_payload(session))


def part_fingerprint(session: object) -> str:
    """Identify exact MIDI plus the controls needed to explain/replay it."""

    payload = {
        "context": _context_payload(session),
        "bass_bytes": _encode_bytes(getattr(session, "bass_bytes")),
        "bass_performance_bytes": _encode_bytes(
            getattr(session, "bass_performance_bytes")
        ),
        "controls": {
            field: getattr(session, field)
            for field in _RESTORED_FIELDS
        },
    }
    return _stable_hash(payload)


def _empty_document() -> dict[str, Any]:
    return {"schema_version": _SCHEMA_VERSION, "snapshots": []}


def _load_unlocked() -> dict[str, Any]:
    if not _HISTORY_FILE.exists():
        return _empty_document()
    try:
        document = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return _empty_document()
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != _SCHEMA_VERSION
        or not isinstance(document.get("snapshots"), list)
    ):
        return _empty_document()
    return document


def _write_unlocked(document: dict[str, Any]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(document, indent=2, sort_keys=True) + "\n"
    tmp = _HISTORY_FILE.with_suffix(".json.tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(_HISTORY_FILE)


def _snapshot_from_session(session: object, *, kept: bool) -> dict[str, Any]:
    created_at = _now()
    return {
        "snapshot_id": f"idea_{uuid4().hex[:12]}",
        "session_id": str(getattr(session, "id")),
        "created_at": created_at,
        "kept": bool(kept),
        "kept_at": created_at if kept else None,
        "context_fingerprint": context_fingerprint(session),
        "part_fingerprint": part_fingerprint(session),
        "context": _context_payload(session),
        "controls": {
            field: getattr(session, field)
            for field in _RESTORED_FIELDS
        },
        "bass_bytes": _encode_bytes(getattr(session, "bass_bytes")),
        "bass_performance_bytes": _encode_bytes(
            getattr(session, "bass_performance_bytes")
        ),
    }


def _trim_unkept(rows: list[dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
    matching = [
        row
        for row in rows
        if str(row.get("session_id", "")) == session_id and not row.get("kept")
    ]
    overflow = max(0, len(matching) - _MAX_UNKEPT_PER_SESSION)
    if overflow == 0:
        return rows
    remove_ids = {
        str(row.get("snapshot_id", ""))
        for row in matching[:overflow]
    }
    return [
        row
        for row in rows
        if str(row.get("snapshot_id", "")) not in remove_ids
    ]


def capture(session: object, *, kept: bool = False) -> dict[str, Any]:
    """Store the current exact bass idea, deduplicating identical captures."""

    if not getattr(session, "bass_bytes", None):
        raise ValueError("Cannot keep a session without a bass part")
    session_id = str(getattr(session, "id"))
    fingerprint = part_fingerprint(session)
    with _LOCK:
        document = _load_unlocked()
        rows = [
            row for row in document["snapshots"]
            if isinstance(row, dict)
        ]
        existing = next(
            (
                row
                for row in rows
                if str(row.get("session_id", "")) == session_id
                and str(row.get("part_fingerprint", "")) == fingerprint
            ),
            None,
        )
        if existing is not None:
            if kept and not existing.get("kept"):
                existing["kept"] = True
                existing["kept_at"] = _now()
                document["snapshots"] = rows
                _write_unlocked(document)
            return dict(existing)

        snapshot = _snapshot_from_session(session, kept=kept)
        rows.append(snapshot)
        rows = _trim_unkept(rows, session_id)
        document["snapshots"] = rows
        _write_unlocked(document)
        return dict(snapshot)


def _compatible_rows(session: object) -> list[dict[str, Any]]:
    session_id = str(getattr(session, "id"))
    context_id = context_fingerprint(session)
    document = _load_unlocked()
    return [
        row
        for row in document["snapshots"]
        if isinstance(row, dict)
        and str(row.get("session_id", "")) == session_id
        and str(row.get("context_fingerprint", "")) == context_id
    ]


def history_state(session: object) -> dict[str, Any]:
    """Return compact navigation metadata without exposing encoded MIDI."""

    with _LOCK:
        rows = _compatible_rows(session)
    active_fingerprint = part_fingerprint(session)
    current_index: int | None = next(
        (
            index
            for index, row in enumerate(rows)
            if str(row.get("part_fingerprint", "")) == active_fingerprint
        ),
        None,
    )
    if current_index is None:
        can_previous = bool(rows)
        can_next = False
    else:
        can_previous = current_index > 0
        can_next = current_index < len(rows) - 1
    return {
        "session_id": str(getattr(session, "id")),
        "count": len(rows),
        "kept_count": sum(1 for row in rows if bool(row.get("kept"))),
        "current_index": (
            current_index + 1 if current_index is not None else None
        ),
        "current_is_kept": bool(
            current_index is not None and rows[current_index].get("kept")
        ),
        "can_previous": can_previous,
        "can_next": can_next,
        "entries": [
            {
                "snapshot_id": str(row.get("snapshot_id", "")),
                "created_at": str(row.get("created_at", "")),
                "kept": bool(row.get("kept")),
                "bass_style": str(
                    (row.get("controls") or {}).get("bass_style", "supportive")
                ),
                "bass_instrument": str(
                    (row.get("controls") or {}).get(
                        "bass_instrument", "finger_bass"
                    )
                ),
            }
            for row in rows
        ],
    }


def recall(session: object, snapshot_id: str) -> dict[str, Any]:
    """Restore one exact compatible idea into the live stored session."""

    with _LOCK:
        rows = _compatible_rows(session)
        snapshot = next(
            (
                row
                for row in rows
                if str(row.get("snapshot_id", "")) == str(snapshot_id)
            ),
            None,
        )
    if snapshot is None:
        raise KeyError(snapshot_id)
    controls = snapshot.get("controls")
    if not isinstance(controls, dict):
        raise ValueError("Stored bass history controls are invalid")
    bass_bytes = _decode_bytes(snapshot.get("bass_bytes"))
    if bass_bytes is None:
        raise ValueError("Stored bass history has no clean MIDI")
    setattr(session, "bass_bytes", bass_bytes)
    setattr(
        session,
        "bass_performance_bytes",
        _decode_bytes(snapshot.get("bass_performance_bytes")),
    )
    for field in _RESTORED_FIELDS:
        if field in controls:
            setattr(session, field, controls[field])
    return dict(snapshot)


def navigate(session: object, direction: str) -> dict[str, Any]:
    """Move to the adjacent exact idea, first preserving the active idea."""

    if direction not in {"previous", "next"}:
        raise ValueError("direction must be previous or next")
    active = capture(session, kept=False)
    with _LOCK:
        rows = _compatible_rows(session)
    active_id = str(active["snapshot_id"])
    active_index = next(
        index
        for index, row in enumerate(rows)
        if str(row.get("snapshot_id", "")) == active_id
    )
    target_index = active_index - 1 if direction == "previous" else active_index + 1
    if target_index < 0 or target_index >= len(rows):
        raise IndexError(direction)
    return recall(session, str(rows[target_index]["snapshot_id"]))
