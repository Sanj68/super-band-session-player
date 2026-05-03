from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.routes import midi_routes
from app.services.midi_audition import EmptyMidiBackend, FakeMidiBackend, MidiOutputInfo, RtMidiBackend


def teardown_function() -> None:
    midi_routes.set_midi_output_backend(RtMidiBackend())


def test_fake_backend_returns_fake_ports() -> None:
    midi_routes.set_midi_output_backend(
        FakeMidiBackend(
            (
                MidiOutputInfo(id="iac-bus-1", name="IAC Driver Bus 1"),
                MidiOutputInfo(id="external", name="External MIDI"),
            )
        )
    )
    client = TestClient(app)

    res = client.get("/api/midi/outputs")

    assert res.status_code == 200
    assert res.json() == {
        "outputs": [
            {"id": "iac-bus-1", "name": "IAC Driver Bus 1"},
            {"id": "external", "name": "External MIDI"},
        ],
        "default": "iac-bus-1",
        "hint": None,
    }


def test_empty_backend_returns_outputs_empty_and_hint() -> None:
    midi_routes.set_midi_output_backend(EmptyMidiBackend("Enable the IAC Driver in Audio MIDI Setup."))
    client = TestClient(app)

    res = client.get("/api/midi/outputs")

    assert res.status_code == 200
    body = res.json()
    assert body["outputs"] == []
    assert body["default"] is None
    assert "IAC Driver" in body["hint"]


def test_endpoint_does_not_500_when_midi_backend_unavailable() -> None:
    class UnavailableBackend:
        def list_outputs(self):
            raise RuntimeError("MIDI backend unavailable.")

    midi_routes.set_midi_output_backend(UnavailableBackend())
    client = TestClient(app)

    res = client.get("/api/midi/outputs")

    assert res.status_code == 200
    body = res.json()
    assert body["outputs"] == []
    assert body["default"] is None
    assert "unavailable" in body["hint"]
