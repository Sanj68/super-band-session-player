"""Bridge routes: feature-flagged contract spike."""

from __future__ import annotations

import asyncio
import json
import os
from threading import Event
from typing import Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import bridge_routes, session_routes
from app.services import bridge_store, session_mutation_gate, session_store
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
    session_routes._SESSIONS[sid].harmony_key_confirmed_by_user = False  # type: ignore[attr-defined]

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
    # Simulate an imported/source-derived session whose harmony is still
    # tentative; SessionCreate itself is authoritative and tested below.
    session_routes._SESSIONS[sid].harmony_key_confirmed_by_user = False  # type: ignore[attr-defined]
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


def test_changed_listener_hypothesis_does_not_inherit_prior_confidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A weak new key/mode must not masquerade as the strong previous take."""

    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    stored.harmony_key_confirmed_by_user = False

    first = _harmonic_frame(
        sid,
        source_id="external-harmonic-listener",
        capture_epoch=1,
        key_pc=0,
    )
    first.update(
        {
            "scale": "major",
            "key_confidence": 0.92,
            "scale_confidence": 0.88,
        }
    )
    first_response = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[first],
    )
    assert first_response.status_code == 200, first_response.text
    first_analysis = session_routes._SESSIONS[sid].source_analysis_override  # type: ignore[attr-defined]
    assert first_analysis.tonal_center_confidence == pytest.approx(0.92)
    assert first_analysis.scale_mode_confidence == pytest.approx(0.88)

    changed = _harmonic_frame(
        sid,
        source_id="external-harmonic-listener",
        capture_epoch=2,
        key_pc=2,
        ppq=8.0,
        bar=2,
    )
    changed.update(
        {
            "scale": "minor",
            "key_confidence": 0.31,
            "scale_confidence": 0.27,
        }
    )
    changed_response = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[changed],
    )

    assert changed_response.status_code == 200, changed_response.text
    assert changed_response.json()["capture_epoch_reset"] is True
    analysis = session_routes._SESSIONS[sid].source_analysis_override  # type: ignore[attr-defined]
    assert analysis.tonal_center_pc_guess == 2
    assert analysis.scale_mode_guess == "minor"
    assert analysis.tonal_center_confidence == pytest.approx(0.31)
    assert analysis.scale_mode_confidence == pytest.approx(0.27)


def test_selected_listener_key_ignores_confidence_from_other_hypotheses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Confidence follows the winning key, not the loudest unrelated bar."""

    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    session_routes._SESSIONS[sid].harmony_key_confirmed_by_user = False  # type: ignore[attr-defined]

    loud_c = _harmonic_frame(
        sid,
        source_id="external-harmonic-listener",
        key_pc=0,
        ppq=0.0,
        bar=0,
    )
    loud_c.update(
        {
            "duration_seconds": 0.1,
            "key_confidence": 0.95,
            "scale_confidence": 0.95,
        }
    )
    sustained_d = _harmonic_frame(
        sid,
        source_id="external-harmonic-listener",
        key_pc=2,
        ppq=4.0,
        bar=1,
    )
    sustained_d.update(
        {
            "duration_seconds": 1.0,
            "key_confidence": 0.4,
            "scale_confidence": 0.4,
        }
    )

    response = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[loud_c, sustained_d],
    )

    assert response.status_code == 200, response.text
    analysis = session_routes._SESSIONS[sid].source_analysis_override  # type: ignore[attr-defined]
    assert analysis.tonal_center_pc_guess == 2
    assert analysis.tonal_center_confidence == pytest.approx(0.4)


