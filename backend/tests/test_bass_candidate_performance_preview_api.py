from __future__ import annotations

import base64
import copy
import io
from pathlib import Path

import pretty_midi
from fastapi.testclient import TestClient

from app.main import app
from app.routes import session_routes
from app.services import bass_candidate_store
from app.services.midi_note_extract import extract_lane_notes


def _bass_midi(
    notes: list[tuple[int, float, float, int]],
    *,
    name: str,
) -> bytes:
    midi = pretty_midi.PrettyMIDI(initial_tempo=100)
    instrument = pretty_midi.Instrument(program=33, name=name)
    instrument.notes = [
        pretty_midi.Note(
            pitch=pitch,
            start=start,
            end=end,
            velocity=velocity,
        )
        for pitch, start, end, velocity in notes
    ]
    midi.instruments.append(instrument)
    out = io.BytesIO()
    midi.write(out)
    return out.getvalue()


def _json_notes(data: bytes) -> list[dict[str, int | float]]:
    return [note.model_dump(mode="json") for note in extract_lane_notes(data)]


def test_candidate_download_and_notes_offer_frozen_performance_without_changing_clean_default(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = (  # type: ignore[attr-defined]
        tmp_path / "bass_candidate_runs.json"
    )
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    clean = _bass_midi(
        [(36, 0.0, 0.5, 96)],
        name="Bass",
    )
    performance = _bass_midi(
        [
            (36, 0.04, 0.34, 72),
            (38, 0.46, 0.55, 34),
        ],
        name="Bass (Performance)",
    )
    assert clean != performance

    def render_take(*_args, **_kwargs):
        return clean, performance, "preview"

    monkeypatch.setattr(
        session_routes,
        "_render_bass_take_with_seed",
        render_take,
    )

    client = TestClient(app)
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 100,
            "key": "C",
            "scale": "major",
            "bar_count": 2,
            "bass_articulation_focus": "ghosted",
        },
    )
    assert created.status_code == 200, created.text
    session_id = created.json()["session"]["id"]
    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 6001},
    )
    assert generated.status_code == 200, generated.text
    run = generated.json()
    take_id = run["takes"][0]["take_id"]
    base_url = (
        f"/api/sessions/{session_id}/bass-candidates/"
        f"{run['run_id']}/{take_id}"
    )

    # No query parameter keeps the historical clean-composition contract.
    clean_default = client.get(base_url)
    clean_explicit = client.get(f"{base_url}?mode=clean")
    assert clean_default.status_code == 200
    assert clean_default.content == clean
    assert clean_explicit.content == clean
    assert clean_default.headers["x-bass-candidate-mode"] == "clean"
    assert clean_default.headers["content-disposition"].endswith(
        f'filename="{session_id}_{run["run_id"]}_{take_id}_bass.mid"'
    )

    performance_download = client.get(f"{base_url}?mode=performance")
    assert performance_download.status_code == 200
    assert performance_download.content == performance
    assert performance_download.headers["x-bass-candidate-requested-mode"] == (
        "performance"
    )
    assert performance_download.headers["x-bass-candidate-mode"] == "performance"
    assert performance_download.headers["content-disposition"].endswith(
        f'filename="{session_id}_{run["run_id"]}_{take_id}_bass_performance.mid"'
    )

    clean_notes = client.get(f"{base_url}/notes")
    performance_notes = client.get(f"{base_url}/notes?mode=performance")
    assert clean_notes.status_code == 200
    assert clean_notes.json() == _json_notes(clean)
    assert performance_notes.status_code == 200
    assert performance_notes.json() == _json_notes(performance)
    assert performance_notes.headers["x-bass-candidate-mode"] == "performance"

    invalid_mode = client.get(f"{base_url}?mode=raw")
    assert invalid_mode.status_code == 422


def test_candidate_performance_request_falls_back_to_verified_clean_for_legacy_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = (  # type: ignore[attr-defined]
        tmp_path / "bass_candidate_runs.json"
    )
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    clean = _bass_midi([(43, 0.0, 0.5, 88)], name="Bass")

    def render_legacy_take(*_args, **_kwargs):
        # The legacy two-value adapter deliberately stores clean as performance
        # during generation; strip that newer payload below to emulate an old run.
        return clean, "legacy preview"

    monkeypatch.setattr(
        session_routes,
        "_render_bass_take_with_seed",
        render_legacy_take,
    )

    client = TestClient(app)
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 100,
            "key": "G",
            "scale": "major",
            "bar_count": 2,
            "bass_articulation_focus": "ghosted",
        },
    )
    session_id = created.json()["session"]["id"]
    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 7001},
    )
    assert generated.status_code == 200, generated.text
    run = generated.json()
    take_id = run["takes"][0]["take_id"]

    stored_run = bass_candidate_store.get_run_for_session(
        session_id,
        run["run_id"],
    )
    assert stored_run is not None
    legacy_run = copy.deepcopy(stored_run)
    legacy_take = next(
        take
        for take in legacy_run["takes"]
        if take["take_id"] == take_id
    )
    for field in (
        "performance_midi_b64",
        "performance_midi_sha256",
        "performance_byte_length",
    ):
        legacy_take.pop(field, None)
    monkeypatch.setattr(
        bass_candidate_store,
        "get_run_for_session",
        lambda _session_id, _run_id: legacy_run,
    )

    base_url = (
        f"/api/sessions/{session_id}/bass-candidates/"
        f"{run['run_id']}/{take_id}"
    )
    download = client.get(f"{base_url}?mode=performance")
    assert download.status_code == 200
    assert download.content == base64.b64decode(legacy_take["midi_b64"])
    assert download.headers["x-bass-candidate-requested-mode"] == "performance"
    assert download.headers["x-bass-candidate-mode"] == "clean"

    notes = client.get(f"{base_url}/notes?mode=performance")
    assert notes.status_code == 200
    assert notes.json() == _json_notes(clean)
    assert notes.headers["x-bass-candidate-mode"] == "clean"
