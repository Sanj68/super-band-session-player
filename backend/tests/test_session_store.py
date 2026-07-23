from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import session_routes
from app.services import session_store
from app.services.source_analysis import build_source_analysis


def _store_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(session_store, "_SESSIONS_FILE", tmp_path / "sessions.json")


def test_full_session_round_trip_preserves_order_midi_and_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_at(tmp_path, monkeypatch)
    first = session_routes.StoredSession(
        id="session-a",
        tempo=98,
        key="D",
        scale="Dorian",
        bar_count=4,
        bass_style="melodic",
        bass_density_bias=0.35,
        bass_bytes=b"MThd-bass",
        bass_performance_bytes=b"MThd-performance",
        drum_bytes=b"MThd-drums",
        chords_bytes=b"MThd-chords",
        lead_bytes=b"MThd-lead",
        bass_locked=True,
        current_bass_candidate_run_id="run-1",
        current_bass_candidate_take_id="take-2",
    )
    first.source_analysis_override = build_source_analysis(first)
    second = session_routes.StoredSession(
        id="session-b",
        tempo=120,
        key="C",
        scale="Major",
        bar_count=8,
    )

    session_store.save_sessions({first.id: first, second.id: second})
    restored = session_store.load_sessions(session_routes.StoredSession)

    assert list(restored) == ["session-a", "session-b"]
    assert restored["session-a"].bass_bytes == b"MThd-bass"
    assert restored["session-a"].bass_performance_bytes == b"MThd-performance"
    assert restored["session-a"].drum_bytes == b"MThd-drums"
    assert restored["session-a"].chords_bytes == b"MThd-chords"
    assert restored["session-a"].lead_bytes == b"MThd-lead"
    assert restored["session-a"].bass_density_bias == 0.35
    assert restored["session-a"].bass_locked is True
    assert restored["session-a"].source_analysis_override == first.source_analysis_override
    assert restored["session-a"].current_bass_candidate_take_id == "take-2"


def test_corrupt_or_unsupported_snapshot_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_at(tmp_path, monkeypatch)
    session_store._SESSIONS_FILE.write_text("{bad json", encoding="utf-8")  # type: ignore[attr-defined]
    with pytest.raises(session_store.SessionStoreError):
        session_store.load_sessions(session_routes.StoredSession)

    session_store._SESSIONS_FILE.write_text(  # type: ignore[attr-defined]
        json.dumps({"schema_version": 999, "sessions": []}),
        encoding="utf-8",
    )
    with pytest.raises(session_store.SessionStoreError):
        session_store.load_sessions(session_routes.StoredSession)


def test_engine_does_not_overwrite_a_corrupt_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_at(tmp_path, monkeypatch)
    corrupt = "{keep this for recovery"
    session_store._SESSIONS_FILE.write_text(corrupt, encoding="utf-8")  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert app.state.session_persistence_enabled is False

    assert session_store._SESSIONS_FILE.read_text(encoding="utf-8") == corrupt  # type: ignore[attr-defined]


def test_engine_lifespan_restores_session_and_persists_plugin_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_at(tmp_path, monkeypatch)
    stored = session_routes.StoredSession(
        id="logic-project-session",
        tempo=100,
        key="C",
        scale="Major",
        bar_count=4,
        bass_bytes=b"MThd",
        bass_style="supportive",
    )
    session_store.save_sessions({stored.id: stored})
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    with TestClient(app) as client:
        assert list(session_routes._SESSIONS) == ["logic-project-session"]  # type: ignore[attr-defined]
        response = client.post(
            "/api/plugin/regenerate",
            json={"session_id": stored.id, "bass_style": "melodic"},
        )
        assert response.status_code == 200

    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    restored = session_store.load_sessions(session_routes.StoredSession)
    assert restored["logic-project-session"].bass_style == "melodic"
    assert restored["logic-project-session"].bass_bytes
