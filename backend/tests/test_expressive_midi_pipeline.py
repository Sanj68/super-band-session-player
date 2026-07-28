from __future__ import annotations

import io

import mido
import pretty_midi
import pytest

from app.services.bass_loop_boundary import normalize_bass_loop_bytes
from app.services.bass_performance import BassPerformanceNote
from app.services.bass_performance_render import render_performance_bass_midi
from app.services.midi_export import apply_loop_phase_offset, merge_lane_midis


def _note(
    *,
    pitch: int,
    start: float,
    end: float,
    velocity: int,
    articulation: str = "normal",
    role: str | None = None,
    bar_index: int | None = None,
    slot_index: int | None = None,
) -> BassPerformanceNote:
    return BassPerformanceNote(
        pitch=pitch,
        start=start,
        end=end,
        velocity=velocity,
        articulation=articulation,  # type: ignore[arg-type]
        role=role,
        bar_index=bar_index,
        slot_index=slot_index,
    )


def _read(data: bytes) -> pretty_midi.PrettyMIDI:
    return pretty_midi.PrettyMIDI(io.BytesIO(data))


def _absolute_channel_messages(data: bytes) -> list[tuple[int, mido.Message]]:
    midi = mido.MidiFile(file=io.BytesIO(data))
    rows: list[tuple[int, mido.Message]] = []
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += int(message.time)
            if not message.is_meta:
                rows.append((tick, message))
    return sorted(rows, key=lambda row: row[0])


def test_slide_to_renders_rpn_prebend_monotonic_curve_and_reset() -> None:
    source = (
        _note(pitch=40, start=0.0, end=0.48, velocity=92),
        _note(
            pitch=43,
            start=0.5,
            end=1.0,
            velocity=86,
            articulation="slide_to",
        ),
    )

    raw = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=1.0,
    )
    midi = _read(raw)
    instrument = midi.instruments[0]

    assert [
        (event.number, event.value)
        for event in instrument.control_changes
    ] == [
        (101, 0),
        (100, 0),
        (6, 12),
        (38, 0),
        (101, 127),
        (100, 127),
    ]
    bend_values = [int(event.pitch) for event in instrument.pitch_bends]
    assert bend_values[0] == -2048  # target is pre-bent down three semitones
    assert bend_values == sorted(bend_values)
    assert bend_values[-1] == 0
    assert all(-8192 <= value <= 8191 for value in bend_values)
    assert instrument.pitch_bends[0].time == pytest.approx(0.5, abs=0.001)

    notes = sorted(instrument.notes, key=lambda note: note.start)
    assert notes[0].end < notes[1].start

    # The SMF order at the target tick is bend first, then target note-on.
    target_tick = midi.time_to_tick(notes[1].start)
    at_target = [
        message.type
        for tick, message in _absolute_channel_messages(raw)
        if tick == target_tick
    ]
    assert "pitchwheel" in at_target
    assert "note_on" in at_target
    assert at_target.index("pitchwheel") < at_target.index("note_on")


def test_descending_slide_curve_is_monotonic_and_range_safe() -> None:
    source = (
        _note(pitch=45, start=0.0, end=0.48, velocity=92),
        _note(
            pitch=40,
            start=0.5,
            end=1.0,
            velocity=86,
            articulation="slide_to",
        ),
    )

    raw = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=1.0,
    )
    bends = _read(raw).instruments[0].pitch_bends
    values = [int(event.pitch) for event in bends]

    assert values[0] == 3413  # target is pre-bent up five semitones
    assert values == sorted(values, reverse=True)
    assert values[-1] == 0
    assert all(-8192 <= value <= 8191 for value in values)


