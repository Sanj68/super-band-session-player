from __future__ import annotations

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
