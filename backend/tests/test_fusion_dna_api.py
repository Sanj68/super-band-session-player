"""API semantics for replacing a shared Fusion law."""

from __future__ import annotations

from copy import deepcopy
import json

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.routes import session_routes
from app.services import bass_candidate_store, bass_history_store
from app.services.source_analysis import build_source_analysis


@pytest.fixture()
def client(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    monkeypatch.setattr(bass_candidate_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        bass_candidate_store,
        "_RUNS_FILE",
        tmp_path / "bass_candidate_runs.json",
    )
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    return TestClient(app)


def _create_fusion(client: TestClient) -> str:
    response = client.post(
        "/api/sessions/",
        json={
            "tempo": 116,
            "key": "D",
            "scale": "natural_minor",
            "bar_count": 8,
            "session_preset": "fusion",
            "chord_progression": ["Dm7", "Gm7", "Bbmaj7", "A7"],
            "bass_instrument": "finger_bass",
            "chord_instrument": "rhodes",
        },
    )
    assert response.status_code == 200
    session = response.json()["session"]
    assert session["bass_engine"] == "phrase_v2"
    return str(session["id"])


def _fusion_source_at_slot(
    session: session_routes.StoredSession,
    slot: int,
):
    base = build_source_analysis(session)
    rows: list[list[float]] = []
    zero_rows: list[list[float]] = []
    for _bar in range(session.bar_count):
        row = [0.0] * 16
        row[int(slot)] = 0.95
        rows.append(row)
        zero_rows.append([0.0] * 16)
    return base.model_copy(
        update={
            "source_lane": "reference_audio",
            "source_groove_resolution": 16,
            "source_onset_weight": rows,
            "source_kick_weight": rows,
            "source_snare_weight": zero_rows,
            "source_slot_pressure": rows,
            "source_groove_confidence": [0.95] * session.bar_count,
        }
    )


def _signature(session: dict[str, object], lane: str) -> tuple[tuple[object, ...], ...]:
    notes = session["lanes"][lane]["notes"]  # type: ignore[index]
    return tuple(
        (
            note["pitch"],
            round(float(note["start"]), 5),
            round(float(note["end"]), 5),
            note["velocity"],
        )
        for note in notes
    )


def _bass_signature_outside_bar_range(
    session: dict[str, object],
    *,
    bar_start: int,
    bar_end: int,
) -> tuple[tuple[object, ...], ...]:
    seconds_per_bar = 60.0 / float(session["tempo"]) * 4.0
    range_start = bar_start * seconds_per_bar
    range_end = bar_end * seconds_per_bar
    notes = session["lanes"]["bass"]["notes"]  # type: ignore[index]
    return tuple(
        (
            note["pitch"],
            round(float(note["start"]), 5),
            round(float(note["end"]), 5),
            note["velocity"],
        )
        for note in notes
        if (
            float(note["start"]) < range_start - 0.02
            or float(note["start"]) >= range_end - 0.02
        )
    )


def _bass_signature_inside_bar_range(
    session: dict[str, object],
    *,
    bar_start: int,
    bar_end: int,
) -> tuple[tuple[object, ...], ...]:
    seconds_per_bar = 60.0 / float(session["tempo"]) * 4.0
    range_start = bar_start * seconds_per_bar
    range_end = bar_end * seconds_per_bar
    notes = session["lanes"]["bass"]["notes"]  # type: ignore[index]
    return tuple(
        (
            note["pitch"],
            round(float(note["start"]), 5),
            round(float(note["end"]), 5),
            note["velocity"],
        )
        for note in notes
        if range_start - 0.02 <= float(note["start"]) < range_end - 0.02
    )


def test_fusion_cannot_be_created_with_an_engine_that_ignores_shared_dna(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/sessions/",
        json={
            "tempo": 116,
            "key": "D",
            "scale": "natural_minor",
            "bar_count": 8,
            "session_preset": "fusion",
            "chord_progression": ["Dm7", "Gm7", "Bbmaj7", "A7"],
            "bass_engine": "baseline",
        },
    )

    assert response.status_code == 200
    assert response.json()["session"]["bass_engine"] == "phrase_v2"


def test_fusion_rejects_named_player_until_contract_adapter_is_real(
    client: TestClient,
) -> None:
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 116,
            "key": "D",
            "scale": "natural_minor",
            "bar_count": 8,
            "session_preset": "fusion",
            "chord_progression": ["Dm7", "Gm7", "Bbmaj7", "A7"],
            "bass_player": "bootsy",
        },
    )
    assert created.status_code == 409
    assert (
        created.json()["detail"]["error"]
        == "fusion_named_bass_player_unsupported"
    )

    session_id = _create_fusion(client)
    client.post(f"/api/sessions/{session_id}/generate")
    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={"bass_player": "marcus"},
    )
    assert patched.status_code == 409
    assert (
        patched.json()["detail"]["error"]
        == "fusion_named_bass_player_unsupported"
    )
    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["bass_player"] is None
    assert current["fusion_contract_active"] is True


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("drum_style", "latin"),
        ("chord_style", "jazzy"),
    ),
)
def test_fusion_rejects_drums_and_keys_style_claims_until_adapters_are_real(
    client: TestClient,
    field: str,
    value: str,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    before = {
        lane: _signature(generated, lane)
        for lane in ("drums", "bass", "chords")
    }

    response = client.patch(
        f"/api/sessions/{session_id}",
        json={field: value},
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["error"]
        == "fusion_lane_style_adapter_unavailable"
    )
    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["fusion_contract_active"] is True
    assert {
        lane: _signature(current, lane)
        for lane in ("drums", "bass", "chords")
    } == before


def test_initial_generate_writes_shared_dna_and_bass_regen_preserves_it(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate")

    assert generated.status_code == 200
    session = generated.json()["session"]
    assert session["fusion_contract_active"] is True
    assert session["fusion_dna_revision"] == 1
    assert session["fusion_source_mode"] == "authored"
    contract_id = session["fusion_contract_id"]

    regenerated = client.post(
        f"/api/sessions/{session_id}/regenerate-selected",
        json={"lanes": ["bass"]},
    )
    assert regenerated.status_code == 200
    assert regenerated.json()["fusion_contract_id"] == contract_id
    assert regenerated.json()["fusion_dna_revision"] == 1


def test_candidates_require_existing_dna_without_mutating_first_time_session(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)

    response = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={
            "take_count": 4,
            "seed": 424245,
            "variation_mode": "controlled_roles",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "fusion_dna_required"
    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["fusion_contract_id"] is None
    assert current["fusion_contract_active"] is False
    assert current["fusion_dna_revision"] == 0


def test_single_lane_regeneration_cannot_create_half_a_fusion_core(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)

    response = client.post(
        f"/api/sessions/{session_id}/regenerate-selected",
        json={"lanes": ["bass"]},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "fusion_dna_required"
    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["fusion_contract_id"] is None
    assert current["fusion_contract_active"] is False
    assert current["fusion_dna_revision"] == 0
    assert current["lanes"]["bass"]["notes"] == []


def test_form_length_change_clears_incompatible_contract_until_generate(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    assert generated["fusion_contract_id"] is not None
    assert generated["fusion_dna_revision"] == 1

    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={"bar_count": 12},
    )

    assert patched.status_code == 200
    changed = patched.json()
    assert changed["bar_count"] == 12
    assert changed["fusion_contract_id"] is None
    assert changed["fusion_contract_active"] is False
    assert changed["fusion_dna_revision"] == 1

    regenerated = client.post(f"/api/sessions/{session_id}/generate")
    assert regenerated.status_code == 200
    rebuilt = regenerated.json()["session"]
    assert rebuilt["fusion_contract_id"] is not None
    assert rebuilt["fusion_contract_active"] is True
    assert rebuilt["fusion_dna_revision"] == 2


def test_noop_form_length_patch_preserves_current_contract(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    contract_id = generated["fusion_contract_id"]

    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={"bar_count": 8},
    )

    assert patched.status_code == 200
    unchanged = patched.json()
    assert unchanged["bar_count"] == 8
    assert unchanged["fusion_contract_id"] == contract_id
    assert unchanged["fusion_contract_active"] is True
    assert unchanged["fusion_dna_revision"] == 1


def test_leaving_fusion_detaches_contract_before_other_lanes_can_overwrite_it(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    assert generated["fusion_contract_id"] is not None
    hidden_payload = deepcopy(
        session_routes._SESSIONS[  # type: ignore[attr-defined]
            session_id
        ].fusion_contract_payload
    )

    left_fusion = client.patch(
        f"/api/sessions/{session_id}",
        json={"session_preset": "soulful_funk"},
    )
    assert left_fusion.status_code == 200
    assert left_fusion.json()["fusion_contract_id"] is None
    assert "Fusion DNA detached" in left_fusion.json()["message"]
    assert session_routes._SESSIONS[  # type: ignore[attr-defined]
        session_id
    ].fusion_contract_payload is None

    regenerated = client.post(f"/api/sessions/{session_id}/generate")
    assert regenerated.status_code == 200
    # Simulate a pre-fix persisted/in-memory hidden payload. Crossing back
    # into Fusion must detach it instead of claiming the non-Fusion lanes.
    session_routes._SESSIONS[  # type: ignore[attr-defined]
        session_id
    ].fusion_contract_payload = hidden_payload
    returned = client.patch(
        f"/api/sessions/{session_id}",
        json={"session_preset": "fusion"},
    )
    assert returned.status_code == 200
    assert returned.json()["fusion_contract_active"] is False
    assert returned.json()["fusion_contract_id"] is None


def test_contract_is_not_reported_active_without_the_complete_core(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    client.post(f"/api/sessions/{session_id}/generate")
    session_routes._SESSIONS[  # type: ignore[attr-defined]
        session_id
    ].chords_bytes = None

    state = client.get(f"/api/sessions/{session_id}").json()

    assert state["fusion_contract_id"] is not None
    assert state["fusion_contract_active"] is False
    assert "complete drums, bass, and keys core" in state[
        "fusion_contract_notice"
    ]


def test_four_candidate_roles_are_distinct_readings_of_unchanged_dna(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    contract_id = generated["fusion_contract_id"]

    response = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={
            "take_count": 4,
            "seed": 424245,
            "variation_mode": "controlled_roles",
        },
    )

    assert response.status_code == 200, response.text
    takes = response.json()["takes"]
    assert [take["candidate_role"] for take in takes] == [
        "pocket_keeper",
        "rhythmic_alternative",
        "harmonic_alternative",
        "performance_alternative",
    ]
    assert len({take["midi_sha256"] for take in takes}) == 4
    assert takes[0]["note_count"] < takes[1]["note_count"]
    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["fusion_contract_id"] == contract_id
    assert current["fusion_dna_revision"] == 1


def test_ranked_candidates_are_rejected_before_they_can_escape_fusion_dna(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    contract_id = generated["fusion_contract_id"]

    response = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={
            "take_count": 4,
            "seed": 424245,
            "variation_mode": "ranked",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "fusion_controlled_roles_required"
    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["fusion_contract_id"] == contract_id
    assert current["fusion_dna_revision"] == 1


def test_fusion_candidate_comparison_requires_the_complete_four_roles(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    client.post(f"/api/sessions/{session_id}/generate")

    response = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={
            "take_count": 2,
            "seed": 424245,
            "variation_mode": "controlled_roles",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "fusion_four_roles_required"


def test_new_dna_hides_obsolete_candidate_runs_from_actionable_list(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    client.post(f"/api/sessions/{session_id}/generate")
    created = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={
            "take_count": 4,
            "seed": 424245,
            "variation_mode": "controlled_roles",
        },
    )
    assert created.status_code == 200, created.text
    listed = client.get(f"/api/sessions/{session_id}/bass-candidates")
    assert listed.status_code == 200
    assert [row["run_id"] for row in listed.json()] == [
        created.json()["run_id"]
    ]

    changed = client.post(f"/api/sessions/{session_id}/fusion-dna/new")
    assert changed.status_code == 200
    refreshed = client.get(f"/api/sessions/{session_id}/bass-candidates")

    assert refreshed.status_code == 200
    assert refreshed.json() == []


def test_legacy_ranked_run_cannot_be_listed_or_promoted_in_fusion(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    client.post(f"/api/sessions/{session_id}/generate")
    created = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={
            "take_count": 4,
            "seed": 424245,
            "variation_mode": "controlled_roles",
        },
    )
    assert created.status_code == 200, created.text
    run = created.json()
    rows = bass_candidate_store.load_runs()
    rows[0]["variation_mode"] = "ranked"
    rows[0]["takes"][0]["candidate_role"] = None
    bass_candidate_store._RUNS_FILE.write_text(  # type: ignore[attr-defined]
        json.dumps({"schema_version": 1, "runs": rows}),
        encoding="utf-8",
    )

    listed = client.get(f"/api/sessions/{session_id}/bass-candidates")
    promoted = client.post(
        f"/api/sessions/{session_id}/bass-candidates/"
        f"{run['run_id']}/{run['takes'][0]['take_id']}/promote"
    )

    assert listed.status_code == 200
    assert listed.json() == []
    assert promoted.status_code == 409
    assert (
        promoted.json()["detail"]["error"]
        == "fusion_candidate_outside_contract_policy"
    )


def test_live_bridge_source_change_hides_old_candidates_and_plugin_part(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    source_a = _fusion_source_at_slot(stored, 6)
    source_b = _fusion_source_at_slot(stored, 10)
    stored.source_analysis_override = source_a
    generated = client.post(f"/api/sessions/{session_id}/generate")
    assert generated.status_code == 200, generated.text
    run_response = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={
            "take_count": 4,
            "seed": 55111,
            "variation_mode": "controlled_roles",
        },
    )
    assert run_response.status_code == 200, run_response.text
    run = run_response.json()
    take_id = run["takes"][0]["take_id"]
    before_contract_id = generated.json()["session"]["fusion_contract_id"]
    before_bass = _signature(generated.json()["session"], "bass")

    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    stored.bridge_live_overlay_active = True
    stored.bridge_live_base_source_analysis_override = source_a
    stored.bridge_live_base_key = stored.key
    stored.bridge_live_base_scale = stored.scale
    stored.source_analysis_override = source_b

    state = client.get(f"/api/sessions/{session_id}").json()
    assert state["fusion_contract_active"] is False
    assert state["fusion_contract_stale"] is True
    assert client.get(f"/api/sessions/{session_id}/bass-candidates").json() == []

    guarded = (
        client.get(
            f"/api/sessions/{session_id}/bass-candidates/"
            f"{run['run_id']}/{take_id}"
        ),
        client.get(
            f"/api/sessions/{session_id}/bass-candidates/"
            f"{run['run_id']}/{take_id}/notes"
        ),
        client.post(
            f"/api/sessions/{session_id}/bass-candidates/"
            f"{run['run_id']}/{take_id}/promote"
        ),
        client.get(
            "/api/plugin/bass-part",
            params={"session_id": session_id},
        ),
        client.post(
            "/api/plugin/command",
            json={"session_id": session_id, "text": "redo bar 2"},
        ),
    )
    for response in guarded:
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "fusion_source_changed"

    current = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    assert current.bridge_live_overlay_active is True
    assert current.source_analysis_override == source_b
    final = client.get(f"/api/sessions/{session_id}").json()
    assert final["fusion_contract_id"] == before_contract_id
    assert _signature(final, "bass") == before_bass


def test_bass_style_reperforms_without_leaving_the_shared_law(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    contract_id = generated["fusion_contract_id"]
    before_signature = _signature(generated, "bass")
    before_performance = session_routes._SESSIONS[  # type: ignore[attr-defined]
        session_id
    ].bass_performance_bytes

    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={"bass_style": "slap"},
    )
    assert patched.status_code == 200
    regenerated = client.post(
        f"/api/sessions/{session_id}/regenerate-selected",
        json={"lanes": ["bass"]},
    )

    assert regenerated.status_code == 200, regenerated.text
    state = regenerated.json()
    assert state["fusion_contract_active"] is True
    assert state["fusion_contract_id"] == contract_id
    assert state["fusion_dna_revision"] == 1
    assert state["bass_style"] == "slap"
    assert before_signature != _signature(state, "bass")
    assert "slap" in state["lanes"]["bass"]["preview"].lower()
    assert (
        session_routes._SESSIONS[  # type: ignore[attr-defined]
            session_id
        ].bass_performance_bytes
        != before_performance
    )


@pytest.mark.parametrize(
    ("endpoint", "payload"),
    [
        (
            "/api/plugin/regenerate",
            {
                "bass_player": "jaco_pastorius",
                "force_new_phrase": True,
            },
        ),
        (
            "/api/plugin/command",
            {"text": "like marcus"},
        ),
    ],
)
def test_plugin_rejects_named_players_that_do_not_yet_adapt_fusion_dna(
    client: TestClient,
    endpoint: str,
    payload: dict[str, object],
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    contract_id = generated["fusion_contract_id"]
    bass_before = _signature(generated, "bass")

    response = client.post(
        endpoint,
        json={"session_id": session_id, **payload},
    )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["error"]
        == "fusion_named_bass_player_unsupported"
    )
    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["bass_player"] is None
    assert current["bass_engine"] == "phrase_v2"
    assert current["fusion_contract_active"] is True
    assert current["fusion_contract_id"] == contract_id
    assert _signature(current, "bass") == bass_before


@pytest.mark.parametrize(
    ("endpoint", "payload", "expected_tempo"),
    [
        (
            "/api/plugin/regenerate",
            {"force_new_phrase": True, "host_tempo": 122},
            122,
        ),
        (
            "/api/plugin/command",
            {"text": "new take"},
            116,
        ),
    ],
)
def test_plugin_new_take_replaces_fusion_dna_and_the_whole_core(
    client: TestClient,
    endpoint: str,
    payload: dict[str, object],
    expected_tempo: int,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    before_id = generated["fusion_contract_id"]
    before = {
        lane: _signature(generated, lane)
        for lane in ("drums", "bass", "chords", "lead")
    }

    response = client.post(
        endpoint,
        json={"session_id": session_id, **payload},
    )

    assert response.status_code == 200, response.text
    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["tempo"] == expected_tempo
    assert current["fusion_contract_id"] != before_id
    assert current["fusion_dna_revision"] == 2
    assert current["fusion_contract_active"] is True
    assert _signature(current, "drums") != before["drums"]
    assert _signature(current, "bass") != before["bass"]
    assert _signature(current, "chords") != before["chords"]
    assert _signature(current, "lead") == before["lead"]


def test_plugin_structural_controls_make_bounded_audible_fusion_changes(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    contract_id = generated["fusion_contract_id"]
    neutral_bass = _signature(generated, "bass")

    busier = client.post(
        "/api/plugin/command",
        json={"session_id": session_id, "text": "busier"},
    )
    assert busier.status_code == 200, busier.text
    busier_state = client.get(f"/api/sessions/{session_id}").json()
    busier_bass = _signature(busier_state, "bass")

    tighter = client.post(
        "/api/plugin/command",
        json={"session_id": session_id, "text": "tighter"},
    )
    assert tighter.status_code == 200, tighter.text
    tighter_state = client.get(f"/api/sessions/{session_id}").json()
    tighter_bass = _signature(tighter_state, "bass")

    assert busier_state["fusion_contract_id"] == contract_id
    assert tighter_state["fusion_contract_id"] == contract_id
    assert busier_state["fusion_dna_revision"] == 1
    assert tighter_state["fusion_dna_revision"] == 1
    assert busier_state["bass_density_bias"] == pytest.approx(0.5)
    assert tighter_state["bass_lock_to_groove"] == pytest.approx(0.75)
    assert busier_bass != neutral_bass
    assert tighter_bass != busier_bass


def test_repeated_plugin_activity_commands_step_through_five_real_gears(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    contract_id = generated["fusion_contract_id"]
    signatures = {0.0: _signature(generated, "bass")}

    for expected in (0.5, 1.0):
        response = client.post(
            "/api/plugin/command",
            json={"session_id": session_id, "text": "busier"},
        )
        assert response.status_code == 200, response.text
        state = client.get(f"/api/sessions/{session_id}").json()
        assert state["bass_density_bias"] == pytest.approx(expected)
        assert state["fusion_contract_id"] == contract_id
        assert state["fusion_contract_active"] is True
        signatures[expected] = _signature(state, "bass")

    for expected in (0.5, 0.0, -0.5, -1.0):
        response = client.post(
            "/api/plugin/command",
            json={"session_id": session_id, "text": "more space"},
        )
        assert response.status_code == 200, response.text
        state = client.get(f"/api/sessions/{session_id}").json()
        assert state["bass_density_bias"] == pytest.approx(expected)
        assert state["fusion_contract_id"] == contract_id
        assert state["fusion_contract_active"] is True
        rendered = _signature(state, "bass")
        if expected in signatures:
            assert rendered == signatures[expected]
        signatures[expected] = rendered

    assert len(set(signatures.values())) == 5


def test_global_context_patch_suspends_stale_core_until_coherent_regeneration(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    contract_id = generated["fusion_contract_id"]
    before = {
        lane: _signature(generated, lane)
        for lane in ("drums", "bass", "chords")
    }

    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={
            "tempo": 132,
            "key": "F",
            "scale": "major",
            "chord_progression": ["Fmaj7", "Gm7", "Bbmaj7", "C7"],
        },
    )

    assert patched.status_code == 200, patched.text
    stale = patched.json()
    assert stale["fusion_contract_id"] == contract_id
    assert stale["fusion_contract_active"] is False
    assert stale["fusion_contract_stale"] is True
    assert "predates" in stale["fusion_contract_notice"]
    assert {
        lane: _signature(stale, lane)
        for lane in ("drums", "bass", "chords")
    } == before

    candidates = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={
            "take_count": 4,
            "variation_mode": "controlled_roles",
        },
    )
    assert candidates.status_code == 409
    assert candidates.json()["detail"]["error"] == "fusion_core_stale"

    regenerated = client.post(f"/api/sessions/{session_id}/generate")
    assert regenerated.status_code == 200, regenerated.text
    fresh = regenerated.json()["session"]
    assert fresh["fusion_contract_id"] == contract_id
    assert fresh["fusion_dna_revision"] == 1
    assert fresh["fusion_contract_active"] is True
    assert fresh["fusion_contract_stale"] is False
    assert _signature(fresh, "drums") != before["drums"]
    assert _signature(fresh, "bass") != before["bass"]
    assert _signature(fresh, "chords") != before["chords"]


def test_normalized_noop_context_patch_does_not_suspend_fusion_core(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    before = {
        lane: _signature(generated, lane)
        for lane in ("drums", "bass", "chords")
    }

    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={
            "tempo": generated["tempo"],
            "key": "d",
            "scale": "natural_minor",
            "bar_count": generated["bar_count"],
            "chord_progression": [
                " dm7 ",
                "Gmin7",
                "bbmaj7",
                "a7",
            ],
        },
    )

    assert patched.status_code == 200, patched.text
    state = patched.json()
    assert state["fusion_contract_active"] is True
    assert state["fusion_contract_stale"] is False
    assert state["chord_progression"] == generated["chord_progression"]
    assert {
        lane: _signature(state, lane)
        for lane in ("drums", "bass", "chords")
    } == before


def test_plugin_history_is_paused_until_fusion_snapshots_have_provenance(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    client.post(f"/api/sessions/{session_id}/generate")
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    rogue = deepcopy(stored)
    rogue.bass_engine = "baseline"
    snapshot = bass_history_store.capture(rogue, kept=True)

    history = client.get(
        "/api/plugin/history",
        params={"session_id": session_id},
    )
    recalled = client.post(
        "/api/plugin/history/recall",
        json={
            "session_id": session_id,
            "snapshot_id": snapshot["snapshot_id"],
        },
    )

    assert history.status_code == 200
    assert history.json()["count"] == 0
    assert recalled.status_code == 409
    assert (
        recalled.json()["detail"]["error"]
        == "fusion_history_provenance_required"
    )
    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["bass_engine"] == "phrase_v2"
    assert current["fusion_contract_active"] is True


def test_selected_bar_variation_uses_seeded_permissioned_moves_inside_dna(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    original = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    contract_id = original["fusion_contract_id"]
    original_outside = _bass_signature_outside_bar_range(
        original,
        bar_start=2,
        bar_end=4,
    )

    variations: list[tuple[tuple[object, ...], ...]] = []
    for seed in (33001, 33002, 33003, 33004):
        response = client.post(
            f"/api/sessions/{session_id}/lanes/bass/regenerate-bars",
            json={
                "bar_start": 2,
                "bar_end": 4,
                "seed": seed,
                "operation": "variation",
            },
        )
        assert response.status_code == 200, response.text
        state = response.json()
        assert state["fusion_contract_id"] == contract_id
        assert state["bass_seed"] == seed
        assert (
            _bass_signature_outside_bar_range(
                state,
                bar_start=2,
                bar_end=4,
            )
            == original_outside
        )
        variations.append(
            _bass_signature_inside_bar_range(
                state,
                bar_start=2,
                bar_end=4,
            )
        )

    assert len(set(variations)) > 1


@pytest.mark.parametrize("surface", ("session_api", "plugin_command"))
def test_partial_bar_edit_cannot_certify_a_whole_stale_fusion_bass(
    client: TestClient,
    surface: str,
) -> None:
    session_id = _create_fusion(client)
    client.post(f"/api/sessions/{session_id}/generate")
    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={"bass_style": "rhythmic"},
    )
    assert patched.status_code == 200
    assert patched.json()["fusion_contract_active"] is False
    assert patched.json()["fusion_contract_stale"] is True

    if surface == "session_api":
        response = client.post(
            f"/api/sessions/{session_id}/lanes/bass/regenerate-bars",
            json={
                "bar_start": 1,
                "bar_end": 2,
                "seed": 44101,
                "operation": "variation",
            },
        )
    else:
        response = client.post(
            "/api/plugin/command",
            json={
                "session_id": session_id,
                "text": "redo bar 2",
            },
        )
    if surface == "plugin_command":
        assert response.status_code == 409
        assert response.json()["detail"]["error"] == "fusion_core_stale"
    else:
        assert response.status_code == 200, response.text
    partial = client.get(f"/api/sessions/{session_id}").json()
    assert partial["fusion_contract_active"] is False
    assert partial["fusion_contract_stale"] is True

    full = client.post(
        f"/api/sessions/{session_id}/regenerate-selected",
        json={"lanes": ["bass"]},
    )
    assert full.status_code == 200, full.text
    assert full.json()["fusion_contract_active"] is True
    assert full.json()["fusion_contract_stale"] is False


@pytest.mark.parametrize("surface", ("session_api", "plugin_command"))
def test_legacy_turnaround_cannot_escape_fusion_contract_space(
    client: TestClient,
    surface: str,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    contract_id = generated["fusion_contract_id"]
    before = {
        lane: _signature(generated, lane)
        for lane in ("drums", "bass", "chords")
    }

    if surface == "session_api":
        response = client.post(
            f"/api/sessions/{session_id}/lanes/bass/regenerate-bars",
            json={
                "bar_start": 3,
                "bar_end": 4,
                "seed": 98123,
                "operation": "turnaround",
            },
        )
    else:
        response = client.post(
            "/api/plugin/command",
            json={
                "session_id": session_id,
                "text": "turnaround on the 4th bar",
            },
        )

    assert response.status_code == 409
    assert (
        response.json()["detail"]["error"]
        == "fusion_turnaround_not_permissioned"
    )
    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["fusion_contract_id"] == contract_id
    assert current["fusion_contract_active"] is True
    assert {
        lane: _signature(current, lane)
        for lane in ("drums", "bass", "chords")
    } == before


def test_new_dna_changes_the_law_and_atomically_rebuilds_the_core(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    before_id = generated["fusion_contract_id"]
    before_covenant = generated["fusion_covenant_id"]
    before = {
        lane: _signature(generated, lane)
        for lane in ("drums", "bass", "chords", "lead")
    }

    response = client.post(f"/api/sessions/{session_id}/fusion-dna/new")

    assert response.status_code == 200
    after = response.json()
    assert after["fusion_contract_id"] != before_id
    assert after["fusion_covenant_id"] != before_covenant
    assert after["fusion_dna_revision"] == 2
    assert _signature(after, "drums") != before["drums"]
    assert _signature(after, "bass") != before["bass"]
    assert _signature(after, "chords") != before["chords"]
    assert _signature(after, "lead") == before["lead"]


def test_new_dna_can_transactionally_rebuild_the_full_band_for_reference_apply(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    before = {
        lane: _signature(generated, lane)
        for lane in ("drums", "bass", "chords", "lead")
    }
    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={
            "tempo": 128,
            "key": "F",
            "scale": "major",
            "chord_progression": ["Fmaj7", "Gm7", "Bbmaj7", "C7"],
        },
    )
    assert patched.status_code == 200
    assert patched.json()["fusion_contract_active"] is False

    response = client.post(
        f"/api/sessions/{session_id}/fusion-dna/new",
        json={"include_lead": True},
    )

    assert response.status_code == 200, response.text
    after = response.json()
    assert after["fusion_contract_active"] is True
    assert "lead rebuilt" in after["message"].lower()
    for lane in ("drums", "bass", "chords", "lead"):
        assert _signature(after, lane) != before[lane]


def test_full_band_new_dna_rolls_back_if_lead_render_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    before_id = generated["fusion_contract_id"]
    before = {
        lane: _signature(generated, lane)
        for lane in ("drums", "bass", "chords", "lead")
    }
    original = session_routes._regenerate_lane_on_stored_session

    def fail_on_lead(*args, **kwargs):
        if args[1] == session_routes.LaneName.lead:
            raise RuntimeError("lead renderer failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        session_routes,
        "_regenerate_lane_on_stored_session",
        fail_on_lead,
    )
    with pytest.raises(RuntimeError, match="lead renderer failed"):
        client.post(
            f"/api/sessions/{session_id}/fusion-dna/new",
            json={"include_lead": True},
        )

    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["fusion_contract_id"] == before_id
    assert current["fusion_dna_revision"] == 1
    for lane in ("drums", "bass", "chords", "lead"):
        assert _signature(current, lane) == before[lane]


def test_new_dna_upgrades_a_legacy_baseline_fusion_session(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    client.post(f"/api/sessions/{session_id}/generate")
    session_routes._SESSIONS[  # type: ignore[attr-defined]
        session_id
    ].bass_engine = "baseline"

    response = client.post(f"/api/sessions/{session_id}/fusion-dna/new")

    assert response.status_code == 200
    state = response.json()
    assert state["bass_engine"] == "phrase_v2"
    assert state["fusion_contract_active"] is True
    assert state["fusion_dna_revision"] == 2


def test_new_dna_refuses_a_half_old_half_new_locked_core(
    client: TestClient,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    before_id = generated["fusion_contract_id"]
    locked = client.patch(
        f"/api/sessions/{session_id}/lane-locks",
        json={"bass": True},
    )
    assert locked.status_code == 200

    response = client.post(f"/api/sessions/{session_id}/fusion-dna/new")

    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "fusion_core_locked"
    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["fusion_contract_id"] == before_id
    assert current["fusion_dna_revision"] == 1


def test_new_dna_rolls_back_contract_and_midi_if_a_lane_fails(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = _create_fusion(client)
    generated = client.post(f"/api/sessions/{session_id}/generate").json()["session"]
    before_id = generated["fusion_contract_id"]
    before = {
        lane: _signature(generated, lane)
        for lane in ("drums", "bass", "chords")
    }
    original = session_routes._regenerate_lane_on_stored_session

    def fail_on_keys(*args, **kwargs):
        lane = args[1]
        if lane == session_routes.LaneName.chords:
            raise RuntimeError("keys renderer failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(
        session_routes,
        "_regenerate_lane_on_stored_session",
        fail_on_keys,
    )
    with pytest.raises(RuntimeError, match="keys renderer failed"):
        client.post(f"/api/sessions/{session_id}/fusion-dna/new")

    current = client.get(f"/api/sessions/{session_id}").json()
    assert current["fusion_contract_id"] == before_id
    assert current["fusion_dna_revision"] == 1
    for lane in ("drums", "bass", "chords"):
        assert _signature(current, lane) == before[lane]
