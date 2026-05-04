"""v0.5 Step 4: Performance MIDI renderer tests.

These tests pin the generic articulation shaping rules (velocity scaling
and duration shortening for ghost/grace), the pass-through guarantee for
normal and deferred articulations, and deterministic output.
"""

from __future__ import annotations

import io

import pretty_midi
import pytest

from app.services.bass_performance import BassPerformanceNote
from app.services.bass_performance_render import render_performance_bass_midi


_TEMPO = 120
_PROGRAM = 33  # Electric Bass (finger)
_SIXTEENTH = 60.0 / _TEMPO / 4.0  # 0.125s @ 120 BPM
# pretty_midi serializes timestamps to integer MIDI ticks, so round-trip
# duration assertions need ~1 tick of slack (PPQ=220 default → ~0.00227s
# per tick @ 120 BPM). Use a comfortable 5ms tolerance.
_TICK_EPSILON = 0.005


def _read(data: bytes) -> pretty_midi.PrettyMIDI:
    return pretty_midi.PrettyMIDI(io.BytesIO(data))


def _note(
    *,
    pitch: int = 40,
    start: float = 0.0,
    end: float = 0.5,
    velocity: int = 90,
    articulation: str = "normal",
) -> BassPerformanceNote:
    return BassPerformanceNote(
        pitch=pitch,
        start=start,
        end=end,
        velocity=velocity,
        articulation=articulation,  # type: ignore[arg-type]
    )


def test_empty_input_returns_valid_midi_bytes() -> None:
    data = render_performance_bass_midi(tuple(), tempo=_TEMPO, program=_PROGRAM)
    assert isinstance(data, bytes) and len(data) > 0
    # pretty_midi drops empty instruments on serialization; the contract is
    # that the bytes are still parseable and contain no playable notes.
    pm = _read(data)
    total_notes = sum(len(inst.notes) for inst in pm.instruments)
    assert total_notes == 0


def test_all_normal_notes_preserve_pitch_velocity_start_end() -> None:
    src = (
        _note(pitch=40, start=0.0, end=0.5, velocity=90, articulation="normal"),
        _note(pitch=43, start=0.5, end=1.0, velocity=100, articulation="normal"),
        _note(pitch=45, start=1.0, end=1.5, velocity=80, articulation="normal"),
    )
    data = render_performance_bass_midi(src, tempo=_TEMPO, program=_PROGRAM)
    pm = _read(data)
    out = pm.instruments[0].notes
    assert len(out) == len(src)
    for rendered, original in zip(out, src):
        assert rendered.pitch == original.pitch
        assert rendered.velocity == original.velocity
        assert rendered.start == pytest.approx(original.start, abs=_TICK_EPSILON)
        assert rendered.end == pytest.approx(original.end, abs=_TICK_EPSILON)


def test_ghost_note_lower_velocity_shorter_duration_pitch_unchanged() -> None:
    original_velocity = 60
    original_pitch = 40
    src = (
        _note(
            pitch=original_pitch,
            start=0.0,
            end=0.5,
            velocity=original_velocity,
            articulation="ghost",
        ),
    )
    data = render_performance_bass_midi(src, tempo=_TEMPO, program=_PROGRAM)
    rendered = _read(data).instruments[0].notes[0]

    assert rendered.pitch == original_pitch
    assert rendered.velocity < original_velocity
    assert 12 <= rendered.velocity <= 48
    assert (rendered.end - rendered.start) <= _SIXTEENTH * 0.5 + _TICK_EPSILON
    assert rendered.start == pytest.approx(0.0, abs=_TICK_EPSILON)


def test_grace_note_lower_velocity_shorter_duration_no_overlap_with_target() -> None:
    grace_start = 0.0
    grace_end = 0.5
    target_start = 0.4
    src = (
        _note(
            pitch=42,
            start=grace_start,
            end=grace_end,
            velocity=70,
            articulation="grace",
        ),
        _note(
            pitch=43,
            start=target_start,
            end=0.9,
            velocity=100,
            articulation="normal",
        ),
    )
    data = render_performance_bass_midi(src, tempo=_TEMPO, program=_PROGRAM)
    notes = _read(data).instruments[0].notes
    grace = notes[0]
    target = notes[1]

    assert grace.pitch == 42
    assert grace.velocity < 70
    assert 20 <= grace.velocity <= 64
    assert (grace.end - grace.start) <= _SIXTEENTH * 0.4 + _TICK_EPSILON
    assert grace.end <= target.start - 0.005 + _TICK_EPSILON


