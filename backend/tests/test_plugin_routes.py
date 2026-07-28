"""Tests for the MIDI FX plugin surface (/api/plugin)."""

from __future__ import annotations

import copy
import io
from threading import Event, Thread

import pretty_midi
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
    assert part["version"] == 2
    assert part["phase_offset_beats"] == pytest.approx(0.0)
    assert part["groove_source_ready"] is False
    assert part["groove_source_frame_count"] == 0
    assert "cannot match that beat yet" in part["groove_source_notice"]
    assert set(part["bass_performance_controls"]) == {
        "ghost",
        "mute",
        "slide",
        "legato",
        "timing_humanize",
        "velocity_humanize",
    }
    assert set(part["bass_performance_controls_effective"]) == set(
        part["bass_performance_controls"]
    )
    assert isinstance(part["automation"], list)
    assert len(part["notes"]) > 0
    for n in part["notes"]:
        assert 0 <= n["start_beats"] < part["bar_count"] * 4 + 1
        assert n["dur_beats"] > 0
        assert 0 < n["pitch"] < 128
    # sorted by start
    starts = [n["start_beats"] for n in part["notes"]]
    assert starts == sorted(starts)


def test_bass_part_projects_performance_automation_in_beats(
    client: TestClient,
) -> None:
    from app.routes import session_routes

    sid = _create_session_with_bass(client)
    rendered = pretty_midi.PrettyMIDI(initial_tempo=100)
    instrument = pretty_midi.Instrument(program=33)
    instrument.notes.append(
        pretty_midi.Note(
            velocity=91,
            pitch=40,
            start=0.0,
            end=0.6,
        )
    )
    # At an equal timestamp, controller setup must precede the bend value.
    instrument.control_changes.extend(
        [
            pretty_midi.ControlChange(number=101, value=0, time=0.3),
            pretty_midi.ControlChange(number=6, value=12, time=0.3),
        ]
    )
    instrument.pitch_bends.extend(
        [
            pretty_midi.PitchBend(pitch=-8192, time=0.3),
            pretty_midi.PitchBend(pitch=8191, time=1.2),
        ]
    )
    rendered.instruments.append(instrument)
    payload = io.BytesIO()
    rendered.write(payload)
    session_routes._SESSIONS[sid].bass_performance_bytes = payload.getvalue()  # noqa: SLF001

    response = client.get(
        "/api/plugin/bass-part",
        params={"session_id": sid},
    )

    assert response.status_code == 200, response.text
    part = response.json()
    assert part["version"] == 2
    assert part["source"] == "performance"
    assert part["notes"] == [
        {
            "pitch": 40,
            "velocity": 91,
            "start_beats": pytest.approx(0.0),
            "dur_beats": pytest.approx(1.0),
        }
    ]
    assert part["automation"] == [
        {
            "type": "control_change",
            "channel": 1,
            "beat": pytest.approx(0.5),
            "controller": 101,
            "value": 0,
        },
        {
            "type": "control_change",
            "channel": 1,
            "beat": pytest.approx(0.5),
            "controller": 6,
            "value": 12,
        },
        {
            "type": "pitch_bend",
            "channel": 1,
            "beat": pytest.approx(0.5),
            "value": -8192,
        },
        {
            "type": "pitch_bend",
            "channel": 1,
            "beat": pytest.approx(2.0),
            "value": 8191,
        },
    ]


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


def test_plugin_regenerate_applies_independent_performance_mix(
    client: TestClient,
) -> None:
    from app.routes import session_routes

    sid = _create_session_with_bass(client)
    stored_before = session_routes._SESSIONS[sid]  # noqa: SLF001
    clean_signature_before = session_routes._lane_note_signature(  # noqa: SLF001
        stored_before.bass_bytes
    )
    seed_before = stored_before.bass_seed
    requested = {
        "ghost": 0.62,
        "mute": 0.24,
        "slide": 0.8,
        "legato": 0.68,
        "timing_humanize": 0.58,
        "velocity_humanize": 0.72,
    }

    response = client.post(
        "/api/plugin/regenerate",
        json={
            "session_id": sid,
            "bass_performance_controls": requested,
        },
    )

    assert response.status_code == 200, response.text
    part = response.json()
    assert part["bass_performance_controls"] == pytest.approx(requested)
    assert part["bass_performance_controls_effective"] == pytest.approx(requested)
    assert part["bass_performance_controls_notice"] is None
    assert session_routes._SESSIONS[sid].bass_performance_controls == requested  # noqa: SLF001
    stored_after = session_routes._SESSIONS[sid]  # noqa: SLF001
    assert stored_after.bass_seed == seed_before
    assert session_routes._lane_note_signature(  # noqa: SLF001
        stored_after.bass_bytes
    ) == clean_signature_before


