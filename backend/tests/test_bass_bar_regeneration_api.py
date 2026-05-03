from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.main import app
from app.routes import session_routes


def _client() -> TestClient:
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    return TestClient(app)


def _create_session(client: TestClient) -> str:
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 96,
            "key": "C",
            "scale": "major",
            "bar_count": 8,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "bass_instrument": "finger_bass",
        },
    )
    assert created.status_code == 200
    return str(created.json()["session"]["id"])


def _note_tuple(note: dict[str, Any]) -> tuple[int, float, float, int]:
    return (
        int(note["pitch"]),
        round(float(note["start"]), 6),
        round(float(note["end"]), 6),
        int(note["velocity"]),
    )


def _notes_in_bars(
    notes: list[dict[str, Any]],
    *,
    tempo: int,
    bar_start: int,
    bar_end: int,
) -> list[tuple[int, float, float, int]]:
    seconds_per_bar = 60.0 / float(tempo) * 4.0
    start = bar_start * seconds_per_bar
    end = bar_end * seconds_per_bar
    return [_note_tuple(n) for n in notes if start <= float(n["start"]) < end]


def _notes_outside_bars(
    notes: list[dict[str, Any]],
    *,
    tempo: int,
    bar_start: int,
    bar_end: int,
) -> list[tuple[int, float, float, int]]:
    seconds_per_bar = 60.0 / float(tempo) * 4.0
    start = bar_start * seconds_per_bar
    end = bar_end * seconds_per_bar
    return [_note_tuple(n) for n in notes if not (start <= float(n["start"]) < end)]


def _generate_session_bass(client: TestClient, session_id: str) -> dict[str, Any]:
    generated = client.post(f"/api/sessions/{session_id}/generate")
    assert generated.status_code == 200
    return dict(generated.json()["session"])


def _regenerate_bass_bars(
    client: TestClient,
    session_id: str,
    *,
    bar_start: int = 2,
    bar_end: int = 4,
    seed: int | None = None,
) -> dict[str, Any]:
    body: dict[str, int] = {"bar_start": bar_start, "bar_end": bar_end}
    if seed is not None:
        body["seed"] = seed
    regenerated = client.post(f"/api/sessions/{session_id}/lanes/bass/regenerate-bars", json=body)
    assert regenerated.status_code == 200
    return dict(regenerated.json())


def test_regenerate_bass_bars_invalid_ranges_return_400() -> None:
    client = _client()
    session_id = _create_session(client)
    _generate_session_bass(client, session_id)

    cases = [
        {"bar_start": -1, "bar_end": 2},
        {"bar_start": 2, "bar_end": 2},
        {"bar_start": 4, "bar_end": 2},
        {"bar_start": 7, "bar_end": 9},
    ]
    for body in cases:
        res = client.post(f"/api/sessions/{session_id}/lanes/bass/regenerate-bars", json=body)
        assert res.status_code == 400
        assert res.json()["detail"]["error"] == "invalid_bar_range"


def test_regenerate_bass_bars_requires_existing_bass_bytes() -> None:
    client = _client()
    session_id = _create_session(client)

    res = client.post(
        f"/api/sessions/{session_id}/lanes/bass/regenerate-bars",
        json={"bar_start": 2, "bar_end": 4, "seed": 22222},
    )

    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "bass_lane_missing"


def test_regenerate_bass_bars_changes_only_selected_bars_and_updates_seed(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(session_routes, "_new_bass_seed", lambda: 33333)
    session_id = _create_session(client)
    original = _generate_session_bass(client, session_id)
    original_notes = original["lanes"]["bass"]["notes"]

    regenerated = _regenerate_bass_bars(client, session_id, seed=22222)
    regenerated_notes = regenerated["lanes"]["bass"]["notes"]

    assert regenerated["bass_seed"] == 22222
    assert _notes_outside_bars(original_notes, tempo=96, bar_start=2, bar_end=4) == _notes_outside_bars(
        regenerated_notes,
        tempo=96,
        bar_start=2,
        bar_end=4,
    )
    assert _notes_in_bars(original_notes, tempo=96, bar_start=2, bar_end=4) != _notes_in_bars(
        regenerated_notes,
        tempo=96,
        bar_start=2,
        bar_end=4,
    )


def test_regenerate_bass_bars_seed_controls_replacement_range() -> None:
    client = _client()
    session_id = _create_session(client)
    _generate_session_bass(client, session_id)

    first = _regenerate_bass_bars(client, session_id, seed=33333)
    second = _regenerate_bass_bars(client, session_id, seed=33333)
    third = _regenerate_bass_bars(client, session_id, seed=44444)

    first_range = _notes_in_bars(first["lanes"]["bass"]["notes"], tempo=96, bar_start=2, bar_end=4)
    second_range = _notes_in_bars(second["lanes"]["bass"]["notes"], tempo=96, bar_start=2, bar_end=4)
    third_range = _notes_in_bars(third["lanes"]["bass"]["notes"], tempo=96, bar_start=2, bar_end=4)

    assert first["bass_seed"] == 33333
    assert second["bass_seed"] == 33333
    assert third["bass_seed"] == 44444
    assert second_range == first_range
    assert third_range != first_range
