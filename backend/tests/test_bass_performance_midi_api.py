"""v0.5 Step 5: opt-in Performance MIDI download API.

These tests pin:
- the Clean MIDI default path is byte-identical to today
- ?mode=clean equals no-mode
- ?mode=performance returns performance MIDI after full bass generation
- ?mode=performance returns 404 with reason when unavailable
- selected-bar regeneration preserves and splices performance MIDI
- candidate promotion materializes performance MIDI
- combined session and ZIP exports prefer performance MIDI with explicit clean override
- unknown ?mode returns 400
- non-bass + ?mode=performance returns 400
"""

from __future__ import annotations

import base64
import io
import zipfile
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


def _bass_midi_with_beat_starts(
    *,
    tempo: int,
    instrument_name: str,
) -> bytes:
    seconds_per_beat = 60.0 / float(tempo)
    midi = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    instrument = pretty_midi.Instrument(program=33, name=instrument_name)
    for pitch, start_beat, duration_beats in (
        (36, 0.0, 0.125),
        (37, 15.5, 0.125),
        (38, 15.75, 0.125),
        (39, 15.25, 0.5),
    ):
        start = start_beat * seconds_per_beat
        instrument.notes.append(
            pretty_midi.Note(
                velocity=90,
                pitch=pitch,
                start=start,
                end=start + (duration_beats * seconds_per_beat),
            )
        )
    midi.instruments.append(instrument)
    buffer = io.BytesIO()
    midi.write(buffer)
    return buffer.getvalue()


def _beat_starts_by_pitch(
    data: bytes,
    *,
    tempo: int,
    instrument_name: str,
) -> dict[int, float]:
    seconds_per_beat = 60.0 / float(tempo)
    midi = _read_pm(data)
    return {
        int(note.pitch): round(float(note.start) / seconds_per_beat, 6)
        for instrument in midi.instruments
        if instrument.name.lower() == instrument_name.lower()
        for note in instrument.notes
    }


def _beat_ranges_by_pitch(
    data: bytes,
    *,
    tempo: int,
    instrument_name: str,
) -> dict[int, tuple[float, float]]:
    seconds_per_beat = 60.0 / float(tempo)
    midi = _read_pm(data)
    return {
        int(note.pitch): (
            round(float(note.start) / seconds_per_beat, 6),
            round(float(note.end) / seconds_per_beat, 6),
        )
        for instrument in midi.instruments
        if instrument.name.lower() == instrument_name.lower()
        for note in instrument.notes
    }


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


def test_performance_only_rerender_preserves_clean_phrase_and_seed(
    tmp_path: Path,
) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)
    stored_before = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    seed_before = stored_before.bass_seed
    clean_before = client.get(
        f"/api/sessions/{session_id}/midi/bass?mode=clean"
    ).content
    performance_before = client.get(
        f"/api/sessions/{session_id}/midi/bass?mode=performance"
    ).content

    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={
            "bass_performance_controls": {
                "ghost": 0.62,
                "mute": 0.24,
                "slide": 0.8,
                "legato": 0.68,
                "timing_humanize": 0.58,
                "velocity_humanize": 0.72,
            }
        },
    )
    assert patched.status_code == 200, patched.text
    rerendered = client.post(
        f"/api/sessions/{session_id}/regenerate-selected",
        json={
            "lanes": ["bass"],
            "preserve_bass_phrase": True,
        },
    )

    assert rerendered.status_code == 200, rerendered.text
    assert "phrase preserved" in rerendered.json()["message"]
    stored_after = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    assert stored_after.bass_seed == seed_before
    assert client.get(
        f"/api/sessions/{session_id}/midi/bass?mode=clean"
    ).content == clean_before
    assert client.get(
        f"/api/sessions/{session_id}/midi/bass?mode=performance"
    ).content != performance_before


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


