"""Fail-closed local persistence for bass candidate-run metadata."""

from __future__ import annotations

import json
import math
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_RUNS_FILE = _DATA_DIR / "bass_candidate_runs.json"
_SCHEMA_VERSION = 1
_LOCK = RLock()


class CandidateStoreError(RuntimeError):
    """Candidate-run metadata could not be read or preserved safely."""


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


def _empty_document() -> dict[str, Any]:
    return {"schema_version": _SCHEMA_VERSION, "runs": []}


def _validate_document(document: object) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise CandidateStoreError("Candidate store root must be an object")

    # Candidate files created before schema versioning had only ``runs``.
    # Accept that exact legacy container and upgrade it on the next append.
    if "schema_version" in document:
        version = document.get("schema_version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != _SCHEMA_VERSION
        ):
            raise CandidateStoreError(
                f"Unsupported candidate store schema: {version!r}"
            )

    rows = document.get("runs")
    if not isinstance(rows, list):
        raise CandidateStoreError("Candidate store has no runs list")
    if any(not isinstance(row, dict) for row in rows):
        raise CandidateStoreError(
            "Candidate store contains a non-object run"
        )
    return {
        "schema_version": _SCHEMA_VERSION,
        "runs": rows,
    }


def _read_unlocked() -> dict[str, Any]:
    if not _RUNS_FILE.exists():
        return _empty_document()
    try:
        raw = _RUNS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        # An external cleanup may race the existence check. A missing store is
        # equivalent to a store that has not yet been created.
        return _empty_document()
    except (OSError, UnicodeError) as exc:
        raise CandidateStoreError(
            f"Candidate store could not be read: {exc}"
        ) from exc

    def reject_non_finite_constant(value: str) -> None:
        raise ValueError(
            f"Candidate store contains non-finite JSON value: {value}"
        )

    try:
        document = json.loads(
            raw,
            parse_constant=reject_non_finite_constant,
        )
        _assert_finite_json(document)
    except (json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise CandidateStoreError(
            f"Candidate store contains invalid JSON: {exc}"
        ) from exc
    return _validate_document(document)


def _atomic_write_json_unlocked(document: dict[str, Any]) -> None:
    tmp = _RUNS_FILE.with_name(
        f".{_RUNS_FILE.name}.{uuid4().hex}.tmp"
    )
    try:
        raw = json.dumps(
            document,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
        _RUNS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(raw, encoding="utf-8")
        tmp.replace(_RUNS_FILE)
    except (OSError, TypeError, ValueError, RecursionError) as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise CandidateStoreError(
            f"Candidate store could not be written safely: {exc}"
        ) from exc


def append_run(run_payload: dict[str, Any]) -> None:
    """Append one JSON-serializable run without losing concurrent appends."""

    if not isinstance(run_payload, dict):
        raise CandidateStoreError("Candidate run must be an object")
    with _LOCK:
        document = _read_unlocked()
        document["runs"].append(run_payload)
        _atomic_write_json_unlocked(document)


def load_runs() -> list[dict[str, Any]]:
    """Load every run, failing closed if the store cannot be trusted."""

    with _LOCK:
        document = _read_unlocked()
        return list(document["runs"])


def list_runs_for_session(session_id: str) -> list[dict[str, Any]]:
    target = str(session_id).strip()
    rows = [
        row
        for row in load_runs()
        if str(row.get("session_id", "")).strip() == target
    ]
    rows.sort(key=lambda row: str(row.get("created_at", "")), reverse=True)
    return rows


def get_run_for_session(
    session_id: str,
    run_id: str,
) -> dict[str, Any] | None:
    sid = str(session_id).strip()
    rid = str(run_id).strip()
    for row in load_runs():
        if (
            str(row.get("session_id", "")).strip() == sid
            and str(row.get("run_id", "")).strip() == rid
        ):
            return row
    return None
