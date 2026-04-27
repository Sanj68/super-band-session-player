#!/usr/bin/env python3
"""Run validation-pack clips through upload + analyze endpoints."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


KEY_TO_PC = {
    "C": 0,
    "C#": 1,
    "DB": 1,
    "D": 2,
    "D#": 3,
    "EB": 3,
    "E": 4,
    "F": 5,
    "F#": 6,
    "GB": 6,
    "G": 7,
    "G#": 8,
    "AB": 8,
    "A": 9,
    "A#": 10,
    "BB": 10,
    "B": 11,
}


def _post_json(url: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_multipart_file(url: str, field_name: str, filename: str, payload: bytes, content_type: str) -> dict:
    boundary = f"----sbsp-{uuid.uuid4().hex}"
    body = bytearray()
    body.extend(f"--{boundary}\r\n".encode("utf-8"))
    body.extend(
        (
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8")
    )
    body.extend(payload)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run validation clips through /reference-audio and /analyze-audio.")
    p.add_argument("--api-base", default="http://127.0.0.1:8000", help="Base API URL.")
    p.add_argument(
        "--manifest",
        default=str(pathlib.Path(__file__).resolve().parents[1] / "data" / "validation_pack" / "manifest.json"),
        help="Path to validation manifest JSON.",
    )
    p.add_argument("--tempo", type=int, default=110, help="Session tempo used for analysis session.")
    p.add_argument("--key", default="C", help="Session key used for analysis session.")
    p.add_argument("--scale", default="major", help="Session scale used for analysis session.")
    p.add_argument("--bars", type=int, default=8, help="Session bar count used for analysis session.")
    p.add_argument(
        "--strict-warn",
        action="store_true",
        help="Exit non-zero if any WARN is produced (default only fails on FAIL).",
    )
    return p.parse_args()


def _num(v: object) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_pc(expected_key: object) -> int | None:
    if expected_key is None:
        return None
    s = str(expected_key).strip().upper()
    if not s:
        return None
    return KEY_TO_PC.get(s)


def _evaluate_clip(
    clip_id: str,
    expected: dict,
    source_analysis: dict,
    ref_audio: dict,
    defaults: dict,
) -> tuple[list[str], list[str]]:
    fails: list[str] = []
    warns: list[str] = []

    tempo_est = _num(source_analysis.get("tempo_estimate_bpm"))
    tempo_conf = _num(source_analysis.get("tempo_confidence"))
    bar_start_conf = _num(source_analysis.get("bar_start_confidence"))
    tonal_pc = source_analysis.get("tonal_center_pc_guess")
    mode_guess = source_analysis.get("scale_mode_guess")
    sections = source_analysis.get("sections") or []
    trim_sec = _num(ref_audio.get("head_trim_seconds"))

    approx = _num(expected.get("tempo_bpm_approx"))
    tol = _num(expected.get("tempo_tolerance_bpm")) or 8.0
    tmin = _num(expected.get("tempo_bpm_min"))
    tmax = _num(expected.get("tempo_bpm_max"))
    if approx is not None and tempo_est is not None:
        if abs(tempo_est - approx) > tol:
            fails.append(f"tempo {tempo_est:.2f} outside approx {approx:.2f}±{tol:.2f}")
    if tmin is not None and tempo_est is not None and tempo_est < tmin:
        fails.append(f"tempo {tempo_est:.2f} < min {tmin:.2f}")
    if tmax is not None and tempo_est is not None and tempo_est > tmax:
        fails.append(f"tempo {tempo_est:.2f} > max {tmax:.2f}")

    expected_pc = _to_pc(expected.get("expected_key"))
    if expected_pc is None:
        v = expected.get("tonal_center_pc_guess")
        expected_pc = int(v) if v is not None else None
    if expected_pc is not None and tonal_pc is not None and int(tonal_pc) != int(expected_pc):
        fails.append(f"tonal_center_pc {tonal_pc} != expected {expected_pc}")

    expected_mode = expected.get("scale_mode_guess")
    if expected_mode is not None and str(expected_mode).strip():
        if str(mode_guess) != str(expected_mode):
            fails.append(f"scale_mode {mode_guess} != expected {expected_mode}")

    expected_sections = expected.get("expected_section_count")
    smin = expected.get("section_count_min")
    smax = expected.get("section_count_max")
    n_sections = len(sections)
    if expected_sections is not None and int(n_sections) != int(expected_sections):
        fails.append(f"section_count {n_sections} != expected {expected_sections}")
    if smin is not None and int(n_sections) < int(smin):
        fails.append(f"section_count {n_sections} < min {smin}")
    if smax is not None and int(n_sections) > int(smax):
        fails.append(f"section_count {n_sections} > max {smax}")

    trim_min = _num(expected.get("head_trim_seconds_min"))
    trim_max = _num(expected.get("head_trim_seconds_max"))
    if trim_min is not None and trim_sec is not None and trim_sec < trim_min:
        fails.append(f"head_trim_seconds {trim_sec:.4f} < min {trim_min:.4f}")
    if trim_max is not None and trim_sec is not None and trim_sec > trim_max:
        fails.append(f"head_trim_seconds {trim_sec:.4f} > max {trim_max:.4f}")

    expect_low = expected.get("expect_low_confidence")
    low_thr = _num(expected.get("low_confidence_threshold"))
    if low_thr is None:
        low_thr = _num(defaults.get("low_confidence_threshold")) or 0.35
    check_val = tempo_conf if tempo_conf is not None else bar_start_conf
    if expect_low is True and check_val is not None and check_val > low_thr:
        warns.append(f"expected low confidence <= {low_thr:.2f}, got {check_val:.3f}")
    if expect_low is False and check_val is not None and check_val <= low_thr:
        warns.append(f"confidence is low ({check_val:.3f}) at threshold {low_thr:.2f}")

    if tempo_est is None:
        fails.append("missing tempo_estimate_bpm in response")
    if trim_sec is None:
        fails.append("missing reference_audio.head_trim_seconds in response")
    if tonal_pc is None:
        warns.append("missing tonal_center_pc_guess in response")

    return fails, warns


def main() -> int:
    args = _parse_args()
    manifest_path = pathlib.Path(args.manifest).resolve()
    if not manifest_path.is_file():
        print(f"Manifest not found: {manifest_path}", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    clips = manifest.get("clips", [])
    defaults = manifest.get("defaults") or {}
    if not clips:
        print("No clips listed in manifest.")
        return 0

    base_dir = manifest_path.parent
    api = args.api_base.rstrip("/")

    print(f"Running validation pack: {manifest_path}")
    print(f"API base: {api}")

    ran = 0
    pass_count = 0
    warn_count = 0
    fail_count = 0
    for clip in clips:
        rel = clip.get("clip_path") or clip.get("filename")
        cid = clip.get("id") or clip.get("filename") or "unknown"
        expected = clip.get("expected") or {}
        clip_path = (base_dir / rel).resolve() if rel else None
        if not clip_path or not clip_path.is_file():
            print(f"- {cid}: SKIP (missing file: {rel})")
            continue
        try:
            create = _post_json(
                f"{api}/api/sessions/",
                {"tempo": args.tempo, "key": args.key, "scale": args.scale, "bar_count": args.bars},
            )
            sid = create["session"]["id"]
            payload = clip_path.read_bytes()
            _post_multipart_file(
                f"{api}/api/sessions/{urllib.parse.quote(sid)}/reference-audio",
                "file",
                clip_path.name,
                payload,
                "audio/wav",
            )
            analyzed = _post_json(f"{api}/api/sessions/{urllib.parse.quote(sid)}/analyze-audio", {})
            sess = analyzed.get("session", analyzed)
            sa = sess["engine_data"]["source_analysis"]
            ra = sess.get("reference_audio", {})
            fails, warns = _evaluate_clip(cid, expected, sa, ra, defaults)
            if fails:
                status = "FAIL"
                fail_count += 1
            elif warns:
                status = "WARN"
                warn_count += 1
            else:
                status = "PASS"
                pass_count += 1
            metrics = (
                f"tempo={sa.get('tempo_estimate_bpm')} "
                f"tempo_conf={sa.get('tempo_confidence')} "
                f"bar_start_conf={sa.get('bar_start_confidence')} "
                f"trim={ra.get('head_trim_seconds')} "
                f"tonal_pc={sa.get('tonal_center_pc_guess')} mode={sa.get('scale_mode_guess')} "
                f"sections={len(sa.get('sections') or [])}"
            )
            print(f"- {cid}: {status} | {metrics}")
            for msg in fails:
                print(f"    FAIL: {msg}")
            for msg in warns:
                print(f"    WARN: {msg}")
            ran += 1
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")
            print(f"- {cid}: FAIL HTTP {e.code} {detail[:220]}")
            fail_count += 1
        except Exception as e:
            print(f"- {cid}: FAIL {e}")
            fail_count += 1
    print(
        f"\nCompleted. Ran {ran} clip(s). "
        f"PASS={pass_count} WARN={warn_count} FAIL={fail_count}"
    )
    if fail_count > 0:
        return 1
    if args.strict_warn and warn_count > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