def test_deterministic_same_input_yields_identical_bytes() -> None:
    src = (
        _note(pitch=40, start=0.0, end=0.25, velocity=55, articulation="ghost"),
        _note(pitch=43, start=0.25, end=0.75, velocity=100, articulation="normal"),
        _note(pitch=42, start=0.7, end=0.78, velocity=72, articulation="grace"),
        _note(pitch=43, start=0.78, end=1.25, velocity=110, articulation="normal"),
    )
    a = render_performance_bass_midi(src, tempo=_TEMPO, program=_PROGRAM)
    b = render_performance_bass_midi(src, tempo=_TEMPO, program=_PROGRAM)
    assert a == b


@pytest.mark.parametrize(
    "articulation", ["slide_from", "slide_to", "hammer"]
)
def test_deferred_articulations_pass_through_as_normal(articulation: str) -> None:
    src = (
        _note(
            pitch=40,
            start=0.0,
            end=0.5,
            velocity=90,
            articulation=articulation,
        ),
    )
    data = render_performance_bass_midi(src, tempo=_TEMPO, program=_PROGRAM)
    rendered = _read(data).instruments[0].notes[0]

    assert rendered.pitch == 40
    assert rendered.velocity == 90
    assert rendered.start == pytest.approx(0.0, abs=_TICK_EPSILON)
    assert rendered.end == pytest.approx(0.5, abs=_TICK_EPSILON)


def test_dead_articulation_is_short_and_quiet() -> None:
    src = (
        _note(
            pitch=40,
            start=0.0,
            end=0.5,
            velocity=90,
            articulation="dead",
        ),
    )
    data = render_performance_bass_midi(src, tempo=_TEMPO, program=_PROGRAM)
    rendered = _read(data).instruments[0].notes[0]

    assert rendered.pitch == 40
    assert 10 <= rendered.velocity <= 36
    assert rendered.end - rendered.start < 0.5


def test_role_bar_slot_metadata_applies_bounded_deterministic_feel() -> None:
    src = (
        _note(
            pitch=40,
            start=0.25,
            end=0.75,
            velocity=80,
            articulation="normal",
        ),
    )
    src = (
        BassPerformanceNote(
            pitch=src[0].pitch,
            start=src[0].start,
            end=src[0].end,
            velocity=src[0].velocity,
            articulation=src[0].articulation,
            role="anchor",
            bar_index=2,
            slot_index=4,
        ),
    )

    a = render_performance_bass_midi(src, tempo=_TEMPO, program=_PROGRAM)
    b = render_performance_bass_midi(src, tempo=_TEMPO, program=_PROGRAM)
    rendered = _read(a).instruments[0].notes[0]

    assert a == b
    assert rendered.pitch == 40
    assert rendered.velocity != 80
    assert 75 <= rendered.velocity <= 95
    assert abs(rendered.start - 0.25) <= 0.0055 + _TICK_EPSILON
    assert rendered.end > rendered.start


def test_source_maps_affect_velocity_and_duration() -> None:
    src = (
        BassPerformanceNote(
            pitch=40,
            start=0.25,
            end=0.75,
            velocity=80,
            articulation="normal",
            role="anchor",
            bar_index=2,
            slot_index=4,
        ),
    )
    row = tuple(0.0 for _ in range(16))
    kick_row = tuple(1.0 if i == 4 else 0.0 for i in range(16))
    pressure_row = tuple(1.0 if i == 4 else 0.0 for i in range(16))
    source_kick = (row, row, kick_row)
    source_pressure = (row, row, pressure_row)

    baseline = _read(render_performance_bass_midi(src, tempo=_TEMPO, program=_PROGRAM)).instruments[0].notes[0]
    shaped = _read(
        render_performance_bass_midi(
            src,
            tempo=_TEMPO,
            program=_PROGRAM,
            source_kick_per_bar=source_kick,
            source_pressure_per_bar=source_pressure,
        )
    ).instruments[0].notes[0]

    assert shaped.velocity >= baseline.velocity
    assert (shaped.end - shaped.start) >= (baseline.end - baseline.start)


def test_input_order_does_not_affect_output_bytes() -> None:
    in_order = (
        _note(pitch=40, start=0.0, end=0.5, velocity=90, articulation="normal"),
        _note(pitch=43, start=0.5, end=1.0, velocity=100, articulation="normal"),
    )
    out_of_order = (in_order[1], in_order[0])
    a = render_performance_bass_midi(in_order, tempo=_TEMPO, program=_PROGRAM)
    b = render_performance_bass_midi(out_of_order, tempo=_TEMPO, program=_PROGRAM)
    assert a == b
