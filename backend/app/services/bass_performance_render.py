"""Performance MIDI renderer.

v0.5: ghost/grace shaping (velocity scale + duration shorten). Pass-through for
all other articulations. Clean MIDI is unaffected by construction — this module
renders a parallel ``Bass (Performance)`` instrument from captured
``BassPerformanceNote`` metadata.

v0.8: layered "feel" shaping on top of v0.5:
  - role-based velocity & duration shaping (anchor / answer / push / release),
  - subtle 4-bar phrase arc dynamics,
  - bounded deterministic micro-timing (no rng; hashed from bar/slot/pitch),
  - dead-note shaping (very short, low velocity),
  - optional source-pressure response (kick-aligned accent, snare-without-kick attenuation).

All shaping is bounded, deterministic for fixed inputs, and never produces
non-positive durations or excessive overlaps.
"""

from __future__ import annotations

import io

import pretty_midi

from app.services.bass_performance import BassPerformanceNote


# --- v0.5 shaping constants (preserved verbatim) ---
_GHOST_VEL_SCALE = 0.55
_GHOST_VEL_MIN = 12
_GHOST_VEL_MAX = 48
_GHOST_DUR_FRAC = 0.5

_GRACE_VEL_SCALE = 0.7
_GRACE_VEL_MIN = 20
_GRACE_VEL_MAX = 64
_GRACE_DUR_FRAC = 0.4
_GRACE_TARGET_GAP = 0.005

_MIN_NOTE_DURATION = 0.001

# --- v0.8 shaping constants ---
# Velocity offsets keyed by phrase role (added before clamping to [1,127]).
_ROLE_VEL_DELTA: dict[str, int] = {
    "anchor": 4,
    "answer": 1,
    "push": 2,
    "release": -3,
}

# Duration multipliers keyed by phrase role (clamped at the end so duration
# never falls below ``_MIN_NOTE_DURATION``).
_ROLE_DUR_MULT: dict[str, float] = {
    "anchor": 1.06,
    "answer": 1.00,
    "push": 0.94,
    "release": 1.10,
}

# 4-bar phrase arc on velocity. Bar index is taken mod 4 — bar 0 establishes,
# bar 1 answers, bar 2 pushes, bar 3 releases.
_PHRASE_ARC_VEL_DELTA: tuple[int, int, int, int] = (-1, 0, 3, -2)

# Micro-timing range: peak ±5ms (well within the ~5ms tick epsilon already used
# by the test suite). Anchor-on-beat-0/8 stays tight.
_MICRO_TIMING_PEAK_SEC = 0.005

# Dead note shaping (was pass-through in v0.5).
_DEAD_VEL_SCALE = 0.45
_DEAD_VEL_MIN = 10
_DEAD_VEL_MAX = 36
_DEAD_DUR_FRAC = 0.4

# Source-pressure response bounds.
_SOURCE_KICK_ACCENT_MAX = 7      # +velocity at fully aligned kick slot
_SOURCE_SNARE_PENALTY_MAX = 6    # -velocity when strong snare without kick
_SOURCE_PRESSURE_DUR_RANGE = 0.08  # ±8% duration scaling from pressure


def render_performance_bass_midi(
    notes: tuple[BassPerformanceNote, ...],
    *,
    tempo: int,
    program: int,
    source_kick_per_bar: tuple[tuple[float, ...], ...] | None = None,
    source_snare_per_bar: tuple[tuple[float, ...], ...] | None = None,
    source_pressure_per_bar: tuple[tuple[float, ...], ...] | None = None,
) -> bytes:
    """Render performance notes to MIDI bytes with feel shaping.

    Optional source maps (bar -> 16-slot rows of 0..1) drive the v0.8
    source-pressure response. Passing ``None`` for all of them disables only
    that source-pressure layer; role/phrase/micro-timing feel still applies
    when notes include role/bar/slot metadata. Ghost/grace shaping is unchanged.
    """
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    inst = pretty_midi.Instrument(
        program=int(program),
        is_drum=False,
        name="Bass (Performance)",
    )
    pm.instruments.append(inst)

    if not notes:
        buf = io.BytesIO()
        pm.write(buf)
        return buf.getvalue()

    sixteenth = 60.0 / float(max(1, int(tempo))) / 4.0
    ordered = tuple(
        sorted(
            notes,
            key=lambda n: (float(n.start), int(n.pitch), float(n.end)),
        )
    )

    rendered_notes: list[pretty_midi.Note] = []
    for idx, note in enumerate(ordered):
        next_note = ordered[idx + 1] if idx + 1 < len(ordered) else None
        rendered = _shape_note(
            note,
            next_note=next_note,
            sixteenth=sixteenth,
            source_kick_per_bar=source_kick_per_bar,
            source_snare_per_bar=source_snare_per_bar,
            source_pressure_per_bar=source_pressure_per_bar,
        )
        rendered_notes.append(rendered)

    # Sort by (start, pitch) so the next-note overlap guard is meaningful.
    rendered_notes.sort(key=lambda n: (float(n.start), int(n.pitch), float(n.end)))
    _enforce_no_excessive_overlap(rendered_notes)
    inst.notes.extend(rendered_notes)

    buf = io.BytesIO()
    pm.write(buf)
    return buf.getvalue()


