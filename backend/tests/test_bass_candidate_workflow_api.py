from __future__ import annotations

import base64
import io
import math
import time
from pathlib import Path

import pretty_midi
from fastapi.testclient import TestClient

from app.main import app
from app.models.session import SourceAnalysis
from app.routes import midi_routes
from app.routes import session_routes
from app.services.midi_audition import FakeMidiBackend, MidiOutputInfo, RtMidiBackend
from app.services import bass_candidate_store


def test_bass_candidate_workflow_generate_list_notes_promote(tmp_path: Path) -> None:
    # Isolate candidate-run persistence for this test.
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]

    # Isolate in-memory session state to avoid cross-test bleed.
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)

    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 108,
            "key": "C",
            "scale": "major",
            "bar_count": 8,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "bass_instrument": "finger_bass",
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]

    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 3, "seed": 42001, "clip_id": "clip-a"},
    )
    assert generated.status_code == 200
    run = generated.json()
    assert run["session_id"] == session_id
    assert run["take_count"] == 3
    assert run["bass_style"] == "supportive"
    assert run["bass_engine"] == "baseline"
    assert isinstance(run["run_id"], str) and run["run_id"]
    assert isinstance(run["takes"], list) and len(run["takes"]) == 3
    first_take = run["takes"][0]
    assert isinstance(first_take["take_id"], str) and first_take["take_id"]
    assert isinstance(first_take["seed"], int)
    assert first_take["note_count"] >= 0
    assert first_take["byte_length"] > 0
    assert isinstance(first_take["preview"], str)

    # New selection-metadata and quality fields must be present on every take.
    for take in run["takes"]:
        assert take.get("selection_stage") in ("strict", "relaxed", "final_fill"), (
            f"unexpected selection_stage: {take.get('selection_stage')!r}"
        )
        assert isinstance(take.get("motif_family"), str) and take["motif_family"], (
            "motif_family should be a non-empty string"
        )
        assert 0.0 <= float(take.get("quality_total", -1)) <= 1.0
        assert isinstance(take.get("quality_scores"), dict)
        assert isinstance(take.get("quality_reason"), str)
        assert take.get("quality_floor_cutoff") is not None
        assert take.get("top_pool_score") is not None

    listed = client.get(f"/api/sessions/{session_id}/bass-candidates")
    assert listed.status_code == 200
    rows = listed.json()
    assert isinstance(rows, list) and len(rows) >= 1
    listed_run = next((r for r in rows if r["run_id"] == run["run_id"]), None)
    assert listed_run is not None
    assert listed_run["session_id"] == session_id
    assert listed_run["take_count"] == 3
    assert len(listed_run["takes"]) == 3

    notes_res = client.get(
        f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{first_take['take_id']}/notes"
    )
    assert notes_res.status_code == 200
    notes = notes_res.json()
    assert isinstance(notes, list)
    assert len(notes) > 0
    for n in notes[:5]:
        assert set(n.keys()) == {"pitch", "start", "end", "velocity"}
        assert 0 <= n["pitch"] <= 127
        assert n["start"] >= 0
        assert n["end"] >= n["start"]
        assert 0 <= n["velocity"] <= 127

    midi_res = client.get(
        f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{first_take['take_id']}"
    )
    assert midi_res.status_code == 200
    assert midi_res.headers["content-type"] == "audio/midi"
    assert "attachment;" in midi_res.headers["content-disposition"]
    assert midi_res.headers["content-disposition"].endswith(
        f'filename="{session_id}_{run["run_id"]}_{first_take["take_id"]}_bass.mid"'
    )
    assert len(midi_res.content) > 0
    stored_run = bass_candidate_store.get_run_for_session(session_id, run["run_id"])
    assert stored_run is not None
    stored_take = next(t for t in stored_run["takes"] if t["take_id"] == first_take["take_id"])
    assert midi_res.content == base64.b64decode(stored_take["midi_b64"].encode("ascii"))

    promoted = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{first_take['take_id']}/promote"
    )
    assert promoted.status_code == 200
    state = promoted.json()
    assert state["id"] == session_id
    assert state["current_bass_candidate_run_id"] == run["run_id"]
    assert state["current_bass_candidate_take_id"] == first_take["take_id"]
    assert state["bass_seed"] == first_take["seed"]
    assert state["lanes"]["bass"]["generated"] is True
    assert isinstance(state["lanes"]["bass"]["notes"], list)
    assert len(state["lanes"]["bass"]["notes"]) > 0


