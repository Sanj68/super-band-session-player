"""Tests for the MIDI FX plugin surface (/api/plugin)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _create_session_with_bass(client: TestClient) -> str:
    res = client.post(
        "/api/sessions/",
        json={
            "tempo": 100,
            "key": "C",
            "scale": "major",
            "bar_count": 2,
            "bass_style": "supportive",
            "bass_engine": "phrase_v2",
        },
    )
    assert res.status_code == 200, res.text
    sid = res.json()["session"]["id"]
    gen = client.post(f"/api/sessions/{sid}/regenerate-selected", json={"lanes": ["bass"]})
    assert gen.status_code == 200, gen.text
    return sid


def test_bass_part_404_when_no_sessions(client: TestClient) -> None:
    from app.routes import session_routes

    saved = dict(session_routes._SESSIONS)
    session_routes._SESSIONS.clear()
    try:
        res = client.get("/api/plugin/bass-part")
        assert res.status_code == 404
    finally:
        session_routes._SESSIONS.update(saved)


def test_bass_part_returns_latest_session_notes(client: TestClient) -> None:
    sid = _create_session_with_bass(client)
    res = client.get("/api/plugin/bass-part")
    assert res.status_code == 200, res.text
    part = res.json()
    assert part["session_id"] == sid
    assert part["tempo"] == 100
    assert part["bar_count"] == 2
    assert part["beats_per_bar"] == 4
    assert len(part["notes"]) > 0
    for n in part["notes"]:
        assert 0 <= n["start_beats"] < part["bar_count"] * 4 + 1
        assert n["dur_beats"] > 0
        assert 0 < n["pitch"] < 128
    # sorted by start
    starts = [n["start_beats"] for n in part["notes"]]
    assert starts == sorted(starts)


def test_plugin_regenerate_applies_style_and_lock(client: TestClient) -> None:
    _create_session_with_bass(client)
    res = client.post(
        "/api/plugin/regenerate",
        json={"bass_style": "melodic", "lock_to_groove": 0.9},
    )
    assert res.status_code == 200, res.text
    part = res.json()
    assert part["bass_style"] == "melodic"
    assert part["lock_to_groove"] == pytest.approx(0.9)
    assert len(part["notes"]) > 0


def test_plugin_regenerate_routes_persona_to_strongest_engine(client: TestClient) -> None:
    from app.routes import session_routes

    sid = _create_session_with_bass(client)

    res = client.post("/api/plugin/regenerate", json={"bass_player": "james_jamerson"})
    assert res.status_code == 200, res.text
    assert res.json()["bass_player"] == "james_jamerson"
    assert session_routes._SESSIONS[sid].bass_engine == "baseline"

    res = client.post("/api/plugin/regenerate", json={"bass_player": "pino"})
    assert res.status_code == 200, res.text
    assert session_routes._SESSIONS[sid].bass_engine == "phrase_v2"

    res = client.post("/api/plugin/regenerate", json={"bass_player": "none"})
    assert res.status_code == 200, res.text
    assert res.json()["bass_player"] is None
    assert session_routes._SESSIONS[sid].bass_engine == "phrase_v2"