def test_normalization_preserves_expression_and_bounded_connected_overlap() -> None:
    source = (
        _note(pitch=40, start=0.0, end=0.48, velocity=92),
        _note(
            pitch=42,
            start=0.5,
            end=1.0,
            velocity=86,
            articulation="hammer",
        ),
    )
    rendered = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=1.0,
    )
    before = _read(rendered)
    before_inst = before.instruments[0]
    before_inst.control_changes.append(
        pretty_midi.ControlChange(number=1, value=91, time=0.25)
    )
    before_inst.pitch_bends.extend(
        (
            pretty_midi.PitchBend(pitch=1024, time=0.25),
            pretty_midi.PitchBend(pitch=0, time=0.35),
        )
    )
    buffer = io.BytesIO()
    before.write(buffer)

    normalized = normalize_bass_loop_bytes(
        buffer.getvalue(),
        tempo=120,
        bar_count=1,
        harmonic_root_pc=4,
    )
    after = _read(normalized)
    instrument = after.instruments[0]
    notes = sorted(instrument.notes, key=lambda note: note.start)
    overlap = float(notes[0].end) - float(notes[1].start)

    assert 0.0 < overlap <= 0.005
    assert [(event.number, event.value) for event in instrument.control_changes] == [
        (1, 91)
    ]
    assert [int(event.pitch) for event in instrument.pitch_bends] == [1024, 0]
    assert all(float(event.time) >= 0.0 for event in instrument.control_changes)
    assert all(float(event.time) >= 0.0 for event in instrument.pitch_bends)
    assert int(instrument.pitch_bends[-1].pitch) == 0
    assert after.resolution == before.resolution


def test_expressive_normalization_is_idempotent() -> None:
    source = (
        _note(pitch=40, start=0.0, end=0.48, velocity=92),
        _note(
            pitch=43,
            start=0.5,
            end=1.0,
            velocity=86,
            articulation="slide_to",
        ),
    )
    rendered = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=1.0,
    )

    first = normalize_bass_loop_bytes(
        rendered,
        tempo=120,
        bar_count=1,
        harmonic_root_pc=4,
    )
    second = normalize_bass_loop_bytes(
        first,
        tempo=120,
        bar_count=1,
        harmonic_root_pc=4,
    )

    assert second == first


def test_normalization_adds_center_reset_for_unclosed_bend() -> None:
    midi = pretty_midi.PrettyMIDI(initial_tempo=120.0, resolution=960)
    instrument = pretty_midi.Instrument(program=33, name="Bass (Performance)")
    instrument.notes.append(
        pretty_midi.Note(
            pitch=40,
            velocity=90,
            start=0.0,
            end=0.75,
        )
    )
    instrument.pitch_bends.append(
        pretty_midi.PitchBend(pitch=-1600, time=0.25)
    )
    midi.instruments.append(instrument)
    buffer = io.BytesIO()
    midi.write(buffer)

    normalized = normalize_bass_loop_bytes(
        buffer.getvalue(),
        tempo=120,
        bar_count=1,
        harmonic_root_pc=4,
    )
    bends = _read(normalized).instruments[0].pitch_bends

    assert [int(event.pitch) for event in bends] == [-1600, 0]
    assert int(bends[-1].pitch) == 0
    assert 0.0 <= float(bends[-1].time) <= 2.0


def test_positive_boundary_bend_normalizes_to_center_idempotently() -> None:
    midi = pretty_midi.PrettyMIDI(initial_tempo=120.0, resolution=960)
    instrument = pretty_midi.Instrument(program=33, name="Bass (Performance)")
    instrument.notes.append(
        pretty_midi.Note(
            pitch=40,
            velocity=90,
            start=0.0,
            end=2.0,
        )
    )
    instrument.pitch_bends.append(
        pretty_midi.PitchBend(pitch=1600, time=2.0)
    )
    midi.instruments.append(instrument)
    buffer = io.BytesIO()
    midi.write(buffer)

    first = normalize_bass_loop_bytes(
        buffer.getvalue(),
        tempo=120,
        bar_count=1,
        harmonic_root_pc=4,
    )
    second = normalize_bass_loop_bytes(
        first,
        tempo=120,
        bar_count=1,
        harmonic_root_pc=4,
    )
    bends = _read(first).instruments[0].pitch_bends

    assert first == second
    assert not bends or int(bends[-1].pitch) == 0


def test_clean_lane_does_not_preserve_accidental_micro_overlap() -> None:
    midi = pretty_midi.PrettyMIDI(initial_tempo=120.0, resolution=960)
    instrument = pretty_midi.Instrument(program=33, name="Bass")
    instrument.notes.extend(
        (
            pretty_midi.Note(
                pitch=40,
                velocity=90,
                start=0.0,
                end=0.503,
            ),
            pretty_midi.Note(
                pitch=42,
                velocity=88,
                start=0.5,
                end=1.0,
            ),
        )
    )
    midi.instruments.append(instrument)
    buffer = io.BytesIO()
    midi.write(buffer)

    normalized = normalize_bass_loop_bytes(
        buffer.getvalue(),
        tempo=120,
        bar_count=1,
        harmonic_root_pc=4,
    )
    notes = sorted(
        _read(normalized).instruments[0].notes,
        key=lambda note: note.start,
    )

    assert float(notes[0].end) <= float(notes[1].start)


