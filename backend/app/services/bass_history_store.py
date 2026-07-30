"""Recoverable local history for the Session Player Bass plugin.

History is deliberately separate from candidate runs. A plugin idea may come
from a direct regeneration, a natural-language edit, or a promoted candidate;
the producer must be able to recover the exact playable MIDI in every case.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
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
    "bass_articulation_focus",
    "bass_expression",
    "bass_performance_controls",
    "bass_phase_offset_beats",
    "bass_density_bias",
    "bass_seed",
    "bass_preview",
    "bass_locked",
    "current_bass_candidate_run_id",
    "current_bass_candidate_take_id",
)
_RESTORED_FIELD_DEFAULTS: dict[str, object] = {
    "bass_articulation_focus": "natural",
    "bass_performance_controls": None,
}


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
    fusion_payload = getattr(session, "fusion_contract_payload", None)
    payload = {
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
    if (
        isinstance(fusion_payload, dict)
        and fusion_payload.get("contract_id") is not None
    ):
        payload["fusion_contract_id"] = str(fusion_payload["contract_id"])
    return payload


def _stable_hash(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def context_fingerprint(session: object) -> str:
    """Identify the musical context in which an idea is safe to recall."""

    return _stable_hash(_context_payload(session))


def part_fingerprint(session: object) -> str:
    """Identify exact MIDI plus the controls needed to explain/replay it."""

    return _part_fingerprint_from_values(
        context=_context_payload(session),
        bass_bytes=_encode_bytes(getattr(session, "bass_bytes")),
        bass_performance_bytes=_encode_bytes(
            getattr(session, "bass_performance_bytes")
        ),
        controls={
            field: getattr(
                session,
                field,
                _RESTORED_FIELD_DEFAULTS.get(field),
            )
            for field in _RESTORED_FIELDS
        },
    )


def _part_fingerprint_from_values(
    *,
    context: object,
    bass_bytes: object,
    bass_performance_bytes: object,
    controls: object,
) -> str:
    return _stable_hash(
        {
            "context": context,
            "bass_bytes": bass_bytes,
            "bass_performance_bytes": bass_performance_bytes,
            "controls": controls,
        }
    )


def _empty_document() -> dict[str, Any]:
    return {"schema_version": _SCHEMA_VERSION, "snapshots": []}


class BassHistoryStoreError(RuntimeError):
    """The history file could not be preserved safely."""


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _controls_have_supported_shape(controls: dict[str, Any]) -> bool:
    required = set(_RESTORED_FIELDS) - {
        "bass_phase_offset_beats",
        "bass_articulation_focus",
        "bass_performance_controls",
    }
    if not required.issubset(controls):
        return False
    if any(
        not isinstance(controls.get(field), str)
        for field in ("bass_style", "bass_instrument", "bass_engine", "bass_preview")
    ):
        return False
    if any(
        controls.get(field) is not None
        and not isinstance(controls.get(field), str)
        for field in (
            "bass_player",
            "current_bass_candidate_run_id",
            "current_bass_candidate_take_id",
        )
    ):
        return False
    if controls.get("bass_lock_to_groove") is not None and not _is_finite_number(
        controls.get("bass_lock_to_groove")
    ):
        return False
    if (
        "bass_articulation_focus" in controls
        and controls.get("bass_articulation_focus")
        not in {"natural", "clean", "ghosted", "muted", "connected"}
    ):
        return False
    if any(
        not _is_finite_number(controls.get(field))
        for field in ("bass_expression", "bass_density_bias")
    ):
        return False
    performance_controls = controls.get("bass_performance_controls")
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
            or any(
                not _is_finite_number(value)
                or not 0.0 <= float(value) <= 1.0
                for value in performance_controls.values()
            )
        ):
            return False
    if "bass_phase_offset_beats" in controls and not _is_finite_number(
        controls.get("bass_phase_offset_beats")
    ):
        return False
    seed = controls.get("bass_seed")
    if seed is not None and (not isinstance(seed, int) or isinstance(seed, bool)):
        return False
    return isinstance(controls.get("bass_locked"), bool)


def _snapshot_has_supported_shape(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    required_text = (
        "snapshot_id",
        "session_id",
        "created_at",
        "context_fingerprint",
        "part_fingerprint",
    )
    if any(
        not isinstance(row.get(field), str) or not row[field]
        for field in required_text
    ):
        return False
    if not isinstance(row.get("kept"), bool):
        return False
    if row.get("kept_at") is not None and not isinstance(row.get("kept_at"), str):
        return False
    if not isinstance(row.get("context"), dict) or not isinstance(row.get("controls"), dict):
        return False
    if not _controls_have_supported_shape(row["controls"]):
        return False
    if not isinstance(row.get("bass_bytes"), str):
        return False
    if row.get("bass_performance_bytes") is not None and not isinstance(
        row.get("bass_performance_bytes"), str
    ):
        return False
    try:
        if _decode_bytes(row["bass_bytes"]) is None:
            return False
        _decode_bytes(row.get("bass_performance_bytes"))
    except (ValueError, binascii.Error):
        return False
    return True


def _document_has_supported_shape(document: object) -> bool:
    version = (
        document.get("schema_version")
        if isinstance(document, dict)
        else None
    )
    return (
        isinstance(document, dict)
        and isinstance(version, int)
        and not isinstance(version, bool)
        and version == _SCHEMA_VERSION
        and isinstance(document.get("snapshots"), list)
        and all(_snapshot_has_supported_shape(row) for row in document["snapshots"])
    )


def _document_fingerprints_are_valid(document: dict[str, Any]) -> bool:
    for row in document["snapshots"]:
        if row["context_fingerprint"] != _stable_hash(row["context"]):
            return False
        expected_part = _part_fingerprint_from_values(
            context=row["context"],
            bass_bytes=row["bass_bytes"],
            bass_performance_bytes=row.get("bass_performance_bytes"),
            controls=row["controls"],
        )
        if row["part_fingerprint"] != expected_part:
            return False
    return True


def _migrate_legacy_articulation_focus(document: dict[str, Any]) -> bool:
    """Upgrade pre-Touch snapshots without quarantining recoverable ideas."""

    changed = False
    for row in document["snapshots"]:
        controls = row["controls"]
        if "bass_articulation_focus" in controls:
            continue
        controls["bass_articulation_focus"] = "natural"
        row["part_fingerprint"] = _part_fingerprint_from_values(
            context=row["context"],
            bass_bytes=row["bass_bytes"],
            bass_performance_bytes=row.get("bass_performance_bytes"),
            controls=controls,
        )
        changed = True
    return changed


def _quarantine_unlocked(reason: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    quarantine_file = _HISTORY_FILE.with_name(
        f"{_HISTORY_FILE.name}.quarantine-{reason}-{stamp}-{uuid4().hex[:8]}"
    )
    try:
        _HISTORY_FILE.replace(quarantine_file)
    except OSError as exc:
        raise BassHistoryStoreError(
            "Bass history is invalid and could not be preserved for recovery"
        ) from exc
    return quarantine_file


def _load_unlocked() -> dict[str, Any]:
    if not _HISTORY_FILE.exists():
        return _empty_document()
    try:
        document = json.loads(_HISTORY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _empty_document()
    except (json.JSONDecodeError, UnicodeError, RecursionError):
        quarantine_file = _quarantine_unlocked("invalid-json")
        raise BassHistoryStoreError(
            f"Invalid bass history was preserved at {quarantine_file.name}"
        )
    except OSError:
        quarantine_file = _quarantine_unlocked("unreadable")
        raise BassHistoryStoreError(
            f"Unreadable bass history was preserved at {quarantine_file.name}"
        )
    if not _document_has_supported_shape(document):
        quarantine_file = _quarantine_unlocked("unsupported-schema")
        raise BassHistoryStoreError(
            f"Unsupported bass history was preserved at {quarantine_file.name}"
        )
    if not _document_fingerprints_are_valid(document):
        quarantine_file = _quarantine_unlocked("invalid-fingerprint")
        raise BassHistoryStoreError(
            f"Invalid bass history was preserved at {quarantine_file.name}"
        )
    if _migrate_legacy_articulation_focus(document):
        _write_unlocked(document)
    return document


def _write_unlocked(document: dict[str, Any]) -> None:
    raw = json.dumps(document, indent=2, sort_keys=True) + "\n"
    tmp = _HISTORY_FILE.with_suffix(".json.tmp")
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(_HISTORY_FILE)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise BassHistoryStoreError(
            "Bass history could not be written safely"
        ) from exc


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
            field: getattr(
                session,
                field,
                _RESTORED_FIELD_DEFAULTS.get(field),
            )
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


def referenced_audio_paths() -> set[str]:
    """Return audio blobs needed to recover every retained Bass snapshot."""

    with _LOCK:
        document = _load_unlocked()
        referenced: set[str] = set()
        for row in document["snapshots"]:
            if not isinstance(row, dict):
                continue
            context = row.get("context")
            if not isinstance(context, dict):
                continue
            for field_name in (
                "reference_audio_path",
                "groove_reference_audio_path",
            ):
                value = context.get(field_name)
                if isinstance(value, str) and value.strip():
                    referenced.add(value)
        return referenced


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
    fusion_context = isinstance(
        getattr(session, "fusion_contract_payload", None),
        dict,
    )
    document = _load_unlocked()
    return [
        row
        for row in document["snapshots"]
        if isinstance(row, dict)
        and str(row.get("session_id", "")) == session_id
        and str(row.get("context_fingerprint", "")) == context_id
        and (
            not fusion_context
            or (
                isinstance(row.get("controls"), dict)
                and str(row["controls"].get("bass_engine", ""))
                == "phrase_v2"
            )
        )
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
                "bass_articulation_focus": str(
                    (row.get("controls") or {}).get(
                        "bass_articulation_focus", "natural"
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
    if "bass_articulation_focus" not in controls:
        setattr(session, "bass_articulation_focus", "natural")
    if "bass_performance_controls" not in controls:
        setattr(session, "bass_performance_controls", None)
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
