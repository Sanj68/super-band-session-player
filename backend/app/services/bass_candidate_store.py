"""Local persistence for bass candidate-run metadata."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_RUNS_FILE = _DATA_DIR / "bass_candidate_runs.json"


def _atomic_write_json(obj: dict[str, Any]) -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, indent=2, sort_keys=True) + "\n"
    tmp = _RUNS_FILE.with_suffix(".json.tmp")
    tmp.write_text(raw, encoding="utf-8")
    tmp.replace(_RUNS_FILE)


def _ensure_file() -> None:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not _RUNS_FILE.exists():
        _atomic_write_json({"runs": []})


def append_run(run_payload: dict[str, Any]) -> None:
    """Append one run metadata row; payload should be JSON-serializable."""
    _ensure_file()
    try:
        data = json.loads(_RUNS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {"runs": []}
    rows = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        rows = []
    rows.append(run_payload)
    _atomic_write_json({"runs": rows})


def load_runs() -> list[dict[str, Any]]:
    _ensure_file()
    try:
        data = json.loads(_RUNS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    rows = data.get("runs") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    return [x for x in rows if isinstance(x, dict)]


def list_runs_for_session(session_id: str) -> list[dict[str, Any]]:
    target = str(session_id).strip()
    rows = [r for r in load_runs() if str(r.get("session_id", "")).strip() == target]
    rows.sort(key=lambda r: str(r.get("created_at", "")), reverse=True)
    return rows


def get_run_for_session(session_id: str, run_id: str) -> dict[str, Any] | None:
    sid = str(session_id).strip()
    rid = str(run_id).strip()
    for row in load_runs():
        if str(row.get("session_id", "")).strip() == sid and str(row.get("run_id", "")).strip() == rid:
            return row
    return None
