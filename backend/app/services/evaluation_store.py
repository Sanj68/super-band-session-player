"""Local JSON persistence for bass clip/take evaluations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.models.evaluation import ClipEvaluationRecord

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_EVALS_FILE = _DATA_DIR / "bass_take_evaluations.json"


def _atomic_write_json(obj: dict[str, Any]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, indent=2, sort_keys=True) + "\n"
    tmp = _EVALS_FILE.with_suffix(".json.tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(_EVALS_FILE)


def _ensure_file() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _EVALS_FILE.exists():
        _atomic_write_json({"clips": []})


def load_records() -> list[ClipEvaluationRecord]:
    _ensure_file()
    try:
        data = json.loads(_EVALS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        _atomic_write_json({"clips": []})
        return []
    raw_list = data.get("clips") if isinstance(data, dict) else None
    if not isinstance(raw_list, list):
        return []
    out: list[ClipEvaluationRecord] = []
    for item in raw_list:
        if not isinstance(item, dict):
            continue
        try:
            out.append(ClipEvaluationRecord.model_validate(item))
        except Exception:
            continue
    return out


def save_records(records: list[ClipEvaluationRecord]) -> None:
    _atomic_write_json({"clips": [r.model_dump(mode="json") for r in records]})


def find_clip_index(records: list[ClipEvaluationRecord], clip_id: str) -> int | None:
    target = clip_id.strip()
    for i, rec in enumerate(records):
        if rec.clip_id == target:
            return i
    return None