def test_self_listener_is_observation_only_for_confirmed_chord_progression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generated parts cannot teach their own harmony back into a fixed chart."""

    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    stored.key = "D"
    stored.scale = "natural_minor"
    stored.chord_progression = ["Bb", "C", "D", "Gm"]

    contaminated = _harmonic_frame(
        sid,
        source_id="session-player-listener",
        key_pc=5,
    )
    contaminated.update(
        {
            "scale": "minor",
            "key_confidence": 0.98,
            "scale_confidence": 0.98,
        }
    )
    response = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[contaminated],
    )

    assert response.status_code == 200, response.text
    assert response.json()["accepted"] == 1
    assert response.json()["harmonic_frame_count"] == 1
    assert response.json()["live_harmonic_bar_count"] == 1
    assert response.json()["live_harmonic_applied"] is False
    assert response.json()["live_harmonic_ignored_reason"] == (
        "confirmed_chord_progression"
    )
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.key == "D"
    assert stored.scale == "natural_minor"
    assert stored.bridge_live_overlay_active is False
    assert stored.source_analysis_override is None

    generated = client.post(
        f"/api/sessions/{sid}/regenerate-selected",
        json={"lanes": ["bass"]},
    )
    assert generated.status_code == 200, generated.text
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.bridge_live_overlay_active is False
    assert stored.source_analysis_override is None
    source = build_source_analysis(stored)
    harmony = build_harmony_plan(stored, source)
    assert harmony.source == "confirmed_chord_progression"
    assert [bar.root_pc for bar in harmony.bars] == [10, 0]


def test_stopped_listener_frames_are_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stopped transport must not grow the harmonic store or alter the session."""

    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    original_key = stored.key
    original_scale = stored.scale
    stopped_frame = {
        "plugin_instance_id": "listener-stopped",
        "session_id": sid,
        "source_id": "session-player-listener",
        "sample_rate": 48000.0,
        "host_tempo": 120.0,
        "tempo_bpm": 120.0,
        "tempo_confidence": 1.0,
        "playing": False,
        "ppq_position": 0.0,
        "bar_index": 0,
        "frame_start_seconds": 0.0,
        "duration_seconds": 0.170667,
        "chroma": [1.0] + [0.0] * 11,
        "key_pc": 9,
        "key": "A",
        "scale": "minor",
        "key_confidence": 0.95,
        "scale_confidence": 0.95,
        "cadence": "tonic",
        "cadence_confidence": 0.8,
    }

    bridge_res = client.post("/api/bridge/harmonic", json=[stopped_frame])

    assert bridge_res.status_code == 200, bridge_res.text
    assert bridge_res.json()["accepted"] == 0
    assert bridge_res.json()["ignored_stopped"] == 1
    assert bridge_res.json()["harmonic_frame_count"] == 0
    assert bridge_res.json()["live_harmonic_bar_count"] == 0
    assert stored.key == original_key
    assert stored.scale == original_scale
    assert stored.source_analysis_override is None


def test_transport_restart_discards_prior_take_overlay_before_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    live = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[_harmonic_frame(sid, key_pc=7)],
    )
    assert live.status_code == 200, live.text
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.bridge_live_overlay_active is True
    assert stored.source_analysis_override is not None

    stopped = client.post(
        f"/api/bridge/sessions/{sid}/transport",
        json={
            "plugin_instance_id": "source-au",
            "session_id": sid,
            "playing": False,
            "ppq_position": 8.0,
            "bar_index": 2,
        },
    )
    restarted = client.post(
        f"/api/bridge/sessions/{sid}/transport",
        json={
            "plugin_instance_id": "source-au",
            "session_id": sid,
            "playing": True,
            "ppq_position": 0.0,
            "bar_index": 0,
        },
    )

    assert stopped.status_code == 200, stopped.text
    assert restarted.status_code == 200, restarted.text
    assert restarted.json()["transport_restarted"] is True
    assert restarted.json()["session_overlay_deferred"] is False
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.bridge_live_overlay_active is False
    assert stored.source_analysis_override is None

    generated = client.post(
        f"/api/sessions/{sid}/regenerate-selected",
        json={"lanes": ["bass"]},
    )
    assert generated.status_code == 200, generated.text
    current = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert current.key == "C"
    assert current.source_analysis_override is None
    assert current.bass_bytes is not None


def test_first_playing_transport_discards_frames_captured_before_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    live = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[_harmonic_frame(sid, key_pc=7)],
    )
    assert live.status_code == 200, live.text
    assert session_routes._SESSIONS[sid].bridge_live_overlay_active is True  # type: ignore[attr-defined]

    first_transport = client.post(
        f"/api/bridge/sessions/{sid}/transport",
        json={
            "plugin_instance_id": "source-au",
            "session_id": sid,
            "playing": True,
            "ppq_position": 0.0,
            "bar_index": 0,
        },
    )

    assert first_transport.status_code == 200, first_transport.text
    assert first_transport.json()["transport_restarted"] is True
    assert first_transport.json()["harmonic_frame_count"] == 0
    current = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert current.bridge_live_overlay_active is False
    assert current.source_analysis_override is None


