from __future__ import annotations

import io
from typing import Any

from fastapi.testclient import TestClient
import mido
import pytest

from app.main import app
from app.routes import session_routes
from app.services.bass_bar_splice import splice_bass_bars


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


def _midi_with_tick_notes(
    notes: list[tuple[int, int, int]],
    *,
    ticks_per_beat: int = 220,
) -> bytes:
    midi = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    events: list[tuple[int, mido.Message]] = []
    for pitch, start_tick, duration_ticks in notes:
        events.append(
            (
                start_tick,
                mido.Message("note_on", note=pitch, velocity=90, time=0),
            )
        )
        events.append(
            (
                start_tick + duration_ticks,
                mido.Message("note_off", note=pitch, velocity=0, time=0),
            )
        )
    events.sort(
        key=lambda event: (
            event[0],
            0 if event[1].type == "note_off" else 1,
        )
    )
    track = mido.MidiTrack()
    previous_tick = 0
    for tick, message in events:
        track.append(message.copy(time=tick - previous_tick))
        previous_tick = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi.tracks.append(track)
    buffer = io.BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()


def test_performance_splice_assigns_early_downbeats_to_their_musical_bar() -> None:
    # At 220 PPQ, the selected range is [880, 1760) ticks. Humanized
    # downbeats arrive a few ticks early on both sides of that range.
    existing = _midi_with_tick_notes(
        [
            (30, 400, 20),
            (40, 876, 20),
            (50, 1755, 20),
        ]
    )
    replacement = _midi_with_tick_notes(
        [
            (31, 400, 20),
            (41, 876, 20),
            (51, 1755, 20),
        ]
    )

    spliced = splice_bass_bars(
        existing_midi=existing,
        replacement_midi=replacement,
        tempo=96,
        bar_start=1,
        bar_end=2,
        humanize_boundary_seconds=0.02,
    )
    output = mido.MidiFile(file=io.BytesIO(spliced))
    pitches = [
        int(message.note)
        for track in output.tracks
        for message in track
        if message.type == "note_on" and int(message.velocity) > 0
    ]

    assert pitches == [30, 41, 50]


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


@pytest.mark.parametrize("engine", ["baseline", "phrase_v2"])
def test_turnaround_adds_explicit_approach_and_preserves_other_bars(engine: str) -> None:
    client = _client()
    session_id = _create_session(client)
    session_routes._SESSIONS[session_id].bass_engine = engine
    original = _generate_session_bass(client, session_id)
    original_notes = original["lanes"]["bass"]["notes"]

    res = client.post(
        f"/api/sessions/{session_id}/lanes/bass/regenerate-bars",
        json={
            "bar_start": 3,
            "bar_end": 4,
            "seed": 20260723,
            "operation": "turnaround",
        },
    )

    assert res.status_code == 200, res.text
    out = res.json()
    notes = out["lanes"]["bass"]["notes"]
    assert "Explicit turnaround applied to bar 4" in out["lanes"]["bass"]["preview"]

    seconds_per_beat = 60.0 / 96.0
    selected_start = 3 * 4 * seconds_per_beat
    final_beat_start = (3 * 4 + 3) * seconds_per_beat
    bar_end = 4 * 4 * seconds_per_beat
    # Humanised notes can begin a few milliseconds before the edit boundary
    # and legitimately be shortened by loop-overlap normalisation. Everything
    # clearly outside that boundary must remain byte-for-note equivalent.
    stable_original = [
        _note_tuple(note)
        for note in original_notes
        if float(note["start"]) < selected_start - 0.125 or float(note["start"]) >= bar_end
    ]
    stable_after = [
        _note_tuple(note)
        for note in notes
        if float(note["start"]) < selected_start - 0.125 or float(note["start"]) >= bar_end
    ]
    assert stable_original == stable_after

    # A humanized downbeat from the following bar can land just before the raw
    # timestamp boundary while still belonging outside the edited bar.
    late_notes = [
        note
        for note in notes
        if final_beat_start <= float(note["start"]) < bar_end - 0.02
    ]
    assert len(late_notes) == 3
    assert int(late_notes[-1]["pitch"]) % 12 in {11, 1}  # chromatic neighbor of next C root


def test_turnaround_rejects_multi_bar_range() -> None:
    client = _client()
    session_id = _create_session(client)
    _generate_session_bass(client, session_id)

    res = client.post(
        f"/api/sessions/{session_id}/lanes/bass/regenerate-bars",
        json={"bar_start": 2, "bar_end": 4, "operation": "turnaround"},
    )

    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "invalid_bar_range"
