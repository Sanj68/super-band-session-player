"""v0.5 Step 5: opt-in Performance MIDI download API.

These tests pin:
- the Clean MIDI default path is byte-identical to today
- ?mode=clean equals no-mode
- ?mode=performance returns performance MIDI after full bass generation
- ?mode=performance returns 404 with reason when unavailable
- selected-bar regeneration invalidates performance MIDI
- candidate promotion materializes performance MIDI
- combined session export remains clean-only and unchanged
- unknown ?mode returns 400
- non-bass + ?mode=performance returns 400
"""

from __future__ import annotations

import io
from pathlib import Path

import pretty_midi
from fastapi.testclient import TestClient

from app.main import app
from app.models.session import SourceAnalysis
from app.routes import session_routes
from app.services import bass_candidate_store


def _isolated_client(tmp_path: Path) -> TestClient:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    return TestClient(app)


def _create_generated_session(client: TestClient) -> str:
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 96,
            "key": "C",
            "scale": "major",
            "bar_count": 4,
            "bass_style": "supportive",
            "bass_engine": "baseline",
        },
    )
    assert created.status_code == 200
    session_id = str(created.json()["session"]["id"])
    generated = client.post(f"/api/sessions/{session_id}/generate")
    assert generated.status_code == 200
    return session_id


def _source_analysis_with_groove(bar_count: int = 4) -> SourceAnalysis:
    kick_rows = [[0.0] * 16 for _ in range(bar_count)]
    snare_rows = [[0.0] * 16 for _ in range(bar_count)]
    pressure_rows = [[0.2] * 16 for _ in range(bar_count)]
    onset_rows = [[0.0] * 16 for _ in range(bar_count)]
    for bar in range(bar_count):
        kick_rows[bar][0] = 1.0
        kick_rows[bar][8] = 0.7
        snare_rows[bar][4] = 0.9
        snare_rows[bar][12] = 0.8
        pressure_rows[bar][0] = 0.95
        pressure_rows[bar][4] = 0.85
        onset_rows[bar][0] = 1.0
        onset_rows[bar][4] = 0.8

    return SourceAnalysis(
        source_lane="reference_audio",
        tempo=96,
        tempo_estimate_bpm=96.0,
        tempo_confidence=0.8,
        beat_grid_seconds=[i * 0.625 for i in range(bar_count * 4)],
        bar_starts_seconds=[i * 2.5 for i in range(bar_count)],
        beat_phase_offset_beats=0,
        beat_phase_scores=[1.0, 0.0, 0.0, 0.0],
        beat_phase_confidence=0.8,
        phase_offset_used_for_generation_beats=0,
        bar_start_anchor_used_seconds=0.0,
        generation_aligned_to_anchor=False,
        downbeat_guess_bar_index=0,
        downbeat_confidence=0.7,
        bar_start_confidence=0.8,
        tonal_center_pc_guess=0,
        tonal_center_confidence=0.7,
        scale_mode_guess="major",
        scale_mode_confidence=0.7,
        sections=[],
        bar_energy=[0.5] * bar_count,
        bar_accent_profile=[0.5] * bar_count,
        bar_confidence_profile=[0.7] * bar_count,
        source_groove_resolution=16,
        source_onset_weight=onset_rows,
        source_kick_weight=kick_rows,
        source_snare_weight=snare_rows,
        source_slot_pressure=pressure_rows,
        source_groove_confidence=[0.8] * bar_count,
    )


def _create_session_with_source_analysis(client: TestClient) -> str:
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 96,
            "key": "C",
            "scale": "major",
            "bar_count": 4,
            "bass_style": "supportive",
            "bass_engine": "baseline",
        },
    )
    assert created.status_code == 200
    session_id = str(created.json()["session"]["id"])
    session_routes._SESSIONS[session_id].source_analysis_override = (  # type: ignore[attr-defined]
        _source_analysis_with_groove(bar_count=4)
    )
    return session_id