def test_listener_estimate_does_not_replace_confirmed_uploaded_harmony(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Listener may advise, but confirmed source harmony remains authoritative."""

    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    stored.key = "D"
    stored.scale = "natural_minor"
    stored.reference_audio_path = "/tmp/confirmed-musical-source.wav"
    stored.harmony_confirmation_required = False
    stored.harmony_map_source = "confirmed_user"

    chroma = [0.02] * 12
    chroma[9] = 0.72
    frames = [
        {
            "plugin_instance_id": "listener-confirmed-harmony-guard",
            "session_id": sid,
            "source_id": "session-player-listener",
            "sample_rate": 48000.0,
            "host_tempo": 88.0,
            "tempo_bpm": 88.0,
            "tempo_confidence": 1.0,
            "playing": True,
            "ppq_position": 0.0,
            "bar_index": 0,
            "frame_start_seconds": 0.0,
            "duration_seconds": 0.170667,
            "chroma": chroma,
            "key_pc": 9,
            "key": "A",
            "scale": "major",
            "key_confidence": 0.82,
            "scale_confidence": 0.79,
            "cadence": "tonic",
            "cadence_confidence": 0.6,
        }
    ]

    bridge_res = client.post("/api/bridge/harmonic", json=frames)

    assert bridge_res.status_code == 200, bridge_res.text
    assert bridge_res.json()["key"] == "D"
    assert bridge_res.json()["scale"] == "natural_minor"
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.key == "D"
    assert stored.scale == "natural_minor"
    assert stored.source_analysis_override.tonal_center_pc_guess == 9
    assert stored.source_analysis_override.scale_mode_guess == "major"


def test_listener_estimate_does_not_replace_session_create_harmony(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Required SessionCreate key/scale are authoritative without test mutation."""

    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.harmony_key_confirmed_by_user is True

    chroma = [0.02] * 12
    chroma[6] = 0.78
    response = client.post(
        "/api/bridge/harmonic",
        json=[
            {
                "plugin_instance_id": "listener-manual-harmony-guard",
                "session_id": sid,
                "source_id": "session-player-listener",
                "sample_rate": 48000.0,
                "host_tempo": 88.0,
                "tempo_bpm": 88.0,
                "tempo_confidence": 1.0,
                "playing": True,
                "ppq_position": 0.0,
                "bar_index": 0,
                "frame_start_seconds": 0.0,
                "duration_seconds": 0.170667,
                "chroma": chroma,
                "key_pc": 6,
                "key": "F#",
                "scale": "major",
                "key_confidence": 0.9,
                "scale_confidence": 0.9,
                "cadence": "tonic",
                "cadence_confidence": 0.6,
            }
        ],
    )

    assert response.status_code == 200, response.text
    assert response.json()["key"] == "C"
    assert response.json()["scale"] == "major"
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.key == "C"
    assert stored.scale == "major"
    assert stored.source_analysis_override.tonal_center_pc_guess == 6
    assert stored.source_analysis_override.scale_mode_guess == "major"
    harmony = build_harmony_plan(stored, stored.source_analysis_override)
    assert harmony.source != "logic_au_harmonic_listener"
    assert all(
        bar.source != "logic_au_harmonic_listener"
        for bar in harmony.bars
    )


def test_harmonic_capture_rebases_absolute_logic_bars_to_session_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)

    response = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[
            _harmonic_frame(sid, capture_epoch=8, key_pc=2, ppq=40.0, bar=10),
            _harmonic_frame(sid, capture_epoch=8, key_pc=7, ppq=44.0, bar=11),
            _harmonic_frame(sid, capture_epoch=8, key_pc=2, ppq=48.0, bar=12),
        ],
    )

    assert response.status_code == 200, response.text
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    summary = stored.source_analysis_override.source_metadata["bridge_harmonic"]
    assert [row["bar_index"] for row in summary["bars"]] == [0, 1]
    assert summary["bars"][0]["frame_count"] == 2
    assert summary["bars"][0]["host_bar_indices"] == [10, 12]
    assert summary["bars"][1]["host_bar_indices"] == [11]


def test_successful_generation_promotes_consumed_live_context_atomically(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    _enable(monkeypatch)
    monkeypatch.setattr(session_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        session_store,
        "_SESSIONS_FILE",
        tmp_path / "sessions.json",
    )
    client = TestClient(app)
    sid = _create_phrase_session(client)
    session_routes._SESSIONS[sid].harmony_key_confirmed_by_user = False  # type: ignore[attr-defined]

    live = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[
            _harmonic_frame(
                sid,
                capture_epoch=3,
                key_pc=2,
                ppq=40.0,
                bar=10,
            )
        ],
    )
    assert live.status_code == 200, live.text
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.bridge_live_overlay_active is True
    assert stored.key == "D"

    generated = client.post(
        f"/api/sessions/{sid}/regenerate-selected",
        json={"lanes": ["bass"]},
    )
    assert generated.status_code == 200, generated.text
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.bridge_live_overlay_active is False
    assert stored.key == "D"
    assert stored.source_analysis_override.source_metadata[
        "live_harmonic_source_tag"
    ] == "logic_au_harmonic_listener"

    session_store.save_sessions(session_routes._SESSIONS)  # type: ignore[attr-defined]
    restored = session_store.load_sessions(session_routes.StoredSession)[sid]
    assert restored.key == "D"
    assert restored.source_analysis_override == stored.source_analysis_override
    assert restored.bass_bytes == stored.bass_bytes


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


