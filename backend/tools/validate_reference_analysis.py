#!/usr/bin/env python3
"""Validation runner: in-process source-analysis check against a manifest.

Reads ``backend/data/validation_pack/manifest.json`` (or another manifest
via ``--manifest``), runs ``analyze_reference_audio`` directly on each
declared clip, compares the canonical v0.3a expectation fields, and
prints a per-clip table with per-field PASS / FAIL / N/A plus an overall
verdict.

This runner is the v0.3a daily driver: it does **not** require the API
server to be running. The HTTP-based runner at
``backend/tools/run_validation_pack.py`` is preserved for end-to-end
checks against a live backend.

Manifest schema (canonical v0.3a, all fields optional unless noted):

    {
      "defaults": { "tempo_tolerance_bpm": 3 },
      "clips": [
        {
          "id":                   "vp01_clear_rhythm_harmony",
          "file":                 "clips/vp01_clear_rhythm_harmony.wav",
          "description":          "Clear groove, clear C minor harmony.",
          "expected_tempo_bpm":   120,
          "tempo_tolerance_bpm":  2,
          "expected_key_pc":      0,
          "expected_scale_mode":  "minor",
          "expected_bar_count":   8,
          "expected_kick_slots":  [0, 4, 6, 10],
          "notes":                "Studio-quality reference."
        }
      ]
    }

For backward compatibility the runner also accepts the older field names
already used in the existing manifest:
``tempo_bpm_approx``, ``expected_key`` (e.g. ``"F#"``), ``scale_mode_guess``,
``filename`` (in place of ``file``).

Conservative-by-design:
* Missing audio file → ``SKIP`` (does not crash).
* Field the analyser does not yet surface (e.g. ``expected_kick_slots``)
  → reported as ``N/A`` rather than fabricated.
* Field with no expectation declared → ``N/A``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO_BACKEND = Path(__file__).resolve().parents[1]
if str(_REPO_BACKEND) not in sys.path:
    sys.path.insert(0, str(_REPO_BACKEND))

from app.utils import music_theory as mt  # noqa: E402

_PC_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")


@dataclass(frozen=True)
class FieldVerdict:
    name: str
    detected: str
    expected: str
    status: str  # PASS | FAIL | N/A
    detail: str = ""


@dataclass(frozen=True)
class ClipVerdict:
    clip_id: str
    file_rel: str
    fields: tuple[FieldVerdict, ...]
    status: str  # PASS | FAIL | SKIP
    summary: str


def _pc_name(pc: int) -> str:
    return _PC_NAMES[pc % 12]


def _expected_tempo(clip: dict, *, default_tol: float) -> tuple[float | None, float]:
    exp = clip.get("expected") or {}
    bpm = exp.get("expected_tempo_bpm", clip.get("expected_tempo_bpm"))
    if bpm is None:
        bpm = exp.get("tempo_bpm_approx", clip.get("tempo_bpm_approx"))
    tol = exp.get("tempo_tolerance_bpm", clip.get("tempo_tolerance_bpm"))
    return (None if bpm is None else float(bpm)), (default_tol if tol is None else float(tol))


def _expected_key_pc(clip: dict) -> int | None:
    exp = clip.get("expected") or {}
    pc = exp.get("expected_key_pc", clip.get("expected_key_pc"))
    if pc is not None:
        try:
            return int(pc) % 12
        except (TypeError, ValueError):
            return None
    name = exp.get("expected_key") or clip.get("expected_key") or exp.get("expected_tonal_center")
    if name is None:
        return None
    try:
        return mt.key_root_pc(str(name))
    except ValueError:
        return None


def _expected_scale_mode(clip: dict) -> str | None:
    exp = clip.get("expected") or {}
    mode = (
        exp.get("expected_scale_mode")
        or clip.get("expected_scale_mode")
        or exp.get("scale_mode_guess")
        or clip.get("scale_mode_guess")
    )
    if mode is None:
        return None
    return str(mode).strip().lower() or None


def _expected_bar_count(clip: dict) -> int | None:
    exp = clip.get("expected") or {}
    v = exp.get("expected_bar_count", clip.get("expected_bar_count"))
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _has_expected_kick_slots(clip: dict) -> bool:
    exp = clip.get("expected") or {}
    return ("expected_kick_slots" in exp) or ("expected_kick_slots" in clip)


def _fields_for_analysis(clip: dict, sa: Any, *, default_tempo_tol: float) -> tuple[FieldVerdict, ...]:
    """Build per-field verdicts. ``sa`` is a ``SourceAnalysis`` (Pydantic) or compatible."""
    fields: list[FieldVerdict] = []

    exp_bpm, exp_tol = _expected_tempo(clip, default_tol=default_tempo_tol)
    if exp_bpm is None:
        fields.append(FieldVerdict("tempo_bpm", f"{float(sa.tempo_estimate_bpm):.2f}", "—", "N/A"))
    else:
        diff = abs(float(sa.tempo_estimate_bpm) - exp_bpm)
        status = "PASS" if diff <= exp_tol else "FAIL"
        fields.append(
            FieldVerdict(
                "tempo_bpm",
                f"{float(sa.tempo_estimate_bpm):.2f}",
                f"{exp_bpm:.2f}±{exp_tol:.1f}",
                status,
                f"|delta|={diff:.2f}",
            )
        )

    exp_pc = _expected_key_pc(clip)
    detected_pc = int(sa.tonal_center_pc_guess)
    if exp_pc is None:
        fields.append(FieldVerdict("key_pc", f"{detected_pc} ({_pc_name(detected_pc)})", "—", "N/A"))
    else:
        status = "PASS" if detected_pc == exp_pc else "FAIL"
        fields.append(
            FieldVerdict(
                "key_pc",
                f"{detected_pc} ({_pc_name(detected_pc)})",
                f"{exp_pc} ({_pc_name(exp_pc)})",
                status,
                f"conf={float(sa.tonal_center_confidence):.2f}",
            )
        )

    exp_mode = _expected_scale_mode(clip)
    detected_mode = (str(sa.scale_mode_guess).strip().lower() or "—")
    if exp_mode is None:
        fields.append(FieldVerdict("scale_mode", detected_mode, "—", "N/A"))
    else:
        status = "PASS" if detected_mode == exp_mode else "FAIL"
        fields.append(
            FieldVerdict(
                "scale_mode",
                detected_mode,
                exp_mode,
                status,
                f"conf={float(sa.scale_mode_confidence):.2f}",
            )
        )

    exp_bars = _expected_bar_count(clip)
    detected_bars = len(list(sa.bar_starts_seconds))
    if exp_bars is None:
        fields.append(FieldVerdict("bar_count", str(detected_bars), "—", "N/A"))
    else:
        status = "PASS" if detected_bars == exp_bars else "FAIL"
        fields.append(FieldVerdict("bar_count", str(detected_bars), str(exp_bars), status))

    if _has_expected_kick_slots(clip):
        fields.append(
            FieldVerdict(
                "kick_slots",
                "—",
                "(declared)",
                "N/A",
                "analyser does not surface kick-slot detection in v0.3a",
            )
        )

    return tuple(fields)


def _evaluate_clip(clip: dict, base_dir: Path, *, default_tempo_tol: float) -> ClipVerdict:
    raw_file = clip.get("file") or clip.get("filename")
    file_rel = str(raw_file or "")
    cid = str(clip.get("id") or (Path(file_rel).stem if file_rel else "unknown"))

    if not raw_file:
        return ClipVerdict(cid, file_rel, (), "SKIP", "no file path declared")

    abs_path = (base_dir / raw_file).resolve()
    if not abs_path.is_file():
        return ClipVerdict(cid, file_rel, (), "SKIP", f"missing audio file: {raw_file}")

    # Lazy import: only require librosa/numpy when actually analysing a clip.
    from app.services.audio_source_analysis import analyze_reference_audio

    expected_bars = _expected_bar_count(clip) or 8
    try:
        result = analyze_reference_audio(
            audio_path=abs_path,
            session_tempo=110,
            bar_count=expected_bars,
            session_key="C",
            session_scale="major",
        )
    except Exception as exc:
        return ClipVerdict(cid, file_rel, (), "FAIL", f"analysis error: {exc}")

    sa = result.source_analysis
    fields = _fields_for_analysis(clip, sa, default_tempo_tol=default_tempo_tol)
    summary = (
        f"tempo_conf={float(sa.tempo_confidence):.2f} "
        f"key_conf={float(sa.tonal_center_confidence):.2f} "
        f"mode_conf={float(sa.scale_mode_confidence):.2f} "
        f"phase_conf={float(sa.beat_phase_confidence):.2f} "
        f"bar_start_conf={float(sa.bar_start_confidence):.2f} "
        f"head_trim={float(result.head_trim_seconds):.3f}s"
    )
    overall = "FAIL" if any(f.status == "FAIL" for f in fields) else "PASS"
    return ClipVerdict(cid, file_rel, fields, overall, summary)


def _print_clip(verdict: ClipVerdict) -> None:
    print(f"\n[{verdict.status}] {verdict.clip_id}  ({verdict.file_rel})")
    if verdict.status == "SKIP":
        print(f"    SKIP: {verdict.summary}")
        return
    if verdict.summary:
        print(f"    {verdict.summary}")
    if not verdict.fields:
        return
    name_w = max(len(f.name) for f in verdict.fields)
    det_w = max(len(f.detected) for f in verdict.fields)
    exp_w = max(len(f.expected) for f in verdict.fields)
    for f in verdict.fields:
        line = (
            f"    {f.name.ljust(name_w)}  "
            f"detected={f.detected.ljust(det_w)}  "
            f"expected={f.expected.ljust(exp_w)}  "
            f"{f.status}"
        )
        if f.detail:
            line += f"  [{f.detail}]"
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the v0.3a in-process validation pack.")
    default_manifest = _REPO_BACKEND / "data" / "validation_pack" / "manifest.json"
    parser.add_argument("--manifest", default=str(default_manifest), help="Path to validation manifest JSON.")
    parser.add_argument("--clip-id", default=None, help="If set, only run the matching clip id.")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest).resolve()
    if not manifest_path.is_file():
        print(f"manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    defaults = manifest.get("defaults") or {}
    default_tempo_tol = float(defaults.get("tempo_tolerance_bpm") or 3.0)
    base_dir = manifest_path.parent

    clips: list[dict[str, Any]] = list(manifest.get("clips") or [])
    if args.clip_id:
        clips = [c for c in clips if (c.get("id") or "") == args.clip_id]
        if not clips:
            print(f"no clip with id={args.clip_id!r}", file=sys.stderr)
            return 2
    if not clips:
        print("no clips listed in manifest")
        return 0

    print(f"Validation pack: {manifest_path}")
    print(f"Clips: {len(clips)}  (default tempo tolerance ±{default_tempo_tol:.1f} BPM)")

    counts = {"PASS": 0, "FAIL": 0, "SKIP": 0}
    verdicts: list[ClipVerdict] = []
    for clip in clips:
        verdict = _evaluate_clip(clip, base_dir, default_tempo_tol=default_tempo_tol)
        counts[verdict.status] = counts.get(verdict.status, 0) + 1
        verdicts.append(verdict)
        _print_clip(verdict)

    print(
        f"\nSummary: PASS={counts.get('PASS', 0)} "
        f"FAIL={counts.get('FAIL', 0)} "
        f"SKIP={counts.get('SKIP', 0)} "
        f"(of {len(verdicts)} clip(s))"
    )
    return 1 if counts.get("FAIL", 0) > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