def test_session_state_tracks_bass_seed_for_generation_and_regeneration(tmp_path: Path, monkeypatch) -> None:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    seeds = iter([10101, 20202])
    monkeypatch.setattr(session_routes, "_new_bass_seed", lambda: next(seeds))

    client = TestClient(app)

    created = client.post(
        "/api/sessions/",
        json={"tempo": 112, "key": "C", "scale": "major", "bar_count": 4},
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    assert created.json()["session"]["bass_seed"] is None

    generated = client.post(f"/api/sessions/{session_id}/generate")
    assert generated.status_code == 200
    first_seed = generated.json()["session"]["bass_seed"]
    assert first_seed == 10101

    regenerated = client.post(f"/api/sessions/{session_id}/lanes/bass/regenerate")
    assert regenerated.status_code == 200
    second_seed = regenerated.json()["session"]["bass_seed"]
    assert second_seed == 20202
    assert second_seed != first_seed


def test_bass_candidate_generation_is_deterministic_for_same_take_seed(tmp_path: Path) -> None:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)

    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 100,
            "key": "D",
            "scale": "minor",
            "bar_count": 4,
            "bass_style": "supportive",
            "bass_engine": "baseline",
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]

    first = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 60606},
    )
    second = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 60606},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    first_run = first.json()
    second_run = second.json()
    assert [t["seed"] for t in first_run["takes"]] == [t["seed"] for t in second_run["takes"]]

    first_take = first_run["takes"][0]
    second_take = second_run["takes"][0]
    first_notes = client.get(
        f"/api/sessions/{session_id}/bass-candidates/{first_run['run_id']}/{first_take['take_id']}/notes"
    )
    second_notes = client.get(
        f"/api/sessions/{session_id}/bass-candidates/{second_run['run_id']}/{second_take['take_id']}/notes"
    )
    assert first_notes.status_code == 200
    assert second_notes.status_code == 200
    assert first_notes.json() == second_notes.json()


def _source_analysis_for_vocabulary(bar_count: int) -> SourceAnalysis:
    pressure = [[0.32] * 16 for _ in range(bar_count)]
    kick = [[0.08] * 16 for _ in range(bar_count)]
    snare = [[0.06] * 16 for _ in range(bar_count)]
    for row in range(bar_count):
        for slot in (0, 3, 6, 10, 14):
            pressure[row][slot] = 0.72
            kick[row][slot] = 0.55
    return SourceAnalysis(
        source_lane="reference_audio",
        tempo=100,
        tempo_estimate_bpm=100.0,
        tempo_confidence=0.85,
        beat_grid_seconds=[0.6 * i for i in range(bar_count * 4)],
        bar_starts_seconds=[2.4 * i for i in range(bar_count)],
        beat_phase_offset_beats=0,
        beat_phase_scores=[1.0, 0.0, 0.0, 0.0],
        beat_phase_confidence=0.8,
        phase_offset_used_for_generation_beats=0,
        bar_start_anchor_used_seconds=0.0,
        generation_aligned_to_anchor=False,
        downbeat_guess_bar_index=0,
        downbeat_confidence=0.7,
        bar_start_confidence=0.8,
        tonal_center_pc_guess=6,
        tonal_center_confidence=0.7,
        scale_mode_guess="minor",
        scale_mode_confidence=0.7,
        sections=[],
        bar_energy=[0.55] * bar_count,
        bar_accent_profile=[0.55] * bar_count,
        bar_confidence_profile=[0.72] * bar_count,
        source_groove_resolution=16,
        source_onset_weight=pressure,
        source_kick_weight=kick,
        source_snare_weight=snare,
        source_slot_pressure=pressure,
        source_groove_confidence=[0.82] * bar_count,
    )


