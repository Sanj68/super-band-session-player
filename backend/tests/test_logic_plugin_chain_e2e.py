"""Logic-first integration proof: Bridge + Listener -> backend -> Bass AU part."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import bridge_routes, session_routes
from app.services import bridge_store, session_store


ROOT = Path(__file__).resolve().parents[2]
PLUGIN_SOURCES = (
    ROOT / "audio-unit" / "Source" / "PluginProcessor.h",
    ROOT / "audio-unit" / "Source" / "PluginProcessor.cpp",
    ROOT / "audio-listener" / "Source" / "PluginProcessor.cpp",
    ROOT / "audio-midifx" / "Source" / "PluginProcessor.cpp",
)


@pytest.fixture()
def logic_chain_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[TestClient]:
    saved_sessions = dict(session_routes._SESSIONS)  # noqa: SLF001
    session_routes._SESSIONS.clear()  # noqa: SLF001
    bridge_store.clear_bridge_state()
    monkeypatch.setattr(session_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        session_store,
        "_SESSIONS_FILE",
        tmp_path / "sessions.json",
    )
    monkeypatch.setenv(bridge_routes._FEATURE_FLAG_ENV, "true")
    try:
        yield TestClient(app)
    finally:
        session_routes._SESSIONS.clear()  # noqa: SLF001
        session_routes._SESSIONS.update(saved_sessions)  # noqa: SLF001
        bridge_store.clear_bridge_state()


def _bridge_frames(session_id: str) -> list[dict[str, object]]:
    frames: list[dict[str, object]] = []
    kick_slots = {0, 6, 10}
    snare_slots = {4, 12}
    for bar in range(4):
        for slot in range(16):
            kick = slot in kick_slots
            snare = slot in snare_slots
            frames.append(
                {
                    "plugin_instance_id": "logic-au-chain-bridge",
                    "session_id": session_id,
                    "source_id": "logic-drum-bus",
                    "capture_epoch": 7,
                    "sample_rate": 48000.0,
                    "host_tempo": 116.0,
                    "tempo": 116.0,
                    "playing": True,
                    "ppq_position": bar * 4.0 + slot * 0.25,
                    "bar_position": slot / 16.0,
                    "bar_index": bar,
                    "frame_start_seconds": (
                        (bar * 16 + slot) * 0.125
                    ),
                    "duration_seconds": 0.125,
                    "rms": 0.62 if kick or snare else 0.24,
                    "band_energy": {
                        "low": 0.95 if kick else 0.18,
                        "mid": 0.82 if snare else 0.22,
                        "high": 0.36,
                    },
                    "low_band_energy": 0.95 if kick else 0.18,
                    "mid_band_energy": 0.82 if snare else 0.22,
                    "high_band_energy": 0.36,
                    "onset_strength": 0.9 if kick or snare else 0.12,
                }
            )
    return frames


def _listener_frames(session_id: str) -> list[dict[str, object]]:
    roots = (2, 7, 9, 2)  # Dm -> Gm -> Am -> Dm
    names = ("D", "G", "A", "D")
    frames: list[dict[str, object]] = []
    bar_seconds = 60.0 / 116.0 * 4.0
    for bar, (root, name) in enumerate(zip(roots, names, strict=True)):
        chroma = [0.01] * 12
        for pitch_class, weight in (
            (root, 1.0),
            ((root + 3) % 12, 0.72),
            ((root + 7) % 12, 0.84),
        ):
            chroma[pitch_class] = weight
        frames.append(
            {
                "plugin_instance_id": "logic-harmonic-au-chain",
                "session_id": session_id,
                "source_id": "session-player-listener",
                "capture_epoch": 11,
                "sample_rate": 48000.0,
                "host_tempo": 116.0,
                "tempo_bpm": 116.0,
                "tempo_confidence": 1.0,
                "playing": True,
                "ppq_position": bar * 4.0,
                "bar_index": bar,
                "frame_start_seconds": bar * bar_seconds,
                "duration_seconds": 8192.0 / 48000.0,
                "chroma": chroma,
                "key_pc": root,
                "key": name,
                "scale": "minor",
                "key_confidence": 0.88,
                "scale_confidence": 0.84,
                "cadence": "tonic" if bar in (0, 3) else "passing",
                "cadence_confidence": 0.64,
            }
        )
    return frames


def test_all_logic_plugins_default_to_owned_backend_port() -> None:
    for path in PLUGIN_SOURCES:
        source = path.read_text(encoding="utf-8")
        assert "127.0.0.1:8000/api" not in source
        assert "127.0.0.1:8001/api" in source


def test_logic_plugin_chain_reaches_playable_bass_contract(
    logic_chain_client: TestClient,
) -> None:
    client = logic_chain_client
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 116,
            "key": "D",
            "scale": "natural_minor",
            "bar_count": 4,
            "session_preset": "fusion",
            "bass_engine": "phrase_v2",
            "bass_instrument": "finger_bass",
            "bass_lock_to_groove": 1.0,
            "bass_articulation_focus": "connected",
            "bass_expression": 0.8,
            "bass_output_transpose_semitones": 12,
        },
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session"]["id"]

    generated = client.post(f"/api/sessions/{session_id}/generate")
    assert generated.status_code == 200, generated.text

    for plugin_id, source_id in (
        ("logic-au-chain-bridge", "logic-drum-bus"),
        ("logic-harmonic-au-chain", "session-player-listener"),
    ):
        heartbeat = client.post(
            "/api/bridge/heartbeat",
            json={
                "plugin_instance_id": plugin_id,
                "plugin_version": "0.1.0",
                "source_id": source_id,
            },
        )
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json()["session_id"] == session_id

    transport = client.post(
        f"/api/bridge/sessions/{session_id}/transport",
        json={
            "plugin_instance_id": "logic-au-chain-bridge",
            "session_id": session_id,
            "host_tempo": 116.0,
            "sample_rate": 48000.0,
            "playing": True,
            "ppq_position": 0.0,
            "bar_index": 0,
            "beat_index": 0,
        },
    )
    assert transport.status_code == 200, transport.text

    bridge_frames = _bridge_frames(session_id)
    for batch_start in range(0, len(bridge_frames), 32):
        batch = client.post(
            f"/api/bridge/sessions/{session_id}/source-frames",
            json=bridge_frames[batch_start : batch_start + 32],
        )
        assert batch.status_code == 200, batch.text
        assert batch.json()["accepted"] == len(
            bridge_frames[batch_start : batch_start + 32]
        )

    harmonic = client.post(
        "/api/bridge/harmonic",
        json=_listener_frames(session_id),
    )
    assert harmonic.status_code == 200, harmonic.text
    assert harmonic.json()["accepted"] == 4
    assert harmonic.json()["live_harmonic_bar_count"] == 4
    assert harmonic.json()["live_harmonic_applied"] is True

    part_response = client.post(
        "/api/plugin/regenerate",
        json={
            "session_id": session_id,
            # Mirrors the Bass AU's NEW BEAT / RESET request. A newly captured
            # Logic source intentionally invalidates the previous Fusion DNA.
            "force_new_phrase": True,
            "host_tempo": 116.0,
            "bass_style": "fusion",
            "bass_player": "none",
            "bass_instrument": "finger_bass",
            "bass_articulation_focus": "connected",
            "bass_density_bias": 0.0,
            "lock_to_groove": 1.0,
            "bass_expression": 0.8,
            "bass_performance_controls": {
                "ghost": 0.2,
                "mute": 0.05,
                "slide": 0.25,
                "legato": 0.25,
                "timing_humanize": 0.5,
                "velocity_humanize": 0.5,
            },
        },
    )
    assert part_response.status_code == 200, part_response.text
    part = part_response.json()

    assert part["version"] == 2
    assert part["session_id"] == session_id
    assert part["tempo"] == 116
    assert part["key"] == "D"
    assert part["bar_count"] == 4
    assert part["bass_style"] == "fusion"
    assert part["bass_articulation_focus"] == "connected"
    assert part["output_transpose_semitones"] == 12
    assert part["groove_source_ready"] is True
    assert part["groove_source_frame_count"] == 64
    assert "groove lock 1.00" in part["preview"]
    assert "captured beat evidence" in part["preview"]
    assert len(part["notes"]) > 0
    assert part["notes"] == sorted(
        part["notes"],
        key=lambda note: note["start_beats"],
    )
    assert all(note["dur_beats"] > 0 for note in part["notes"])
    assert all(
        0 <= note["start_beats"] < part["bar_count"] * 4
        for note in part["notes"]
    )
    assert all(
        0 < note["pitch"] + part["output_transpose_semitones"] < 128
        for note in part["notes"]
    )

    bridge_state = client.get(
        f"/api/bridge/sessions/{session_id}/state"
    )
    assert bridge_state.status_code == 200, bridge_state.text
    state = bridge_state.json()
    assert state["frame_count"] == 64
    assert state["harmonic_frame_count"] == 4
    assert state["source_id"] == "logic-drum-bus"
    assert state["harmonic_source_id"] == "session-player-listener"

    stored = session_routes._SESSIONS[session_id]  # noqa: SLF001
    metadata = stored.source_analysis_override.source_metadata
    assert metadata["last_groove_source_tag"] == "logic_au_bridge"
    assert metadata["live_harmonic_source_tag"] == (
        "logic_au_harmonic_listener"
    )