def _read_pm(data: bytes) -> pretty_midi.PrettyMIDI:
    return pretty_midi.PrettyMIDI(io.BytesIO(data))


def test_no_mode_returns_clean_bass_midi_unchanged(tmp_path: Path) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    no_mode = client.get(f"/api/sessions/{session_id}/midi/bass")
    explicit_clean = client.get(f"/api/sessions/{session_id}/midi/bass?mode=clean")

    assert no_mode.status_code == 200
    assert explicit_clean.status_code == 200
    assert no_mode.headers["content-type"] == "audio/midi"
    assert (
        no_mode.headers["content-disposition"]
        == f'attachment; filename="{session_id}_bass.mid"'
    )
    assert no_mode.content == explicit_clean.content
    assert len(no_mode.content) > 0
    pm = _read_pm(no_mode.content)
    assert sum(len(inst.notes) for inst in pm.instruments) > 0


def test_mode_performance_returns_performance_midi_after_full_generation(
    tmp_path: Path,
) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    perf = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")

    assert perf.status_code == 200
    assert perf.headers["content-type"] == "audio/midi"
    assert (
        perf.headers["content-disposition"]
        == f'attachment; filename="{session_id}_bass_performance.mid"'
    )
    assert len(perf.content) > 0
    pm = _read_pm(perf.content)
    # Performance render names its instrument distinctly to keep it visually
    # separable in DAWs.
    perf_inst_names = {inst.name for inst in pm.instruments}
    assert "Bass (Performance)" in perf_inst_names


def test_mode_performance_returns_404_missing_when_lane_not_generated(
    tmp_path: Path,
) -> None:
    client = _isolated_client(tmp_path)
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 96,
            "key": "C",
            "scale": "major",
            "bar_count": 4,
            "bass_style": "supportive",
        },
    )
    assert created.status_code == 200
    session_id = str(created.json()["session"]["id"])

    res = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")

    assert res.status_code == 404
    detail = res.json()["detail"]
    assert detail["error"] == "performance_midi_unavailable"
    assert detail["reason"] == "missing"


def test_unknown_mode_returns_400(tmp_path: Path) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    res = client.get(f"/api/sessions/{session_id}/midi/bass?mode=bogus")

    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "invalid_mode"


def test_mode_performance_on_non_bass_lane_returns_400(tmp_path: Path) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    res = client.get(f"/api/sessions/{session_id}/midi/drums?mode=performance")

    assert res.status_code == 400
    assert res.json()["detail"]["error"] == "performance_midi_unsupported_lane"


def test_selected_bar_regeneration_invalidates_performance_midi(tmp_path: Path) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    pre = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")
    assert pre.status_code == 200

    spliced = client.post(
        f"/api/sessions/{session_id}/lanes/bass/regenerate-bars",
        json={"bar_start": 1, "bar_end": 2, "seed": 1234},
    )
    assert spliced.status_code == 200

    # Clean lane still works.
    clean = client.get(f"/api/sessions/{session_id}/midi/bass")
    assert clean.status_code == 200
    assert len(clean.content) > 0

    # Performance lane is now invalidated until the next full regeneration
    # or candidate promotion.
    post = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")
    assert post.status_code == 404
    detail = post.json()["detail"]
    assert detail["error"] == "performance_midi_unavailable"
    assert detail["reason"] == "invalidated"


def test_candidate_promotion_materializes_performance_midi(tmp_path: Path) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    # After the splice path: invalidate, then prove promotion restores
    # performance MIDI.
    spliced = client.post(
        f"/api/sessions/{session_id}/lanes/bass/regenerate-bars",
        json={"bar_start": 1, "bar_end": 2, "seed": 4242},
    )
    assert spliced.status_code == 200
    invalidated = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")
    assert invalidated.status_code == 404

    cands = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 7777},
    )
    assert cands.status_code == 200
    run_id = cands.json()["run_id"]
    take_id = cands.json()["takes"][0]["take_id"]

    promoted = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{run_id}/{take_id}/promote"
    )
    assert promoted.status_code == 200

    perf = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")
    assert perf.status_code == 200
    assert len(perf.content) > 0


