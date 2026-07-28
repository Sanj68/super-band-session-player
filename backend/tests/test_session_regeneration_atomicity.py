from __future__ import annotations

import copy
import io
from threading import Event, Thread

import pretty_midi
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.main import app
from app.models.session import (
    AddPartToSuitBody,
    GenerateAroundAnchorBody,
    LaneName,
    RegenerateSelectedBody,
)
from app.routes import plugin_routes, session_routes


def _generated_session() -> tuple[TestClient, str]:
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    client = TestClient(app)
    created = client.post(
        "/api/sessions/",
        json={"tempo": 100, "key": "C", "scale": "major", "bar_count": 4},
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    generated = client.post(f"/api/sessions/{session_id}/generate")
    assert generated.status_code == 200
    return client, session_id


def _valid_drum_midi() -> bytes:
    midi = pretty_midi.PrettyMIDI(initial_tempo=100.0)
    drums = pretty_midi.Instrument(program=0, is_drum=True, name="Atomic Test")
    drums.notes.append(
        pretty_midi.Note(
            velocity=100,
            pitch=36,
            start=0.0,
            end=0.1,
        )
    )
    out = io.BytesIO()
    midi.write(out)
    return out.getvalue()


def _valid_melodic_midi(*, pitch: int) -> bytes:
    midi = pretty_midi.PrettyMIDI(initial_tempo=100.0)
    instrument = pretty_midi.Instrument(
        program=32,
        is_drum=False,
        name="Atomic Melodic Test",
    )
    instrument.notes.append(
        pretty_midi.Note(
            velocity=100,
            pitch=pitch,
            start=0.0,
            end=0.5,
        )
    )
    midi.instruments.append(instrument)
    out = io.BytesIO()
    midi.write(out)
    return out.getvalue()


def test_regenerate_selected_commits_nothing_when_a_later_generator_fails(
    monkeypatch,
) -> None:
    _, session_id = _generated_session()
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    before = copy.deepcopy(stored)
    staged_drums = _valid_drum_midi()

    monkeypatch.setattr(
        session_routes.generator,
        "generate_drums",
        lambda **_kwargs: (staged_drums, "staged drums"),
    )

    def fail_bass(**_kwargs):
        raise RuntimeError("bass generator failed")

    monkeypatch.setattr(session_routes.generator, "generate_bass", fail_bass)

    with pytest.raises(RuntimeError, match="bass generator failed"):
        session_routes.regenerate_selected(
            session_id,
            RegenerateSelectedBody(lanes=[LaneName.drums, LaneName.bass]),
        )

    assert stored == before


@pytest.mark.parametrize("operation", ["generate", "regenerate_unlocked"])
def test_other_full_session_generation_is_atomic_when_a_later_lane_fails(
    monkeypatch,
    operation: str,
) -> None:
    _, session_id = _generated_session()
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    before = copy.deepcopy(stored)

    monkeypatch.setattr(
        session_routes.generator,
        "generate_drums",
        lambda **_kwargs: (_valid_drum_midi(), "staged drums"),
    )

    def fail_bass(**_kwargs):
        raise RuntimeError("bass generator failed")

    monkeypatch.setattr(session_routes.generator, "generate_bass", fail_bass)

    with pytest.raises(RuntimeError, match="bass generator failed"):
        if operation == "generate":
            session_routes.generate_session(session_id)
        else:
            session_routes.regenerate_unlocked(session_id)

    assert stored == before


def test_generate_around_anchor_is_atomic_including_anchor_selection(
    monkeypatch,
) -> None:
    _, session_id = _generated_session()
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    stored.anchor_lane = "drums"
    before = copy.deepcopy(stored)

    monkeypatch.setattr(
        session_routes.generator,
        "generate_drums",
        lambda **_kwargs: (_valid_drum_midi(), "staged drums"),
    )

    def fail_bass(**_kwargs):
        raise RuntimeError("bass generator failed")

    monkeypatch.setattr(session_routes.generator, "generate_bass", fail_bass)

    with pytest.raises(RuntimeError, match="bass generator failed"):
        session_routes.generate_around_anchor(
            session_id,
            GenerateAroundAnchorBody(anchor_lane=LaneName.lead),
        )

    assert stored == before


def test_single_bass_lane_regeneration_commits_nothing_when_performance_render_fails(
    monkeypatch,
) -> None:
    _, session_id = _generated_session()
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    before = copy.deepcopy(stored)

    def fail_performance_render(**_kwargs):
        raise RuntimeError("performance render failed")

    monkeypatch.setattr(
        session_routes,
        "_render_bass_performance_bytes",
        fail_performance_render,
    )

    with pytest.raises(RuntimeError, match="performance render failed"):
        session_routes.regenerate_lane(session_id, LaneName.bass)

    assert stored == before


def test_regenerate_selected_harmony_preflight_cannot_mutate_live_session(
    monkeypatch,
) -> None:
    _, session_id = _generated_session()
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    stored.reference_audio_path = "unconfirmed-source.wav"
    stored.reference_audio_filename = "unconfirmed-source.wav"
    stored.chord_progression = None
    stored.harmony_map_confirmation_required = False
    stored.harmony_map_source = "none"
    before = copy.deepcopy(stored)
    drum_generator_called = False

    def unexpected_drums(**_kwargs):
        nonlocal drum_generator_called
        drum_generator_called = True
        return _valid_drum_midi(), "unexpected"

    monkeypatch.setattr(
        session_routes.generator,
        "generate_drums",
        unexpected_drums,
    )

    with pytest.raises(HTTPException) as raised:
        session_routes.regenerate_selected(
            session_id,
            RegenerateSelectedBody(lanes=[LaneName.drums, LaneName.bass]),
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["error"] == "harmony_map_confirmation_required"
    assert drum_generator_called is False
    assert stored == before


def test_regenerate_selected_rebuilds_selected_anchor_before_dependants(
    monkeypatch,
) -> None:
    _, session_id = _generated_session()
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    stored.anchor_lane = LaneName.lead.value
    rebuilt_lead = _valid_melodic_midi(pitch=67)
    rebuilt_bass = _valid_melodic_midi(pitch=36)
    calls: list[tuple[LaneName, object | None]] = []
    built_contexts: list[object] = []
    original_build_context = session_routes.build_session_context

    def fake_regenerate(staged, lane: LaneName, *, context) -> None:
        calls.append((lane, context))
        if lane == LaneName.lead:
            assert context is None
            staged.lead_bytes = rebuilt_lead
            staged.lead_preview = "rebuilt anchor"
            return
        assert lane == LaneName.bass
        assert staged.lead_bytes == rebuilt_lead
        assert context is built_contexts[0]
        staged.bass_bytes = rebuilt_bass
        staged.bass_performance_bytes = rebuilt_bass
        staged.bass_preview = "rebuilt from new anchor"
        staged.bass_seed = 99
        staged.current_bass_candidate_run_id = None
        staged.current_bass_candidate_take_id = None

    monkeypatch.setattr(
        session_routes,
        "_regenerate_lane_on_stored_session",
        fake_regenerate,
    )
    def build_context_spy(staged):
        assert staged.lead_bytes == rebuilt_lead, (
            "context was built from the old anchor"
        )
        context = original_build_context(staged)
        assert context is not None
        built_contexts.append(context)
        return context

    monkeypatch.setattr(
        session_routes,
        "build_session_context",
        build_context_spy,
    )

    result = session_routes.regenerate_selected(
        session_id,
        RegenerateSelectedBody(
            lanes=[LaneName.bass, LaneName.lead]
        ),
    )

    assert [lane for lane, _context in calls] == [
        LaneName.lead,
        LaneName.bass,
    ]
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    assert stored.lead_bytes == rebuilt_lead
    assert stored.bass_bytes == rebuilt_bass
    assert result.lanes[LaneName.bass].preview == "rebuilt from new anchor"


def test_plugin_get_observes_only_complete_old_or_new_generation_revision(
    monkeypatch,
) -> None:
    _, session_id = _generated_session()
    old = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    old.bass_bytes = _valid_melodic_midi(pitch=48)
    old.bass_performance_bytes = _valid_melodic_midi(pitch=65)
    old.bass_preview = "old coherent revision"
    publish_entered = Event()
    release_publish = Event()

    class BlockingSessionMap(dict):
        blocked_once = False

        def __setitem__(self, key, value):
            if (
                key == session_id
                and value is not old
                and not self.blocked_once
            ):
                self.blocked_once = True
                publish_entered.set()
                if not release_publish.wait(timeout=2.0):
                    raise TimeoutError("atomic publish was never released")
            return super().__setitem__(key, value)

    mapping = BlockingSessionMap(session_routes._SESSIONS)  # type: ignore[attr-defined]
    monkeypatch.setattr(session_routes, "_SESSIONS", mapping)

    def stage_new_revision(staged, lane: LaneName, *, context) -> None:
        assert lane == LaneName.bass
        staged.bass_bytes = _valid_melodic_midi(pitch=50)
        staged.bass_performance_bytes = _valid_melodic_midi(pitch=67)
        staged.bass_preview = "new coherent revision"

    monkeypatch.setattr(
        session_routes,
        "_regenerate_lane_on_stored_session",
        stage_new_revision,
    )
    errors: list[BaseException] = []

    def regenerate() -> None:
        try:
            session_routes.regenerate_lane(session_id, LaneName.bass)
        except BaseException as exc:
            errors.append(exc)

    worker = Thread(target=regenerate)
    worker.start()
    try:
        assert publish_entered.wait(timeout=1.0)
        during = plugin_routes.get_bass_part(session_id)
        assert during.preview == "old coherent revision"
        assert [note.pitch for note in during.notes] == [65]
    finally:
        release_publish.set()
        worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert errors == []
    after = plugin_routes.get_bass_part(session_id)
    assert after.preview == "new coherent revision"
    assert [note.pitch for note in after.notes] == [67]


def test_add_part_to_suit_obeys_harmony_gate_without_generating(
    monkeypatch,
) -> None:
    _, session_id = _generated_session()
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    stored.harmony_confirmation_required = True
    before = copy.deepcopy(stored)
    lead_generator_called = False

    def unexpected_lead(**_kwargs):
        nonlocal lead_generator_called
        lead_generator_called = True
        return b"", "unexpected"

    monkeypatch.setattr(
        session_routes.generator,
        "generate_lead",
        unexpected_lead,
    )

    with pytest.raises(HTTPException) as raised:
        session_routes.add_part_to_suit(
            session_id,
            AddPartToSuitBody(target_lane="lead", mode="solo"),
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["error"] == "harmony_confirmation_required"
    assert lead_generator_called is False
    assert stored == before
