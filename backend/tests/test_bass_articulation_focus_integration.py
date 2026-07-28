"""Integration contract for public bass touch and activity controls.

This module intentionally exercises only public HTTP/session-store boundaries.
Detailed articulation inference and MIDI-shaping behaviour belongs in the
focused service tests; these checks protect the values that Logic and persisted
sessions depend on.
"""

from __future__ import annotations

import copy
import io
import json
from pathlib import Path

import pretty_midi
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import session_routes
from app.services import bass_candidate_store, session_store


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Keep session and candidate state local to each contract test."""

    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    monkeypatch.setattr(bass_candidate_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        bass_candidate_store,
        "_RUNS_FILE",
        tmp_path / "bass_candidate_runs.json",
    )
    yield TestClient(app)
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]


def _create_session(
    client: TestClient,
    **overrides: object,
) -> tuple[str, dict[str, object]]:
    payload: dict[str, object] = {
        "tempo": 100,
        "key": "D",
        "scale": "natural_minor",
        "bar_count": 2,
        "bass_style": "rhythmic",
        "bass_engine": "phrase_v2",
        "bass_instrument": "finger_bass",
    }
    payload.update(overrides)
    response = client.post("/api/sessions/", json=payload)
    assert response.status_code == 200, response.text
    state = response.json()["session"]
    return str(state["id"]), state


def _assert_focus_state(
    state: dict[str, object],
    *,
    requested: str,
    effective: str,
    density: float,
    notice: bool,
) -> None:
    assert state["bass_articulation_focus"] == requested
    assert state["bass_articulation_effective"] == effective
    assert float(state["bass_density_bias"]) == pytest.approx(density)
    if notice:
        assert isinstance(state["bass_articulation_notice"], str)
        assert str(state["bass_articulation_notice"]).strip()
    else:
        assert state["bass_articulation_notice"] is None


def _midi_note_tuples(data: bytes) -> list[tuple[int, float, float, int]]:
    """Return only audible note properties, excluding MIDI metadata."""

    midi = pretty_midi.PrettyMIDI(io.BytesIO(data))
    return sorted(
        (
            int(note.pitch),
            round(float(note.start), 6),
            round(float(note.end), 6),
            int(note.velocity),
        )
        for instrument in midi.instruments
        for note in instrument.notes
    )


def _download_bass_parts(
    client: TestClient,
    session_id: str,
) -> tuple[bytes, bytes]:
    clean = client.get(f"/api/sessions/{session_id}/midi/bass?mode=clean")
    performance = client.get(
        f"/api/sessions/{session_id}/midi/bass?mode=performance"
    )
    assert clean.status_code == 200, clean.text
    assert performance.status_code == 200, performance.text
    return clean.content, performance.content


def test_session_create_patch_and_state_round_trip_focus_and_activity(
    client: TestClient,
) -> None:
    session_id, created = _create_session(client)
    _assert_focus_state(
        created,
        requested="natural",
        effective="natural",
        density=0.0,
        notice=False,
    )

    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={
            "bass_articulation_focus": "ghosted",
            "bass_density_bias": -0.65,
        },
    )
    assert patched.status_code == 200, patched.text
    _assert_focus_state(
        patched.json(),
        requested="ghosted",
        effective="ghosted",
        density=-0.65,
        notice=False,
    )

    fetched = client.get(f"/api/sessions/{session_id}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == patched.json() | {"message": fetched.json()["message"]}
    _assert_focus_state(
        fetched.json(),
        requested="ghosted",
        effective="ghosted",
        density=-0.65,
        notice=False,
    )

    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    assert stored.bass_articulation_focus == "ghosted"
    assert stored.bass_density_bias == pytest.approx(-0.65)


def test_state_keeps_requested_focus_and_reports_instrument_fallback(
    client: TestClient,
) -> None:
    session_id, created = _create_session(
        client,
        bass_instrument="sub_bass",
        bass_articulation_focus="connected",
        bass_density_bias=0.75,
    )
    _assert_focus_state(
        created,
        requested="connected",
        effective="clean",
        density=0.75,
        notice=True,
    )

    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={"bass_instrument": "finger_bass"},
    )
    assert patched.status_code == 200, patched.text
    _assert_focus_state(
        patched.json(),
        requested="connected",
        effective="connected",
        density=0.75,
        notice=False,
    )


@pytest.mark.parametrize(
    "invalid_patch",
    [
        {"bass_articulation_focus": "invented"},
        {"bass_density_bias": -1.01},
        {"bass_density_bias": 1.01},
    ],
)
def test_invalid_focus_or_activity_is_rejected_without_mutation(
    client: TestClient,
    invalid_patch: dict[str, object],
) -> None:
    session_id, before = _create_session(
        client,
        bass_articulation_focus="clean",
        bass_density_bias=0.25,
    )

    response = client.patch(
        f"/api/sessions/{session_id}",
        json=invalid_patch,
    )

    assert response.status_code == 422
    after = client.get(f"/api/sessions/{session_id}")
    assert after.status_code == 200
    comparable_before = dict(before)
    comparable_before["message"] = after.json()["message"]
    assert after.json() == comparable_before


def test_session_store_loads_legacy_defaults_and_round_trips_new_controls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_path = tmp_path / "sessions.json"
    monkeypatch.setattr(session_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(session_store, "_SESSIONS_FILE", snapshot_path)
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "snapshot_revision": 1,
                "sessions": [
                    {
                        "id": "legacy-focus-session",
                        "tempo": 100,
                        "key": "D",
                        "scale": "natural_minor",
                        "bar_count": 2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    restored = session_store.load_sessions(session_routes.StoredSession)
    legacy = restored["legacy-focus-session"]
    assert legacy.bass_articulation_focus == "natural"
    assert legacy.bass_density_bias == pytest.approx(0.0)
    legacy_state = session_routes._to_state(legacy)  # noqa: SLF001
    assert legacy_state.bass_articulation_focus == "natural"
    assert legacy_state.bass_articulation_effective == "natural"
    assert legacy_state.bass_articulation_notice is None

    legacy.bass_articulation_focus = "ghosted"
    legacy.bass_density_bias = -0.4
    session_store.save_sessions(restored)
    round_tripped = session_store.load_sessions(
        session_routes.StoredSession
    )["legacy-focus-session"]
    assert round_tripped.bass_articulation_focus == "ghosted"
    assert round_tripped.bass_density_bias == pytest.approx(-0.4)


def test_plugin_part_and_regenerate_round_trip_focus_and_activity(
    client: TestClient,
) -> None:
    session_id, _created = _create_session(client)
    generated = client.post(
        f"/api/sessions/{session_id}/regenerate-selected",
        json={"lanes": ["bass"]},
    )
    assert generated.status_code == 200, generated.text

    initial = client.get(
        "/api/plugin/bass-part",
        params={"session_id": session_id},
    )
    assert initial.status_code == 200, initial.text
    _assert_focus_state(
        initial.json(),
        requested="natural",
        effective="natural",
        density=0.0,
        notice=False,
    )

    regenerated = client.post(
        "/api/plugin/regenerate",
        json={
            "session_id": session_id,
            "bass_articulation_focus": "muted",
            "bass_density_bias": 0.55,
        },
    )
    assert regenerated.status_code == 200, regenerated.text
    _assert_focus_state(
        regenerated.json(),
        requested="muted",
        effective="muted",
        density=0.55,
        notice=False,
    )

    fallback = client.post(
        "/api/plugin/regenerate",
        json={
            "session_id": session_id,
            "bass_instrument": "sub_bass",
            "bass_articulation_focus": "connected",
        },
    )
    assert fallback.status_code == 200, fallback.text
    _assert_focus_state(
        fallback.json(),
        requested="connected",
        effective="clean",
        density=0.55,
        notice=True,
    )

    polled = client.get(
        "/api/plugin/bass-part",
        params={"session_id": session_id},
    )
    assert polled.status_code == 200, polled.text
    _assert_focus_state(
        polled.json(),
        requested="connected",
        effective="clean",
        density=0.55,
        notice=True,
    )
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    assert stored.bass_articulation_focus == "connected"
    assert stored.bass_density_bias == pytest.approx(0.55)


def test_focus_and_activity_change_stales_candidate_promotion(
    client: TestClient,
) -> None:
    session_id, _created = _create_session(
        client,
        bass_articulation_focus="clean",
        bass_density_bias=-0.2,
    )
    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 260726},
    )
    assert generated.status_code == 200, generated.text
    run = generated.json()
    assert run["bass_articulation_focus"] == "clean"
    assert float(run["bass_density_bias"]) == pytest.approx(-0.2)
    run_id = run["run_id"]
    take_id = run["takes"][0]["take_id"]

    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={
            "bass_articulation_focus": "ghosted",
            "bass_density_bias": 0.4,
        },
    )
    assert patched.status_code == 200, patched.text
    stored_after_patch = copy.deepcopy(
        session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    )

    promoted = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{run_id}/{take_id}/promote"
    )

    assert promoted.status_code == 409, promoted.text
    detail = promoted.json()["detail"]
    assert detail["error"] == "stale_candidate_generation_context"
    assert detail["reason"] == "session_context_changed"
    assert (
        session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
        == stored_after_patch
    )


@pytest.mark.parametrize("focus", ["ghosted", "muted", "connected"])
def test_supported_explicit_touch_at_character_midpoint_shapes_public_performance_midi(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    focus: str,
) -> None:
    """Touch changes audible MIDI properties, not only public state labels."""

    # Baseline seed 1 contains both eligible punctuation and close-note
    # transitions, so every explicit supported Touch has something musical to
    # shape without replacing the generator in this public-boundary test.
    monkeypatch.setattr(session_routes, "_new_bass_seed", lambda: 1)
    session_id, _created = _create_session(
        client,
        bass_style="rhythmic",
        bass_engine="baseline",
        bass_instrument="finger_bass",
        bass_articulation_focus="clean",
        bass_expression=0.5,
        bar_count=4,
    )
    clean_generated = client.post(
        f"/api/sessions/{session_id}/regenerate-selected",
        json={"lanes": ["bass"]},
    )
    assert clean_generated.status_code == 200, clean_generated.text
    clean_source, clean_performance = _download_bass_parts(client, session_id)

    selected = client.patch(
        f"/api/sessions/{session_id}",
        json={
            "bass_articulation_focus": focus,
            "bass_expression": 0.5,
        },
    )
    assert selected.status_code == 200, selected.text
    _assert_focus_state(
        selected.json(),
        requested=focus,
        effective=focus,
        density=0.0,
        notice=False,
    )
    focused_generated = client.post(
        f"/api/sessions/{session_id}/regenerate-selected",
        json={"lanes": ["bass"]},
    )
    assert focused_generated.status_code == 200, focused_generated.text
    _assert_focus_state(
        focused_generated.json(),
        requested=focus,
        effective=focus,
        density=0.0,
        notice=False,
    )
    focused_source, focused_performance = _download_bass_parts(client, session_id)

    # The pinned seed proves Touch is a performance-only decision: composition
    # stays fixed while at least one note's timing, duration, or velocity moves.
    assert _midi_note_tuples(focused_source) == _midi_note_tuples(clean_source)
    assert _midi_note_tuples(focused_performance) != _midi_note_tuples(
        clean_performance
    )


def test_unsupported_sub_connected_is_clean_effective_and_render_identical(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(session_routes, "_new_bass_seed", lambda: 1)
    session_id, _created = _create_session(
        client,
        bass_style="rhythmic",
        bass_engine="baseline",
        bass_instrument="sub_bass",
        bass_articulation_focus="clean",
        bass_expression=0.5,
        bar_count=4,
    )
    clean_generated = client.post(
        f"/api/sessions/{session_id}/regenerate-selected",
        json={"lanes": ["bass"]},
    )
    assert clean_generated.status_code == 200, clean_generated.text
    clean_source, clean_performance = _download_bass_parts(client, session_id)

    selected = client.patch(
        f"/api/sessions/{session_id}",
        json={
            "bass_articulation_focus": "connected",
            "bass_expression": 0.5,
        },
    )
    assert selected.status_code == 200, selected.text
    _assert_focus_state(
        selected.json(),
        requested="connected",
        effective="clean",
        density=0.0,
        notice=True,
    )
    fallback_generated = client.post(
        f"/api/sessions/{session_id}/regenerate-selected",
        json={"lanes": ["bass"]},
    )
    assert fallback_generated.status_code == 200, fallback_generated.text
    _assert_focus_state(
        fallback_generated.json(),
        requested="connected",
        effective="clean",
        density=0.0,
        notice=True,
    )
    fallback_source, fallback_performance = _download_bass_parts(
        client,
        session_id,
    )

    assert fallback_source == clean_source
    assert fallback_performance == clean_performance
