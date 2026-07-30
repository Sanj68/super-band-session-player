"""Fail-closed local JSON persistence for bass clip/take evaluations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Callable, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from app.models.evaluation import ClipEvaluationRecord

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_EVALS_FILE = _DATA_DIR / "bass_take_evaluations.json"
_SCHEMA_VERSION = 1
_LOCK = RLock()

ResultT = TypeVar("ResultT")


class EvaluationStoreError(RuntimeError):
    """Saved evaluations could not be preserved or restored safely."""


def _empty_document() -> dict[str, Any]:
    return {"schema_version": _SCHEMA_VERSION, "clips": []}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _quarantine_pattern() -> str:
    return f"{_EVALS_FILE.name}.quarantine-*"


def _quarantine_unlocked(reason: str) -> Path:
    quarantine = _EVALS_FILE.with_name(
        f"{_EVALS_FILE.name}.quarantine-{reason}-{uuid4().hex}"
    )
    try:
        _EVALS_FILE.replace(quarantine)
    except OSError as exc:
        raise EvaluationStoreError(
            "Saved evaluations are invalid and could not be preserved for recovery"
        ) from exc
    return quarantine


def _assert_initializable_unlocked() -> None:
    if _EVALS_FILE.exists():
        return
    quarantines = sorted(_EVALS_FILE.parent.glob(_quarantine_pattern()))
    if quarantines:
        raise EvaluationStoreError(
            f"Saved evaluations require recovery from {quarantines[-1].name}"
        )
    _write_unlocked(_empty_document())


def _write_unlocked(document: dict[str, Any]) -> None:
    try:
        raw = json.dumps(
            document,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
    except (TypeError, ValueError) as exc:
        raise EvaluationStoreError(
            f"Saved evaluations contain non-JSON state: {exc}"
        ) from exc
    temporary = _EVALS_FILE.with_name(
        f".{_EVALS_FILE.name}.{uuid4().hex}.tmp"
    )
    try:
        _EVALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(_EVALS_FILE)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise EvaluationStoreError(
            "Saved evaluations could not be written safely"
        ) from exc


def _load_unlocked() -> list[ClipEvaluationRecord]:
    _assert_initializable_unlocked()
    try:
        document = json.loads(
            _EVALS_FILE.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except FileNotFoundError:
        _assert_initializable_unlocked()
        return _load_unlocked()
    except (json.JSONDecodeError, UnicodeError, RecursionError, ValueError):
        quarantine = _quarantine_unlocked("invalid-json")
        raise EvaluationStoreError(
            f"Invalid saved evaluations were preserved at {quarantine.name}"
        )
    except OSError:
        quarantine = _quarantine_unlocked("unreadable")
        raise EvaluationStoreError(
            f"Unreadable saved evaluations were preserved at {quarantine.name}"
        )
    if not isinstance(document, dict):
        quarantine = _quarantine_unlocked("invalid-document")
        raise EvaluationStoreError(
            f"Invalid saved evaluations were preserved at {quarantine.name}"
        )
    schema = document.get("schema_version", _SCHEMA_VERSION)
    rows = document.get("clips")
    if schema != _SCHEMA_VERSION or not isinstance(rows, list):
        quarantine = _quarantine_unlocked("unsupported-schema")
        raise EvaluationStoreError(
            f"Unsupported saved evaluations were preserved at {quarantine.name}"
        )
    records: list[ClipEvaluationRecord] = []
    try:
        for row in rows:
            records.append(ClipEvaluationRecord.model_validate(row))
    except (TypeError, ValidationError, ValueError):
        quarantine = _quarantine_unlocked("invalid-record")
        raise EvaluationStoreError(
            f"Invalid evaluation records were preserved at {quarantine.name}"
        )
    return records


def _document(
    records: list[ClipEvaluationRecord],
) -> dict[str, Any]:
    try:
        validated = [
            ClipEvaluationRecord.model_validate(record).model_dump(mode="json")
            for record in records
        ]
    except (TypeError, ValidationError, ValueError) as exc:
        raise EvaluationStoreError(
            "Refusing to save invalid evaluation records"
        ) from exc
    return {
        "schema_version": _SCHEMA_VERSION,
        "clips": validated,
    }


def load_records() -> list[ClipEvaluationRecord]:
    with _LOCK:
        return _load_unlocked()


def save_records(records: list[ClipEvaluationRecord]) -> None:
    with _LOCK:
        _write_unlocked(_document(records))


def mutate_records(
    mutation: Callable[[list[ClipEvaluationRecord]], ResultT],
) -> ResultT:
    """Apply one complete read-modify-write transaction under the store lock."""

    with _LOCK:
        records = _load_unlocked()
        result = mutation(records)
        _write_unlocked(_document(records))
        return result


def find_clip_index(
    records: list[ClipEvaluationRecord],
    clip_id: str,
) -> int | None:
    target = clip_id.strip()
    for i, record in enumerate(records):
        if record.clip_id == target:
            return i
    return None