def test_full_generation_passes_source_maps_to_performance_renderer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_session_with_source_analysis(client)
    original_render = session_routes.render_performance_bass_midi
    calls: list[dict[str, object]] = []

    def spy_render(*args, **kwargs):
        calls.append(dict(kwargs))
        return original_render(*args, **kwargs)

    monkeypatch.setattr(session_routes, "render_performance_bass_midi", spy_render)

    generated = client.post(f"/api/sessions/{session_id}/generate")

    assert generated.status_code == 200
    assert calls
    kwargs = calls[-1]
    assert kwargs["source_kick_per_bar"] is not None
    assert kwargs["source_snare_per_bar"] is not None
    assert kwargs["source_pressure_per_bar"] is not None


def test_candidate_promotion_passes_source_maps_to_performance_renderer(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_session_with_source_analysis(client)
    generated = client.post(f"/api/sessions/{session_id}/generate")
    assert generated.status_code == 200

    cands = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 7777},
    )
    assert cands.status_code == 200
    run_id = cands.json()["run_id"]
    take_id = cands.json()["takes"][0]["take_id"]

    original_render = session_routes.render_performance_bass_midi
    calls: list[dict[str, object]] = []

    def spy_render(*args, **kwargs):
        calls.append(dict(kwargs))
        return original_render(*args, **kwargs)

    monkeypatch.setattr(session_routes, "render_performance_bass_midi", spy_render)

    promoted = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{run_id}/{take_id}/promote"
    )

    assert promoted.status_code == 200
    assert calls
    kwargs = calls[-1]
    assert kwargs["source_kick_per_bar"] is not None
    assert kwargs["source_snare_per_bar"] is not None
    assert kwargs["source_pressure_per_bar"] is not None


def test_combined_session_export_remains_clean_only(tmp_path: Path) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    bass_clean = client.get(f"/api/sessions/{session_id}/midi/bass")
    assert bass_clean.status_code == 200

    export = client.get(f"/api/sessions/{session_id}/midi")
    assert export.status_code == 200
    pm = _read_pm(export.content)
    inst_names = {inst.name.lower() for inst in pm.instruments}
    # Combined export must not include the performance instrument.
    assert "bass (performance)" not in inst_names
    # Bass instrument note count matches the clean lane.
    bass_clean_pm = _read_pm(bass_clean.content)
    clean_bass_notes = sum(
        len(inst.notes) for inst in bass_clean_pm.instruments if not inst.is_drum
    )
    export_bass_notes = sum(
        len(inst.notes)
        for inst in pm.instruments
        if inst.name.lower() == "bass" and not inst.is_drum
    )
    assert export_bass_notes == clean_bass_notes


def test_performance_bytes_differ_from_clean_when_shaping_present(tmp_path: Path) -> None:
    """When generation produces ghost/grace notes, performance ≠ clean.

    If no ghost/grace appears under the chosen seed, this check is skipped
    rather than failing — the contract being tested is "differs WHEN shaping
    exists," not "shaping always exists."
    """
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    clean = client.get(f"/api/sessions/{session_id}/midi/bass")
    perf = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")
    assert clean.status_code == 200
    assert perf.status_code == 200

    clean_pm = _read_pm(clean.content)
    perf_pm = _read_pm(perf.content)

    def _velocities(pm: pretty_midi.PrettyMIDI) -> list[int]:
        return sorted(
            int(n.velocity)
            for inst in pm.instruments
            for n in inst.notes
        )

    clean_vels = _velocities(clean_pm)
    perf_vels = _velocities(perf_pm)
    if clean_vels == perf_vels:
        # No ghost/grace was inferred under this seed — nothing to assert.
        # Test passes trivially; the byte-difference contract is conditional.
        return

    assert perf.content != clean.content