def _shape_note(
    note: BassPerformanceNote,
    *,
    next_note: BassPerformanceNote | None,
    sixteenth: float,
    source_kick_per_bar: tuple[tuple[float, ...], ...] | None,
    source_snare_per_bar: tuple[tuple[float, ...], ...] | None,
    source_pressure_per_bar: tuple[tuple[float, ...], ...] | None,
) -> pretty_midi.Note:
    pitch = int(note.pitch)
    velocity = int(note.velocity)
    start = float(note.start)
    end = float(note.end)
    role = str(note.role or "")
    bar = note.bar_index
    slot = note.slot_index

    # 1) v0.5 articulation shaping (ghost / grace) — unchanged.
    if note.articulation == "ghost":
        velocity = _clamp_int(round(velocity * _GHOST_VEL_SCALE), _GHOST_VEL_MIN, _GHOST_VEL_MAX)
        max_dur = sixteenth * _GHOST_DUR_FRAC
        end = _shorten_to(start, end, max_dur)
        return pretty_midi.Note(pitch=pitch, velocity=velocity, start=start, end=end)
    if note.articulation == "grace":
        velocity = _clamp_int(round(velocity * _GRACE_VEL_SCALE), _GRACE_VEL_MIN, _GRACE_VEL_MAX)
        max_dur = sixteenth * _GRACE_DUR_FRAC
        end = _shorten_to(start, end, max_dur)
        if next_note is not None:
            target_cap = float(next_note.start) - _GRACE_TARGET_GAP
            if target_cap > start + _MIN_NOTE_DURATION and end > target_cap:
                end = target_cap
        return pretty_midi.Note(pitch=pitch, velocity=velocity, start=start, end=end)
    if note.articulation == "dead":
        velocity = _clamp_int(round(velocity * _DEAD_VEL_SCALE), _DEAD_VEL_MIN, _DEAD_VEL_MAX)
        max_dur = sixteenth * _DEAD_DUR_FRAC
        end = _shorten_to(start, end, max_dur)
        return pretty_midi.Note(pitch=pitch, velocity=velocity, start=start, end=end)
    # slide_from / slide_to / hammer keep v0.5 pass-through semantics for now,
    # but still receive the v0.8 feel layer below.

    # 2) v0.8 feel layer: applied to normal/slide/hammer notes only.
    vel_delta = _ROLE_VEL_DELTA.get(role, 0)
    dur_mult = _ROLE_DUR_MULT.get(role, 1.0)

    # 4-bar phrase arc on velocity (bar % 4).
    if bar is not None:
        vel_delta += _PHRASE_ARC_VEL_DELTA[int(bar) % 4]

    # Source-pressure response (optional).
    src_kick = _grid_value(source_kick_per_bar, bar, slot)
    src_snare = _grid_value(source_snare_per_bar, bar, slot)
    src_pressure = _grid_value(source_pressure_per_bar, bar, slot)

    if src_kick is not None and src_kick > 0.0:
        vel_delta += int(round(_SOURCE_KICK_ACCENT_MAX * min(1.0, src_kick)))
    if src_snare is not None and src_kick is not None:
        if src_snare >= 0.5 and src_kick < 0.25:
            vel_delta -= int(round(_SOURCE_SNARE_PENALTY_MAX * min(1.0, src_snare)))
    if src_pressure is not None:
        # High pressure => slightly drives note (longer); low pressure => slightly
        # opens space (shorter). Bounded.
        scale = 1.0 + _SOURCE_PRESSURE_DUR_RANGE * (src_pressure - 0.4)
        if scale < 1.0 - _SOURCE_PRESSURE_DUR_RANGE:
            scale = 1.0 - _SOURCE_PRESSURE_DUR_RANGE
        if scale > 1.0 + _SOURCE_PRESSURE_DUR_RANGE:
            scale = 1.0 + _SOURCE_PRESSURE_DUR_RANGE
        dur_mult *= scale

    # 3) Apply velocity & duration shaping.
    velocity = _clamp_int(velocity + vel_delta, 1, 127)
    duration = max(_MIN_NOTE_DURATION, (end - start) * dur_mult)
    end = start + duration

    # 4) Bounded deterministic micro-timing offset (no rng; hash-based).
    offset = _micro_timing_offset(role=role, bar=bar, slot=slot, pitch=pitch)
    if offset != 0.0:
        new_start = max(0.0, start + offset)
        # Keep duration constant under timing nudge.
        end = new_start + duration
        start = new_start

    # 5) Don't bleed into next note (cap end before next start).
    if next_note is not None:
        gap_cap = float(next_note.start) - 1e-4
        if gap_cap > start + _MIN_NOTE_DURATION and end > gap_cap:
            end = gap_cap

    if end <= start:
        end = start + _MIN_NOTE_DURATION

    return pretty_midi.Note(pitch=pitch, velocity=velocity, start=start, end=end)