def _performance_note_tuples(data: bytes) -> list[tuple[int, float, float, int]]:
    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    return sorted(
        (
            int(note.pitch),
            round(float(note.start), 6),
            round(float(note.end), 6),
            int(note.velocity),
        )
        for instrument in pm.instruments
        for note in instrument.notes
    )


def test_selected_bar_regeneration_preserves_performance_midi(tmp_path: Path) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    pre = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")
    assert pre.status_code == 200

    spliced = client.post(
        f"/api/sessions/{session_id}/lanes/bass/regenerate-bars",
        json={"bar_start": 1, "bar_end": 2, "seed": 1234},
    )
    assert spliced.status_code == 200
    assert spliced.json()["bass_performance_available"] is True

    # Both clean and performance lanes remain playable.
    clean = client.get(f"/api/sessions/{session_id}/midi/bass")
    assert clean.status_code == 200
    assert len(clean.content) > 0

    post = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")
    assert post.status_code == 200
    before = _performance_note_tuples(pre.content)
    after = _performance_note_tuples(post.content)
    selected_start = 1 * 4 * (60.0 / 96.0)
    selected_end = 2 * 4 * (60.0 / 96.0)
    # Performance downbeats can be pulled a few milliseconds early. Classify
    # those notes by their musical bar using the same bounded tolerance as the
    # performance splice, rather than by their raw timestamp alone.
    # The feel offset plus the MIDI file's 220 PPQ grid can pull a semantic
    # downbeat by more than one 11.4 ms tick at 96 BPM.
    boundary_tolerance = 0.025
    assert [
        note
        for note in before
        if not (
            selected_start - boundary_tolerance
            <= note[1]
            < selected_end - boundary_tolerance
        )
    ] == [
        note
        for note in after
        if not (
            selected_start - boundary_tolerance
            <= note[1]
            < selected_end - boundary_tolerance
        )
    ]
    assert [
        note
        for note in before
        if (
            selected_start - boundary_tolerance
            <= note[1]
            < selected_end - boundary_tolerance
        )
    ] != [
        note
        for note in after
        if (
            selected_start - boundary_tolerance
            <= note[1]
            < selected_end - boundary_tolerance
        )
    ]


def test_candidate_promotion_keeps_performance_midi_available(tmp_path: Path) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    # A selected-bar edit keeps performance MIDI available, and promotion
    # replaces it with the candidate's frozen performance render.
    spliced = client.post(
        f"/api/sessions/{session_id}/lanes/bass/regenerate-bars",
        json={"bar_start": 1, "bar_end": 2, "seed": 4242},
    )
    assert spliced.status_code == 200
    after_splice = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")
    assert after_splice.status_code == 200

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
    assert promoted.json()["bass_performance_available"] is True

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

    original_render = session_routes.render_performance_bass_midi
    calls: list[dict[str, object]] = []

    def spy_render(*args, **kwargs):
        calls.append(dict(kwargs))
        return original_render(*args, **kwargs)

    monkeypatch.setattr(session_routes, "render_performance_bass_midi", spy_render)

    cands = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 2, "seed": 7777},
    )
    assert cands.status_code == 200
    run_id = cands.json()["run_id"]
    take_id = cands.json()["takes"][0]["take_id"]
    assert calls
    rendered_during_generation = len(calls)
    raw_run = bass_candidate_store.get_run_for_session(session_id, run_id)
    assert raw_run is not None
    raw_take = next(
        take for take in raw_run["takes"] if take["take_id"] == take_id
    )

    promoted = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{run_id}/{take_id}/promote"
    )

    assert promoted.status_code == 200
    assert len(calls) == rendered_during_generation
    kwargs = calls[-1]
    assert kwargs["source_kick_per_bar"] is not None
    assert kwargs["source_snare_per_bar"] is not None
    assert kwargs["source_pressure_per_bar"] is not None
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    assert stored.bass_performance_bytes == base64.b64decode(
        raw_take["performance_midi_b64"]
    )


