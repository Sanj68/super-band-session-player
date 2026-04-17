"""Local JSON persistence for saved band setups."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.setup import BandSetup

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_SETUPS_FILE = _DATA_DIR / "band_setups.json"


def _ensure_file() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _SETUPS_FILE.exists():
        _atomic_write_json({"setups": []})


def _atomic_write_json(obj: dict[str, Any]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, indent=2, sort_keys=True) + "\n"
    tmp = _SETUPS_FILE.with_suffix(".json.tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(_SETUPS_FILE)


def load_setups() -> list[BandSetup]:
    _ensure_file()
    try:
        data = json.loads(_SETUPS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _atomic_write_json({"setups": []})
        return []
    raw_list = data.get("setups") if isinstance(data, dict) else None
    if not isinstance(raw_list, list):
        return []
    out: list[BandSetup] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        try:
            out.append(BandSetup.model_validate(item))
        except Exception:
            continue
    return out


def save_setups(setups: list[BandSetup]) -> None:
    _atomic_write_json({"setups": [s.model_dump(mode="json") for s in setups]})


def find_by_name(setups: list[BandSetup], name: str) -> int | None:
    target = name.strip()
    for i, s in enumerate(setups):
        if s.name == target:
            return i
    return None
