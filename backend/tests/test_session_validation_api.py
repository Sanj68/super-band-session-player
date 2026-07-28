"""Validation boundaries for session harmony inputs."""

from fastapi.testclient import TestClient

from app.main import app
from app.routes import session_routes


def _create_session(client: TestClient) -> str:
    response = client.post(
        "/api/sessions/",
        json={
            "tempo": 120,
            "key": "C",
            "scale": "major",
            "bar_count": 4,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["session"]["id"]


def test_create_rejects_unknown_key_and_scale() -> None:
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    client = TestClient(app)

    bad_key = client.post(
        "/api/sessions/",
        json={"tempo": 120, "key": "H", "scale": "major", "bar_count": 4},
    )
    bad_scale = client.post(
        "/api/sessions/",
        json={"tempo": 120, "key": "C", "scale": "mystery_mode", "bar_count": 4},
    )

    assert bad_key.status_code == 422
    assert bad_scale.status_code == 422


def test_patch_rejects_unknown_harmony_without_mutating_session() -> None:
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    client = TestClient(app)
    session_id = _create_session(client)
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]

    bad_key = client.patch(
        f"/api/sessions/{session_id}",
        json={"key": "H"},
    )
    bad_scale = client.patch(
        f"/api/sessions/{session_id}",
        json={"scale": "mystery_mode"},
    )

    assert bad_key.status_code == 422
    assert bad_scale.status_code == 422
    assert stored.key == "C"
    assert stored.scale == "major"


def test_harmony_aliases_are_canonicalized() -> None:
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    client = TestClient(app)
    session_id = _create_session(client)

    response = client.patch(
        f"/api/sessions/{session_id}",
        json={"key": "bb", "scale": "aeolian"},
    )

    assert response.status_code == 200, response.text
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    assert stored.key == "Bb"
    assert stored.scale == "natural_minor"
