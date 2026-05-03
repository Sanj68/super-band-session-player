from __future__ import annotations

from io import BytesIO
import time

from fastapi.testclient import TestClient

from app.main import app
from app.routes import midi_routes, session_routes
from app.services.midi_audition import FakeMidiBackend, MidiOutputInfo, RtMidiBackend


def teardown_function() -> None:
    midi_routes.get_audition_player().stop()
    midi_routes.set_midi_output_backend(RtMidiBackend())
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]


def _client() -> TestClient:
    return TestClient(app)


def _backend() -> FakeMidiBackend:
    backend = FakeMidiBackend((MidiOutputInfo(id="iac-1", name="IAC Driver Bus 1"),))
    midi_routes.set_midi_output_backend(backend)
    return backend


def _midi_bytes(*, pitch: int = 48, seconds: float = 2.0) -> bytes:
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    track.append(mido.Message("note_on", note=pitch, velocity=92, channel=0, time=0))
    ticks = int(seconds * 960)
    track.append(mido.Message("note_off", note=pitch, velocity=0, channel=0, time=ticks))
    out = BytesIO()
    mid.save(file=out)
    return out.getvalue()


def _session(session_id: str = "test-session", *, clean: bytes | None = None, performance: bytes | None = None) -> str:
    session_routes._SESSIONS[session_id] = session_routes.StoredSession(  # type: ignore[attr-defined]
        id=session_id,
        tempo=120,
        key="C",
        scale="major",
        bar_count=4,
        bass_bytes=clean,
        bass_performance_bytes=performance,
    )
    return session_id


def _wait_for(predicate, *, timeout: float = 1.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def _message_types(messages: list[object]) -> list[str]:
    return [str(getattr(message, "type", "")) for message in messages]


def test_audition_clean_bass_writes_midi_messages_to_fake_backend() -> None:
    backend = _backend()
    session_id = _session(clean=_midi_bytes(pitch=48))

    res = _client().post(f"/api/sessions/{session_id}/audition/bass", json={"output": "IAC Driver Bus 1", "mode": "clean"})

    assert res.status_code == 200
    assert res.json()["status"] == "playing"
    assert res.json()["mode"] == "clean"
    _wait_for(lambda: "note_on" in _message_types(backend.sent_messages))
    assert backend.opened_outputs == ["IAC Driver Bus 1"]


def test_audition_performance_bass_uses_performance_bytes_when_available() -> None:
    backend = _backend()
    session_id = _session(clean=_midi_bytes(pitch=48), performance=_midi_bytes(pitch=65))

    res = _client().post(
        f"/api/sessions/{session_id}/audition/bass",
        json={"output": "iac-1", "mode": "performance"},
    )

    assert res.status_code == 200
    assert res.json()["mode"] == "performance"
    _wait_for(lambda: any(getattr(message, "type", "") == "note_on" for message in backend.sent_messages))
    note_ons = [message for message in backend.sent_messages if getattr(message, "type", "") == "note_on"]
    assert any(getattr(message, "note", None) == 65 for message in note_ons)


def test_audition_without_generated_bass_returns_400() -> None:
    _backend()
    session_id = _session(clean=None)

    res = _client().post(f"/api/sessions/{session_id}/audition/bass", json={"output": "iac-1", "mode": "clean"})

    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "bass_lane_missing"


def test_performance_mode_unavailable_returns_404() -> None:
    _backend()
    session_id = _session(clean=_midi_bytes(), performance=None)

    res = _client().post(
        f"/api/sessions/{session_id}/audition/bass",
        json={"output": "iac-1", "mode": "performance"},
    )

    assert res.status_code == 404
    assert res.json()["detail"]["error"] == "performance_midi_unavailable"


def test_stop_is_idempotent() -> None:
    _backend()
    client = _client()

    first = client.post("/api/midi/stop")
    second = client.post("/api/midi/stop")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == {"status": "stopped"}
    assert second.json() == {"status": "stopped"}


def test_starting_new_audition_stops_previous_one() -> None:
    backend = _backend()
    first_session = _session("first", clean=_midi_bytes(pitch=48))
    second_session = _session("second", clean=_midi_bytes(pitch=50))
    client = _client()

    first = client.post(f"/api/sessions/{first_session}/audition/bass", json={"output": "iac-1", "mode": "clean"})
    assert first.status_code == 200
    _wait_for(lambda: "note_on" in _message_types(backend.sent_messages))

    second = client.post(f"/api/sessions/{second_session}/audition/bass", json={"output": "iac-1", "mode": "clean"})

    assert second.status_code == 200
    assert second.json()["session_id"] == "second"
    _wait_for(lambda: client.get("/api/midi/audition/state").json()["session_id"] == "second")
    assert "control_change" in _message_types(backend.sent_messages)


def test_state_endpoint_reports_playing_and_idle() -> None:
    backend = _backend()
    session_id = _session(clean=_midi_bytes())
    client = _client()

    idle = client.get("/api/midi/audition/state")
    assert idle.status_code == 200
    assert idle.json()["playing"] is False

    started = client.post(f"/api/sessions/{session_id}/audition/bass", json={"output": "iac-1", "mode": "clean"})
    assert started.status_code == 200
    _wait_for(lambda: "note_on" in _message_types(backend.sent_messages))

    playing = client.get("/api/midi/audition/state")
    assert playing.status_code == 200
    assert playing.json()["playing"] is True
    assert playing.json()["session_id"] == session_id

    stopped = client.post("/api/midi/stop")
    assert stopped.status_code == 200
    assert client.get("/api/midi/audition/state").json()["playing"] is False


def test_stop_sends_note_off_and_all_notes_off_messages() -> None:
    backend = _backend()
    session_id = _session(clean=_midi_bytes(pitch=52))
    client = _client()

    started = client.post(f"/api/sessions/{session_id}/audition/bass", json={"output": "iac-1", "mode": "clean"})
    assert started.status_code == 200
    _wait_for(lambda: "note_on" in _message_types(backend.sent_messages))

    stopped = client.post("/api/midi/stop")

    assert stopped.status_code == 200
    _wait_for(lambda: "control_change" in _message_types(backend.sent_messages))
    note_offs = [message for message in backend.sent_messages if getattr(message, "type", "") == "note_off"]
    all_notes_off = [
        message
        for message in backend.sent_messages
        if getattr(message, "type", "") == "control_change" and getattr(message, "control", None) == 123
    ]
    assert any(getattr(message, "note", None) == 52 for message in note_offs)
    assert len(all_notes_off) == 16


def test_invalid_output_returns_400() -> None:
    _backend()
    session_id = _session(clean=_midi_bytes())

    res = _client().post(
        f"/api/sessions/{session_id}/audition/bass",
        json={"output": "missing-output", "mode": "clean"},
    )

    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "midi_output_unavailable"