def _source_frame(
    session_id: str,
    *,
    plugin_id: str = "source-au",
    source_id: str = "drum-bus",
    capture_epoch: int | None = 1,
    playing: bool = True,
    ppq: float | None = 0.0,
    bar: int = 0,
) -> dict[str, object]:
    return {
        "plugin_instance_id": plugin_id,
        "session_id": session_id,
        "source_id": source_id,
        "capture_epoch": capture_epoch,
        "sample_rate": 48000.0,
        "host_tempo": 120.0,
        "playing": playing,
        "ppq_position": ppq,
        "bar_index": bar,
        "frame_start_seconds": 0.0,
        "duration_seconds": 0.125,
        "rms": 0.5,
        "low_band_energy": 0.8,
        "mid_band_energy": 0.3,
        "high_band_energy": 0.2,
        "onset_strength": 0.7,
    }


def _harmonic_frame(
    session_id: str,
    *,
    plugin_id: str = "listener-au",
    source_id: str = "master-bus",
    capture_epoch: int | None = 1,
    key_pc: int = 0,
    ppq: float = 0.0,
    bar: int = 0,
) -> dict[str, object]:
    chroma = [0.0] * 12
    chroma[key_pc] = 1.0
    return {
        "plugin_instance_id": plugin_id,
        "session_id": session_id,
        "source_id": source_id,
        "capture_epoch": capture_epoch,
        "sample_rate": 48000.0,
        "host_tempo": 120.0,
        "tempo_bpm": 120.0,
        "tempo_confidence": 1.0,
        "playing": True,
        "ppq_position": ppq,
        "bar_index": bar,
        "frame_start_seconds": 0.0,
        "duration_seconds": 0.125,
        "chroma": chroma,
        "key_pc": key_pc,
        "key": ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")[key_pc],
        "scale": "major",
        "key_confidence": 0.95,
        "scale_confidence": 0.95,
    }


def test_mismatched_source_batch_is_rejected_before_any_frame_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_session(client)

    rejected = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[
            _source_frame(sid, ppq=0.0),
            _source_frame("different-session", ppq=0.5),
        ],
    )

    assert rejected.status_code == 400
    assert rejected.json()["detail"]["error"] == "session_id_mismatch"
    state = bridge_store.get_bridge_state(sid)
    assert state["frame_count"] == 0
    assert state["source_epoch"] == 0


def test_mismatched_harmonic_batch_is_rejected_before_any_frame_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_session(client)

    rejected = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[
            _harmonic_frame(sid, ppq=0.0),
            _harmonic_frame("different-session", ppq=0.5),
        ],
    )

    assert rejected.status_code == 400
    assert rejected.json()["detail"]["error"] == "session_id_mismatch"
    state = bridge_store.get_bridge_state(sid)
    assert state["harmonic_frame_count"] == 0
    assert state["harmonic_epoch"] == 0


def test_non_finite_positions_are_rejected_before_the_bridge_store_is_poisoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_session(client)
    source = _source_frame(sid)
    source["ppq_position"] = float("nan")
    harmonic = _harmonic_frame(sid)
    harmonic["ppq_position"] = float("inf")
    source_duration = _source_frame(sid)
    source_duration["duration_seconds"] = float("inf")
    harmonic_duration = _harmonic_frame(sid)
    harmonic_duration["duration_seconds"] = float("inf")

    source_response = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        content=json.dumps([source]),
        headers={"content-type": "application/json"},
    )
    harmonic_response = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        content=json.dumps([harmonic]),
        headers={"content-type": "application/json"},
    )
    transport_response = client.post(
        f"/api/bridge/sessions/{sid}/transport",
        content=json.dumps(
            {
                "plugin_instance_id": "source-au",
                "session_id": sid,
                "playing": True,
                "ppq_position": float("-inf"),
            }
        ),
        headers={"content-type": "application/json"},
    )
    source_duration_response = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        content=json.dumps([source_duration]),
        headers={"content-type": "application/json"},
    )
    harmonic_duration_response = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        content=json.dumps([harmonic_duration]),
        headers={"content-type": "application/json"},
    )

    assert source_response.status_code == 422
    assert harmonic_response.status_code == 422
    assert transport_response.status_code == 422
    assert source_duration_response.status_code == 422
    assert harmonic_duration_response.status_code == 422
    state = bridge_store.get_bridge_state(sid)
    assert state["frame_count"] == 0
    assert state["harmonic_frame_count"] == 0


