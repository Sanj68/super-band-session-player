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


def _create_phrase_session(client: TestClient) -> str:
    res = client.post(
        "/api/sessions/",
        json={
            "tempo": 120,
            "key": "C",
            "scale": "major",
            "bar_count": 2,
            "bass_style": "supportive",
            "bass_engine": "phrase_v2",
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
    assert body["session_id"] == "x"


def test_unbound_heartbeat_resolves_latest_session_when_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)

    pending = client.post(
        "/api/bridge/heartbeat",
        json={"plugin_instance_id": "plug-pending", "plugin_version": "0.1"},
    )
    assert pending.status_code == 200
    assert pending.json()["session_id"] is None

    first = _create_session(client)
    second = _create_session(client)
    bound = client.post(
        "/api/bridge/heartbeat",
        json={"plugin_instance_id": "plug-auto", "plugin_version": "0.1"},
    )

    assert first != second
    assert bound.status_code == 200
    assert bound.json()["session_id"] == second
    assert bridge_store.get_bridge_state(second)["plugin_instance_id"] == "plug-auto"


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


def test_live_source_frames_update_phrase_v2_bass_conditioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)

    frames = []
    for bar in range(2):
        for i in range(8):
            frames.append(
                {
                    "plugin_instance_id": "plug-1",
                    "session_id": sid,
                    "source_id": "logic-live",
                    "sample_rate": 48000.0,
                    "tempo": 120.0,
                    "playing": True,
                    "bar_position": bar * 4.0 + i * 0.5,
                    "bar_index": bar,
                    "duration_seconds": 0.125,
                    "RMS": 0.55,
                    "band_energy": {"low": 0.95 if i in (0, 3, 5) else 0.2, "mid": 0.35, "high": 0.25},
                    "onset_strength": 0.85 if i in (0, 3, 5) else 0.15,
                }
            )

    bridge_res = client.post(f"/api/bridge/sessions/{sid}/source-frames", json=frames)
    assert bridge_res.status_code == 200, bridge_res.text
    assert bridge_res.json()["accepted"] == 16
    assert bridge_res.json()["live_source_groove_bar_count"] == 2

    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.source_analysis_override is not None
    assert stored.source_analysis_override.source_lane == "none"
    assert stored.source_analysis_override.source_metadata["last_groove_source_tag"] == "logic_au_bridge"

    gen_res = client.post(f"/api/sessions/{sid}/regenerate-selected", json={"lanes": ["bass"]})
    assert gen_res.status_code == 200, gen_res.text
    bass_preview = gen_res.json()["lanes"]["bass"]["preview"]
    # v0.3b: the preview now states the reference lock explicitly (and
    # would honestly say "evidence too thin" if bridge confidence were low).
    assert "locked to the reference groove" in bass_preview


def test_harmonic_frames_update_source_analysis_and_phrase_v2_chambers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    res = client.post(
        "/api/sessions/",
        json={
            "tempo": 120,
            "key": "C",
            "scale": "major",
            "bar_count": 2,
            "bass_style": "melodic",
            "bass_engine": "phrase_v2",
            "bass_instrument": "finger_bass",
            "bass_player": "paul_chambers",
        },
    )
    assert res.status_code == 200, res.text
    sid = res.json()["session"]["id"]

    chroma = [0.0] * 12
    chroma[9] = 1.0
    chroma[0] = 0.7
    chroma[4] = 0.8
    frames = [
        {
            "plugin_instance_id": "listener-1",
            "session_id": sid,
            "source_id": "master-bus",
            "sample_rate": 48000.0,
            "tempo": 118.0,
            "tempo_confidence": 0.8,
            "playing": True,
            "bar_position": 0.25 * bar,
            "bar_index": bar,
            "duration_seconds": 1.0,
            "chroma": chroma,
            "key_pc": 9,
            "key": "A",
            "scale": "minor",
            "key_confidence": 0.82,
            "scale_confidence": 0.79,
            "cadence": "tonic",
            "cadence_confidence": 0.5,
        }
        for bar in range(2)
    ]

    bridge_res = client.post(f"/api/bridge/sessions/{sid}/harmonic", json=frames)
    assert bridge_res.status_code == 200, bridge_res.text
    body = bridge_res.json()
    assert body["accepted"] == 2
    assert body["harmonic_frame_count"] == 2
    assert body["live_harmonic_bar_count"] == 2
    assert body["key"] == "A"
    assert body["scale"] == "minor"

    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.source_analysis_override is not None
    assert stored.source_analysis_override.tonal_center_pc_guess == 9
    assert stored.source_analysis_override.scale_mode_guess == "minor"
    harmony = build_harmony_plan(stored, stored.source_analysis_override)
    assert harmony.source == "logic_au_harmonic_listener"
    assert harmony.bars[0].root_pc == 9

    gen_res = client.post(f"/api/sessions/{sid}/regenerate-selected", json={"lanes": ["bass"]})
    assert gen_res.status_code == 200, gen_res.text
    bass_preview = gen_res.json()["lanes"]["bass"]["preview"]
    assert "paul_chambers" in bass_preview
    assert "live harmonic context" in bass_preview


def test_listener_au_batch_contract_reaches_harmonic_conditioning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: Listener AU posts an array using the backend's exact field names."""

    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    chroma = [0.02] * 12
    chroma[2] = 0.72
    chroma[5] = 0.11
    chroma[9] = 0.13
    listener_batch = [
        {
            "plugin_instance_id": "logic-harmonic-au-contract",
            "session_id": sid,
            "source_id": "session-player-listener",
            "sample_rate": 48000.0,
            "host_tempo": 126.0,
            "tempo_bpm": 126.0,
            "tempo_confidence": 1.0,
            "playing": True,
            "ppq_position": 0.0,
            "bar_index": 0,
            "frame_start_seconds": 0.0,
            "duration_seconds": 0.170667,
            "chroma": chroma,
            "key_pc": 2,
            "key": "D",
            "scale": "minor",
            "key_confidence": 0.81,
            "scale_confidence": 0.77,
            "cadence": "tonic",
            "cadence_confidence": 0.6,
        }
    ]

    bridge_res = client.post("/api/bridge/harmonic", json=listener_batch)

    assert bridge_res.status_code == 200, bridge_res.text
    assert bridge_res.json()["accepted"] == 1
    assert bridge_res.json()["live_harmonic_bar_count"] == 1
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.key == "D"
    assert stored.scale == "minor"
    assert stored.source_analysis_override.source_metadata["live_harmonic_source_tag"] == (
        "logic_au_harmonic_listener"
    )


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
