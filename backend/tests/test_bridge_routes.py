"""Bridge routes: feature-flagged contract spike."""

from __future__ import annotations

import os
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import bridge_routes, session_routes
from app.services import bridge_store
from app.services.conditioning import build_unified_conditioning, has_source_groove
from app.services.session_context import build_session_context
from app.services.source_analysis import build_groove_profile, build_harmony_plan, build_source_analysis


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    bridge_store.clear_bridge_state()
    monkeypatch.delenv(bridge_routes._FEATURE_FLAG_ENV, raising=False)
    yield
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    bridge_store.clear_bridge_state()


def _enable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(bridge_routes._FEATURE_FLAG_ENV, "true")


def _create_session(client: TestClient) -> str:
    res = client.post(
        "/api/sessions/",
        json={
            "tempo": 120,
            "key": "C",
            "scale": "major",
            "bar_count": 2,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "bass_instrument": "finger_bass",
        },
    )
    assert res.status_code == 200, res.text
    return res.json()["session"]["id"]


def test_bridge_routes_disabled_by_default_returns_404() -> None:
    assert os.environ.get(bridge_routes._FEATURE_FLAG_ENV) is None
    client = TestClient(app)
    res = client.post(
        "/api/bridge/heartbeat",
        json={"plugin_instance_id": "plug-1"},
    )
    assert res.status_code == 404
    assert res.json()["detail"]["error"] == "bridge_disabled"


def test_heartbeat_records_plugin_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    res = client.post(
        "/api/bridge/heartbeat",
        json={"plugin_instance_id": "plug-1", "plugin_version": "0.1", "session_id": "x"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["connected"] is True
    assert body["plugin_instance_id"] == "plug-1"


def test_transport_records_for_existing_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_session(client)
    res = client.post(
        f"/api/bridge/sessions/{sid}/transport",
        json={
            "plugin_instance_id": "plug-1",
            "session_id": sid,
            "host_tempo": 120.0,
            "sample_rate": 48000.0,
            "playing": True,
            "ppq_position": 1.5,
            "bar_index": 0,
            "beat_index": 1,
        },
    )
    assert res.status_code == 200
    state = res.json()
    assert state["connected"] is True
    assert state["last_transport"]["host_tempo"] == 120.0


def test_source_frame_ingestion_increments_frame_count(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_session(client)

    frames = []
    for i in range(8):
        frames.append(
            {
                "plugin_instance_id": "plug-1",
                "session_id": sid,
                "source_id": "drum-bus",
                "sample_rate": 48000.0,
                "host_tempo": 120.0,
                "playing": True,
                "ppq_position": float(i) * 0.5,
                "bar_index": 0,
                "duration_seconds": 0.125,
                "rms": 0.3 + (i % 4) * 0.1,
                "low_band_energy": 0.8 if i % 4 == 0 else 0.2,
                "mid_band_energy": 0.5 if i % 4 == 2 else 0.1,
                "high_band_energy": 0.4,
                "onset_strength": 0.7 if i % 2 == 0 else 0.2,
            }
        )

    res = client.post(f"/api/bridge/sessions/{sid}/source-frames", json=frames)
    assert res.status_code == 200
    body = res.json()
    assert body["accepted"] == 8
    assert body["frame_count"] == 8

    state_res = client.get(f"/api/bridge/sessions/{sid}/state")
    assert state_res.status_code == 200
    s = state_res.json()
    assert s["connected"] is True
    assert s["frame_count"] == 8
    assert s["source_id"] == "drum-bus"


def test_commit_source_groove_updates_session_override_and_visible_to_conditioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_session(client)

    frames = []
    for bar in range(2):
        for i in range(8):
            frames.append(
                {
                    "plugin_instance_id": "plug-1",
                    "session_id": sid,
                    "source_id": "drum-bus",
                    "sample_rate": 48000.0,
                    "host_tempo": 120.0,
                    "playing": True,
                    "ppq_position": bar * 4.0 + i * 0.5,
                    "bar_index": bar,
                    "duration_seconds": 0.125,
                    "rms": 0.6,
                    "low_band_energy": 0.9 if i in (0, 4) else 0.2,
                    "mid_band_energy": 0.6 if i in (2, 6) else 0.1,
                    "high_band_energy": 0.3,
                    "onset_strength": 0.8 if i % 2 == 0 else 0.2,
                }
            )

    sf_res = client.post(f"/api/bridge/sessions/{sid}/source-frames", json=frames)
    assert sf_res.status_code == 200

    commit_res = client.post(f"/api/bridge/sessions/{sid}/commit-source-groove")
    assert commit_res.status_code == 200, commit_res.text
    body = commit_res.json()
    assert body["committed_bar_count"] == 2
    assert body["groove_resolution"] == 16

    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.source_analysis_override is not None
    src = stored.source_analysis_override
    assert any(any(v > 0 for v in row) for row in src.source_kick_weight)

    ctx = build_session_context(stored)
    groove = build_groove_profile(src, context=ctx)
    harmony = build_harmony_plan(stored, src)
    uc = build_unified_conditioning(session=stored, source=src, groove=groove, harmony=harmony, context=ctx)
    assert has_source_groove(uc) is True


def test_commit_without_frames_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_session(client)

    res = client.post(f"/api/bridge/sessions/{sid}/commit-source-groove")
    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "no_bridge_frames"


def test_session_id_mismatch_in_transport_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_session(client)
    res = client.post(
        f"/api/bridge/sessions/{sid}/transport",
        json={
            "plugin_instance_id": "plug-1",
            "session_id": "different",
            "host_tempo": 120.0,
            "sample_rate": 48000.0,
            "playing": False,
        },
    )
    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "session_id_mismatch"


def test_state_for_unknown_session_returns_disconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    res = client.get("/api/bridge/sessions/nope/state")
    assert res.status_code == 200
    body = res.json()
    assert body["connected"] is False
    assert body["frame_count"] == 0
