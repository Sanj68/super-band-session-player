#!/usr/bin/env python3
"""Generate the v0.3a scenario clips (vp06-vp12) with exact ground truth.

Synthesized with numpy directly (no synth deps), so every expectation in the
manifest is true BY CONSTRUCTION — the same philosophy as Meter Core's
certification signals. Clips land in data/validation_pack/clips/ (untracked;
this script is the reproducible source) and manifest.json is updated in
place, replacing any entry with the same id.

Scenarios per docs/SESSION_PLAYER_BUILD_NOTES.md §19: off-grid live,
click-quantised, half-time, swung, latin, modal vamp, dense keys.
"""

from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np

SR = 44100
RNG = np.random.default_rng(420)

ROOT = Path(__file__).resolve().parents[1] / "data" / "validation_pack"
CLIPS = ROOT / "clips"

# pitch-class helpers
PC = {"C": 0, "C#": 1, "D": 2, "Eb": 3, "E": 4, "F": 5, "F#": 6,
      "G": 7, "Ab": 8, "A": 9, "Bb": 10, "B": 11}


def hz(pc: int, octave: int) -> float:
    midi = 12 * (octave + 1) + pc
    return 440.0 * 2 ** ((midi - 69) / 12)


def kick(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    f = 110 * np.exp(-t * 18) + 38
    env = np.exp(-t * 14)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env


def snare(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    noise = RNG.standard_normal(n)
    # crude bandpass via diff (HP) + cumulative mean (LP)
    noise = np.diff(noise, prepend=0.0)
    tone = np.sin(2 * np.pi * 190 * t)
    return (0.7 * noise + 0.4 * tone) * np.exp(-t * 22)


def hat(n: int) -> np.ndarray:
    t = np.arange(n) / SR
    noise = np.diff(RNG.standard_normal(n), prepend=0.0)
    return noise * np.exp(-t * 60) * 0.5


def saw_note(freq: float, n: int, detune: float = 0.4) -> np.ndarray:
    t = np.arange(n) / SR
    out = np.zeros(n)
    for d in (-detune, 0.0, detune):
        f = freq * 2 ** (d / 1200)
        # band-limited-ish saw: first 12 harmonics
        for k in range(1, 13):
            if k * f > SR / 2.2:
                break
            out += np.sin(2 * np.pi * k * f * t) / k
    env = np.minimum(1.0, np.arange(n) / (0.01 * SR)) * np.exp(-t * 2.2)
    return out / 18 * env


def bass_note(freq: float, n: int) -> np.ndarray:
    t = np.arange(n) / SR
    sig = np.sin(2 * np.pi * freq * t) + 0.3 * np.sin(2 * np.pi * 2 * freq * t)
    env = np.minimum(1.0, np.arange(n) / (0.005 * SR)) * np.exp(-t * 3.5)
    return sig * env * 0.8


def place(buf: np.ndarray, sig: np.ndarray, at_s: float, gain: float = 1.0) -> None:
    i = max(0, int(at_s * SR))
    j = min(len(buf), i + len(sig))
    if j > i:
        buf[i:j] += sig[: j - i] * gain


def chord(buf: np.ndarray, pcs: list[int], octave: int, at_s: float,
          dur_s: float, gain: float = 1.0) -> None:
    n = int(dur_s * SR)
    for p in pcs:
        place(buf, saw_note(hz(p, octave), n), at_s, gain / len(pcs))


def write_wav(name: str, buf: np.ndarray) -> None:
    peak = np.max(np.abs(buf)) or 1.0
    pcm = (buf / peak * 0.7 * 32767).astype(np.int16)
    CLIPS.mkdir(parents=True, exist_ok=True)
    with wave.open(str(CLIPS / name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(f"wrote clips/{name}  ({len(buf)/SR:.1f}s)")


def beat_grid(bpm: float, bars: int, beats_per_bar: int = 4):
    beat = 60.0 / bpm
    total = bars * beats_per_bar * beat
    buf = np.zeros(int(total * SR) + SR // 4)
    return buf, beat


# ---- scenario builders -----------------------------------------------------

def vp06_offgrid_live() -> dict:
    """96 BPM live feel: human jitter on every hit, A minor pad."""
    bpm, bars = 96.0, 8
    buf, beat = beat_grid(bpm, bars)
    for bar in range(bars):
        t0 = bar * 4 * beat
        for b, drum in ((0, "k"), (1, "s"), (2, "k"), (2.5, "k"), (3, "s")):
            jit = float(RNG.normal(0, 0.018))  # ±18ms sigma — live but in-pocket
            at = t0 + b * beat + jit
            if at < 0:
                continue
            place(buf, kick(SR // 3) if drum == "k" else snare(SR // 4), at)
        for eighth in range(8):
            place(buf, hat(SR // 10), t0 + eighth * beat / 2 + float(RNG.normal(0, 0.01)), 0.5)
        chord(buf, [PC["A"], PC["C"], PC["E"]], 3, t0, 4 * beat, 0.8)
        place(buf, bass_note(hz(PC["A"], 1), int(beat * SR * 2)), t0)
    write_wav("vp06_offgrid_live.wav", buf)
    return {
        "id": "vp06_offgrid_live",
        "file": "clips/vp06_offgrid_live.wav",
        "filename": "clips/vp06_offgrid_live.wav",
        "description": "Off-grid live feel: every hit human-jittered (sigma 18ms), A minor.",
        "notes": "Synthesized by tools/generate_validation_clips.py — truth by construction.",
        "expected": {
            "expected_tempo_bpm": 96, "tempo_bpm_approx": 96,
            "tempo_tolerance_bpm": 3,
            "expected_key": "A", "expected_key_pc": 9,
            "expected_scale_mode": "minor", "scale_mode_guess": "minor",
        },
    }


def vp07_click_quantised() -> dict:
    """128 BPM rigid four-on-floor, F major — tight tolerance."""
    bpm, bars = 128.0, 8
    buf, beat = beat_grid(bpm, bars)
    for bar in range(bars):
        t0 = bar * 4 * beat
        for b in range(4):
            place(buf, kick(SR // 3), t0 + b * beat)
            place(buf, hat(SR // 12), t0 + b * beat + beat / 2, 0.6)
        place(buf, snare(SR // 4), t0 + 1 * beat)
        place(buf, snare(SR // 4), t0 + 3 * beat)
        root = [PC["F"], PC["A"], PC["C"]] if bar % 2 == 0 else [PC["Bb"], PC["D"], PC["F"]]
        chord(buf, root, 4, t0, 4 * beat, 0.7)
        place(buf, bass_note(hz(PC["F"] if bar % 2 == 0 else PC["Bb"], 1), int(beat * SR)), t0)
    write_wav("vp07_click_quantised.wav", buf)
    return {
        "id": "vp07_click_quantised",
        "file": "clips/vp07_click_quantised.wav",
        "filename": "clips/vp07_click_quantised.wav",
        "description": "Click-quantised four-on-floor at 128, F major (F-Bb vamp).",
        "notes": "Machine-tight grid; tempo must pass at ±2.",
        "expected": {
            "expected_tempo_bpm": 128, "tempo_bpm_approx": 128,
            "tempo_tolerance_bpm": 2,
            "expected_key": "F", "expected_key_pc": 5,
            "expected_scale_mode": "major", "scale_mode_guess": "major",
        },
    }


def vp08_halftime() -> dict:
    """140 BPM grid, snare only on beat 3 — classic half-time trap feel."""
    bpm, bars = 140.0, 8
    buf, beat = beat_grid(bpm, bars)
    for bar in range(bars):
        t0 = bar * 4 * beat
        place(buf, kick(SR // 3), t0)
        place(buf, kick(SR // 3), t0 + 1.75 * beat, 0.8)
        place(buf, snare(SR // 4), t0 + 2 * beat)  # the half-time snare
        for sixteenth in range(16):
            if sixteenth % 2 == 0 or RNG.random() < 0.3:
                place(buf, hat(SR // 14), t0 + sixteenth * beat / 4, 0.5)
        chord(buf, [PC["D"], PC["F"], PC["A"]], 3, t0, 4 * beat, 0.7)
        place(buf, bass_note(hz(PC["D"], 1), int(beat * SR * 3)), t0)
    write_wav("vp08_halftime.wav", buf)
    return {
        "id": "vp08_halftime",
        "file": "clips/vp08_halftime.wav",
        "filename": "clips/vp08_halftime.wav",
        "description": "Half-time feel on a 140 grid (snare on 3), D minor.",
        "notes": "Octave trap: 70 BPM is the expected WRONG answer. Truth is 140.",
        "expected": {
            "expected_tempo_bpm": 140, "tempo_bpm_approx": 140,
            "tempo_tolerance_bpm": 3,
            "expected_key": "D", "expected_key_pc": 2,
            "expected_scale_mode": "minor", "scale_mode_guess": "minor",
        },
    }


def vp09_swung() -> dict:
    """100 BPM swung 8ths (66%), G major comping."""
    bpm, bars = 100.0, 8
    buf, beat = beat_grid(bpm, bars)
    swing = 0.66
    for bar in range(bars):
        t0 = bar * 4 * beat
        for b in range(4):
            place(buf, kick(SR // 3), t0 + b * beat, 0.9 if b in (0, 2) else 0.0)
            place(buf, hat(SR // 12), t0 + b * beat, 0.6)
            place(buf, hat(SR // 12), t0 + (b + swing) * beat, 0.45)  # swung off-beat
        place(buf, snare(SR // 4), t0 + 1 * beat)
        place(buf, snare(SR // 4), t0 + 3 * beat)
        root = [PC["G"], PC["B"], PC["D"]] if bar % 2 == 0 else [PC["C"], PC["E"], PC["G"]]
        chord(buf, root, 4, t0 + 0.5 * beat, 2 * beat, 0.6)
        place(buf, bass_note(hz(PC["G"] if bar % 2 == 0 else PC["C"], 1), int(beat * SR)), t0)
    write_wav("vp09_swung.wav", buf)
    return {
        "id": "vp09_swung",
        "file": "clips/vp09_swung.wav",
        "filename": "clips/vp09_swung.wav",
        "description": "Swung 8ths (66%) at 100 BPM, G major (G-C vamp).",
        "notes": "Swing must not bend the tempo estimate.",
        "expected": {
            "expected_tempo_bpm": 100, "tempo_bpm_approx": 100,
            "tempo_tolerance_bpm": 3,
            "expected_key": "G", "expected_key_pc": 7,
            "expected_scale_mode": "major", "scale_mode_guess": "major",
        },
    }


def vp10_latin() -> dict:
    """110 BPM son clave + montuno-ish line in C major."""
    bpm, bars = 110.0, 8
    buf, beat = beat_grid(bpm, bars)
    # 3-2 son clave positions in 2 bars of 4/4 (in beats)
    clave = [0.0, 1.5, 3.0, 5.0, 6.0]
    for two_bar in range(bars // 2):
        t0 = two_bar * 8 * beat
        for c in clave:
            place(buf, snare(SR // 6), t0 + c * beat, 0.8)
        for b in range(8):
            place(buf, kick(SR // 3), t0 + b * beat, 0.7 if b % 2 == 0 else 0.0)
            place(buf, hat(SR // 12), t0 + b * beat + beat / 2, 0.5)
        # montuno: C and G7 alternating, arpeggiated
        for half, pcs in ((0, [PC["C"], PC["E"], PC["G"]]), (4, [PC["G"], PC["B"], PC["F"]])):
            for i, p in enumerate(pcs + pcs[:1]):
                place(buf, saw_note(hz(p, 4), int(0.4 * beat * SR)),
                      t0 + (half + i) * beat, 0.5)
        place(buf, bass_note(hz(PC["C"], 1), int(beat * SR * 2)), t0)
        place(buf, bass_note(hz(PC["G"], 1), int(beat * SR * 2)), t0 + 4 * beat)
    write_wav("vp10_latin.wav", buf)
    return {
        "id": "vp10_latin",
        "file": "clips/vp10_latin.wav",
        "filename": "clips/vp10_latin.wav",
        "description": "Son-clave latin feel at 110 BPM, C major (C-G7 montuno).",
        "notes": "Clave accents off the grid pull naive onset tempo estimators.",
        "expected": {
            "expected_tempo_bpm": 110, "tempo_bpm_approx": 110,
            "tempo_tolerance_bpm": 3,
            "expected_key": "C", "expected_key_pc": 0,
            "expected_scale_mode": "major", "scale_mode_guess": "major",
        },
    }


def vp11_modal_vamp() -> dict:
    """92 BPM D-dorian two-chord vamp (Dm7-G7)."""
    bpm, bars = 92.0, 8
    buf, beat = beat_grid(bpm, bars)
    for bar in range(bars):
        t0 = bar * 4 * beat
        place(buf, kick(SR // 3), t0)
        place(buf, kick(SR // 3), t0 + 2.5 * beat, 0.7)
        place(buf, snare(SR // 4), t0 + 2 * beat, 0.8)
        for eighth in range(8):
            place(buf, hat(SR // 12), t0 + eighth * beat / 2, 0.4)
        pcs = ([PC["D"], PC["F"], PC["A"], PC["C"]] if bar % 2 == 0
               else [PC["G"], PC["B"], PC["D"], PC["F"]])
        chord(buf, pcs, 3, t0, 4 * beat, 0.8)
        place(buf, bass_note(hz(PC["D"] if bar % 2 == 0 else PC["G"], 1),
                             int(beat * SR * 2)), t0)
    write_wav("vp11_modal_vamp.wav", buf)
    return {
        "id": "vp11_modal_vamp",
        "file": "clips/vp11_modal_vamp.wav",
        "filename": "clips/vp11_modal_vamp.wav",
        "description": "D dorian vamp (Dm7-G7) at 92 BPM — modal, no leading tone.",
        "notes": "Dorian reads as 'D minor' in a major/minor world; key D is the pass.",
        "expected": {
            "expected_tempo_bpm": 92, "tempo_bpm_approx": 92,
            "tempo_tolerance_bpm": 3,
            "expected_key": "D", "expected_key_pc": 2,
            "expected_scale_mode": "minor", "scale_mode_guess": "minor",
        },
    }


def vp12_dense_keys() -> dict:
    """116 BPM dense polyphonic comping in Eb major, light percussion."""
    bpm, bars = 116.0, 8
    buf, beat = beat_grid(bpm, bars)
    prog = [
        [PC["Eb"], PC["G"], PC["Bb"], PC["D"]],   # Ebmaj7
        [PC["C"], PC["Eb"], PC["G"], PC["Bb"]],   # Cm7
        [PC["Ab"], PC["C"], PC["Eb"], PC["G"]],   # Abmaj7
        [PC["Bb"], PC["D"], PC["F"], PC["Ab"]],   # Bb7
    ]
    for bar in range(bars):
        t0 = bar * 4 * beat
        pcs = prog[bar % 4]
        # dense: stabs on 1, 2.5, 4 across two octaves
        for at, gain in ((0.0, 0.9), (1.5, 0.7), (3.0, 0.8)):
            chord(buf, pcs, 4, t0 + at * beat, 1.2 * beat, gain)
            chord(buf, pcs, 5, t0 + at * beat, 1.0 * beat, gain * 0.5)
        place(buf, bass_note(hz(pcs[0], 1), int(beat * SR * 2)), t0)
        place(buf, hat(SR // 12), t0 + 1 * beat, 0.4)
        place(buf, hat(SR // 12), t0 + 3 * beat, 0.4)
        place(buf, kick(SR // 3), t0, 0.6)
    write_wav("vp12_dense_keys.wav", buf)
    return {
        "id": "vp12_dense_keys",
        "file": "clips/vp12_dense_keys.wav",
        "filename": "clips/vp12_dense_keys.wav",
        "description": "Dense polyphonic keys (Ebmaj7-Cm7-Abmaj7-Bb7) at 116, sparse drums.",
        "notes": "Harmony-dominant texture; tempo must survive weak percussion.",
        "expected": {
            "expected_tempo_bpm": 116, "tempo_bpm_approx": 116,
            "tempo_tolerance_bpm": 3,
            "expected_key": "Eb", "expected_key_pc": 3,
            "expected_scale_mode": "major", "scale_mode_guess": "major",
        },
    }


def main() -> None:
    entries = [
        vp06_offgrid_live(),
        vp07_click_quantised(),
        vp08_halftime(),
        vp09_swung(),
        vp10_latin(),
        vp11_modal_vamp(),
        vp12_dense_keys(),
    ]
    manifest_path = ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    by_id = {c["id"]: c for c in manifest["clips"]}
    for e in entries:
        by_id[e["id"]] = e
    manifest["clips"] = sorted(by_id.values(), key=lambda c: c["id"])
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest: {len(manifest['clips'])} clips")


if __name__ == "__main__":
    main()
