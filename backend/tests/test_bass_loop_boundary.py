"""Loop-boundary normalization tests.

Covers the long-standing bass MIDI gap fix: every bass-bytes egress
must produce MIDI whose first playable note starts at exactly 0.0 and
whose final bar contains a resolving note near the loop end.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

import pretty_midi
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.session import LaneNote, SourceAnalysis
from app.routes import session_routes
from app.services import bass_candidate_store
from app.services.bass_loop_boundary import (
    normalize_bass_lane_notes,
    normalize_bass_loop_bytes,
)


SLOT_TOLERANCE_SEC = 1e-3


def _bass_notes_from_bytes(midi_bytes: bytes) -> list[pretty_midi.Note]:
    pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
    notes: list[pretty_midi.Note] = []
    for inst in pm.instruments:
        if inst.is_drum:
            continue
        notes.extend(inst.notes)
    notes.sort(key=lambda n: (float(n.start), int(n.pitch)))
    return notes


def _assert_loop_boundary(
    midi_bytes: bytes,
    *,
    tempo: int,
    bar_count: int,
    label: str,
) -> None:
    notes = _bass_notes_from_bytes(midi_bytes)
    assert notes, f"{label}: expected at least one bass note"
    spb = 60.0 / float(tempo)
    sixteenth = spb / 4.0
    bar_len = 4.0 * spb
    loop_end = bar_count * bar_len

    # First note starts at exactly 0.0 (within numerical noise).
    assert float(notes[0].start) <= SLOT_TOLERANCE_SEC, (
        f"{label}: first note must start at 0.0, got {notes[0].start:.6f}"
    )

    # No negative starts, no notes past loop end.
    for n in notes:
        assert float(n.start) >= 0.0, f"{label}: negative start {n.start}"
        assert float(n.end) <= loop_end + 1e-3, (
            f"{label}: note end {n.end:.6f} past loop end {loop_end:.6f}"
        )
        assert float(n.start) < loop_end, (
            f"{label}: note start {n.start:.6f} at/past loop end {loop_end:.6f}"
        )

    # Final bar contains at least one note.
    last_bar_origin = (bar_count - 1) * bar_len
    final_bar_notes = [n for n in notes if last_bar_origin - 1e-6 <= float(n.start) < loop_end]
    assert final_bar_notes, f"{label}: final bar empty"

    # Loop length: the latest note end is within 1 sixteenth of loop_end.
    latest_end = max(float(n.end) for n in notes)
    assert abs(latest_end - loop_end) <= sixteenth + 1e-3, (
        f"{label}: latest note end {latest_end:.6f} not within sixteenth ({sixteenth:.6f}) of loop_end {loop_end:.6f}"
    )


# --------------------------------------------------------------------------- #
# Unit tests for the helper itself.
# --------------------------------------------------------------------------- #


def test_normalize_lane_notes_shifts_first_note_to_zero() -> None:
    tempo = 117
    bar_count = 8
    spb = 60.0 / tempo
    sixteenth = spb / 4.0
    notes = [
        LaneNote(pitch=42, start=0.4, end=0.4 + sixteenth * 4, velocity=92),
        LaneNote(pitch=42, start=4.0, end=4.0 + sixteenth * 2, velocity=84),
        LaneNote(pitch=42, start=15.5, end=16.0, velocity=86),
    ]
    out = normalize_bass_lane_notes(
        notes, tempo=tempo, bar_count=bar_count, harmonic_root_pc=6
    )
    assert out
    assert out[0].start == pytest.approx(0.0, abs=SLOT_TOLERANCE_SEC)
    for n in out:
        assert n.start >= 0.0
        assert n.end <= bar_count * 4.0 * spb + 1e-6


def test_normalize_lane_notes_inserts_anchor_when_first_note_late() -> None:
    tempo = 120
    bar_count = 8
    spb = 60.0 / tempo
    bar_len = 4.0 * spb
    sixteenth = spb / 4.0
    # Single note inside bar 1 (no shift large enough to put it at 0).
    notes = [
        LaneNote(pitch=45, start=bar_len * 1.0 + sixteenth * 4, end=bar_len * 1.0 + sixteenth * 6, velocity=80),
    ]
    out = normalize_bass_lane_notes(
        notes, tempo=tempo, bar_count=bar_count, harmonic_root_pc=6
    )
    assert out
    # After shifting min_start to 0, but the resulting first start is exactly 0.
    assert out[0].start == pytest.approx(0.0, abs=SLOT_TOLERANCE_SEC)
    # Final bar must contain a note (inserted by the helper).
    last_bar_origin = (bar_count - 1) * bar_len
    assert any(n.start >= last_bar_origin - 1e-6 for n in out)


def test_normalize_lane_notes_preserves_loop_length_and_resolves_tail() -> None:
    tempo = 110
    bar_count = 8
    spb = 60.0 / tempo
    bar_len = 4.0 * spb
    sixteenth = spb / 4.0
    loop_end = bar_count * bar_len
    notes = [
        LaneNote(pitch=42, start=0.0, end=sixteenth * 2, velocity=92),
        LaneNote(pitch=42, start=bar_len * 4, end=bar_len * 4 + sixteenth, velocity=80),
    ]
    out = normalize_bass_lane_notes(
        notes, tempo=tempo, bar_count=bar_count, harmonic_root_pc=6
    )
    last_bar_origin = (bar_count - 1) * bar_len
    final_bar_notes = [n for n in out if n.start >= last_bar_origin - 1e-6]
    assert final_bar_notes
    latest_end = max(float(n.end) for n in out)
    assert latest_end <= loop_end - 1e-5
    assert latest_end >= loop_end - sixteenth - 1e-3


def test_normalize_bytes_clamps_overshoot_and_negative() -> None:
    """Byte path: pretty_midi notes can have negative starts / end past loop_end.

    LaneNote itself enforces start >= 0 at the model level, so the
    byte-level helper is the only path where these clamps matter.
    """
    tempo = 120
    bar_count = 4
    spb = 60.0 / tempo
    bar_len = 4.0 * spb
    loop_end = bar_count * bar_len
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    inst = pretty_midi.Instrument(program=33, name="Bass")
    inst.notes.append(pretty_midi.Note(velocity=92, pitch=42, start=-0.05, end=0.4))
    inst.notes.append(
        pretty_midi.Note(velocity=80, pitch=42, start=loop_end - 0.05, end=loop_end + 0.5)
    )
    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    raw = buf.getvalue()
    out_bytes = normalize_bass_loop_bytes(raw, tempo=tempo, bar_count=bar_count, harmonic_root_pc=0)
    out_notes = _bass_notes_from_bytes(out_bytes)
    assert out_notes
    for n in out_notes:
        assert float(n.start) >= 0.0
        assert float(n.end) <= loop_end + 1e-3
        assert float(n.end) > float(n.start)


def test_normalize_bytes_idempotent() -> None:
    tempo = 117
    bar_count = 8
    notes = [
        LaneNote(pitch=42, start=0.4, end=0.6, velocity=92),
        LaneNote(pitch=45, start=8.0, end=8.2, velocity=80),
    ]
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    inst = pretty_midi.Instrument(program=33, name="Bass")
    for n in notes:
        inst.notes.append(
            pretty_midi.Note(velocity=n.velocity, pitch=n.pitch, start=n.start, end=n.end)
        )
    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    raw = buf.getvalue()
    once = normalize_bass_loop_bytes(raw, tempo=tempo, bar_count=bar_count, harmonic_root_pc=6)
    twice = normalize_bass_loop_bytes(once, tempo=tempo, bar_count=bar_count, harmonic_root_pc=6)
    n1 = _bass_notes_from_bytes(once)
    n2 = _bass_notes_from_bytes(twice)
    assert len(n1) == len(n2)
    for a, b in zip(n1, n2):
        assert float(a.start) == pytest.approx(float(b.start), abs=1e-6)
        assert float(a.end) == pytest.approx(float(b.end), abs=1e-6)
        assert int(a.pitch) == int(b.pitch)


# --------------------------------------------------------------------------- #
# Integration tests via the API. These exercise every storage/egress path.
# --------------------------------------------------------------------------- #


def _source_analysis_with_offset_anchor(bar_count: int, *, tempo: int, anchor_sec: float) -> SourceAnalysis:
    """Source analysis fixture whose downbeat is shifted right by anchor_sec.

    This is what reproduces the long-standing leading-gap bug: every
    generator path multiplies bar 0 starts by this anchor, and Logic
    sees the resulting silence at the head of the region.
    """
    pressure = [[0.32] * 16 for _ in range(bar_count)]
    kick = [[0.08] * 16 for _ in range(bar_count)]
    snare = [[0.06] * 16 for _ in range(bar_count)]
    for row in range(bar_count):
        for slot in (0, 3, 6, 10, 14):
            pressure[row][slot] = 0.72
            kick[row][slot] = 0.55
    spb = 60.0 / float(tempo)
    bar_len = 4.0 * spb
    return SourceAnalysis(
        source_lane="reference_audio",
        tempo=tempo,
        tempo_estimate_bpm=float(tempo),
        tempo_confidence=0.85,
        beat_grid_seconds=[anchor_sec + spb * i for i in range(bar_count * 4)],
        bar_starts_seconds=[anchor_sec + bar_len * i for i in range(bar_count)],
        beat_phase_offset_beats=0,
        beat_phase_scores=[1.0, 0.0, 0.0, 0.0],
        beat_phase_confidence=0.8,
        phase_offset_used_for_generation_beats=0,
        bar_start_anchor_used_seconds=float(anchor_sec),
        generation_aligned_to_anchor=True,
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


def _isolate(tmp_path: Path) -> TestClient:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    return TestClient(app)


def test_default_generated_bass_midi_first_note_at_zero(tmp_path: Path) -> None:
    """Default lane: plain Generate Session, no source override."""
    client = _isolate(tmp_path)
    tempo = 108
    bar_count = 8
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": tempo,
            "key": "C",
            "scale": "major",
            "bar_count": bar_count,
            "bass_style": "supportive",
            "bass_engine": "baseline",
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    gen = client.post(f"/api/sessions/{session_id}/generate")
    assert gen.status_code == 200
    midi_resp = client.get(f"/api/sessions/{session_id}/midi/bass")
    assert midi_resp.status_code == 200
    _assert_loop_boundary(
        midi_resp.content, tempo=tempo, bar_count=bar_count, label="default lane"
    )


def test_source_aware_bass_midi_first_note_at_zero(tmp_path: Path) -> None:
    """Source-aware default lane with a non-zero detected bar anchor."""
    client = _isolate(tmp_path)
    tempo = 100
    bar_count = 8
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": tempo,
            "key": "F#",
            "scale": "natural_minor",
            "bar_count": bar_count,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "chord_progression": ["F#m7"] * bar_count,
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    session_routes._SESSIONS[session_id].source_analysis_override = (  # type: ignore[attr-defined]
        _source_analysis_with_offset_anchor(bar_count, tempo=tempo, anchor_sec=0.4)
    )
    gen = client.post(f"/api/sessions/{session_id}/generate")
    assert gen.status_code == 200
    midi_resp = client.get(f"/api/sessions/{session_id}/midi/bass")
    assert midi_resp.status_code == 200
    _assert_loop_boundary(
        midi_resp.content, tempo=tempo, bar_count=bar_count, label="source-aware lane"
    )


def test_labelled_vocabulary_candidate_midi_first_note_at_zero(tmp_path: Path) -> None:
    """Labelled vocabulary candidate path under non-zero source anchor."""
    client = _isolate(tmp_path)
    tempo = 100
    bar_count = 8
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": tempo,
            "key": "F#",
            "scale": "natural_minor",
            "bar_count": bar_count,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "chord_progression": ["F#m7"] * bar_count,
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    session_routes._SESSIONS[session_id].source_analysis_override = (  # type: ignore[attr-defined]
        _source_analysis_with_offset_anchor(bar_count, tempo=tempo, anchor_sec=0.4)
    )
    run = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 5, "seed": 9090},
    )
    assert run.status_code == 200
    body = run.json()
    labelled = [t for t in body["takes"] if t.get("template_id")]
    assert labelled, "expected labelled vocabulary takes"
    take_id = labelled[0]["take_id"]
    midi = client.get(
        f"/api/sessions/{session_id}/bass-candidates/{body['run_id']}/{take_id}"
    )
    assert midi.status_code == 200
    _assert_loop_boundary(
        midi.content, tempo=tempo, bar_count=bar_count, label="labelled candidate"
    )


def test_promoted_clean_and_performance_midi_first_note_at_zero(tmp_path: Path) -> None:
    """Promoted candidate clean and performance bytes both first note at 0.0."""
    client = _isolate(tmp_path)
    tempo = 100
    bar_count = 8
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": tempo,
            "key": "F#",
            "scale": "natural_minor",
            "bar_count": bar_count,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "chord_progression": ["F#m7"] * bar_count,
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    session_routes._SESSIONS[session_id].source_analysis_override = (  # type: ignore[attr-defined]
        _source_analysis_with_offset_anchor(bar_count, tempo=tempo, anchor_sec=0.4)
    )
    # Generate the session (so a performance overlay path is exercised on promote).
    gen = client.post(f"/api/sessions/{session_id}/generate")
    assert gen.status_code == 200
    run = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 5, "seed": 1234},
    )
    assert run.status_code == 200
    body = run.json()
    labelled = [t for t in body["takes"] if t.get("template_id")]
    assert labelled
    take_id = labelled[0]["take_id"]
    promote = client.post(
        f"/api/sessions/{session_id}/bass-candidates/{body['run_id']}/{take_id}/promote"
    )
    assert promote.status_code == 200

    clean = client.get(f"/api/sessions/{session_id}/midi/bass")
    assert clean.status_code == 200
    _assert_loop_boundary(
        clean.content, tempo=tempo, bar_count=bar_count, label="promoted clean"
    )

    perf = client.get(f"/api/sessions/{session_id}/midi/bass?mode=performance")
    assert perf.status_code == 200
    _assert_loop_boundary(
        perf.content, tempo=tempo, bar_count=bar_count, label="promoted performance"
    )


def test_exported_loop_length_close_to_bars_times_quarter(tmp_path: Path) -> None:
    """Exported 8-bar bass MIDI fits within bars * 4 * 60/tempo."""
    client = _isolate(tmp_path)
    tempo = 117
    bar_count = 8
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": tempo,
            "key": "F#",
            "scale": "natural_minor",
            "bar_count": bar_count,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "chord_progression": ["F#m7"] * bar_count,
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    session_routes._SESSIONS[session_id].source_analysis_override = (  # type: ignore[attr-defined]
        _source_analysis_with_offset_anchor(bar_count, tempo=tempo, anchor_sec=0.35)
    )
    gen = client.post(f"/api/sessions/{session_id}/generate")
    assert gen.status_code == 200
    midi = client.get(f"/api/sessions/{session_id}/midi/bass")
    assert midi.status_code == 200
    notes = _bass_notes_from_bytes(midi.content)
    assert notes
    spb = 60.0 / tempo
    sixteenth = spb / 4.0
    loop_end = bar_count * 4.0 * spb
    latest_end = max(float(n.end) for n in notes)
    assert latest_end <= loop_end + 1e-3
    assert (loop_end - latest_end) <= sixteenth + 1e-3


def test_no_negative_starts_no_overshoot_after_normalize(tmp_path: Path) -> None:
    """Hard invariant across the API path."""
    client = _isolate(tmp_path)
    tempo = 100
    bar_count = 8
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": tempo,
            "key": "F#",
            "scale": "natural_minor",
            "bar_count": bar_count,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "chord_progression": ["F#m7"] * bar_count,
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    session_routes._SESSIONS[session_id].source_analysis_override = (  # type: ignore[attr-defined]
        _source_analysis_with_offset_anchor(bar_count, tempo=tempo, anchor_sec=0.6)
    )
    gen = client.post(f"/api/sessions/{session_id}/generate")
    assert gen.status_code == 200
    midi = client.get(f"/api/sessions/{session_id}/midi/bass")
    assert midi.status_code == 200
    notes = _bass_notes_from_bytes(midi.content)
    assert notes
    spb = 60.0 / tempo
    loop_end = bar_count * 4.0 * spb
    for n in notes:
        assert float(n.start) >= 0.0
        assert float(n.end) <= loop_end + 1e-3
        assert float(n.start) < loop_end


def test_final_bar_has_at_least_one_note(tmp_path: Path) -> None:
    client = _isolate(tmp_path)
    tempo = 117
    bar_count = 8
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": tempo,
            "key": "F#",
            "scale": "natural_minor",
            "bar_count": bar_count,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "chord_progression": ["F#m7"] * bar_count,
        },
    )
    assert created.status_code == 200
    session_id = created.json()["session"]["id"]
    session_routes._SESSIONS[session_id].source_analysis_override = (  # type: ignore[attr-defined]
        _source_analysis_with_offset_anchor(bar_count, tempo=tempo, anchor_sec=0.4)
    )
    gen = client.post(f"/api/sessions/{session_id}/generate")
    assert gen.status_code == 200
    midi = client.get(f"/api/sessions/{session_id}/midi/bass")
    assert midi.status_code == 200
    notes = _bass_notes_from_bytes(midi.content)
    spb = 60.0 / tempo
    bar_len = 4.0 * spb
    loop_end = bar_count * bar_len
    last_origin = (bar_count - 1) * bar_len
    final = [n for n in notes if last_origin - 1e-6 <= float(n.start) < loop_end]
    assert final, "final bar must have at least one note"
