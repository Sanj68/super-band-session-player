from __future__ import annotations

import io
from pathlib import Path

import pretty_midi
from fastapi.testclient import TestClient

from app.main import app
from app.routes import session_routes
from app.services import bass_candidate_store


def _create_generated_session(client: TestClient) -> str:
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 112,
            "key": "C",
            "scale": "major",
            "bar_count": 4,
            "bass_style": "supportive",
            "bass_engine": "baseline",
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    generated = client.post(f"/api/sessions/{session_id}/generate")
    assert generated.status_code == 200
    return session_id


def _bass_notes(data: bytes) -> list[pretty_midi.Note]:
    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes: list[pretty_midi.Note] = []
    for inst in pm.instruments:
        if inst.name.lower() == "bass":
            notes.extend(inst.notes)
    return sorted(notes, key=lambda n: (n.start, n.pitch, n.end, n.velocity))


def test_session_midi_export_returns_downloadable_midi(tmp_path: Path) -> None:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)
    session_id = _create_generated_session(client)

    res = client.get(f"/api/sessions/{session_id}/midi")

    assert res.status_code == 200
    assert res.headers["content-type"] == "audio/midi"
    assert res.headers["content-disposition"] == f'attachment; filename="session_{session_id}.mid"'
    assert len(res.content) > 0
    pm = pretty_midi.PrettyMIDI(io.BytesIO(res.content))
    assert len(pm.instruments) >= 4
    assert sum(len(inst.notes) for inst in pm.instruments) > 0


def test_session_midi_export_404_for_unknown_session() -> None:
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)
    res = client.get("/api/sessions/no-such-session/midi")

    assert res.status_code == 404


def test_session_midi_export_reflects_promoted_bass_candidate(tmp_path: Path) -> None:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)
    session_id = _create_generated_session(client)

    gen = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 98765},
    )
    assert gen.status_code == 200
    run_id = gen.json()["run_id"]
    take_id = gen.json()["takes"][0]["take_id"]

    notes_res = client.get(f"/api/sessions/{session_id}/bass-candidates/{run_id}/{take_id}/notes")
    assert notes_res.status_code == 200
    candidate_notes = notes_res.json()
    assert len(candidate_notes) > 0

    promoted = client.post(f"/api/sessions/{session_id}/bass-candidates/{run_id}/{take_id}/promote")
    assert promoted.status_code == 200

    exported = client.get(f"/api/sessions/{session_id}/midi")
    assert exported.status_code == 200
    exported_bass_notes = _bass_notes(exported.content)

    assert len(exported_bass_notes) == len(candidate_notes)
    for exported_note, candidate_note in zip(exported_bass_notes, candidate_notes):
        assert exported_note.pitch == candidate_note["pitch"]
        assert round(exported_note.start, 6) == round(candidate_note["start"], 6)
        assert round(exported_note.end, 6) == round(candidate_note["end"], 6)
        assert exported_note.velocity == candidate_note["velocity"]
