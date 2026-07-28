from __future__ import annotations

import asyncio
import json
from pathlib import Path
from threading import Event, Thread, current_thread

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import main as main_module
from app.routes import session_routes
from app.services import session_mutation_gate, session_store
from app.services.source_analysis import build_source_analysis


def _store_at(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(session_store, "_SESSIONS_FILE", tmp_path / "sessions.json")


def test_full_session_round_trip_preserves_order_midi_and_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_at(tmp_path, monkeypatch)
    first = session_routes.StoredSession(
        id="session-a",
        tempo=98,
        key="D",
        scale="Dorian",
        bar_count=4,
        bass_style="melodic",
        bass_density_bias=0.35,
        bass_bytes=b"MThd-bass",
        bass_performance_bytes=b"MThd-performance",
        drum_bytes=b"MThd-drums",
        chords_bytes=b"MThd-chords",
        lead_bytes=b"MThd-lead",
        bass_locked=True,
        current_bass_candidate_run_id="run-1",
        current_bass_candidate_take_id="take-2",
    )
    first.source_analysis_override = build_source_analysis(first)
    first.groove_reference_analysis_override = build_source_analysis(first).model_copy(
        update={"source_lane": "groove_reference_audio"}
    )
    second = session_routes.StoredSession(
        id="session-b",
        tempo=120,
        key="C",
        scale="Major",
        bar_count=8,
    )

    session_store.save_sessions({first.id: first, second.id: second})
    restored = session_store.load_sessions(session_routes.StoredSession)

    assert list(restored) == ["session-a", "session-b"]
    assert restored["session-a"].bass_bytes == b"MThd-bass"
    assert restored["session-a"].bass_performance_bytes == b"MThd-performance"
    assert restored["session-a"].drum_bytes == b"MThd-drums"
    assert restored["session-a"].chords_bytes == b"MThd-chords"
    assert restored["session-a"].lead_bytes == b"MThd-lead"
    assert restored["session-a"].bass_density_bias == 0.35
    assert restored["session-a"].bass_locked is True
    assert restored["session-a"].source_analysis_override == first.source_analysis_override
    assert (
        restored["session-a"].groove_reference_analysis_override
        == first.groove_reference_analysis_override
    )
    assert restored["session-a"].current_bass_candidate_take_id == "take-2"


def test_live_bridge_overlay_is_not_serialized_until_committed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_at(tmp_path, monkeypatch)
    stored = session_routes.StoredSession(
        id="transient-bridge-session",
        tempo=120,
        key="A",
        scale="major",
        bar_count=2,
    )
    durable_base = build_source_analysis(stored).model_copy(
        update={"tonal_center_pc_guess": 2, "scale_mode_guess": "natural_minor"}
    )
    live_overlay = durable_base.model_copy(
        update={
            "tonal_center_pc_guess": 9,
            "scale_mode_guess": "major",
            "source_metadata": {
                "live_harmonic_source_tag": "logic_au_harmonic_listener"
            },
        }
    )
    stored.key = "A"
    stored.scale = "major"
    stored.source_analysis_override = live_overlay
    stored.bridge_live_overlay_active = True
    stored.bridge_live_base_source_analysis_override = durable_base
    stored.bridge_live_base_key = "D"
    stored.bridge_live_base_scale = "natural_minor"

    session_store.save_sessions({stored.id: stored})
    restored = session_store.load_sessions(session_routes.StoredSession)[stored.id]

    assert restored.key == "D"
    assert restored.scale == "natural_minor"
    assert restored.source_analysis_override == durable_base
    assert restored.bridge_live_overlay_active is False
    assert restored.bridge_live_base_source_analysis_override is None


def test_duplicate_uses_durable_base_and_clears_session_scoped_candidate_ids(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_at(tmp_path, monkeypatch)
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    client = TestClient(app)
    created = client.post(
        "/api/sessions/",
        json={"tempo": 100, "key": "C", "scale": "major", "bar_count": 2},
    )
    source_id = created.json()["session"]["id"]
    source = session_routes._SESSIONS[source_id]  # type: ignore[attr-defined]
    durable_analysis = build_source_analysis(source)
    live_analysis = durable_analysis.model_copy(
        update={
            "tonal_center_pc_guess": 2,
            "source_metadata": {"live_harmonic_source_tag": "listener"},
        }
    )
    source.current_bass_candidate_run_id = "source-run"
    source.current_bass_candidate_take_id = "source-take"
    source.source_analysis_override = live_analysis
    source.key = "D"
    source.bridge_live_overlay_active = True
    source.bridge_live_base_source_analysis_override = durable_analysis
    source.bridge_live_base_key = "C"
    source.bridge_live_base_scale = "major"

    response = client.post(f"/api/sessions/{source_id}/duplicate")

    assert response.status_code == 200, response.text
    clone_id = response.json()["id"]
    clone = session_routes._SESSIONS[clone_id]  # type: ignore[attr-defined]
    assert clone.key == "C"
    assert clone.scale == "major"
    assert clone.source_analysis_override == durable_analysis
    assert clone.bridge_live_overlay_active is False
    assert clone.current_bass_candidate_run_id is None
    assert clone.current_bass_candidate_take_id is None

    session_store.save_sessions({clone.id: clone})
    restored = session_store.load_sessions(session_routes.StoredSession)[clone.id]
    assert restored.key == "C"
    assert restored.source_analysis_override == durable_analysis
    assert restored.current_bass_candidate_run_id is None


def test_legacy_harmony_gate_is_derived_without_mutating_get_or_failed_generation() -> None:
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    stored = session_routes.StoredSession(
        id="legacy-gate-session",
        tempo=100,
        key="C",
        scale="major",
        bar_count=4,
        reference_audio_path="/tmp/legacy-source.wav",
        reference_audio_filename="legacy-source.wav",
    )
    stored.source_analysis_override = build_source_analysis(stored).model_copy(
        update={
            "source_metadata": {
                "harmony_suggestions": {
                    "chords": ["C", "Am", "F", "G"],
                    "confidence": [0.5, 0.5, 0.5, 0.5],
                }
            }
        }
    )
    session_routes._SESSIONS[stored.id] = stored  # type: ignore[attr-defined]
    before = json.dumps(
        session_store._session_to_payload(stored),  # type: ignore[attr-defined]
        sort_keys=True,
    )
    client = TestClient(app)

    state = client.get(f"/api/sessions/{stored.id}")
    blocked = client.post(
        f"/api/sessions/{stored.id}/bass-candidates",
        json={"take_count": 2, "seed": 12345},
    )

    assert state.status_code == 200, state.text
    assert state.json()["harmony_map_confirmation_required"] is True
    assert state.json()["suggested_chord_progression"] == [
        "C",
        "Am",
        "F",
        "G",
    ]
    assert blocked.status_code == 409
    assert (
        blocked.json()["detail"]["error"]
        == "harmony_map_confirmation_required"
    )
    after = json.dumps(
        session_store._session_to_payload(  # type: ignore[attr-defined]
            session_routes._SESSIONS[stored.id]  # type: ignore[attr-defined]
        ),
        sort_keys=True,
    )
    assert after == before


def test_cancelled_durable_gate_waiter_cannot_leak_lock() -> None:
    session_mutation_gate.acquire()

    async def exercise() -> None:
        entered = asyncio.Event()

        async def waiter() -> None:
            async with main_module._session_mutation_gate_claim():  # noqa: SLF001
                entered.set()

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not entered.is_set()

    try:
        asyncio.run(exercise())
    finally:
        session_mutation_gate.release()

    assert session_mutation_gate.try_acquire() is True
    session_mutation_gate.release()


def test_corrupt_or_unsupported_snapshot_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_at(tmp_path, monkeypatch)
    session_store._SESSIONS_FILE.write_text("{bad json", encoding="utf-8")  # type: ignore[attr-defined]
    with pytest.raises(session_store.SessionStoreError):
        session_store.load_sessions(session_routes.StoredSession)

    session_store._SESSIONS_FILE.write_text(  # type: ignore[attr-defined]
        json.dumps({"schema_version": 999, "sessions": []}),
        encoding="utf-8",
    )
    with pytest.raises(session_store.SessionStoreError):
        session_store.load_sessions(session_routes.StoredSession)


@pytest.mark.parametrize(
    "session_payload",
    [
        {
            "id": "bad-tempo",
            "tempo": "loud",
            "key": "C",
            "scale": "major",
            "bar_count": 4,
        },
        {
            "id": "bad-range",
            "tempo": 999,
            "key": "C",
            "scale": "major",
            "bar_count": 4,
        },
    ],
)
def test_invalid_persisted_session_primitives_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session_payload: dict[str, object],
) -> None:
    _store_at(tmp_path, monkeypatch)
    session_store._SESSIONS_FILE.write_text(  # type: ignore[attr-defined]
        json.dumps(
            {
                "schema_version": 1,
                "snapshot_revision": 1,
                "sessions": [session_payload],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(session_store.SessionStoreError):
        session_store.load_sessions(session_routes.StoredSession)


def test_nonfinite_snapshot_input_and_output_fail_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_at(tmp_path, monkeypatch)
    nonfinite_json = (
        '{"schema_version":1,"snapshot_revision":1,"sessions":['
        '{"id":"nan","tempo":100,"key":"C","scale":"major",'
        '"bar_count":4,"bass_expression":NaN}]}'
    )
    session_store._SESSIONS_FILE.write_text(  # type: ignore[attr-defined]
        nonfinite_json,
        encoding="utf-8",
    )
    with pytest.raises(session_store.SessionStoreError):
        session_store.load_sessions(session_routes.StoredSession)

    overflow_json = (
        '{"schema_version":1,"snapshot_revision":1,"sessions":['
        '{"id":"overflow","tempo":100,"key":"C","scale":"major",'
        '"bar_count":4,"suggested_chord_confidence":[1e999]}]}'
    )
    session_store._SESSIONS_FILE.write_text(  # type: ignore[attr-defined]
        overflow_json,
        encoding="utf-8",
    )
    with pytest.raises(session_store.SessionStoreError):
        session_store.load_sessions(session_routes.StoredSession)

    valid = session_routes.StoredSession(
        id="preserve-valid",
        tempo=100,
        key="C",
        scale="major",
        bar_count=4,
    )
    session_store.save_sessions({valid.id: valid})
    before = session_store._SESSIONS_FILE.read_bytes()  # type: ignore[attr-defined]
    valid.bass_expression = float("inf")

    with pytest.raises(session_store.SessionStoreError):
        session_store.save_sessions({valid.id: valid})

    assert session_store._SESSIONS_FILE.read_bytes() == before  # type: ignore[attr-defined]


def test_engine_does_not_overwrite_a_corrupt_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_at(tmp_path, monkeypatch)
    corrupt = "{keep this for recovery"
    session_store._SESSIONS_FILE.write_text(corrupt, encoding="utf-8")  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    with TestClient(app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json()["ok"] is False
        assert health.json()["session_persistence"] == "degraded"
        assert app.state.session_persistence_enabled is False
        rejected = client.post(
            "/api/sessions/",
            json={
                "tempo": 100,
                "key": "C",
                "scale": "major",
                "bar_count": 4,
            },
        )
        assert rejected.status_code == 503
        assert (
            rejected.json()["detail"]["error"]
            == "session_persistence_unavailable"
        )
        assert session_routes._SESSIONS == {}  # type: ignore[attr-defined]

    assert session_store._SESSIONS_FILE.read_text(encoding="utf-8") == corrupt  # type: ignore[attr-defined]


def test_engine_lifespan_restores_session_and_persists_plugin_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _store_at(tmp_path, monkeypatch)
    stored = session_routes.StoredSession(
        id="logic-project-session",
        tempo=100,
        key="C",
        scale="Major",
        bar_count=4,
        bass_bytes=b"MThd",
        bass_style="supportive",
    )
    session_store.save_sessions({stored.id: stored})
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    with TestClient(app) as client:
        assert list(session_routes._SESSIONS) == ["logic-project-session"]  # type: ignore[attr-defined]
        response = client.post(
            "/api/plugin/regenerate",
            json={"session_id": stored.id, "bass_style": "melodic"},
        )
        assert response.status_code == 200

    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    restored = session_store.load_sessions(session_routes.StoredSession)
    assert restored["logic-project-session"].bass_style == "melodic"
    assert restored["logic-project-session"].bass_bytes


def test_api_returns_503_and_rolls_back_when_session_snapshot_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    stored = session_routes.StoredSession(
        id="persistence-failure-session",
        tempo=100,
        key="C",
        scale="major",
        bar_count=4,
        harmony_key_confirmed_by_user=True,
    )
    session_routes._SESSIONS[stored.id] = stored  # type: ignore[attr-defined]
    monkeypatch.setattr(
        app.state,
        "session_persistence_enabled",
        True,
        raising=False,
    )

    def fail_save(_sessions: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(session_store, "save_sessions", fail_save)

    response = TestClient(app).patch(
        f"/api/sessions/{stored.id}",
        json={"tempo": 101},
    )

    assert response.status_code == 503
    assert (
        response.json()["detail"]["error"]
        == "session_persistence_unavailable"
    )
    assert response.headers["retry-after"] == "1"
    restored = session_routes._SESSIONS[stored.id]  # type: ignore[attr-defined]
    assert restored.tempo == 100
    assert restored is not stored


def test_handler_exception_after_mutation_rolls_back_session_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    stored = session_routes.StoredSession(
        id="handler-failure-session",
        tempo=100,
        key="C",
        scale="major",
        bar_count=4,
        harmony_key_confirmed_by_user=True,
    )
    session_routes._SESSIONS[stored.id] = stored  # type: ignore[attr-defined]

    def fail_response(*_args, **_kwargs):
        raise RuntimeError("response construction failed")

    monkeypatch.setattr(session_routes, "_to_state", fail_response)

    response = TestClient(app, raise_server_exceptions=False).patch(
        f"/api/sessions/{stored.id}",
        json={"tempo": 101},
    )

    assert response.status_code == 500
    restored = session_routes._SESSIONS[stored.id]  # type: ignore[attr-defined]
    assert restored.tempo == 100
    assert restored is not stored


def test_concurrent_saves_cannot_overwrite_a_newer_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed old serializer must not replace a later session revision."""

    _store_at(tmp_path, monkeypatch)
    stored = session_routes.StoredSession(
        id="concurrent-session",
        tempo=90,
        key="C",
        scale="major",
        bar_count=4,
    )
    original_serializer = session_store._session_to_payload  # type: ignore[attr-defined]
    old_captured = Event()
    release_old = Event()
    newer_serializer_entered = Event()
    errors: list[BaseException] = []
    revisions: list[int] = []

    def delayed_serializer(session: object) -> dict[str, object]:
        payload = original_serializer(session)
        if current_thread().name == "old-save":
            old_captured.set()
            if not release_old.wait(timeout=2.0):
                raise TimeoutError("old save was never released")
        elif current_thread().name == "new-save":
            newer_serializer_entered.set()
        return payload

    monkeypatch.setattr(
        session_store,
        "_session_to_payload",
        delayed_serializer,
    )

    def save() -> None:
        try:
            revisions.append(
                session_store.save_sessions({stored.id: stored})
            )
        except BaseException as exc:  # pragma: no cover - surfaced below
            errors.append(exc)

    old_thread = Thread(target=save, name="old-save")
    old_thread.start()
    assert old_captured.wait(timeout=1.0)

    stored.tempo = 123
    new_thread = Thread(target=save, name="new-save")
    new_thread.start()

    # With serialization outside the write lock, the newer serializer enters
    # and finishes first, after which the delayed old raw overwrites it.
    newer_serializer_entered.wait(timeout=0.2)
    release_old.set()
    old_thread.join(timeout=2.0)
    new_thread.join(timeout=2.0)

    assert not old_thread.is_alive()
    assert not new_thread.is_alive()
    assert errors == []
    assert len(revisions) == 2
    assert max(revisions) - min(revisions) == 1
    restored = session_store.load_sessions(session_routes.StoredSession)
    assert restored[stored.id].tempo == 123
    document = json.loads(
        session_store._SESSIONS_FILE.read_text(encoding="utf-8")  # type: ignore[attr-defined]
    )
    assert document["snapshot_revision"] == max(revisions)
