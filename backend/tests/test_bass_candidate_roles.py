from __future__ import annotations

import io
from pathlib import Path

import pretty_midi
from fastapi.testclient import TestClient

from app.main import app
from app.routes import session_routes
from app.services import bass_candidate_store
from app.services.bass_candidate_roles import ROLE_ORDER, bass_candidate_role_spec


def _client(tmp_path: Path) -> TestClient:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    return TestClient(app)


def _create_phrase_v2_session(client: TestClient) -> str:
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 88,
            "key": "D",
            "scale": "natural_minor",
            "bar_count": 16,
            "bass_style": "supportive",
            "bass_engine": "phrase_v2",
            "bass_instrument": "finger_bass",
            "bass_lock_to_groove": 0.7,
            "chord_progression": ["Bb", "C", "D", "Gm"],
        },
    )
    assert created.status_code == 200
    return created.json()["session"]["id"]


def _non_root_count(notes: list[dict[str, float | int]], *, tempo: int = 88) -> int:
    roots = (10, 0, 2, 7)  # Bb, C, D, G
    bar_seconds = (60.0 / tempo) * 4.0
    count = 0
    for note in notes:
        bar = min(15, int(float(note["start"]) / bar_seconds))
        if int(note["pitch"]) % 12 != roots[bar % len(roots)]:
            count += 1
    return count


def test_role_catalog_is_neutral_and_purposeful() -> None:
    assert ROLE_ORDER == (
        "pocket_keeper",
        "rhythmic_alternative",
        "harmonic_alternative",
        "performance_alternative",
    )
    for role in ROLE_ORDER:
        spec = bass_candidate_role_spec(role)
        assert spec is not None
        assert spec.label
        assert spec.description
        assert not any(name in f"{spec.label} {spec.description}".lower() for name in ("jaco", "pino", "marcus"))


def test_controlled_roles_create_audibly_distinct_safe_four_take_set(tmp_path: Path) -> None:
    client = _client(tmp_path)
    session_id = _create_phrase_v2_session(client)

    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={
            "take_count": 4,
            "seed": 424245,
            "variation_mode": "controlled_roles",
            "clip_id": "combined-88-dm",
        },
    )
    assert generated.status_code == 200
    run = generated.json()
    assert run["variation_mode"] == "controlled_roles"
    assert [take["candidate_role"] for take in run["takes"]] == list(ROLE_ORDER)
    assert all(take["candidate_role_label"] for take in run["takes"])
    assert all(take["candidate_role_description"] for take in run["takes"])
    listed = client.get(f"/api/sessions/{session_id}/bass-candidates")
    assert listed.status_code == 200
    listed_run = next(row for row in listed.json() if row["run_id"] == run["run_id"])
    assert listed_run["variation_mode"] == "controlled_roles"
    assert [take["candidate_role"] for take in listed_run["takes"]] == list(ROLE_ORDER)

    notes_by_role: dict[str, list[dict[str, float | int]]] = {}
    for take in run["takes"]:
        notes_response = client.get(
            f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{take['take_id']}/notes"
        )
        assert notes_response.status_code == 200
        notes_by_role[take["candidate_role"]] = notes_response.json()

        midi_response = client.get(
            f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{take['take_id']}"
        )
        assert midi_response.status_code == 200
        pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_response.content))
        tempos, bpms = pm.get_tempo_changes()
        assert tempos[0] == 0.0
        assert abs(float(bpms[0]) - 88.0) < 0.001
        playable = [note for inst in pm.instruments if not inst.is_drum for note in inst.notes]
        loop_end = (60.0 / 88.0) * 64.0
        assert playable
        assert max(note.end for note in playable) <= loop_end + 0.002
        assert min(note.end - note.start for note in playable) >= 0.025
        d_natural_minor_pcs = {0, 2, 4, 5, 7, 9, 10}
        assert {note.pitch % 12 for note in playable} <= d_natural_minor_pcs
        for pitch in {note.pitch for note in playable}:
            same_pitch = sorted((note for note in playable if note.pitch == pitch), key=lambda note: note.start)
            assert all(a.end <= b.start + 0.002 for a, b in zip(same_pitch, same_pitch[1:]))

    pocket = notes_by_role["pocket_keeper"]
    rhythmic = notes_by_role["rhythmic_alternative"]
    harmonic = notes_by_role["harmonic_alternative"]
    performance = notes_by_role["performance_alternative"]

    assert len(rhythmic) >= len(pocket) + 12
    assert _non_root_count(harmonic) > _non_root_count(pocket)
    assert min(int(note["velocity"]) for note in performance) < min(int(note["velocity"]) for note in pocket)
    assert min(float(note["end"]) - float(note["start"]) for note in performance) < min(
        float(note["end"]) - float(note["start"]) for note in pocket
    )

    performance_take = next(
        take for take in run["takes"] if take["candidate_role"] == "performance_alternative"
    )
    promoted = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{run['run_id']}/{performance_take['take_id']}/promote"
    )
    assert promoted.status_code == 200
    performance_midi = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")
    assert performance_midi.status_code == 200
    assert performance_midi.headers["content-type"] == "audio/midi"


def test_controlled_roles_are_repeatable_for_same_seed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    session_id = _create_phrase_v2_session(client)
    body = {"take_count": 4, "seed": 717171, "variation_mode": "controlled_roles"}

    first = client.post(f"/api/sessions/{session_id}/bass-candidates", json=body).json()
    second = client.post(f"/api/sessions/{session_id}/bass-candidates", json=body).json()

    for first_take, second_take in zip(first["takes"], second["takes"]):
        assert first_take["candidate_role"] == second_take["candidate_role"]
        first_notes = client.get(
            f"/api/sessions/{session_id}/bass-candidates/{first['run_id']}/{first_take['take_id']}/notes"
        ).json()
        second_notes = client.get(
            f"/api/sessions/{session_id}/bass-candidates/{second['run_id']}/{second_take['take_id']}/notes"
        ).json()
        assert first_notes == second_notes


def test_controlled_roles_fail_honestly_outside_neutral_phrase_v2(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 100,
            "key": "C",
            "scale": "major",
            "bar_count": 4,
            "bass_style": "supportive",
            "bass_engine": "baseline",
        },
    )
    session_id = created.json()["session"]["id"]
    response = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 4, "variation_mode": "controlled_roles"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"] == "controlled_roles_requires_phrase_v2"
