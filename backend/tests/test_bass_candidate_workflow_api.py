from __future__ import annotations

import base64
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.routes import session_routes
from app.services import bass_candidate_store


def test_bass_candidate_workflow_generate_list_notes_promote(tmp_path: Path) -> None:
    # Isolate candidate-run persistence for this test.
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]

    # Isolate in-memory session state to avoid cross-test bleed.
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)

    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 108,
            "key": "C",
            "scale": "major",
            "bar_count": 8,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "bass_instrument": "finger_bass",
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]

    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 3, "seed": 42001, "clip_id": "clip-a"},
    )
    assert generated.status_code == 200
    run = generated.json()
    assert run["session_id"] == session_id
    assert run["take_count"] == 3
    assert run["bass_style"] == "supportive"
    assert run["bass_engine"] == "baseline"
    assert isinstance(run["run_id"], str) and run["run_id"]
    assert isinstance(run["takes"], list) and len(run["takes"]) == 3
    first_take = run["takes"][0]
    assert isinstance(first_take["take_id"], str) and first_take["take_id"]
    assert isinstance(first_take["seed"], int)
    assert first_take["note_count"] >= 0
    assert first_take["byte_length"] > 0
    assert isinstance(first_take["preview"], str)

    # New selection-metadata and quality fields must be present on every take.
    for take in run["takes"]:
        assert take.get("selection_stage") in ("strict", "relaxed", "final_fill"), (
            f"unexpected selection_stage: {take.get('selection_stage')!r}"
        )
        assert isinstance(take.get("motif_family"), str) and take["motif_family"], (
            "motif_family should be a non-empty string"
        )
        assert 0.0 <= float(take.get("quality_total", -1)) <= 1.0
        assert isinstance(take.get("quality_scores"), dict)
        assert isinstance(take.get("quality_reason"), str)
        assert take.get("quality_floor_cutoff") is not None
        assert take.get("top_pool_score") is not None

    listed = client.get(f"/api/sessions/{session_id}/bass-candidates")
    assert listed.status_code == 200
    rows = listed.json()
    assert isinstance(rows, list) and len(rows) >= 1
    listed_run = next((r for r in rows if r["run_id"] == run["run_id"]), None)
    assert listed_run is not None
    assert listed_run["session_id"] == session_id
    assert listed_run["take_count"] == 3
    assert len(listed_run["takes"]) == 3

    notes_res = client.get(
        f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{first_take['take_id']}/notes"
    )
    assert notes_res.status_code == 200
    notes = notes_res.json()
    assert isinstance(notes, list)
    assert len(notes) > 0
    for n in notes[:5]:
        assert set(n.keys()) == {"pitch", "start", "end", "velocity"}
        assert 0 <= n["pitch"] <= 127
        assert n["start"] >= 0
        assert n["end"] >= n["start"]
        assert 0 <= n["velocity"] <= 127

    midi_res = client.get(
        f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{first_take['take_id']}"
    )
    assert midi_res.status_code == 200
    assert midi_res.headers["content-type"] == "audio/midi"
    assert "attachment;" in midi_res.headers["content-disposition"]
    assert midi_res.headers["content-disposition"].endswith(
        f'filename="{session_id}_{run["run_id"]}_{first_take["take_id"]}_bass.mid"'
    )
    assert len(midi_res.content) > 0
    stored_run = bass_candidate_store.get_run_for_session(session_id, run["run_id"])
    assert stored_run is not None
    stored_take = next(t for t in stored_run["takes"] if t["take_id"] == first_take["take_id"])
    assert midi_res.content == base64.b64decode(stored_take["midi_b64"].encode("ascii"))

    promoted = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{first_take['take_id']}/promote"
    )
    assert promoted.status_code == 200
    state = promoted.json()
    assert state["id"] == session_id
    assert state["current_bass_candidate_run_id"] == run["run_id"]
    assert state["current_bass_candidate_take_id"] == first_take["take_id"]
    assert state["lanes"]["bass"]["generated"] is True
    assert isinstance(state["lanes"]["bass"]["notes"], list)
    assert len(state["lanes"]["bass"]["notes"]) > 0


def test_bass_candidate_promote_404_on_invalid_ids(tmp_path: Path) -> None:
    """promote returns 404 for unknown session_id, run_id, or take_id."""
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)

    created = client.post(
        "/api/sessions/",
        json={"tempo": 100, "key": "D", "scale": "minor", "bar_count": 4},
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]

    gen = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 7777},
    )
    assert gen.status_code == 200
    run_id = gen.json()["run_id"]
    take_id = gen.json()["takes"][0]["take_id"]

    # Unknown session_id → 404.
    r = client.post(f"/api/sessions/no-such-session/bass-candidates/{run_id}/{take_id}/promote")
    assert r.status_code == 404

    # Valid session, unknown run_id → 404.
    r = client.post(f"/api/sessions/{session_id}/bass-candidates/bad-run/{take_id}/promote")
    assert r.status_code == 404

    # Valid session + run, unknown take_id → 404.
    r = client.post(f"/api/sessions/{session_id}/bass-candidates/{run_id}/bad-take/promote")
    assert r.status_code == 404


def test_bass_candidate_midi_download_404_on_invalid_ids(tmp_path: Path) -> None:
    """candidate MIDI download returns safe 404s for unknown run_id or take_id."""
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)

    created = client.post(
        "/api/sessions/",
        json={"tempo": 104, "key": "A", "scale": "minor", "bar_count": 4},
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]

    gen = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 8888},
    )
    assert gen.status_code == 200
    run_id = gen.json()["run_id"]
    take_id = gen.json()["takes"][0]["take_id"]

    r = client.get(f"/api/sessions/{session_id}/bass-candidates/bad-run/{take_id}")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "candidate_run_not_found"

    r = client.get(f"/api/sessions/{session_id}/bass-candidates/{run_id}/bad-take")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "candidate_take_not_found"


def test_bass_candidate_promote_lane_notes_match_candidate_notes(tmp_path: Path) -> None:
    """After promotion, the session bass lane notes are exactly the promoted candidate's notes."""
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)

    created = client.post(
        "/api/sessions/",
        json={"tempo": 96, "key": "F", "scale": "major", "bar_count": 4},
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]

    gen = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 55555},
    )
    assert gen.status_code == 200
    run_id = gen.json()["run_id"]
    take_id = gen.json()["takes"][0]["take_id"]

    # Fetch the candidate's own notes before any promotion.
    notes_res = client.get(
        f"/api/sessions/{session_id}/bass-candidates/{run_id}/{take_id}/notes"
    )
    assert notes_res.status_code == 200
    candidate_notes = notes_res.json()
    assert len(candidate_notes) > 0

    # Promote the candidate.
    promote_res = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{run_id}/{take_id}/promote"
    )
    assert promote_res.status_code == 200
    state = promote_res.json()
    assert state["current_bass_candidate_run_id"] == run_id
    assert state["current_bass_candidate_take_id"] == take_id

    # The session bass lane notes must be identical to the candidate's notes.
    lane_notes = state["lanes"]["bass"]["notes"]
    assert len(lane_notes) == len(candidate_notes)
    for lane_note, cand_note in zip(lane_notes, candidate_notes):
        assert lane_note["pitch"] == cand_note["pitch"]
        assert lane_note["start"] == cand_note["start"]
        assert lane_note["end"] == cand_note["end"]
        assert lane_note["velocity"] == cand_note["velocity"]
