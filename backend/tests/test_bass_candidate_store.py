from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import session_routes
from app.services import bass_candidate_store


@pytest.fixture
def candidate_store_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Path:
    runs_file = tmp_path / "bass_candidate_runs.json"
    monkeypatch.setattr(bass_candidate_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(bass_candidate_store, "_RUNS_FILE", runs_file)
    return runs_file


def test_corrupt_store_fails_closed_without_overwriting_original(
    candidate_store_path: Path,
) -> None:
    corrupt = '{"runs": ['
    candidate_store_path.write_text(corrupt, encoding="utf-8")

    with pytest.raises(bass_candidate_store.CandidateStoreError):
        bass_candidate_store.load_runs()
    with pytest.raises(bass_candidate_store.CandidateStoreError):
        bass_candidate_store.append_run(
            {"run_id": "must-not-overwrite", "session_id": "session-1"}
        )

    assert candidate_store_path.read_text(encoding="utf-8") == corrupt
    assert list(candidate_store_path.parent.glob(".*.tmp")) == []


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"schema_version": 999, "runs": []},
        {"schema_version": True, "runs": []},
        {"schema_version": 1, "runs": {}},
        {"schema_version": 1, "runs": ["not-an-object"]},
        {
            "schema_version": 1,
            "runs": [{"run_id": "non-finite", "score": float("nan")}],
        },
    ],
)
def test_unsupported_store_shapes_fail_closed(
    candidate_store_path: Path,
    document: object,
) -> None:
    candidate_store_path.write_text(
        json.dumps(document),
        encoding="utf-8",
    )

    with pytest.raises(bass_candidate_store.CandidateStoreError):
        bass_candidate_store.load_runs()


def test_exponent_overflow_store_value_fails_closed(
    candidate_store_path: Path,
) -> None:
    candidate_store_path.write_text(
        (
            '{"schema_version":1,"runs":[{"run_id":"overflow",'
            '"takes":[{"quality_scores":{"pocket":1e999}}]}]}'
        ),
        encoding="utf-8",
    )

    with pytest.raises(bass_candidate_store.CandidateStoreError):
        bass_candidate_store.load_runs()


