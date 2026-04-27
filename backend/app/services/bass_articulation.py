"""Symbolic articulation/performance shaping for bass MIDI notes."""

from __future__ import annotations


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def shape_note(
    *,
    start: float,
    end: float,
    velocity: int,
    slot: int,
    role: str,
    style: str,
    is_structural: bool,
    cadence_bias: float,
    sustain_mult: float,
    bar_end: float,
) -> tuple[float, int]:
    """Return shaped (end, velocity) for expressive MIDI playback intent."""
    dur = max(0.02, float(end - start))
    role_lm = {
        "anchor": 1.04,
        "answer": 0.97,
        "push": 0.9,
        "release": 1.1,
    }.get(role, 1.0)
    lm = role_lm * float(_clamp(sustain_mult, 0.75, 1.25))
    if is_structural:
        lm *= 1.06
    else:
        lm *= 0.94
    if slot >= 12 and cadence_bias >= 0.5:
        lm *= 1.08
    if style in ("rhythmic", "fusion") and slot % 4 != 0:
        lm *= 0.9
    if style == "supportive" and slot % 4 == 0:
        lm *= 1.06
    if style == "supportive" and role == "anchor" and slot == 0:
        lm *= 1.08
    if style == "supportive" and role == "release" and slot >= 8:
        lm *= 1.12
    if style == "fusion" and role == "release" and slot >= 12:
        lm *= 1.1

    new_end = min(float(bar_end) - 1e-4, float(start) + (dur * lm))
    if new_end <= start:
        new_end = min(float(bar_end) - 1e-4, float(start) + 0.03)

    dv = 0.0
    if is_structural:
        dv += 4.0
    if role == "push":
        dv += 3.0
    elif role == "release":
        dv -= 4.0
    if slot >= 12 and cadence_bias >= 0.55:
        dv += 5.0
    if slot % 4 != 0 and style in ("supportive", "melodic"):
        dv -= 3.0
    if slot % 4 != 0 and style in ("rhythmic", "fusion"):
        dv += 1.0
    if style == "supportive" and role == "anchor" and slot == 0:
        dv += 4.0
    if style == "supportive" and role == "release" and slot >= 8:
        dv += 3.0
    if style == "fusion" and role == "release" and slot >= 12:
        dv += 3.0
    new_vel = int(_clamp(float(velocity) + dv, 36.0, 118.0))
    return new_end, new_vel


def ghost_eligibility(
    *,
    style: str,
    role: str,
    cadence_bias: float,
    bar_density: float,
) -> float:
    """Return probability multiplier for ghost-note usage in current bar."""
    if style not in ("supportive", "rhythmic", "fusion"):
        return 0.0
    base = {
        "anchor": 0.55,
        "answer": 0.9,
        "push": 1.0,
        "release": 0.45,
    }.get(role, 0.7)
    # Dense bars and high cadence focus should reduce ghost clutter.
    dense_cut = 1.0 - _clamp((bar_density - 10.0) / 10.0, 0.0, 0.45)
    cad_cut = 1.0 - (0.35 * _clamp(cadence_bias, 0.0, 1.0))
    return float(_clamp(base * dense_cut * cad_cut, 0.0, 1.0))