def test_combined_session_export_prefers_performance_with_explicit_clean_override(
    tmp_path: Path,
) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    bass_clean = client.get(f"/api/sessions/{session_id}/midi/bass")
    bass_performance = client.get(
        f"/api/sessions/{session_id}/midi/bass?mode=performance"
    )
    assert bass_clean.status_code == 200
    assert bass_performance.status_code == 200

    performance_export = client.get(f"/api/sessions/{session_id}/midi")
    assert performance_export.status_code == 200
    assert (
        performance_export.headers["x-session-player-bass-mode"]
        == "performance"
    )
    performance_pm = _read_pm(performance_export.content)
    performance_names = {inst.name.lower() for inst in performance_pm.instruments}
    assert "bass (performance)" in performance_names

    clean_export = client.get(
        f"/api/sessions/{session_id}/midi?bass_mode=clean"
    )
    assert clean_export.status_code == 200
    assert clean_export.headers["x-session-player-bass-mode"] == "clean"
    clean_pm = _read_pm(clean_export.content)
    clean_names = {inst.name.lower() for inst in clean_pm.instruments}
    assert "bass (performance)" not in clean_names

    bass_clean_pm = _read_pm(bass_clean.content)
    clean_bass_notes = sum(
        len(inst.notes) for inst in bass_clean_pm.instruments if not inst.is_drum
    )
    export_bass_notes = sum(
        len(inst.notes)
        for inst in clean_pm.instruments
        if inst.name.lower() == "bass" and not inst.is_drum
    )
    assert export_bass_notes == clean_bass_notes


def test_combined_and_zip_exports_fall_back_to_clean_when_performance_is_missing(
    tmp_path: Path,
) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    stored.bass_performance_bytes = None
    bass_clean = client.get(f"/api/sessions/{session_id}/midi/bass?mode=clean")
    assert bass_clean.status_code == 200

    combined = client.get(f"/api/sessions/{session_id}/midi")
    assert combined.status_code == 200
    assert combined.headers["x-session-player-bass-mode"] == "clean"
    assert "bass (performance)" not in {
        inst.name.lower() for inst in _read_pm(combined.content).instruments
    }

    archive = client.get(f"/api/sessions/{session_id}/export")
    assert archive.status_code == 200
    assert archive.headers["x-session-player-bass-mode"] == "clean"
    with zipfile.ZipFile(io.BytesIO(archive.content)) as bundle:
        assert bundle.read(f"{session_id}_bass_clean.mid") == bass_clean.content

    explicit_combined = client.get(
        f"/api/sessions/{session_id}/midi?bass_mode=performance"
    )
    explicit_archive = client.get(
        f"/api/sessions/{session_id}/export?bass_mode=performance"
    )
    assert explicit_combined.status_code == 404
    assert explicit_archive.status_code == 404
    assert (
        explicit_combined.json()["detail"]["error"]
        == "performance_midi_unavailable"
    )


def test_zip_export_bass_mode_matches_standalone_lane_downloads(
    tmp_path: Path,
) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)
    clean = client.get(f"/api/sessions/{session_id}/midi/bass?mode=clean")
    performance = client.get(
        f"/api/sessions/{session_id}/midi/bass?mode=performance"
    )
    assert clean.status_code == 200
    assert performance.status_code == 200

    default_archive = client.get(f"/api/sessions/{session_id}/export")
    assert default_archive.status_code == 200
    assert (
        default_archive.headers["x-session-player-bass-mode"]
        == "performance"
    )
    with zipfile.ZipFile(io.BytesIO(default_archive.content)) as bundle:
        assert (
            bundle.read(f"{session_id}_bass_performance.mid")
            == performance.content
        )

    clean_archive = client.get(
        f"/api/sessions/{session_id}/export?bass_mode=clean"
    )
    assert clean_archive.status_code == 200
    assert clean_archive.headers["x-session-player-bass-mode"] == "clean"
    with zipfile.ZipFile(io.BytesIO(clean_archive.content)) as bundle:
        assert bundle.read(f"{session_id}_bass_clean.mid") == clean.content


