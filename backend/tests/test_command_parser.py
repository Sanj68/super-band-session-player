"""v0.7 prompting layer: parser grammar + /api/plugin/command."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.command_parser import parse_command


# ---- grammar ----------------------------------------------------------------

def test_busier_and_sparser() -> None:
    assert parse_command("make it busier", bar_count=4).density_delta > 0
    assert parse_command("more space please", bar_count=4).density_delta < 0
    assert parse_command("strip it back", bar_count=4).density_delta < 0


def test_lock_phrases() -> None:
    assert parse_command("tighter on the kick", bar_count=4).lock_delta > 0
    assert parse_command("loosen it up", bar_count=4).lock_delta < 0
    assert parse_command("ignore the drums", bar_count=4).lock_set == 0.0


def test_player_aliases() -> None:
    assert parse_command("play it like jamerson", bar_count=4).player == "james_jamerson"
    assert parse_command("give me some motown", bar_count=4).player == "james_jamerson"
    assert parse_command("jaco vibes", bar_count=4).player == "jaco_pastorius"
    assert parse_command("go upright", bar_count=4).player == "paul_chambers"


def test_style_aliases() -> None:
    assert parse_command("make it funkier", bar_count=4).style == "rhythmic"
    assert parse_command("smoother", bar_count=4).style == "melodic"


def test_bar_references_one_based_speech() -> None:
    # the spec's own example
    plan = parse_command("add a turnaround on the 4th bar", bar_count=4)
    assert plan.bar_ranges == [(3, 4)]
    assert plan.bar_operation == "turnaround"
    assert any("turnaround" in a for a in plan.applied)

    assert parse_command("redo bar 2", bar_count=8).bar_ranges == [(1, 2)]
    assert parse_command("bars 2-4 again", bar_count=8).bar_ranges == [(1, 4)]
    assert parse_command("fix the last bar", bar_count=8).bar_ranges == [(7, 8)]
    # bare "add a fill" lands on the last bar
    assert parse_command("add a fill", bar_count=4).bar_ranges == [(3, 4)]


def test_bar_clamping() -> None:
    assert parse_command("redo bar 99", bar_count=4).bar_ranges == [(3, 4)]


def test_full_regenerate() -> None:
    assert parse_command("new take", bar_count=4).full_regenerate
    assert parse_command("try again", bar_count=4).full_regenerate


def test_compound_command() -> None:
    plan = parse_command("busier, tighter, like jamerson", bar_count=4)
    assert plan.density_delta > 0
    assert plan.lock_delta > 0
    assert plan.player == "james_jamerson"
    assert len(plan.applied) == 3


def test_unrecognized_is_honest() -> None:
    plan = parse_command("paint it purple", bar_count=4)
    assert not plan.has_ops
    assert plan.unrecognized == ["paint it purple"]


# ---- endpoint ---------------------------------------------------------------

@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def _session_with_bass(client: TestClient) -> str:
    res = client.post(
        "/api/sessions/",
        json={
            "tempo": 100,
            "key": "C",
            "scale": "major",
            "bar_count": 4,
            "bass_style": "supportive",
            "bass_engine": "phrase_v2",
        },
    )
    sid = res.json()["session"]["id"]
    client.post(f"/api/sessions/{sid}/regenerate-selected", json={"lanes": ["bass"]})
    return sid


def test_command_endpoint_applies_and_regenerates(client: TestClient) -> None:
    from app.routes import session_routes

    sid = _session_with_bass(client)
    res = client.post("/api/plugin/command", json={"text": "busier, like jamerson"})
    assert res.status_code == 200, res.text
    out = res.json()
    assert out["ok"] is True
    assert out["part"]["bass_player"] == "james_jamerson"
    s = session_routes._SESSIONS[sid]
    assert s.bass_density_bias == pytest.approx(0.5)
    assert s.bass_engine == "baseline"  # persona routing applies here too


def test_command_endpoint_bar_op(client: TestClient) -> None:
    _session_with_bass(client)
    res = client.post("/api/plugin/command", json={"text": "turnaround on the 4th bar"})
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True
    assert "bar 4" in res.json()["message"]
    assert "Explicit turnaround applied" in res.json()["part"]["preview"]


def test_command_endpoint_honest_on_nonsense(client: TestClient) -> None:
    _session_with_bass(client)
    res = client.post("/api/plugin/command", json={"text": "paint it purple"})
    assert res.status_code == 200
    out = res.json()
    assert out["ok"] is False
    assert "didn't catch that" in out["message"]
    assert out["part"] is None


def test_command_endpoint_only_mutates_bound_session(client: TestClient) -> None:
    from app.routes import session_routes

    first = _session_with_bass(client)
    second = _session_with_bass(client)

    res = client.post(
        "/api/plugin/command",
        json={"session_id": first, "text": "busier"},
    )

    assert res.status_code == 200, res.text
    assert res.json()["part"]["session_id"] == first
    assert session_routes._SESSIONS[first].bass_density_bias == pytest.approx(0.5)
    assert session_routes._SESSIONS[second].bass_density_bias == pytest.approx(0.0)