def test_stopped_source_frames_are_ignored_and_close_the_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]

    stopped = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[
            _source_frame(
                sid,
                capture_epoch=4,
                playing=False,
            )
        ],
    )

    assert stopped.status_code == 200, stopped.text
    assert stopped.json()["accepted"] == 0
    assert stopped.json()["ignored_stopped"] == 1
    assert stopped.json()["frame_count"] == 0
    assert stopped.json()["live_source_groove_bar_count"] == 0
    assert stored.source_analysis_override is None

    resumed = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[_source_frame(sid, capture_epoch=5, ppq=8.0, bar=2)],
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["accepted"] == 1
    assert resumed.json()["frame_count"] == 1


def test_mismatched_stopped_capture_epoch_cannot_churn_active_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale default stop row must not close a newer playing capture."""

    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    source_playing = _source_frame(
        sid,
        capture_epoch=2,
        ppq=44.0,
        bar=11,
    )
    source_stopped = _source_frame(
        sid,
        capture_epoch=0,
        playing=False,
        ppq=None,
        bar=0,
    )
    harmonic_playing = _harmonic_frame(
        sid,
        capture_epoch=2,
        key_pc=7,
        ppq=44.0,
        bar=11,
    )
    harmonic_stopped = dict(
        _harmonic_frame(
            sid,
            capture_epoch=0,
            key_pc=0,
            ppq=0.0,
            bar=0,
        )
    )
    harmonic_stopped.update({"playing": False, "ppq_position": None})

    for expected_count in range(1, 4):
        source = client.post(
            f"/api/bridge/sessions/{sid}/source-frames",
            json=[source_playing, source_stopped],
        )
        harmonic = client.post(
            f"/api/bridge/sessions/{sid}/harmonic",
            json=[harmonic_playing, harmonic_stopped],
        )

        assert source.status_code == 200, source.text
        assert source.json()["accepted"] == 1
        assert source.json()["ignored_stopped"] == 1
        assert source.json()["capture_epoch_reset"] is False
        assert source.json()["source_epoch"] == 1
        assert source.json()["frame_count"] == expected_count
        assert harmonic.status_code == 200, harmonic.text
        assert harmonic.json()["accepted"] == 1
        assert harmonic.json()["ignored_stopped"] == 1
        assert harmonic.json()["capture_epoch_reset"] is False
        assert harmonic.json()["harmonic_epoch"] == 1
        assert harmonic.json()["harmonic_frame_count"] == expected_count

    harmonic_summary = bridge_store.summarize_harmonic_frames(sid)
    assert harmonic_summary is not None
    assert harmonic_summary["bars"][0]["host_bar_indices"] == [11]

    # Simulate the observed backend restart while the old AU replays the same
    # broken pair. The first new frame establishes epoch 1; its stale epoch-0
    # stop still cannot create a close/reopen loop.
    bridge_store.clear_bridge_state(sid)
    for expected_count in range(1, 3):
        replay = client.post(
            f"/api/bridge/sessions/{sid}/harmonic",
            json=[harmonic_playing, harmonic_stopped],
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["capture_epoch_reset"] is False
        assert replay.json()["harmonic_epoch"] == 1
        assert replay.json()["harmonic_frame_count"] == expected_count


def test_matching_stopped_capture_epoch_still_closes_active_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real same-epoch stop remains an explicit take boundary."""

    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    source_playing = _source_frame(
        sid,
        capture_epoch=2,
        ppq=44.0,
        bar=11,
    )
    harmonic_playing = _harmonic_frame(
        sid,
        capture_epoch=2,
        key_pc=7,
        ppq=44.0,
        bar=11,
    )

    first_source = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[source_playing],
    )
    first_harmonic = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[harmonic_playing],
    )
    assert first_source.status_code == 200, first_source.text
    assert first_harmonic.status_code == 200, first_harmonic.text

    source_stopped = dict(source_playing)
    source_stopped.update(
        {"playing": False, "ppq_position": None, "bar_index": 0}
    )
    harmonic_stopped = dict(harmonic_playing)
    harmonic_stopped.update(
        {"playing": False, "ppq_position": None, "bar_index": 0}
    )
    stopped_source = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[source_stopped],
    )
    stopped_harmonic = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[harmonic_stopped],
    )
    assert stopped_source.status_code == 200, stopped_source.text
    assert stopped_source.json()["ignored_stopped"] == 1
    assert stopped_harmonic.status_code == 200, stopped_harmonic.text
    assert stopped_harmonic.json()["ignored_stopped"] == 1

    resumed_source = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[source_playing],
    )
    resumed_harmonic = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[harmonic_playing],
    )
    assert resumed_source.status_code == 200, resumed_source.text
    assert resumed_source.json()["capture_epoch_reset"] is True
    assert resumed_source.json()["source_epoch"] == 2
    assert resumed_source.json()["frame_count"] == 1
    assert resumed_harmonic.status_code == 200, resumed_harmonic.text
    assert resumed_harmonic.json()["capture_epoch_reset"] is True
    assert resumed_harmonic.json()["harmonic_epoch"] == 2
    assert resumed_harmonic.json()["harmonic_frame_count"] == 1