def _slot_signature(notes: list[dict], *, tempo: int, bar_count: int) -> tuple[tuple[int, ...], ...]:
    spb = 60.0 / float(tempo)
    bar_len = 4.0 * spb
    rows: list[list[int]] = [[] for _ in range(bar_count)]
    for note in notes:
        bar = max(0, min(bar_count - 1, int(math.floor(float(note["start"]) / bar_len))))
        rel = float(note["start"]) - (bar * bar_len)
        slot = max(0, min(15, int(round(rel / (spb / 4.0)))))
        rows[bar].append(slot)
    return tuple(tuple(sorted(set(row))) for row in rows)


def _pitch_classes_from_midi(midi_bytes: bytes) -> set[int]:
    pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
    pcs: set[int] = set()
    for ins in pm.instruments:
        if ins.is_drum:
            continue
        for note in ins.notes:
            pcs.add(int(note.pitch) % 12)
    return pcs


def _wait_for(predicate, *, timeout: float = 1.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate()


def test_source_minor_candidates_include_sub_one_vocabulary_labels(tmp_path: Path) -> None:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 100,
            "key": "F#",
            "scale": "minor",
            "bar_count": 4,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "chord_progression": ["F#m7", "F#m7", "F#m7", "F#m7"],
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    session_routes._SESSIONS[session_id].source_analysis_override = _source_analysis_for_vocabulary(4)  # type: ignore[attr-defined]

    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 5, "seed": 9090},
    )
    assert generated.status_code == 200
    run = generated.json()
    labels = [take.get("label") for take in run["takes"]]
    assert labels == [
        "Warm Jazz-Funk",
        "Dark Slinky Grit",
        "Fusion Answer",
        "Hip-Hop Soul Restraint",
        "Tight Head-Nod Pocket",
    ]
    assert [take.get("template_id") for take in run["takes"]] == [
        "warm_jazz_funk_01",
        "dark_slinky_grit_01",
        "fusion_answer_01",
        "hiphop_soul_restraint_01",
        "tight_headnod_pocket_01",
    ]

    listed = client.get(f"/api/sessions/{session_id}/bass-candidates")
    assert listed.status_code == 200
    assert [take.get("label") for take in listed.json()[0]["takes"]] == labels

    notes_res = client.get(
        f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{run['takes'][0]['take_id']}/notes"
    )
    assert notes_res.status_code == 200
    notes = notes_res.json()
    assert notes
    ordered = sorted(notes, key=lambda n: float(n["start"]))
    assert int(ordered[0]["pitch"]) % 12 == 6  # F# vocabulary root anchor
    spb_fn = 60.0 / 100.0
    sixteenth_fn = spb_fn / 4.0
    bar_len_fn = 4.0 * spb_fn
    assert round((float(ordered[0]["start"]) - 0.0) / sixteenth_fn) <= 1
    last_origin = 3 * bar_len_fn
    minor7_allow_fsharp = {6, 9, 1, 4}
    assert all(int(n["pitch"]) % 12 in minor7_allow_fsharp for n in notes)
    assert any(
        float(n["start"]) >= last_origin + 11.5 * sixteenth_fn - 1e-6
        and int(n["pitch"]) % 12 in minor7_allow_fsharp
        for n in notes
    )

    pitch_classes = {int(note["pitch"]) % 12 for note in notes}
    assert pitch_classes != {6}
    slots = _slot_signature(notes, tempo=100, bar_count=4)
    assert any(set(row) - {0, 8} for row in slots)

    repeated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 5, "seed": 9090},
    )
    assert repeated.status_code == 200
    assert [take.get("label") for take in repeated.json()["takes"]] == labels


