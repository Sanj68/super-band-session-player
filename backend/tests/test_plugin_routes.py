"""Tests for the MIDI FX plugin surface (/api/plugin)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _create_session_with_bass(client: TestClient) -> str:
    res = client.post(
        "/api/sessions/",
        json={
            "tempo": 100,
            "key": "C",
            "scale": "major",
            "bar_count": 2,
            "bass_style": "supportive",
            "bass_engine": "phrase_v2",
        },
    )
    assert res.status_code == 200, res.text
    sid = res.json()["session"]["id"]
    gen = client.post(f"/api/sessions/{sid}/regenerate-selected", json={"lanes": ["bass"]})
    assert gen.status_code == 200, gen.text
    return sid


def test_bass_part_404_when_no_sessions(client: TestClient) -> None:
    from app.routes import session_routes

    saved = dict(session_routes._SESSIONS)
    session_routes._SESSIONS.clear()
    try:
        res = client.get("/api/plugin/bass-part")
        assert res.status_code == 404
    finally:
        session_routes._SESSIONS.update(saved)


def test_bass_part_returns_latest_session_notes(client: TestClient) -> None:
    sid = _create_session_with_bass(client)
    res = client.get("/api/plugin/bass-part")
    assert res.status_code == 200, res.text
    part = res.json()
    assert part["session_id"] == sid
    assert part["tempo"] == 100
    assert part["key"] == "C"
    assert part["scale"] == "major"
    assert part["bar_count"] == 2
    assert part["beats_per_bar"] == 4
    assert part["phase_offset_beats"] == pytest.approx(0.0)
    assert len(part["notes"]) > 0
    for n in part["notes"]:
        assert 0 <= n["start_beats"] < part["bar_count"] * 4 + 1
        assert n["dur_beats"] > 0
        assert 0 < n["pitch"] < 128
    # sorted by start
    starts = [n["start_beats"] for n in part["notes"]]
    assert starts == sorted(starts)


def test_bass_part_exposes_patchable_playback_phase(client: TestClient) -> None:
    sid = _create_session_with_bass(client)
    patched = client.patch(
        f"/api/sessions/{sid}",
        json={"bass_phase_offset_beats": 1.0},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["bass_phase_offset_beats"] == pytest.approx(1.0)

    part = client.get(
        "/api/plugin/bass-part",
        params={"session_id": sid},
    )
    assert part.status_code == 200, part.text
    assert part.json()["phase_offset_beats"] == pytest.approx(1.0)


def test_bass_part_honours_explicit_session_binding(client: TestClient) -> None:
    first = _create_session_with_bass(client)
    second = _create_session_with_bass(client)

    latest = client.get("/api/plugin/bass-part")
    bound = client.get("/api/plugin/bass-part", params={"session_id": first})

    assert latest.status_code == 200
    assert latest.json()["session_id"] == second
    assert bound.status_code == 200
    assert bound.json()["session_id"] == first


def test_unknown_bound_session_does_not_fall_back_to_latest(client: TestClient) -> None:
    _create_session_with_bass(client)
    res = client.get("/api/plugin/bass-part", params={"session_id": "missing-session"})
    assert res.status_code == 404


def test_plugin_regenerate_applies_style_lock_and_expression(client: TestClient) -> None:
    _create_session_with_bass(client)
    res = client.post(
        "/api/plugin/regenerate",
        json={
            "bass_style": "melodic",
            "lock_to_groove": 0.9,
            "bass_expression": 0.85,
        },
    )
    assert res.status_code == 200, res.text
    part = res.json()
    assert part["bass_style"] == "melodic"
    assert part["lock_to_groove"] == pytest.approx(0.9)
    assert part["bass_expression"] == pytest.approx(0.85)
    assert len(part["notes"]) > 0


def test_plugin_regenerate_routes_persona_to_strongest_engine(client: TestClient) -> None:
    from app.routes import session_routes

    sid = _create_session_with_bass(client)

    res = client.post("/api/plugin/regenerate", json={"bass_player": "james_jamerson"})
    assert res.status_code == 200, res.text
    assert res.json()["bass_player"] == "james_jamerson"
    assert session_routes._SESSIONS[sid].bass_engine == "baseline"

    res = client.post("/api/plugin/regenerate", json={"bass_player": "pino"})
    assert res.status_code == 200, res.text
    assert session_routes._SESSIONS[sid].bass_engine == "phrase_v2"

    res = client.post("/api/plugin/regenerate", json={"bass_player": "none"})
    assert res.status_code == 200, res.text
    assert res.json()["bass_player"] is None
    assert session_routes._SESSIONS[sid].bass_engine == "phrase_v2"


def test_plugin_regenerate_only_mutates_bound_session(client: TestClient) -> None:
    from app.routes import session_routes

    first = _create_session_with_bass(client)
    second = _create_session_with_bass(client)

    res = client.post(
        "/api/plugin/regenerate",
        json={"session_id": first, "bass_style": "melodic"},
    )

    assert res.status_code == 200, res.text
    assert res.json()["session_id"] == first
    assert session_routes._SESSIONS[first].bass_style == "melodic"
    assert session_routes._SESSIONS[second].bass_style == "supportive"


def test_plugin_lists_neutral_instrument_profiles_including_upright(
    client: TestClient,
) -> None:
    res = client.get("/api/plugin/instrument-profiles")
    assert res.status_code == 200, res.text
    assert [row["id"] for row in res.json()] == [
        "finger_bass",
        "fretless_bass",
        "upright_bass",
        "sub_bass",
    ]


def test_plugin_advice_is_evidence_backed_and_non_destructive(
    client: TestClient,
) -> None:
    from app.routes import session_routes

    sid = _create_session_with_bass(client)
    stored = session_routes._SESSIONS[sid]
    original_seed = stored.bass_seed
    original_bytes = stored.bass_bytes

    res = client.get(
        "/api/plugin/advice",
        params={"session_id": sid, "bass_instrument": "fretless_bass"},
    )

    assert res.status_code == 200, res.text
    advice = res.json()
    assert advice["advisory_only"] is True
    assert advice["instrument_profile"]["id"] == "fretless_bass"
    assert [path["id"] for path in advice["paths"]] == [
        "accompany",
        "counterpoint",
        "explore",
    ]
    assert all(path["preserves"] for path in advice["paths"])
    assert all(path["changes"] for path in advice["paths"])
    assert stored.bass_seed == original_seed
    assert stored.bass_bytes == original_bytes


def test_plugin_regenerate_applies_upright_family(client: TestClient) -> None:
    _create_session_with_bass(client)
    res = client.post(
        "/api/plugin/regenerate",
        json={"bass_instrument": "upright_bass"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["bass_instrument"] == "upright_bass"


def test_plugin_keep_regenerate_and_history_navigation_are_exact(
    client: TestClient,
) -> None:
    sid = _create_session_with_bass(client)
    original = client.get(
        "/api/plugin/bass-part", params={"session_id": sid}
    ).json()

    kept = client.post("/api/plugin/keep", json={"session_id": sid})
    assert kept.status_code == 200, kept.text
    assert kept.json()["message"].startswith("Idea kept")
    kept_history = kept.json()["history"]
    assert kept_history["count"] == 1
    assert kept_history["kept_count"] == 1
    assert kept_history["current_index"] == 1
    assert kept_history["current_is_kept"] is True
    assert kept_history["can_previous"] is False
    assert kept_history["can_next"] is False

    regenerated = client.post(
        "/api/plugin/regenerate",
        json={
            "session_id": sid,
            "bass_style": "melodic",
            "lock_to_groove": 0.9,
            "bass_expression": 0.85,
        },
    )
    assert regenerated.status_code == 200, regenerated.text
    changed = regenerated.json()
    assert changed["notes"] != original["notes"]

    history = client.get(
        "/api/plugin/history", params={"session_id": sid}
    )
    assert history.status_code == 200, history.text
    assert history.json()["count"] == 1
    assert history.json()["current_index"] is None
    assert history.json()["can_previous"] is True
    assert history.json()["can_next"] is False

    keep_changed = client.post("/api/plugin/keep", json={"session_id": sid})
    assert keep_changed.status_code == 200, keep_changed.text
    assert keep_changed.json()["history"]["count"] == 2
    assert keep_changed.json()["history"]["kept_count"] == 2

    earlier = client.post(
        "/api/plugin/history/navigate",
        json={"session_id": sid, "direction": "previous"},
    )
    assert earlier.status_code == 200, earlier.text
    recalled_original = earlier.json()["part"]
    assert recalled_original["notes"] == original["notes"]
    assert recalled_original["bass_style"] == original["bass_style"]
    assert earlier.json()["history"]["current_index"] == 1
    assert earlier.json()["history"]["can_next"] is True

    later = client.post(
        "/api/plugin/history/navigate",
        json={"session_id": sid, "direction": "next"},
    )
    assert later.status_code == 200, later.text
    recalled_changed = later.json()["part"]
    assert recalled_changed["notes"] == changed["notes"]
    assert recalled_changed["bass_style"] == "melodic"
    assert recalled_changed["lock_to_groove"] == pytest.approx(0.9)
    assert recalled_changed["bass_expression"] == pytest.approx(0.85)


def test_plugin_history_recall_is_bound_to_session_and_context(
    client: TestClient,
) -> None:
    first = _create_session_with_bass(client)
    second = _create_session_with_bass(client)

    kept = client.post("/api/plugin/keep", json={"session_id": first})
    snapshot_id = kept.json()["history"]["entries"][0]["snapshot_id"]

    wrong_session = client.post(
        "/api/plugin/history/recall",
        json={"session_id": second, "snapshot_id": snapshot_id},
    )
    assert wrong_session.status_code == 404
    assert wrong_session.json()["detail"]["error"] == "history_snapshot_not_found"

    from app.routes import session_routes

    session_routes._SESSIONS[first].key = "D"  # noqa: SLF001
    changed_context = client.post(
        "/api/plugin/history/recall",
        json={"session_id": first, "snapshot_id": snapshot_id},
    )
    assert changed_context.status_code == 404
    assert changed_context.json()["detail"]["error"] == "history_snapshot_not_found"


def test_plugin_history_boundary_fails_without_mutating_part(
    client: TestClient,
) -> None:
    sid = _create_session_with_bass(client)
    before = client.get(
        "/api/plugin/bass-part", params={"session_id": sid}
    ).json()

    response = client.post(
        "/api/plugin/history/navigate",
        json={"session_id": sid, "direction": "previous"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "history_boundary"
    after = client.get(
        "/api/plugin/bass-part", params={"session_id": sid}
    ).json()
    assert after == before
