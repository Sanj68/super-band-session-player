from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.routes import session_routes
from app.services import acceptance_fixture, session_store


@pytest.fixture()
def fixture_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setattr(session_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(session_store, "_SESSIONS_FILE", tmp_path / "sessions.json")
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    return TestClient(app)


def _create_session(client: TestClient) -> str:
    response = client.post(
        "/api/sessions/",
        json={
            "tempo": 116,
            "key": "D",
            "scale": "natural_minor",
            "bar_count": 16,
            "bass_output_transpose_semitones": 12,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["session"]["id"]


def test_seal_is_idempotent_and_rejects_durable_mutation(
    fixture_client: TestClient,
) -> None:
    session_id = _create_session(fixture_client)
    sealed = fixture_client.post(
        f"/api/sessions/{session_id}/acceptance-fixture",
        json={"name": "116 BPM Logic acceptance"},
    )
    assert sealed.status_code == 200, sealed.text
    body = sealed.json()
    assert body["acceptance_fixture_name"] == "116 BPM Logic acceptance"
    assert body["acceptance_sealed_at"]
    assert len(body["acceptance_receipt_sha256"]) == 64

    same = fixture_client.post(
        f"/api/sessions/{session_id}/acceptance-fixture",
        json={"name": "116 BPM Logic acceptance"},
    )
    assert same.status_code == 200
    assert same.json()["acceptance_receipt_sha256"] == body["acceptance_receipt_sha256"]

    changed = fixture_client.patch(
        f"/api/sessions/{session_id}",
        json={"tempo": 117},
    )
    assert changed.status_code == 409
    assert changed.json()["detail"]["error"] == "acceptance_fixture_sealed"
    assert fixture_client.get(f"/api/sessions/{session_id}").json()["tempo"] == 116


def test_sealed_fixture_can_be_duplicated_to_mutable_working_session(
    fixture_client: TestClient,
) -> None:
    session_id = _create_session(fixture_client)
    sealed = fixture_client.post(
        f"/api/sessions/{session_id}/acceptance-fixture",
        json={"name": "Approved fixture"},
    )
    assert sealed.status_code == 200

    duplicated = fixture_client.post(f"/api/sessions/{session_id}/duplicate")
    assert duplicated.status_code == 200, duplicated.text
    clone = duplicated.json()
    assert clone["id"] != session_id
    assert clone["acceptance_fixture_name"] is None
    assert clone["acceptance_sealed_at"] is None
    assert clone["acceptance_receipt_sha256"] is None

    patched = fixture_client.patch(
        f"/api/sessions/{clone['id']}",
        json={"tempo": 117},
    )
    assert patched.status_code == 200, patched.text


def test_live_bridge_overlay_does_not_break_durable_fixture_manifest(
    fixture_client: TestClient,
) -> None:
    session_id = _create_session(fixture_client)
    sealed = fixture_client.post(
        f"/api/sessions/{session_id}/acceptance-fixture",
        json={"name": "Bridge-safe fixture"},
    )
    assert sealed.status_code == 200
    current = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    staged = replace(current)
    staged.bridge_live_overlay_active = True
    staged.bridge_live_base_key = current.key
    staged.bridge_live_base_scale = current.scale
    staged.key = "A"
    staged.scale = "major"

    published = session_routes._publish_staged_session(current, staged)  # noqa: SLF001

    assert published.key == "A"
    assert published.acceptance_receipt_sha256 == current.acceptance_receipt_sha256
    acceptance_fixture.validate_sealed_session(
        session_routes._durable_session_view(published)  # noqa: SLF001
    )


def test_sealed_fixture_round_trips_and_tamper_fails_closed(
    fixture_client: TestClient,
) -> None:
    session_id = _create_session(fixture_client)
    sealed = fixture_client.post(
        f"/api/sessions/{session_id}/acceptance-fixture",
        json={"name": "Persistent fixture"},
    )
    assert sealed.status_code == 200

    session_store.save_sessions(session_routes._SESSIONS)  # type: ignore[attr-defined]
    restored = session_store.load_sessions(session_routes.StoredSession)
    assert restored[session_id].acceptance_fixture_name == "Persistent fixture"
    acceptance_fixture.validate_sealed_session(restored[session_id])

    document = json.loads(
        session_store._SESSIONS_FILE.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    )
    document["sessions"][0]["tempo"] = 117
    session_store._SESSIONS_FILE.write_text(  # type: ignore[attr-defined]
        json.dumps(document),
        encoding="utf-8",
    )

    with pytest.raises(
        session_store.SessionStoreError,
        match="Acceptance fixture state does not match manifest",
    ):
        session_store.load_sessions(session_routes.StoredSession)


def test_publish_reports_a_clear_conflict_for_direct_mutation(
    fixture_client: TestClient,
) -> None:
    session_id = _create_session(fixture_client)
    fixture_client.post(
        f"/api/sessions/{session_id}/acceptance-fixture",
        json={"name": "Direct guard fixture"},
    )
    current = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    staged = replace(current, bass_output_transpose_semitones=0)

    with pytest.raises(HTTPException) as raised:
        session_routes._publish_staged_session(current, staged)  # noqa: SLF001

    assert raised.value.status_code == 409
    assert raised.value.detail["error"] == "acceptance_fixture_sealed"


def test_plugin_regenerate_cannot_rewrite_sealed_fixture(
    fixture_client: TestClient,
) -> None:
    session_id = _create_session(fixture_client)
    generated = fixture_client.post(
        f"/api/sessions/{session_id}/regenerate-selected",
        json={"lanes": ["bass"]},
    )
    assert generated.status_code == 200, generated.text
    sealed = fixture_client.post(
        f"/api/sessions/{session_id}/acceptance-fixture",
        json={"name": "Plugin-bound fixture"},
    )
    assert sealed.status_code == 200
    before = session_routes._SESSIONS[session_id].bass_bytes  # type: ignore[attr-defined]

    regenerated = fixture_client.post(
        "/api/plugin/regenerate",
        json={
            "session_id": session_id,
            "bass_style": "melodic",
        },
    )

    assert regenerated.status_code == 409
    assert regenerated.json()["detail"]["error"] == "acceptance_fixture_sealed"
    assert session_routes._SESSIONS[session_id].bass_bytes == before  # type: ignore[attr-defined]