def test_promoted_labelled_candidate_keeps_guarded_performance_bytes(tmp_path: Path) -> None:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 100,
            "key": "F#",
            "scale": "minor",
            "bar_count": 4,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "chord_progression": ["F#m7", "F#m7", "F#m7", "F#m7"],
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    session_routes._SESSIONS[session_id].source_analysis_override = _source_analysis_for_vocabulary(4)  # type: ignore[attr-defined]

    generated = client.post(f"/api/sessions/{session_id}/bass-candidates", json={"take_count": 5, "seed": 9090})
    assert generated.status_code == 200
    run = generated.json()
    labelled_take = run["takes"][0]
    assert labelled_take.get("label")
    assert labelled_take.get("template_id")

    promoted = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{labelled_take['take_id']}/promote"
    )
    assert promoted.status_code == 200

    clean = client.get(f"/api/sessions/{session_id}/midi/bass?mode=clean")
    perf = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")
    assert clean.status_code == 200
    assert perf.status_code == 200
    assert clean.content == perf.content
    assert _pitch_classes_from_midi(clean.content).issubset({6, 9, 1, 4})
    assert _pitch_classes_from_midi(perf.content).issubset({6, 9, 1, 4})


def test_promoting_unlabelled_candidate_keeps_seed_rerender_path(tmp_path: Path, monkeypatch) -> None:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    call_count = {"perf_regen": 0}
    original = session_routes.generator.generate_bass

    def _wrapped_generate_bass(*args, **kwargs):
        if kwargs.get("return_performance_notes"):
            call_count["perf_regen"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(session_routes.generator, "generate_bass", _wrapped_generate_bass)

    client = TestClient(app)
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 104,
            "key": "C",
            "scale": "major",
            "bar_count": 4,
            "bass_style": "melodic",
            "bass_engine": "baseline",
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]

    generated = client.post(f"/api/sessions/{session_id}/bass-candidates", json={"take_count": 2, "seed": 31337})
    assert generated.status_code == 200
    run = generated.json()
    unlabelled_take = run["takes"][0]
    assert unlabelled_take.get("label") is None
    assert unlabelled_take.get("template_id") is None

    before = call_count["perf_regen"]
    promoted = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{unlabelled_take['take_id']}/promote"
    )
    assert promoted.status_code == 200
    assert call_count["perf_regen"] == before + 1


def test_audition_performance_after_labelled_promotion_uses_guarded_bytes(tmp_path: Path) -> None:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    backend = FakeMidiBackend((MidiOutputInfo(id="iac-1", name="IAC Driver Bus 1"),))
    midi_routes.set_midi_output_backend(backend)
    try:
        client = TestClient(app)
        created = client.post(
            "/api/sessions/",
            json={
                "tempo": 100,
                "key": "F#",
                "scale": "minor",
                "bar_count": 4,
                "bass_style": "supportive",
                "bass_engine": "baseline",
                "chord_progression": ["F#m7", "F#m7", "F#m7", "F#m7"],
            },
        )
        assert created.status_code == 200
        session_id = created.json()["session"]["id"]
        session_routes._SESSIONS[session_id].source_analysis_override = _source_analysis_for_vocabulary(4)  # type: ignore[attr-defined]

        generated = client.post(f"/api/sessions/{session_id}/bass-candidates", json={"take_count": 5, "seed": 9090})
        assert generated.status_code == 200
        run = generated.json()
        labelled = run["takes"][0]
        assert labelled.get("label")
        assert labelled.get("template_id")
        promoted = client.post(
            f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{labelled['take_id']}/promote"
        )
        assert promoted.status_code == 200

        res = client.post(
            f"/api/sessions/{session_id}/audition/bass",
            json={"output": "iac-1", "mode": "performance"},
        )
        assert res.status_code == 200
        _wait_for(lambda: any(getattr(m, "type", "") == "note_on" for m in backend.sent_messages))
        note_ons = [m for m in backend.sent_messages if getattr(m, "type", "") == "note_on"]
        assert note_ons
        assert {int(getattr(m, "note", 0)) % 12 for m in note_ons}.issubset({6, 9, 1, 4})
    finally:
        midi_routes.get_audition_player().stop()
        midi_routes.set_midi_output_backend(RtMidiBackend())


