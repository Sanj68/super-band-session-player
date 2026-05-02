"""v0.5 Step 2: optional performance-note capture from bass generation.

Step 2 contract:
- Default ``return_performance_notes=False`` returns the existing 2-tuple unchanged.
- ``return_performance_notes=True`` returns a 3-tuple whose third element is a
  faithful structural mirror of the emitted MIDI notes — same count, same
  pitch/velocity/start/end, ``articulation="normal"`` everywhere (Step 2 does
  not infer articulation), engine-correct ``source`` tags.
- The capture path must NOT perturb generated MIDI bytes.
"""

from __future__ import annotations

from app.services import generator
from app.services.bass_generator import generate_bass
from app.services.bass_performance import BassPerformanceNote, _VALID_ARTICULATIONS
from app.services.bass_phrase_engine_v2 import generate_bass_phrase_v2
from app.services.midi_note_extract import extract_lane_notes


_BASELINE_KW = dict(
    tempo=92,
    bar_count=8,
    key="C",
    scale="natural_minor",
    bass_style="supportive",
    bass_instrument="finger_bass",
    bass_player="pino",
    bass_engine="baseline",
    chord_progression=None,
    session_preset=None,
    context=None,
    conditioning=None,
)

_PHRASE_V2_KW = dict(
    tempo=100,
    bar_count=8,
    key="C",
    scale="natural_minor",
    bass_style="supportive",
    bass_instrument="finger_bass",
    bass_player="pino",
    session_preset=None,
    context=None,
)


def _midi_note_quad(n) -> tuple[int, int, float, float]:
    return (int(n.pitch), int(n.velocity), float(n.start), float(n.end))


def _perf_quad(p: BassPerformanceNote) -> tuple[int, int, float, float]:
    return (int(p.pitch), int(p.velocity), float(p.start), float(p.end))


def test_default_return_shape_baseline_is_two_tuple() -> None:
    result = generate_bass(seed=12345, **_BASELINE_KW)
    assert isinstance(result, tuple) and len(result) == 2
    data, preview = result
    assert isinstance(data, bytes) and len(data) > 0
    assert isinstance(preview, str)


def test_default_return_shape_phrase_v2_is_two_tuple() -> None:
    result = generate_bass_phrase_v2(seed=12345, **_PHRASE_V2_KW)
    assert isinstance(result, tuple) and len(result) == 2


def test_capture_return_shape_baseline_is_three_tuple() -> None:
    result = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    assert isinstance(result, tuple) and len(result) == 3
    data, preview, perf = result
    assert isinstance(data, bytes) and len(data) > 0
    assert isinstance(preview, str)
    assert isinstance(perf, tuple) and len(perf) > 0
    assert all(isinstance(p, BassPerformanceNote) for p in perf)


def test_capture_return_shape_phrase_v2_is_three_tuple() -> None:
    result = generate_bass_phrase_v2(seed=12345, return_performance_notes=True, **_PHRASE_V2_KW)
    assert isinstance(result, tuple) and len(result) == 3
    data, preview, perf = result
    assert isinstance(data, bytes) and len(data) > 0
    assert isinstance(perf, tuple) and len(perf) > 0
    assert all(isinstance(p, BassPerformanceNote) for p in perf)


def test_capture_does_not_perturb_baseline_midi_bytes() -> None:
    data_off, _ = generate_bass(seed=12345, **_BASELINE_KW)
    data_on, _, _ = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    assert data_off == data_on


def test_capture_does_not_perturb_phrase_v2_midi_bytes() -> None:
    data_off, _ = generate_bass_phrase_v2(seed=4242, **_PHRASE_V2_KW)
    data_on, _, _ = generate_bass_phrase_v2(seed=4242, return_performance_notes=True, **_PHRASE_V2_KW)
    assert data_off == data_on


def test_baseline_perf_count_matches_midi_note_count() -> None:
    data, _, perf = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    midi_notes = extract_lane_notes(data)
    assert len(perf) == len(midi_notes) > 0


def test_phrase_v2_perf_count_matches_midi_note_count() -> None:
    data, _, perf = generate_bass_phrase_v2(seed=4242, return_performance_notes=True, **_PHRASE_V2_KW)
    midi_notes = extract_lane_notes(data)
    assert len(perf) == len(midi_notes) > 0


def _assert_quads_mirror(midi_quads, perf_quads, *, time_tol: float) -> None:
    """Pitch/velocity must match exactly; start/end may differ by up to ``time_tol``
    seconds because the MIDI read-back path quantizes to ticks while captured
    BassPerformanceNote floats are pre-quantization."""
    assert len(midi_quads) == len(perf_quads)
    for (mp, mv, ms, me), (pp, pv, ps, pe) in zip(midi_quads, perf_quads):
        assert mp == pp
        assert mv == pv
        assert abs(ms - ps) <= time_tol
        assert abs(me - pe) <= time_tol


def test_baseline_perf_mirrors_midi_pitch_velocity_start_end() -> None:
    data, _, perf = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    midi_notes = extract_lane_notes(data)
    midi_quads = sorted(_midi_note_quad(n) for n in midi_notes)
    perf_quads = sorted(_perf_quad(p) for p in perf)
    # Default ppq=220 at 92 BPM → ~3ms/tick. Allow 5ms slack.
    _assert_quads_mirror(midi_quads, perf_quads, time_tol=0.005)


