"""Performance MIDI renderer.

Ghost, grace, mute/dead, and connected-note shaping is rendered into a parallel
``Bass (Performance)`` instrument from captured ``BassPerformanceNote``
metadata. ``slide_to`` uses standard channel pitch bend with an explicit RPN
pitch-bend-range setup; ``hammer`` uses a bounded generic legato overlap.

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
import math

import pretty_midi

from app.services.bass_instrument_profiles import bass_instrument_profile
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

# Expressive MIDI contract. Twelve semitones is wide enough for the engine's
# bounded connected intervals while remaining a conventional, explicitly
# negotiated pitch-bend range for generic monophonic bass instruments.
_PERFORMANCE_MIDI_RESOLUTION = 960
_LEGACY_MIDI_RESOLUTION = 220
_PITCH_BEND_RANGE_SEMITONES = 12
_PITCH_BEND_MIN = -8192
_PITCH_BEND_MAX = 8191
_SLIDE_CURVE_MAX_POINTS = 12


def render_performance_bass_midi(
    notes: tuple[BassPerformanceNote, ...],
    *,
    tempo: int,
    program: int,
    expression_amount: float = 0.5,
    articulation_focus: str | None = "natural",
    timing_humanize: float | None = None,
    velocity_humanize: float | None = None,
    instrument_family: str | None = None,
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
    needs_expressive_resolution = any(
        str(note.articulation) in {"slide_to", "hammer"}
        for note in notes
    )
    pm = pretty_midi.PrettyMIDI(
        initial_tempo=float(tempo),
        resolution=(
            _PERFORMANCE_MIDI_RESOLUTION
            if needs_expressive_resolution
            else _LEGACY_MIDI_RESOLUTION
        ),
    )
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
    amount = max(0.0, min(1.0, float(expression_amount)))
    feel_scale = min(1.5, amount * 2.0)
    # ``None`` is the compatibility contract: keep the existing
    # Character-driven timing and velocity scaling semantics.
    timing_humanize_scale = _independent_humanize_scale(
        timing_humanize,
        fallback=feel_scale,
    )
    velocity_humanize_scale = _independent_humanize_scale(
        velocity_humanize,
        fallback=feel_scale,
    )
    # Natural keeps the legacy threshold/strength exactly. An explicit
    # Connected selection must be audible at the default Character midpoint,
    # so its generic legato renderer follows the full 0..1 amount instead.
    normalized_focus = str(articulation_focus or "natural").strip().lower()
    connected_scale = (
        amount
        if normalized_focus == "connected"
        else max(0.0, min(1.0, (amount - 0.5) * 2.0))
    )
    instrument = bass_instrument_profile(instrument_family)
    ordered = tuple(
        sorted(
            notes,
            key=lambda n: (float(n.start), int(n.pitch), float(n.end)),
        )
    )

    rendered_pairs: list[tuple[BassPerformanceNote, pretty_midi.Note]] = []
    for idx, note in enumerate(ordered):
        next_note = ordered[idx + 1] if idx + 1 < len(ordered) else None
        rendered = _shape_note(
            note,
            next_note=next_note,
            sixteenth=sixteenth,
            source_kick_per_bar=source_kick_per_bar,
            source_snare_per_bar=source_snare_per_bar,
            source_pressure_per_bar=source_pressure_per_bar,
            feel_scale=feel_scale,
            velocity_humanize_scale=velocity_humanize_scale,
            timing_humanize_scale=timing_humanize_scale,
            connected_scale=connected_scale,
            sustain_multiplier=instrument.sustain_multiplier,
            timing_scale=instrument.timing_scale,
        )
        rendered_pairs.append((note, rendered))

    # Humanization can reorder unusually close onsets. Keep each rendered note
    # paired with its source metadata through that sort so slide/hammer intent
    # can never migrate to a different pitch.
    rendered_pairs.sort(
        key=lambda pair: (
            float(pair[1].start),
            int(pair[1].pitch),
            float(pair[1].end),
        )
    )
    rendered_sources = tuple(pair[0] for pair in rendered_pairs)
    rendered_notes = [pair[1] for pair in rendered_pairs]
    _apply_connected_note_intent(
        rendered_notes,
        rendered_sources,
        sixteenth=sixteenth,
        connected_scale=connected_scale,
    )
    _enforce_no_excessive_overlap(rendered_notes)
    inst.notes.extend(rendered_notes)
    _render_slide_pitch_bends(
        inst,
        rendered_notes,
        rendered_sources,
        tempo=tempo,
    )

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
    feel_scale: float,
    velocity_humanize_scale: float,
    timing_humanize_scale: float,
    connected_scale: float,
    sustain_multiplier: float,
    timing_scale: float,
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
    if note.articulation == "slide_to":
        velocity = _clamp_int(round(velocity * (1.0 - 0.10 * connected_scale)), 1, 127)
    elif note.articulation == "hammer":
        velocity = _clamp_int(round(velocity * (1.0 - 0.16 * connected_scale)), 1, 127)

    # 2) v0.8 feel layer: applied to normal/slide/hammer notes only.
    vel_delta = int(
        round(_ROLE_VEL_DELTA.get(role, 0) * velocity_humanize_scale)
    )
    dur_mult = (
        1.0 + (_ROLE_DUR_MULT.get(role, 1.0) - 1.0) * feel_scale
    ) * max(0.5, min(1.5, float(sustain_multiplier)))

    # 4-bar phrase arc on velocity (bar % 4).
    if bar is not None:
        vel_delta += int(
            round(
                _PHRASE_ARC_VEL_DELTA[int(bar) % 4]
                * velocity_humanize_scale
            )
        )

    # Source-pressure response (optional).
    src_kick = _grid_value(source_kick_per_bar, bar, slot)
    src_snare = _grid_value(source_snare_per_bar, bar, slot)
    src_pressure = _grid_value(source_pressure_per_bar, bar, slot)

    if src_kick is not None and src_kick > 0.0:
        vel_delta += int(
            round(
                _SOURCE_KICK_ACCENT_MAX
                * min(1.0, src_kick)
                * velocity_humanize_scale
            )
        )
    if src_snare is not None and src_kick is not None:
        if src_snare >= 0.5 and src_kick < 0.25:
            vel_delta -= int(
                round(
                    _SOURCE_SNARE_PENALTY_MAX
                    * min(1.0, src_snare)
                    * velocity_humanize_scale
                )
            )
    if src_pressure is not None:
        # High pressure => slightly drives note (longer); low pressure => slightly
        # opens space (shorter). Bounded.
        scale = 1.0 + _SOURCE_PRESSURE_DUR_RANGE * (src_pressure - 0.4)
        if scale < 1.0 - _SOURCE_PRESSURE_DUR_RANGE:
            scale = 1.0 - _SOURCE_PRESSURE_DUR_RANGE
        if scale > 1.0 + _SOURCE_PRESSURE_DUR_RANGE:
            scale = 1.0 + _SOURCE_PRESSURE_DUR_RANGE
        dur_mult *= 1.0 + (scale - 1.0) * feel_scale

    # 3) Apply velocity & duration shaping.
    velocity = _clamp_int(velocity + vel_delta, 1, 127)
    duration = max(_MIN_NOTE_DURATION, (end - start) * dur_mult)
    end = start + duration

    # 4) Bounded deterministic micro-timing offset (no rng; hash-based).
    offset = (
        _micro_timing_offset(role=role, bar=bar, slot=slot, pitch=pitch)
        * timing_humanize_scale
        * max(0.0, min(1.0, float(timing_scale)))
    )
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


def _apply_connected_note_intent(
    rendered: list[pretty_midi.Note],
    source: tuple[BassPerformanceNote, ...],
    *,
    sixteenth: float,
    connected_scale: float,
) -> None:
    """Create a tiny generic legato overlap for hammer destinations.

    ``slide_to`` uses a true pre-bent target-note curve below. Overlapping its
    source note would make channel pitch bend detune two voices at once, so
    only hammer-on intent retains this generic monophonic-legato trigger.
    """

    if connected_scale <= 0.0 or len(rendered) != len(source):
        return
    overlap = min(0.0035, max(0.001, sixteenth * 0.03 * connected_scale))
    for idx in range(1, len(rendered)):
        if source[idx].articulation != "hammer":
            continue
        previous = rendered[idx - 1]
        current = rendered[idx]
        if abs(int(current.pitch) - int(previous.pitch)) > 7:
            continue
        previous.end = max(float(previous.end), float(current.start) + overlap)


def _independent_humanize_scale(
    value: float | None,
    *,
    fallback: float,
) -> float:
    if value is None:
        return float(fallback)
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(amount):
        return 0.0
    return max(0.0, min(1.0, amount)) * 1.5


def _pitch_bend_value(semitones: int) -> int:
    normalized = float(semitones) / float(_PITCH_BEND_RANGE_SEMITONES)
    raw = int(round(normalized * 8192.0))
    return max(_PITCH_BEND_MIN, min(_PITCH_BEND_MAX, raw))


def _append_pitch_bend_range_setup(
    instrument: pretty_midi.Instrument,
    *,
    tick_seconds: float,
) -> None:
    """Negotiate a ±12-semitone bend range with standard RPN 0,0."""

    # Keep the six messages on distinct ticks. PrettyMIDI otherwise sorts CCs
    # at one tick by controller number, which would corrupt RPN ordering.
    setup = (
        (101, 0),  # RPN MSB
        (100, 0),  # RPN LSB: pitch-bend sensitivity
        (6, _PITCH_BEND_RANGE_SEMITONES),  # Data Entry MSB: semitones
        (38, 0),  # Data Entry LSB: cents
        (101, 127),  # RPN null
        (100, 127),
    )
    for index, (number, value) in enumerate(setup):
        instrument.control_changes.append(
            pretty_midi.ControlChange(
                number=int(number),
                value=int(value),
                time=max(0.0, float(index) * tick_seconds),
            )
        )


def _render_slide_pitch_bends(
    instrument: pretty_midi.Instrument,
    rendered: list[pretty_midi.Note],
    source: tuple[BassPerformanceNote, ...],
    *,
    tempo: int,
) -> None:
    """Render ``slide_to`` as a pre-bent target resolving to center.

    Pitch bend is channel-wide, so the source note is released at least one
    MIDI tick before the target onset. The target begins bent to the source
    pitch, then follows a bounded monotonic curve to its written pitch.
    """

    if len(rendered) != len(source) or len(rendered) < 2:
        return
    tick_seconds = 60.0 / (
        float(max(1, int(tempo))) * float(_PERFORMANCE_MIDI_RESOLUTION)
    )
    sixteenth = 60.0 / float(max(1, int(tempo))) / 4.0
    curves: list[tuple[int, float, float]] = []

    for index in range(1, len(rendered)):
        if source[index].articulation != "slide_to":
            continue
        previous = rendered[index - 1]
        target = rendered[index]
        source_offset = int(previous.pitch) - int(target.pitch)
        if not 1 <= abs(source_offset) <= _PITCH_BEND_RANGE_SEMITONES:
            continue

        safe_previous_end = float(target.start) - tick_seconds
        if float(previous.end) > safe_previous_end:
            if safe_previous_end <= float(previous.start) + _MIN_NOTE_DURATION:
                continue
            previous.end = safe_previous_end

        target_duration = float(target.end) - float(target.start)
        if target_duration <= tick_seconds:
            continue
        desired_curve = max(0.04, sixteenth * 0.75)
        curve_duration = min(desired_curve, target_duration * 0.5)
        curve_duration = max(tick_seconds * 2.0, curve_duration)
        curve_end = min(float(target.end), float(target.start) + curve_duration)
        if curve_end <= float(target.start) + tick_seconds:
            continue
        curves.append(
            (
                _pitch_bend_value(source_offset),
                float(target.start),
                float(curve_end),
            )
        )

    if not curves:
        return

    _append_pitch_bend_range_setup(instrument, tick_seconds=tick_seconds)
    for initial_bend, curve_start, curve_end in curves:
        available_ticks = max(
            2,
            int(round((curve_end - curve_start) / tick_seconds)),
        )
        steps = min(_SLIDE_CURVE_MAX_POINTS, available_ticks)
        for step in range(steps + 1):
            progress = float(step) / float(steps)
            value = int(round(float(initial_bend) * (1.0 - progress)))
            instrument.pitch_bends.append(
                pretty_midi.PitchBend(
                    pitch=max(_PITCH_BEND_MIN, min(_PITCH_BEND_MAX, value)),
                    time=max(
                        0.0,
                        curve_start + ((curve_end - curve_start) * progress),
                    ),
                )
            )

    instrument.pitch_bends.sort(key=lambda bend: (float(bend.time), int(bend.pitch)))
    # Every generated lane leaves channel bend centered, even if later event
    # transforms clamp or reorder coincident curve points.
    final_time = max(float(curve_end) for _bend, _start, curve_end in curves)
    if not instrument.pitch_bends or int(instrument.pitch_bends[-1].pitch) != 0:
        instrument.pitch_bends.append(
            pretty_midi.PitchBend(pitch=0, time=max(0.0, final_time))
        )


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