def test_capture_epoch_and_identity_changes_replace_prior_take_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)

    first = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[
            _source_frame(sid, capture_epoch=10, ppq=0.0, bar=0),
            _source_frame(sid, capture_epoch=10, ppq=1.0, bar=0),
        ],
    )
    assert first.status_code == 200
    assert first.json()["frame_count"] == 2

    later_position_new_take = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[
            _source_frame(
                sid,
                capture_epoch=11,
                ppq=8.0,
                bar=2,
            )
        ],
    )
    assert later_position_new_take.status_code == 200
    assert later_position_new_take.json()["capture_epoch_reset"] is True
    assert later_position_new_take.json()["frame_count"] == 1
    assert later_position_new_take.json()["source_epoch"] == 2
    summarized = bridge_store.summarize_frames_to_groove_frames(sid)
    assert [row.bar_index for row in summarized] == [2]
    assert summarized[0].source_metadata["frame_count"] == 1

    new_identity = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[
            _source_frame(
                sid,
                plugin_id="replacement-source-au",
                source_id="new-drum-bus",
                capture_epoch=1,
                ppq=8.5,
                bar=2,
            )
        ],
    )
    assert new_identity.status_code == 200
    assert new_identity.json()["capture_epoch_reset"] is True
    assert new_identity.json()["frame_count"] == 1
    assert new_identity.json()["source_epoch"] == 3
    assert new_identity.json()["source_id"] == "new-drum-bus"


def test_first_source_epoch_after_bridge_restart_replaces_persisted_take(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)

    first = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[
            _source_frame(sid, capture_epoch=10, ppq=0.0, bar=0),
            _source_frame(sid, capture_epoch=10, ppq=4.0, bar=1),
        ],
    )
    assert first.status_code == 200, first.text
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.source_analysis_override is not None
    assert any(stored.source_analysis_override.source_slot_pressure[0])
    assert any(stored.source_analysis_override.source_slot_pressure[1])

    # Clearing the in-memory Bridge state simulates a backend restart while
    # the durable session snapshot still contains take A's merged groove.
    # The next take begins at absolute Logic bar 10, which must be rebased to
    # loop-local bar 0 for this two-bar session.
    bridge_store.clear_bridge_state()
    second = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[
            _source_frame(
                sid,
                capture_epoch=1,
                ppq=40.0,
                bar=10,
            )
        ],
    )

    assert second.status_code == 200, second.text
    assert second.json()["capture_epoch_reset"] is True
    assert second.json()["frame_count"] == 1
    source = session_routes._SESSIONS[sid].source_analysis_override  # type: ignore[attr-defined]
    assert source is not None
    assert len(source.source_slot_pressure) == 2, source.model_dump()
    assert any(source.source_slot_pressure[0])
    assert not any(source.source_slot_pressure[1])