def test_plugin_force_new_phrase_refreshes_clean_take_at_same_tempo(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import session_routes

    sid = _create_session_with_bass(client)
    stored_before = session_routes._SESSIONS[sid]  # noqa: SLF001
    seed_before = stored_before.bass_seed
    clean_signature_before = session_routes._lane_note_signature(  # noqa: SLF001
        stored_before.bass_bytes
    )
    assert seed_before is not None
    fresh_seed = seed_before + 137
    monkeypatch.setattr(session_routes, "_new_bass_seed", lambda: fresh_seed)

    response = client.post(
        "/api/plugin/regenerate",
        json={
            "session_id": sid,
            "force_new_phrase": True,
            "host_tempo": 100.0,
        },
    )

    assert response.status_code == 200, response.text
    part = response.json()
    stored_after = session_routes._SESSIONS[sid]  # noqa: SLF001
    assert part["tempo"] == 100
    assert stored_after.tempo == 100
    assert stored_after.bass_seed == fresh_seed
    assert stored_after.bass_seed != seed_before
    assert session_routes._lane_note_signature(  # noqa: SLF001
        stored_after.bass_bytes
    ) != clean_signature_before


def test_plugin_forced_phrase_adopts_rounded_host_tempo(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import session_routes

    sid = _create_session_with_bass(client)
    seed_before = session_routes._SESSIONS[sid].bass_seed  # noqa: SLF001
    assert seed_before is not None
    fresh_seed = seed_before + 211
    monkeypatch.setattr(session_routes, "_new_bass_seed", lambda: fresh_seed)

    response = client.post(
        "/api/plugin/regenerate",
        json={
            "session_id": sid,
            "force_new_phrase": True,
            "host_tempo": 116.4,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["tempo"] == 116
    stored_after = session_routes._SESSIONS[sid]  # noqa: SLF001
    assert stored_after.tempo == 116
    assert stored_after.bass_seed == fresh_seed


def test_plugin_host_tempo_is_ignored_without_forced_phrase(
    client: TestClient,
) -> None:
    sid = _create_session_with_bass(client)

    response = client.post(
        "/api/plugin/regenerate",
        json={
            "session_id": sid,
            "host_tempo": 133.0,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["tempo"] == 100


@pytest.mark.parametrize("host_tempo", [39.9, 240.1])
def test_plugin_regenerate_rejects_invalid_host_tempo(
    client: TestClient,
    host_tempo: float,
) -> None:
    sid = _create_session_with_bass(client)

    response = client.post(
        "/api/plugin/regenerate",
        json={
            "session_id": sid,
            "force_new_phrase": True,
            "host_tempo": host_tempo,
        },
    )

    assert response.status_code == 422


def test_plugin_and_session_report_live_beat_source_readiness(
    client: TestClient,
) -> None:
    from app.models.bridge import BridgeSourceFeatureFrame
    from app.services import bridge_store

    sid = _create_session_with_bass(client)
    bridge_store.clear_bridge_state(sid)

    missing_part = client.get(
        "/api/plugin/bass-part",
        params={"session_id": sid},
    )
    missing_session = client.get(f"/api/sessions/{sid}")

    assert missing_part.status_code == 200, missing_part.text
    assert missing_session.status_code == 200, missing_session.text
    for payload in (missing_part.json(), missing_session.json()):
        assert payload["groove_source_ready"] is False
        assert payload["groove_source_frame_count"] == 0
        assert "Session Player Bridge" in payload["groove_source_notice"]
        assert "cannot match that beat yet" in payload["groove_source_notice"]

    bridge_store.record_source_frame(
        BridgeSourceFeatureFrame(
            plugin_instance_id="logic-source-test",
            session_id=sid,
            source_id="drum-track",
            capture_epoch=1,
            sample_rate=48_000.0,
            host_tempo=116.0,
            playing=True,
            ppq_position=0.0,
            bar_index=0,
            frame_start_seconds=0.0,
            duration_seconds=0.125,
            rms=0.5,
            low_band_energy=0.8,
            mid_band_energy=0.3,
            high_band_energy=0.2,
            onset_strength=0.7,
        )
    )

    ready_part = client.get(
        "/api/plugin/bass-part",
        params={"session_id": sid},
    )
    ready_session = client.get(f"/api/sessions/{sid}")

    assert ready_part.status_code == 200, ready_part.text
    assert ready_session.status_code == 200, ready_session.text
    for payload in (ready_part.json(), ready_session.json()):
        assert payload["groove_source_ready"] is True
        assert payload["groove_source_frame_count"] == 1
        assert payload["groove_source_notice"] is None


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


@pytest.mark.parametrize(
    "body",
    [
        {"bass_style": "invented_style"},
        {"bass_player": "invented_player"},
    ],
)
def test_plugin_regenerate_rejects_unknown_vocabulary_without_mutation(
    client: TestClient,
    body: dict[str, str],
) -> None:
    from app.routes import session_routes

    sid = _create_session_with_bass(client)
    before = session_routes._to_state(session_routes._SESSIONS[sid])  # noqa: SLF001

    response = client.post(
        "/api/plugin/regenerate",
        json={"session_id": sid, **body},
    )

    assert response.status_code == 422
    after = session_routes._to_state(session_routes._SESSIONS[sid])  # noqa: SLF001
    assert after == before


def test_plugin_regenerate_rejects_locked_bass_before_history_or_controls_change(
    client: TestClient,
) -> None:
    from app.routes import session_routes
    from app.services import bass_history_store

    sid = _create_session_with_bass(client)
    locked = client.patch(
        f"/api/sessions/{sid}/lane-locks",
        json={"bass": True},
    )
    assert locked.status_code == 200, locked.text
    stored = session_routes._SESSIONS[sid]  # noqa: SLF001
    before_style = stored.bass_style
    before_bytes = stored.bass_bytes

    response = client.post(
        "/api/plugin/regenerate",
        json={"session_id": sid, "bass_style": "melodic"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "bass_lane_locked"
    assert stored.bass_style == before_style
    assert stored.bass_bytes == before_bytes
    assert bass_history_store.history_state(stored)["count"] == 0


def test_plugin_command_rejects_locked_bass_without_mutation(
    client: TestClient,
) -> None:
    from app.routes import session_routes

    sid = _create_session_with_bass(client)
    locked = client.patch(
        f"/api/sessions/{sid}/lane-locks",
        json={"bass": True},
    )
    assert locked.status_code == 200, locked.text
    stored = session_routes._SESSIONS[sid]  # noqa: SLF001
    before_density = stored.bass_density_bias
    before_bytes = stored.bass_bytes

    response = client.post(
        "/api/plugin/command",
        json={"session_id": sid, "text": "busier"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "bass_lane_locked"
    assert stored.bass_density_bias == before_density
    assert stored.bass_bytes == before_bytes


@pytest.mark.parametrize("action", ["regenerate", "command"])
def test_plugin_generation_failure_rolls_back_controls_and_midi(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    from app.routes import plugin_routes, session_routes

    sid = _create_session_with_bass(client)
    stored = session_routes._SESSIONS[sid]  # noqa: SLF001
    before = copy.deepcopy(stored)

    def fail_bass(**_kwargs):
        raise RuntimeError("bass generator failed")

    monkeypatch.setattr(session_routes.generator, "generate_bass", fail_bass)

    with pytest.raises(RuntimeError, match="bass generator failed"):
        if action == "regenerate":
            plugin_routes.plugin_regenerate(
                plugin_routes.PluginRegenerateBody(
                    session_id=sid,
                    bass_style="melodic",
                    lock_to_groove=0.9,
                )
            )
        else:
            plugin_routes.plugin_command(
                plugin_routes.PluginCommandBody(
                    session_id=sid,
                    text="busier melodic",
                )
            )

    assert stored == before


def test_plugin_polling_reads_only_committed_part_during_regeneration(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.routes import session_routes

    sid = _create_session_with_bass(client)
    before = client.get(
        "/api/plugin/bass-part",
        params={"session_id": sid},
    ).json()
    original_generate = session_routes.generator.generate_bass
    generator_entered = Event()
    release_generator = Event()
    worker_result: dict[str, object] = {}

    def blocked_generate(**kwargs):
        generator_entered.set()
        if not release_generator.wait(timeout=2.0):
            raise TimeoutError("test never released the bass generator")
        return original_generate(**kwargs)

    monkeypatch.setattr(
        session_routes.generator,
        "generate_bass",
        blocked_generate,
    )

    def regenerate() -> None:
        worker_client = TestClient(app)
        worker_result["response"] = worker_client.post(
            "/api/plugin/regenerate",
            json={"session_id": sid, "bass_style": "melodic"},
        )

    worker = Thread(target=regenerate)
    worker.start()
    assert generator_entered.wait(timeout=1.0)
    try:
        during = client.get(
            "/api/plugin/bass-part",
            params={"session_id": sid},
        )
        assert during.status_code == 200
        assert during.json() == before
    finally:
        release_generator.set()
        worker.join(timeout=3.0)

    assert not worker.is_alive()
    response = worker_result["response"]
    assert getattr(response, "status_code") == 200
    after = client.get(
        "/api/plugin/bass-part",
        params={"session_id": sid},
    )
    assert after.status_code == 200
    assert after.json()["bass_style"] == "melodic"
    assert after.json() != before


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
        json={
            "bass_instrument": "upright_bass",
            "bass_performance_controls": {
                "ghost": 0.62,
                "mute": 0.24,
                "slide": 0.8,
                "legato": 0.68,
                "timing_humanize": 0.58,
                "velocity_humanize": 0.72,
            },
        },
    )
    assert res.status_code == 200, res.text
    part = res.json()
    assert part["bass_instrument"] == "upright_bass"
    assert part["bass_performance_controls_effective"] == pytest.approx(
        {
            "ghost": 0.62,
            "mute": 0.0,
            "slide": 0.7,
            "legato": 0.68,
            "timing_humanize": 0.58,
            "velocity_humanize": 0.72,
        }
    )
    assert "Muted/dead notes are unavailable for Upright" in (
        part["bass_performance_controls_notice"] or ""
    )
    assert "Slides are limited for Upright" in (
        part["bass_performance_controls_notice"] or ""
    )


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
    from app.routes import session_routes
    from app.services.source_analysis import build_source_analysis

    sid = _create_session_with_bass(client)
    stored = session_routes._SESSIONS[sid]  # noqa: SLF001
    durable_source = build_source_analysis(stored)
    stored.bridge_live_overlay_active = True
    stored.bridge_live_base_source_analysis_override = durable_source
    stored.bridge_live_base_key = "C"
    stored.bridge_live_base_scale = "major"
    stored.key = "D"
    stored.scale = "dorian"
    stored.source_analysis_override = durable_source.model_copy(
        update={"tonal_center_pc_guess": 2, "scale_mode_guess": "dorian"}
    )
    stored_before = copy.deepcopy(stored)
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
    assert stored == stored_before


def test_plugin_keep_fails_closed_when_history_is_corrupt(
    client: TestClient,
) -> None:
    from app.services import bass_history_store

    sid = _create_session_with_bass(client)
    bass_history_store._HISTORY_FILE.write_text(  # noqa: SLF001
        "{not valid history",
        encoding="utf-8",
    )

    failed = client.post("/api/plugin/keep", json={"session_id": sid})

    assert failed.status_code == 503
    assert failed.json()["detail"]["error"] == "bass_history_unavailable"
    assert not bass_history_store._HISTORY_FILE.exists()  # noqa: SLF001
    quarantined = list(
        bass_history_store._DATA_DIR.glob(  # noqa: SLF001
            "bass_history.json.quarantine-invalid-json-*"
        )
    )
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == "{not valid history"

    retried = client.post("/api/plugin/keep", json={"session_id": sid})

    assert retried.status_code == 200
    assert retried.json()["history"]["count"] == 1
