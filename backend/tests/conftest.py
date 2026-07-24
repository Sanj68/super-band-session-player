"""Shared test isolation for local persistent stores."""

from __future__ import annotations

import pytest

from app.services import bass_history_store


@pytest.fixture(autouse=True)
def isolate_bass_history_store(tmp_path, monkeypatch) -> None:
    """Never let plugin-route tests write idea history into the working data."""

    monkeypatch.setattr(bass_history_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        bass_history_store,
        "_HISTORY_FILE",
        tmp_path / "bass_history.json",
    )