def test_transport_restart_resets_legacy_source_at_a_later_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older AUs without capture_epoch still get an explicit take boundary."""

    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    first = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[
            _source_frame(
                sid,
                capture_epoch=None,
                ppq=0.0,
                bar=0,
            )
        ],
    )
    assert first.status_code == 200
    assert first.json()["frame_count"] == 1

    stopped = client.post(
        f"/api/bridge/sessions/{sid}/transport",
        json={
            "plugin_instance_id": "source-au",
            "session_id": sid,
            "playing": False,
            "ppq_position": 0.0,
        },
    )
    restarted = client.post(
        f"/api/bridge/sessions/{sid}/transport",
        json={
            "plugin_instance_id": "source-au",
            "session_id": sid,
            "playing": True,
            "ppq_position": 12.0,
        },
    )
    assert stopped.status_code == 200
    assert restarted.status_code == 200
    assert restarted.json()["frame_count"] == 0

    later = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[
            _source_frame(
                sid,
                capture_epoch=None,
                ppq=12.0,
                bar=3,
            )
        ],
    )
    assert later.status_code == 200
    assert later.json()["frame_count"] == 1
    assert later.json()["source_epoch"] == 2


def test_harmonic_identity_does_not_overwrite_primary_source_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)

    source = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[_source_frame(sid)],
    )
    assert source.status_code == 200
    harmonic = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[_harmonic_frame(sid, key_pc=0)],
    )
    assert harmonic.status_code == 200
    assert harmonic.json()["harmonic_epoch"] == 1
    state = client.get(f"/api/bridge/sessions/{sid}/state").json()
    assert state["plugin_instance_id"] == "source-au"
    assert state["source_plugin_instance_id"] == "source-au"
    assert state["source_id"] == "drum-bus"
    assert state["harmonic_plugin_instance_id"] == "listener-au"
    assert state["harmonic_source_id"] == "master-bus"

    newer_harmonic_take = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[
            _harmonic_frame(
                sid,
                capture_epoch=2,
                key_pc=6,
                ppq=8.0,
                bar=2,
            )
        ],
    )
    assert newer_harmonic_take.status_code == 200
    assert newer_harmonic_take.json()["capture_epoch_reset"] is True
    assert newer_harmonic_take.json()["harmonic_frame_count"] == 1
    assert newer_harmonic_take.json()["harmonic_epoch"] == 2
    summary = bridge_store.summarize_harmonic_frames(sid)
    assert summary is not None
    assert summary["key_pc"] == 6


def test_ephemeral_bridge_traffic_does_not_write_session_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    writes: list[int] = []

    monkeypatch.setattr(
        app.state,
        "session_persistence_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        session_store,
        "save_sessions",
        lambda sessions: writes.append(len(sessions)) or len(writes),
    )

    heartbeat = client.post(
        "/api/bridge/heartbeat",
        json={
            "plugin_instance_id": "source-au",
            "session_id": sid,
            "source_id": "drum-bus",
        },
    )
    transport = client.post(
        f"/api/bridge/sessions/{sid}/transport",
        json={
            "plugin_instance_id": "source-au",
            "session_id": sid,
            "playing": False,
        },
    )
    source = client.post(
        f"/api/bridge/sessions/{sid}/source-frames",
        json=[_source_frame(sid)],
    )
    harmonic = client.post(
        f"/api/bridge/sessions/{sid}/harmonic",
        json=[_harmonic_frame(sid)],
    )

    assert [
        heartbeat.status_code,
        transport.status_code,
        source.status_code,
        harmonic.status_code,
    ] == [200, 200, 200, 200]
    assert writes == []

    committed = client.post(
        f"/api/bridge/sessions/{sid}/commit-source-groove"
    )
    assert committed.status_code == 200, committed.text
    assert writes == [1]
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.key == "C"
    assert stored.scale == "major"
    assert stored.bridge_live_overlay_active is False
    assert "bridge_harmonic" not in stored.source_analysis_override.source_metadata


def test_ephemeral_bridge_records_without_waiting_for_durable_save(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    sid = _create_phrase_session(TestClient(app))
    route_entered = Event()
    release_route = Event()
    bridge_recorded = Event()
    durable_save_completed = Event()
    deferred_overlay_applied = Event()
    original_describe_scale = session_routes.mt.describe_scale
    original_record_source = bridge_store.record_source_frame
    original_apply_source = bridge_routes._apply_live_source_groove
    blocked_once = False

    def blocking_describe_scale(scale: str) -> str:
        nonlocal blocked_once
        if scale == "dorian" and not blocked_once:
            blocked_once = True
            route_entered.set()
            if not release_route.wait(timeout=2.0):
                raise TimeoutError("durable route was never released")
        return original_describe_scale(scale)

    def save_spy(_sessions: object) -> int:
        durable_save_completed.set()
        return 1

    def record_spy(frame):
        assert not durable_save_completed.is_set()
        bridge_recorded.set()
        return original_record_source(frame)

    def apply_spy(*args, **kwargs):
        result = original_apply_source(*args, **kwargs)
        deferred_overlay_applied.set()
        return result

    monkeypatch.setattr(session_routes.mt, "describe_scale", blocking_describe_scale)
    monkeypatch.setattr(session_store, "save_sessions", save_spy)
    monkeypatch.setattr(bridge_store, "record_source_frame", record_spy)
    monkeypatch.setattr(
        bridge_routes,
        "_apply_live_source_groove",
        apply_spy,
    )
    monkeypatch.setattr(
        app.state,
        "session_persistence_enabled",
        True,
        raising=False,
    )

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            durable_task = asyncio.create_task(
                client.patch(
                    f"/api/sessions/{sid}",
                    json={"scale": "dorian"},
                )
            )
            assert await asyncio.to_thread(
                route_entered.wait,
                1.0,
            )
            bridge_task = asyncio.create_task(
                client.post(
                    f"/api/bridge/sessions/{sid}/source-frames",
                    json=[_source_frame(sid)],
                )
            )
            bridge_response = await asyncio.wait_for(bridge_task, timeout=0.5)
            assert bridge_recorded.is_set()
            assert not durable_save_completed.is_set()
            assert bridge_response.json()["session_overlay_deferred"] is True
            release_route.set()
            return await durable_task, bridge_response

    try:
        durable_response, bridge_response = asyncio.run(exercise())
    finally:
        release_route.set()

    assert durable_response.status_code == 200, durable_response.text
    assert bridge_response.status_code == 200, bridge_response.text
    assert durable_save_completed.is_set()
    assert bridge_recorded.is_set()
    assert deferred_overlay_applied.wait(timeout=1.0)
    stored = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert stored.bridge_live_overlay_active is True
    assert stored.source_analysis_override is not None


def test_bridge_overlay_re_resolves_session_after_durable_object_swap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable(monkeypatch)
    client = TestClient(app)
    sid = _create_phrase_session(client)
    original = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    frame_recorded = Event()
    release_record = Event()
    original_record_source = bridge_store.record_source_frame

    def blocking_record(frame):
        result = original_record_source(frame)
        frame_recorded.set()
        if not release_record.wait(timeout=2.0):
            raise TimeoutError("source-frame route was never released")
        return result

    monkeypatch.setattr(
        bridge_store,
        "record_source_frame",
        blocking_record,
    )

    async def exercise() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as async_client:
            bridge_task = asyncio.create_task(
                async_client.post(
                    f"/api/bridge/sessions/{sid}/source-frames",
                    json=[_source_frame(sid)],
                )
            )
            assert await asyncio.to_thread(frame_recorded.wait, 1.0)
            session_mutation_gate.acquire()
            try:
                replacement = session_routes._duplicate_stored_session(  # noqa: SLF001
                    original,
                    sid,
                )
                session_routes._SESSIONS[sid] = replacement  # type: ignore[attr-defined]
            finally:
                session_mutation_gate.release()
            release_record.set()
            return await bridge_task

    try:
        response = asyncio.run(exercise())
    finally:
        release_record.set()

    assert response.status_code == 200, response.text
    current = session_routes._SESSIONS[sid]  # type: ignore[attr-defined]
    assert current is not original
    assert current.bridge_live_overlay_active is True
    assert current.source_analysis_override is not None
    assert original.bridge_live_overlay_active is False
    assert original.source_analysis_override is None


def test_durable_mutation_waits_for_claimed_bridge_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inverse interleaving cannot save halfway through an overlay apply."""

    _enable(monkeypatch)
    sid = _create_phrase_session(TestClient(app))
    overlay_entered = Event()
    release_overlay = Event()
    durable_route_entered = Event()
    original_apply = bridge_routes._apply_live_source_groove
    original_describe_scale = session_routes.mt.describe_scale

    def blocking_apply(*args, **kwargs):
        overlay_entered.set()
        if not release_overlay.wait(timeout=2.0):
            raise TimeoutError("bridge overlay was never released")
        return original_apply(*args, **kwargs)

    def describe_spy(scale: str) -> str:
        durable_route_entered.set()
        return original_describe_scale(scale)

    monkeypatch.setattr(
        bridge_routes,
        "_apply_live_source_groove",
        blocking_apply,
    )
    monkeypatch.setattr(
        session_routes.mt,
        "describe_scale",
        describe_spy,
    )

    async def exercise() -> tuple[httpx.Response, httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            bridge_task = asyncio.create_task(
                client.post(
                    f"/api/bridge/sessions/{sid}/source-frames",
                    json=[_source_frame(sid)],
                )
            )
            assert await asyncio.to_thread(overlay_entered.wait, 1.0)
            durable_task = asyncio.create_task(
                client.patch(
                    f"/api/sessions/{sid}",
                    json={"scale": "dorian"},
                )
            )
            await asyncio.sleep(0.05)
            assert not durable_route_entered.is_set()
            release_overlay.set()
            return await bridge_task, await durable_task

    try:
        bridge_response, durable_response = asyncio.run(exercise())
    finally:
        release_overlay.set()

    assert bridge_response.status_code == 200, bridge_response.text
    assert durable_response.status_code == 200, durable_response.text
    assert durable_route_entered.is_set()