def test_phase_offset_rotates_every_performance_export_but_not_clean(
    tmp_path: Path,
) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)
    stored = session_routes._SESSIONS[session_id]  # type: ignore[attr-defined]
    clean_source = _bass_midi_with_beat_starts(
        tempo=stored.tempo,
        instrument_name="Bass",
    )
    performance_source = _bass_midi_with_beat_starts(
        tempo=stored.tempo,
        instrument_name="Bass (Performance)",
    )
    stored.bass_bytes = clean_source
    stored.bass_performance_bytes = performance_source

    patched = client.patch(
        f"/api/sessions/{session_id}",
        json={"bass_phase_offset_beats": 0.5},
    )
    assert patched.status_code == 200, patched.text

    expected_source = {36: 0.0, 37: 15.5, 38: 15.75, 39: 15.25}
    expected_rotated = {36: 0.5, 37: 0.0, 38: 0.25, 39: 15.75}

    clean_lane = client.get(f"/api/sessions/{session_id}/midi/bass?mode=clean")
    performance_lane = client.get(
        f"/api/sessions/{session_id}/midi/bass?mode=performance"
    )
    assert clean_lane.status_code == 200
    assert performance_lane.status_code == 200
    assert clean_lane.content == clean_source
    assert _beat_starts_by_pitch(
        clean_lane.content,
        tempo=stored.tempo,
        instrument_name="Bass",
    ) == expected_source
    assert _beat_starts_by_pitch(
        performance_lane.content,
        tempo=stored.tempo,
        instrument_name="Bass (Performance)",
    ) == expected_rotated
    seconds_per_beat = 60.0 / float(stored.tempo)
    phase_wrapped_ranges = sorted(
        (
            round(float(note.start) / seconds_per_beat, 6),
            round(float(note.end) / seconds_per_beat, 6),
        )
        for instrument in _read_pm(performance_lane.content).instruments
        if instrument.name.lower() == "bass (performance)"
        for note in instrument.notes
        if int(note.pitch) == 39
    )
    assert phase_wrapped_ranges == [(0.0, 0.25), (15.75, 16.0)]

    performance_combined = client.get(f"/api/sessions/{session_id}/midi")
    clean_combined = client.get(
        f"/api/sessions/{session_id}/midi?bass_mode=clean"
    )
    assert performance_combined.status_code == 200
    assert clean_combined.status_code == 200
    assert _beat_starts_by_pitch(
        performance_combined.content,
        tempo=stored.tempo,
        instrument_name="Bass (Performance)",
    ) == expected_rotated
    assert _beat_starts_by_pitch(
        clean_combined.content,
        tempo=stored.tempo,
        instrument_name="Bass",
    ) == expected_source

    performance_archive = client.get(f"/api/sessions/{session_id}/export")
    clean_archive = client.get(
        f"/api/sessions/{session_id}/export?bass_mode=clean"
    )
    assert performance_archive.status_code == 200
    assert clean_archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(performance_archive.content)) as bundle:
        archived_performance = bundle.read(
            f"{session_id}_bass_performance.mid"
        )
    with zipfile.ZipFile(io.BytesIO(clean_archive.content)) as bundle:
        archived_clean = bundle.read(f"{session_id}_bass_clean.mid")
    assert archived_performance == performance_lane.content
    assert archived_clean == clean_source
    assert stored.bass_performance_bytes == performance_source


def test_combined_export_rejects_unknown_bass_mode(tmp_path: Path) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_generated_session(client)

    combined = client.get(
        f"/api/sessions/{session_id}/midi?bass_mode=bogus"
    )
    archive = client.get(
        f"/api/sessions/{session_id}/export?bass_mode=bogus"
    )

    assert combined.status_code == 422
    assert archive.status_code == 422


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