def test_bass_candidate_promote_404_on_invalid_ids(tmp_path: Path) -> None:
    """promote returns 404 for unknown session_id, run_id, or take_id."""
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)

    created = client.post(
        "/api/sessions/",
        json={"tempo": 100, "key": "D", "scale": "minor", "bar_count": 4},
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]

    gen = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 7777},
    )
    assert gen.status_code == 200
    run_id = gen.json()["run_id"]
    take_id = gen.json()["takes"][0]["take_id"]

    # Unknown session_id → 404.
    r = client.post(f"/api/sessions/no-such-session/bass-candidates/{run_id}/{take_id}/promote")
    assert r.status_code == 404

    # Valid session, unknown run_id → 404.
    r = client.post(f"/api/sessions/{session_id}/bass-candidates/bad-run/{take_id}/promote")
    assert r.status_code == 404

    # Valid session + run, unknown take_id → 404.
    r = client.post(f"/api/sessions/{session_id}/bass-candidates/{run_id}/bad-take/promote")
    assert r.status_code == 404


def test_bass_candidate_midi_download_404_on_invalid_ids(tmp_path: Path) -> None:
    """candidate MIDI download returns safe 404s for unknown run_id or take_id."""
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)

    created = client.post(
        "/api/sessions/",
        json={"tempo": 104, "key": "A", "scale": "minor", "bar_count": 4},
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]

    gen = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 8888},
    )
    assert gen.status_code == 200
    run_id = gen.json()["run_id"]
    take_id = gen.json()["takes"][0]["take_id"]

    r = client.get(f"/api/sessions/{session_id}/bass-candidates/bad-run/{take_id}")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "candidate_run_not_found"

    r = client.get(f"/api/sessions/{session_id}/bass-candidates/{run_id}/bad-take")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "candidate_take_not_found"


def test_bass_candidate_promote_lane_notes_match_candidate_notes(tmp_path: Path) -> None:
    """After promotion, the session bass lane notes are exactly the promoted candidate's notes."""
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    client = TestClient(app)

    created = client.post(
        "/api/sessions/",
        json={"tempo": 96, "key": "F", "scale": "major", "bar_count": 4},
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]

    gen = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 55555},
    )
    assert gen.status_code == 200
    run_id = gen.json()["run_id"]
    take_id = gen.json()["takes"][0]["take_id"]

    # Fetch the candidate's own notes before any promotion.
    notes_res = client.get(
        f"/api/sessions/{session_id}/bass-candidates/{run_id}/{take_id}/notes"
    )
    assert notes_res.status_code == 200
    candidate_notes = notes_res.json()
    assert len(candidate_notes) > 0

    # Promote the candidate.
    promote_res = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{run_id}/{take_id}/promote"
    )
    assert promote_res.status_code == 200
    state = promote_res.json()
    assert state["current_bass_candidate_run_id"] == run_id
    assert state["current_bass_candidate_take_id"] == take_id

    # The session bass lane notes must be identical to the candidate's notes.
    lane_notes = state["lanes"]["bass"]["notes"]
    assert len(lane_notes) == len(candidate_notes)
    for lane_note, cand_note in zip(lane_notes, candidate_notes):
        assert lane_note["pitch"] == cand_note["pitch"]
        assert lane_note["start"] == cand_note["start"]
        assert lane_note["end"] == cand_note["end"]
        assert lane_note["velocity"] == cand_note["velocity"]