def _grid_value(
    grid: tuple[tuple[float, ...], ...] | None,
    bar: int | None,
    slot: int | None,
) -> float | None:
    if grid is None or bar is None or slot is None:
        return None
    if not grid:
        return None
    b = max(0, min(int(bar), len(grid) - 1))
    row = grid[b]
    if not row:
        return None
    s = max(0, min(int(slot), len(row) - 1))
    try:
        v = float(row[s])
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _micro_timing_offset(
    *,
    role: str,
    bar: int | None,
    slot: int | None,
    pitch: int,
) -> float:
    """Return a tiny deterministic timing offset in seconds.

    Anchors on slots 0 and 8 stay locked to the grid (offset 0). Release/passing
    notes can lay back a hair; pushes can land a hair early. Magnitude never
    exceeds ``_MICRO_TIMING_PEAK_SEC``.
    """
    if slot in (0, 8) and role == "anchor":
        return 0.0
    if bar is None or slot is None:
        return 0.0

    # Stable, deterministic, small integer hash.
    h = (int(bar) * 1009 + int(slot) * 31 + (int(pitch) & 0x0F)) & 0xFFFF
    # Map to [-1, 1].
    unit = (h / 65535.0) * 2.0 - 1.0

    # Role-specific lean. Push: tend slightly early; release: tend slightly late.
    bias = 0.0
    if role == "push":
        bias = -0.4
    elif role == "release":
        bias = 0.5
    elif role == "answer":
        bias = 0.15
    leaned = max(-1.0, min(1.0, 0.6 * unit + bias))
    return leaned * _MICRO_TIMING_PEAK_SEC


def _enforce_no_excessive_overlap(notes: list[pretty_midi.Note]) -> None:
    """Trim any note that overruns the next note's start by more than ~5ms."""
    for i, note in enumerate(notes):
        if i + 1 >= len(notes):
            continue
        nxt = notes[i + 1]
        cap = float(nxt.start) - 0.001
        if cap > float(note.start) + _MIN_NOTE_DURATION and float(note.end) > cap + 0.005:
            note.end = cap


def _clamp_int(value: int, lo: int, hi: int) -> int:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return int(value)


def _shorten_to(start: float, end: float, max_dur: float) -> float:
    capped_end = start + max(_MIN_NOTE_DURATION, float(max_dur))
    return min(end, capped_end)


# Back-compat: keep _clamp name for any internal callers expecting it.
_clamp = _clamp_int


__all__ = ["render_performance_bass_midi"]
