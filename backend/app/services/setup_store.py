"""Fail-closed local JSON persistence for saved band setups."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Callable, TypeVar
from uuid import uuid4

from pydantic import ValidationError

from app.models.setup import BandSetup

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_SETUPS_FILE = _DATA_DIR / "band_setups.json"
_SCHEMA_VERSION = 1
_LOCK = RLock()

ResultT = TypeVar("ResultT")


class SetupStoreError(RuntimeError):
    """Saved setups could not be preserved or restored safely."""


def _empty_document() -> dict[str, Any]:
    return {"schema_version": _SCHEMA_VERSION, "setups": []}


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON constant: {value}")


def _quarantine_pattern() -> str:
    return f"{_SETUPS_FILE.name}.quarantine-*"


def _quarantine_unlocked(reason: str) -> Path:
    quarantine = _SETUPS_FILE.with_name(
        f"{_SETUPS_FILE.name}.quarantine-{reason}-{uuid4().hex}"
    )
    try:
        _SETUPS_FILE.replace(quarantine)
    except OSError as exc:
        raise SetupStoreError(
            "Saved setups are invalid and could not be preserved for recovery"
        ) from exc
    return quarantine


def _assert_initializable_unlocked() -> None:
    if _SETUPS_FILE.exists():
        return
    quarantines = sorted(_SETUPS_FILE.parent.glob(_quarantine_pattern()))
    if quarantines:
        raise SetupStoreError(
            f"Saved setups require recovery from {quarantines[-1].name}"
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
        raise SetupStoreError(
            f"Saved setups contain non-JSON state: {exc}"
        ) from exc
    temporary = _SETUPS_FILE.with_name(
        f".{_SETUPS_FILE.name}.{uuid4().hex}.tmp"
    )
    try:
        _SETUPS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(_SETUPS_FILE)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise SetupStoreError(
            "Saved setups could not be written safely"
        ) from exc


def _load_unlocked() -> list[BandSetup]:
    _assert_initializable_unlocked()
    try:
        document = json.loads(
            _SETUPS_FILE.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except FileNotFoundError:
        _assert_initializable_unlocked()
        return _load_unlocked()
    except (json.JSONDecodeError, UnicodeError, RecursionError, ValueError):
        quarantine = _quarantine_unlocked("invalid-json")
        raise SetupStoreError(
            f"Invalid saved setups were preserved at {quarantine.name}"
        )
    except OSError:
        quarantine = _quarantine_unlocked("unreadable")
        raise SetupStoreError(
            f"Unreadable saved setups were preserved at {quarantine.name}"
        )
    if not isinstance(document, dict):
        quarantine = _quarantine_unlocked("invalid-document")
        raise SetupStoreError(
            f"Invalid saved setups were preserved at {quarantine.name}"
        )
    schema = document.get("schema_version", _SCHEMA_VERSION)
    rows = document.get("setups")
    if schema != _SCHEMA_VERSION or not isinstance(rows, list):
        quarantine = _quarantine_unlocked("unsupported-schema")
        raise SetupStoreError(
            f"Unsupported saved setups were preserved at {quarantine.name}"
        )
    setups: list[BandSetup] = []
    try:
        for row in rows:
            setups.append(BandSetup.model_validate(row))
    except (TypeError, ValidationError, ValueError):
        quarantine = _quarantine_unlocked("invalid-record")
        raise SetupStoreError(
            f"Invalid saved setup records were preserved at {quarantine.name}"
        )
    return setups


def _document(setups: list[BandSetup]) -> dict[str, Any]:
    try:
        validated = [
            BandSetup.model_validate(setup).model_dump(mode="json")
            for setup in setups
        ]
    except (TypeError, ValidationError, ValueError) as exc:
        raise SetupStoreError("Refusing to save invalid setup records") from exc
    return {
        "schema_version": _SCHEMA_VERSION,
        "setups": validated,
    }


def load_setups() -> list[BandSetup]:
    with _LOCK:
        return _load_unlocked()


def save_setups(setups: list[BandSetup]) -> None:
    with _LOCK:
        _write_unlocked(_document(setups))


def mutate_setups(
    mutation: Callable[[list[BandSetup]], ResultT],
) -> ResultT:
    """Apply one complete read-modify-write transaction under the store lock."""

    with _LOCK:
        setups = _load_unlocked()
        result = mutation(setups)
        _write_unlocked(_document(setups))
        return result


def find_by_name(setups: list[BandSetup], name: str) -> int | None:
    target = name.strip()
    for i, setup in enumerate(setups):
        if setup.name == target:
            return i
    return None