def test_legacy_container_is_read_and_upgraded_on_append(
    candidate_store_path: Path,
) -> None:
    candidate_store_path.write_text(
        json.dumps(
            {
                "runs": [
                    {
                        "run_id": "legacy-run",
                        "session_id": "session-1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert [
        row["run_id"] for row in bass_candidate_store.load_runs()
    ] == ["legacy-run"]
    bass_candidate_store.append_run(
        {"run_id": "new-run", "session_id": "session-1"}
    )

    document = json.loads(
        candidate_store_path.read_text(encoding="utf-8")
    )
    assert document["schema_version"] == 1
    assert [row["run_id"] for row in document["runs"]] == [
        "legacy-run",
        "new-run",
    ]


def test_failed_atomic_replace_preserves_old_store_and_cleans_temp(
    candidate_store_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = json.dumps(
        {
            "schema_version": 1,
            "runs": [
                {"run_id": "original", "session_id": "session-1"}
            ],
        }
    )
    candidate_store_path.write_text(original, encoding="utf-8")
    real_replace = Path.replace

    def fail_target_replace(source: Path, target: Path) -> Path:
        if Path(target) == candidate_store_path:
            raise OSError("simulated replace failure")
        return real_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_target_replace)

    with pytest.raises(bass_candidate_store.CandidateStoreError):
        bass_candidate_store.append_run(
            {"run_id": "not-committed", "session_id": "session-1"}
        )

    assert candidate_store_path.read_text(encoding="utf-8") == original
    assert not list(candidate_store_path.parent.glob(".*.tmp"))


def test_concurrent_appends_preserve_every_run(
    candidate_store_path: Path,
) -> None:
    count = 24
    barrier = Barrier(count)

    def append(index: int) -> None:
        barrier.wait()
        bass_candidate_store.append_run(
            {
                "run_id": f"run-{index}",
                "session_id": "session-1",
                "created_at": f"{index:02d}",
            }
        )

    with ThreadPoolExecutor(max_workers=count) as executor:
        list(executor.map(append, range(count)))

    rows = bass_candidate_store.load_runs()
    assert len(rows) == count
    assert {row["run_id"] for row in rows} == {
        f"run-{index}" for index in range(count)
    }
    assert not list(candidate_store_path.parent.glob(".*.tmp"))


def _create_session(client: TestClient) -> str:
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    response = client.post(
        "/api/sessions/",
        json={
            "tempo": 100,
            "key": "D",
            "scale": "natural_minor",
            "bar_count": 4,
        },
    )
    assert response.status_code == 200, response.text
    return str(response.json()["session"]["id"])


def test_candidate_api_maps_corrupt_store_to_503(
    candidate_store_path: Path,
) -> None:
    client = TestClient(app)
    session_id = _create_session(client)
    candidate_store_path.write_text("{invalid-json", encoding="utf-8")

    response = client.get(
        f"/api/sessions/{session_id}/bass-candidates"
    )
    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 1234},
    )

    assert response.status_code == 503
    assert generated.status_code == 503
    assert (
        response.json()["detail"]["error"]
        == "candidate_store_unavailable"
    )
    assert (
        generated.json()["detail"]["error"]
        == "candidate_store_unavailable"
    )
    assert candidate_store_path.read_text(encoding="utf-8") == "{invalid-json"


def test_candidate_list_maps_malformed_numeric_metadata_to_409(
    candidate_store_path: Path,
) -> None:
    client = TestClient(app)
    session_id = _create_session(client)
    bass_candidate_store.append_run(
        {
            "run_id": "malformed-list-run",
            "session_id": session_id,
            "created_at": "2026-07-26T00:00:00+00:00",
            "take_count": 1,
            "takes": [
                {
                    "take_id": "take-1",
                    "seed": ["not", "numeric"],
                    "note_count": 1,
                    "byte_length": 1,
                }
            ],
        }
    )

    response = client.get(
        f"/api/sessions/{session_id}/bass-candidates"
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["error"]
        == "candidate_run_metadata_invalid"
    )
    assert response.json()["detail"]["run_id"] == "malformed-list-run"


def test_candidate_promotion_maps_malformed_context_metadata_to_409(
    candidate_store_path: Path,
) -> None:
    client = TestClient(app)
    session_id = _create_session(client)
    bass_candidate_store.append_run(
        {
            "run_id": "malformed-promotion-run",
            "session_id": session_id,
            "generation_context_version": {"not": "numeric"},
            "generation_context_fingerprint": "x" * 64,
            "takes": [{"take_id": "take-1"}],
        }
    )

    response = client.post(
        f"/api/sessions/{session_id}/bass-candidates/"
        "malformed-promotion-run/take-1/promote"
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["error"]
        == "candidate_run_metadata_invalid"
    )
    assert (
        response.json()["detail"]["run_id"]
        == "malformed-promotion-run"
    )


def test_candidate_promotion_maps_malformed_take_seed_to_409(
    candidate_store_path: Path,
) -> None:
    client = TestClient(app)
    session_id = _create_session(client)
    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 4321},
    )
    assert generated.status_code == 200, generated.text
    run = generated.json()
    take_id = run["takes"][0]["take_id"]
    document = json.loads(
        candidate_store_path.read_text(encoding="utf-8")
    )
    document["runs"][0]["takes"][0]["seed"] = {"not": "an integer"}
    candidate_store_path.write_text(
        json.dumps(document),
        encoding="utf-8",
    )

    response = client.post(
        f"/api/sessions/{session_id}/bass-candidates/"
        f"{run['run_id']}/{take_id}/promote"
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["error"]
        == "candidate_run_metadata_invalid"
    )