def test_phase_rotation_moves_notes_bends_and_cc_without_stuck_bend() -> None:
    source = (
        _note(pitch=40, start=0.0, end=0.48, velocity=92),
        _note(
            pitch=43,
            start=0.5,
            end=1.0,
            velocity=86,
            articulation="slide_to",
        ),
    )
    rendered = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=1.0,
    )
    normalized = normalize_bass_loop_bytes(
        rendered,
        tempo=120,
        bar_count=1,
        harmonic_root_pc=4,
    )
    normalized_midi = _read(normalized)
    before = normalized_midi.instruments[0]
    before.control_changes.append(
        pretty_midi.ControlChange(number=1, value=88, time=0.25)
    )
    buffer = io.BytesIO()
    normalized_midi.write(buffer)
    normalized = buffer.getvalue()

    shifted_bytes = apply_loop_phase_offset(
        normalized,
        tempo=120,
        bar_count=1,
        phase_offset_beats=1.0,
    )
    after = _read(shifted_bytes).instruments[0]
    phase_seconds = 0.5
    loop_seconds = 2.0

    # One additional event establishes the bend state active at the phase cut
    # before the first note. The original curve remains note-attached.
    assert int(after.pitch_bends[0].pitch) == 0
    shifted_curve = after.pitch_bends[1:]
    assert [int(event.pitch) for event in shifted_curve] == [
        int(event.pitch) for event in before.pitch_bends
    ]
    assert [round(event.time, 4) for event in shifted_curve] == [
        round((event.time + phase_seconds) % loop_seconds, 4)
        for event in before.pitch_bends
    ]
    before_rpn = [
        event
        for event in before.control_changes
        if int(event.number) != 1
    ]
    after_rpn = [
        event
        for event in after.control_changes
        if int(event.number) != 1
    ]
    assert [round(event.time, 4) for event in after_rpn] == [
        round(event.time, 4) for event in before_rpn
    ]
    shifted_modulation = [
        event for event in after.control_changes if int(event.number) == 1
    ]
    assert len(shifted_modulation) == 2
    assert shifted_modulation[0].value == 88
    assert shifted_modulation[0].time < shifted_modulation[1].time
    assert shifted_modulation[1].time == pytest.approx(
        (0.25 + phase_seconds) % loop_seconds,
        abs=0.002,
    )
    assert int(after.pitch_bends[-1].pitch) == 0
    assert all(0.0 <= float(event.time) <= loop_seconds for event in after.pitch_bends)
    assert all(
        0.0 <= float(event.time) < loop_seconds
        for event in after.control_changes
    )


def test_wrapped_phase_curve_preserves_cyclic_seam_state_without_snap() -> None:
    source = (
        _note(pitch=40, start=0.0, end=0.48, velocity=92),
        _note(
            pitch=43,
            start=0.5,
            end=1.0,
            velocity=86,
            articulation="slide_to",
        ),
    )
    rendered = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=1.0,
    )

    # The cut falls inside the slide curve: its reset wraps near loop start
    # while its pre-bend lands near the end.
    shifted = apply_loop_phase_offset(
        rendered,
        tempo=120,
        bar_count=1,
        phase_offset_beats=2.9,
    )
    bends = _read(shifted).instruments[0].pitch_bends

    assert any(int(event.pitch) != 0 for event in bends)
    # The state immediately after the RPN guard equals the state immediately
    # before the boundary. The wrapped tail then reaches center near the head
    # of the next pass instead of being snapped to zero at the seam.
    assert int(bends[0].pitch) == int(bends[-1].pitch) != 0
    assert any(int(event.pitch) == 0 for event in bends[1:-1])
    assert all(0.0 <= float(event.time) <= 2.0 for event in bends)

    instrument = _read(shifted).instruments[0]
    target_segments = [
        note for note in instrument.notes if int(note.pitch) == 43
    ]
    assert len(target_segments) == 2
    assert all(0.0 <= note.start < note.end <= 2.0 for note in target_segments)


