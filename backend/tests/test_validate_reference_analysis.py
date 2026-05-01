"""Comparator tests for the v0.3a validation runner.

These tests exercise the field-comparison logic without invoking
librosa or touching audio: a synthetic ``SourceAnalysis`` is built via
the existing Pydantic model, and ``_fields_for_analysis`` is asked to
verdict it against various manifest-shaped clip dicts.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from app.models.session import SourceAnalysis


_RUNNER_PATH = (
    Path(__file__).resolve().parents[1] / "tools" / "validate_reference_analysis.py"
)
_RUNNER_MODULE_NAME = "_v0_3a_validate_reference_analysis"


def _load_runner():
    if _RUNNER_MODULE_NAME in sys.modules:
        return sys.modules[_RUNNER_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(_RUNNER_MODULE_NAME, _RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[_RUNNER_MODULE_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_source_analysis(
    *,
    tempo_estimate_bpm: float = 120.0,
    tonal_pc: int = 0,
    scale_mode: str = "minor",
    bar_starts_count: int = 8,
) -> SourceAnalysis:
    bar_starts = [round(i * 2.0, 6) for i in range(bar_starts_count)]
    return SourceAnalysis(
        source_lane="reference_audio",
        tempo=110,
        tempo_estimate_bpm=float(tempo_estimate_bpm),
        tempo_confidence=0.7,
        beat_grid_seconds=[round(i * 0.5, 6) for i in range(bar_starts_count * 4)],
        bar_starts_seconds=bar_starts,
        beat_phase_offset_beats=0,
        beat_phase_scores=[1.0, 0.0, 0.0, 0.0],
        beat_phase_confidence=0.6,
        phase_offset_used_for_generation_beats=0,
        bar_start_anchor_used_seconds=0.0,
        generation_aligned_to_anchor=False,
        downbeat_guess_bar_index=0,
        downbeat_confidence=0.6,
        bar_start_confidence=0.55,
        tonal_center_pc_guess=int(tonal_pc),
        tonal_center_confidence=0.7,
        scale_mode_guess=str(scale_mode),
        scale_mode_confidence=0.65,
        sections=[],
        bar_energy=[0.0] * bar_starts_count,
        bar_accent_profile=[0.0] * bar_starts_count,
        bar_confidence_profile=[0.0] * bar_starts_count,
    )


def test_canonical_clip_passes_all_fields() -> None:
    runner = _load_runner()
    sa = _make_source_analysis(
        tempo_estimate_bpm=120.4, tonal_pc=0, scale_mode="minor", bar_starts_count=8
    )
    clip = {
        "id": "vp01",
        "file": "clips/vp01.wav",
        "expected": {
            "expected_tempo_bpm": 120,
            "tempo_tolerance_bpm": 2,
            "expected_key_pc": 0,
            "expected_scale_mode": "minor",
            "expected_bar_count": 8,
        },
    }
    fields = runner._fields_for_analysis(clip, sa, default_tempo_tol=3.0)
    by_name = {f.name: f for f in fields}
    assert by_name["tempo_bpm"].status == "PASS"
    assert by_name["key_pc"].status == "PASS"
    assert by_name["scale_mode"].status == "PASS"
    assert by_name["bar_count"].status == "PASS"
    assert "kick_slots" not in by_name


def test_legacy_field_names_are_accepted() -> None:
    runner = _load_runner()
    sa = _make_source_analysis(tempo_estimate_bpm=120.0, tonal_pc=9, scale_mode="minor")
    clip = {
        "filename": "clips/legacy.wav",
        "expected": {
            "tempo_bpm_approx": 120,
            "tempo_tolerance_bpm": 3,
            "expected_key": "A",
            "scale_mode_guess": "minor",
        },
    }
    fields = runner._fields_for_analysis(clip, sa, default_tempo_tol=3.0)
    by_name = {f.name: f for f in fields}
    assert by_name["tempo_bpm"].status == "PASS"
    assert by_name["key_pc"].status == "PASS"
    assert by_name["scale_mode"].status == "PASS"


def test_tempo_outside_tolerance_fails() -> None:
    runner = _load_runner()
    sa = _make_source_analysis(tempo_estimate_bpm=128.0)
    clip = {
        "id": "x",
        "file": "x.wav",
        "expected": {"expected_tempo_bpm": 120, "tempo_tolerance_bpm": 2},
    }
    fields = runner._fields_for_analysis(clip, sa, default_tempo_tol=3.0)
    by_name = {f.name: f for f in fields}
    assert by_name["tempo_bpm"].status == "FAIL"


def test_unspecified_expectations_report_na_not_pass() -> None:
    runner = _load_runner()
    sa = _make_source_analysis()
    clip = {"id": "x", "file": "x.wav", "expected": {}}
    fields = runner._fields_for_analysis(clip, sa, default_tempo_tol=3.0)
    statuses = {f.name: f.status for f in fields}
    assert statuses == {
        "tempo_bpm": "N/A",
        "key_pc": "N/A",
        "scale_mode": "N/A",
        "bar_count": "N/A",
    }


def test_kick_slots_declaration_reports_na_with_reason() -> None:
    runner = _load_runner()
    sa = _make_source_analysis()
    clip = {
        "id": "x",
        "file": "x.wav",
        "expected": {"expected_kick_slots": [0, 4, 6, 10]},
    }
    fields = runner._fields_for_analysis(clip, sa, default_tempo_tol=3.0)
    by_name = {f.name: f for f in fields}
    assert "kick_slots" in by_name
    assert by_name["kick_slots"].status == "N/A"
    assert "kick-slot detection" in by_name["kick_slots"].detail


def test_missing_audio_file_yields_skip(tmp_path: Path) -> None:
    runner = _load_runner()
    clip = {"id": "absent", "file": "clips/does_not_exist.wav"}
    verdict = runner._evaluate_clip(clip, base_dir=tmp_path, default_tempo_tol=3.0)
    assert verdict.status == "SKIP"
    assert "missing audio file" in verdict.summary
    assert verdict.fields == ()


def test_invalid_expected_key_string_falls_back_to_na() -> None:
    runner = _load_runner()
    sa = _make_source_analysis(tonal_pc=0)
    clip = {
        "id": "x",
        "file": "x.wav",
        "expected": {"expected_key": "  "},
    }
    fields = runner._fields_for_analysis(clip, sa, default_tempo_tol=3.0)
    by_name = {f.name: f for f in fields}
    assert by_name["key_pc"].status == "N/A"