def test_phrase_v2_perf_mirrors_midi_pitch_velocity_start_end() -> None:
    data, _, perf = generate_bass_phrase_v2(seed=4242, return_performance_notes=True, **_PHRASE_V2_KW)
    midi_notes = extract_lane_notes(data)
    midi_quads = sorted(_midi_note_quad(n) for n in midi_notes)
    perf_quads = sorted(_perf_quad(p) for p in perf)
    _assert_quads_mirror(midi_quads, perf_quads, time_tol=0.005)


def test_baseline_source_is_baseline() -> None:
    _, _, perf = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    assert perf
    assert all(p.source == "baseline" for p in perf)


def test_phrase_v2_source_is_phrase_v2() -> None:
    _, _, perf = generate_bass_phrase_v2(seed=4242, return_performance_notes=True, **_PHRASE_V2_KW)
    assert perf
    assert all(p.source == "phrase_v2" for p in perf)


def test_baseline_routed_via_generate_bass_engine_phrase_v2_keeps_source() -> None:
    """generate_bass(..., bass_engine='phrase_v2') forwards to phrase_v2; source must reflect that."""
    kw = dict(_BASELINE_KW)
    kw["bass_engine"] = "phrase_v2"
    _, _, perf = generate_bass(seed=4242, return_performance_notes=True, **kw)
    assert perf
    assert all(p.source == "phrase_v2" for p in perf)


def test_articulation_vocabulary_is_valid_baseline() -> None:
    _, _, perf = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    assert perf
    assert {p.articulation for p in perf}.issubset(_VALID_ARTICULATIONS)


def test_articulation_vocabulary_is_valid_phrase_v2() -> None:
    _, _, perf = generate_bass_phrase_v2(seed=4242, return_performance_notes=True, **_PHRASE_V2_KW)
    assert perf
    assert {p.articulation for p in perf}.issubset(_VALID_ARTICULATIONS)


def test_baseline_bar_and_slot_indexes_in_range() -> None:
    _, _, perf = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    assert perf
    bar_count = int(_BASELINE_KW["bar_count"])
    for p in perf:
        assert p.bar_index is not None
        assert 0 <= p.bar_index < bar_count
        assert p.slot_index is not None
        assert 0 <= p.slot_index <= 15


def test_phrase_v2_bar_and_slot_indexes_in_range() -> None:
    _, _, perf = generate_bass_phrase_v2(seed=4242, return_performance_notes=True, **_PHRASE_V2_KW)
    assert perf
    bar_count = int(_PHRASE_V2_KW["bar_count"])
    for p in perf:
        assert p.bar_index is not None
        assert 0 <= p.bar_index < bar_count
        assert p.slot_index is not None
        assert 0 <= p.slot_index <= 15


def test_baseline_role_is_known_phrase_role() -> None:
    valid_roles = {"anchor", "answer", "push", "release"}
    _, _, perf = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    assert perf
    assert {p.role for p in perf}.issubset(valid_roles)


def test_phrase_v2_role_is_known_phrase_role() -> None:
    # phrase_v2's _bar_role returns from {"anchor", "push", "release", "answer"}
    valid_roles = {"anchor", "answer", "push", "release"}
    _, _, perf = generate_bass_phrase_v2(seed=4242, return_performance_notes=True, **_PHRASE_V2_KW)
    assert perf
    assert {p.role for p in perf}.issubset(valid_roles)


def test_baseline_confidence_is_none_in_step2() -> None:
    _, _, perf = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    assert perf
    assert all(p.confidence is None for p in perf)


def test_phrase_v2_confidence_is_none_in_step2() -> None:
    _, _, perf = generate_bass_phrase_v2(seed=4242, return_performance_notes=True, **_PHRASE_V2_KW)
    assert perf
    assert all(p.confidence is None for p in perf)


def test_same_seed_same_perf_tuple_baseline() -> None:
    _, _, a = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    _, _, b = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    assert a == b


def test_same_seed_same_perf_tuple_phrase_v2() -> None:
    _, _, a = generate_bass_phrase_v2(seed=4242, return_performance_notes=True, **_PHRASE_V2_KW)
    _, _, b = generate_bass_phrase_v2(seed=4242, return_performance_notes=True, **_PHRASE_V2_KW)
    assert a == b


def test_different_seeds_diverge_perf_tuple_baseline() -> None:
    _, _, a = generate_bass(seed=12345, return_performance_notes=True, **_BASELINE_KW)
    _, _, b = generate_bass(seed=99999, return_performance_notes=True, **_BASELINE_KW)
    assert a != b


def test_generator_wrapper_passes_flag_through_default_off() -> None:
    result = generator.generate_bass(seed=77, **_BASELINE_KW)
    assert isinstance(result, tuple) and len(result) == 2


def test_generator_wrapper_passes_flag_through_capture_on() -> None:
    result = generator.generate_bass(seed=77, return_performance_notes=True, **_BASELINE_KW)
    assert isinstance(result, tuple) and len(result) == 3
    _, _, perf = result
    assert isinstance(perf, tuple) and len(perf) > 0
    assert all(isinstance(p, BassPerformanceNote) for p in perf)
    assert all(p.source == "baseline" for p in perf)