def test_phase_wrapped_target_waits_for_complete_rpn_setup() -> None:
    source = (
        _note(pitch=40, start=0.0, end=0.48, velocity=92),
        _note(
            pitch=43,
            start=0.5,
            end=1.0,
            velocity=86,
            articulation="slide_to",
        ),
    )
    rendered = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=1.0,
    )
    shifted = apply_loop_phase_offset(
        rendered,
        tempo=120,
        bar_count=1,
        phase_offset_beats=3.0,
    )
    messages = _absolute_channel_messages(shifted)
    data_entry_tick = next(
        tick
        for tick, message in messages
        if message.type == "control_change"
        and message.control == 6
        and message.value == 12
    )
    first_nonzero_bend_tick = next(
        tick
        for tick, message in messages
        if message.type == "pitchwheel" and message.pitch != 0
    )
    target_note_tick = next(
        tick
        for tick, message in messages
        if message.type == "note_on"
        and message.note == 43
        and message.velocity > 0
    )

    assert data_entry_tick < first_nonzero_bend_tick
    assert data_entry_tick < target_note_tick
    at_target = [
        message.type
        for tick, message in messages
        if tick == target_note_tick
    ]
    assert at_target.index("pitchwheel") < at_target.index("note_on")


def test_humanized_close_slide_never_bends_the_wrong_destination() -> None:
    source = (
        _note(
            pitch=35,
            start=0.100,
            end=0.300,
            velocity=90,
            role="release",
            bar_index=1,
            slot_index=5,
        ),
        _note(
            pitch=36,
            start=0.102,
            end=0.350,
            velocity=90,
            articulation="slide_to",
            role="push",
            bar_index=1,
            slot_index=6,
        ),
    )
    rendered = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=1.0,
        timing_humanize=1.0,
    )
    instrument = _read(rendered).instruments[0]

    # This deliberately close pair swaps onset order after humanization. The
    # safe result is to omit its no-longer-valid slide, never to attach a
    # positive bend to pitch 35 as though it were the destination.
    assert [int(note.pitch) for note in instrument.notes] == [36, 35]
    assert instrument.pitch_bends == []


def test_combined_export_preserves_expression_events_and_source_resolution() -> None:
    source = (
        _note(pitch=40, start=0.0, end=0.48, velocity=92),
        _note(
            pitch=43,
            start=0.5,
            end=1.0,
            velocity=86,
            articulation="slide_to",
        ),
    )
    bass = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=1.0,
    )
    source_midi = _read(bass)
    source_instrument = source_midi.instruments[0]

    merged = _read(
        merge_lane_midis(
            tempo=120,
            lanes={"bass": bass},
        )
    )
    merged_instrument = merged.instruments[0]

    assert merged.resolution == source_midi.resolution == 960
    assert [
        (event.number, event.value, round(event.time, 4))
        for event in merged_instrument.control_changes
    ] == [
        (event.number, event.value, round(event.time, 4))
        for event in source_instrument.control_changes
    ]
    assert [
        (event.pitch, round(event.time, 4))
        for event in merged_instrument.pitch_bends
    ] == [
        (event.pitch, round(event.time, 4))
        for event in source_instrument.pitch_bends
    ]
    assert int(merged_instrument.pitch_bends[-1].pitch) == 0


def test_independent_humanize_controls_and_fixed_input_determinism() -> None:
    source = (
        _note(
            pitch=40,
            start=0.25,
            end=0.75,
            velocity=80,
            role="answer",
            bar_index=2,
            slot_index=5,
        ),
    )

    legacy = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=0.8,
    )
    explicit_none = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=0.8,
        timing_humanize=None,
        velocity_humanize=None,
    )
    neutral_a = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=0.8,
        timing_humanize=0.0,
        velocity_humanize=0.0,
    )
    neutral_b = render_performance_bass_midi(
        source,
        tempo=120,
        program=33,
        expression_amount=0.8,
        timing_humanize=0.0,
        velocity_humanize=0.0,
    )

    assert legacy == explicit_none
    assert neutral_a == neutral_b
    legacy_note = _read(legacy).instruments[0].notes[0]
    neutral_note = _read(neutral_a).instruments[0].notes[0]
    assert neutral_note.start == pytest.approx(source[0].start, abs=0.001)
    assert neutral_note.velocity == source[0].velocity
    assert (
        legacy_note.start != pytest.approx(neutral_note.start, abs=0.0001)
        or legacy_note.velocity != neutral_note.velocity
    )
