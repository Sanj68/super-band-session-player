"""v0.4a Step 1: explicit seed produces deterministic bass MIDI bytes.

The default behaviour (seed=None) must remain unchanged: the function uses
the global ``random`` module and is non-deterministic across calls.
"""

from __future__ import annotations

import random
from types import SimpleNamespace

from app.models.session import GrooveProfile, HarmonyPlan, SourceAnalysis
from app.services import generator
from app.services.bass_generator import generate_bass
from app.services.bass_phrase_engine_v2 import generate_bass_phrase_v2
from app.services.conditioning import build_unified_conditioning
from app.services.midi_note_extract import extract_lane_notes


_BASE_KW = dict(
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


def test_same_seed_same_bytes_baseline() -> None:
    a, _ = generate_bass(seed=12345, **_BASE_KW)
    b, _ = generate_bass(seed=12345, **_BASE_KW)
    assert a == b
    assert len(a) > 0


def test_different_seeds_diverge_baseline() -> None:
    a, _ = generate_bass(seed=12345, **_BASE_KW)
    b, _ = generate_bass(seed=99999, **_BASE_KW)
    # Different seeds should change something musical: bytes or note signature.
    notes_a = [(n.pitch, round(n.start, 3), round(n.end, 3)) for n in extract_lane_notes(a)]
    notes_b = [(n.pitch, round(n.start, 3), round(n.end, 3)) for n in extract_lane_notes(b)]
    assert a != b or notes_a != notes_b


def test_same_seed_same_bytes_phrase_v2() -> None:
    kw = dict(
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
    a, _ = generate_bass_phrase_v2(seed=4242, **kw)
    b, _ = generate_bass_phrase_v2(seed=4242, **kw)
    assert a == b
    assert len(a) > 0


def test_different_seeds_diverge_phrase_v2() -> None:
    kw = dict(
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
    a, _ = generate_bass_phrase_v2(seed=4242, **kw)
    b, _ = generate_bass_phrase_v2(seed=9090, **kw)
    notes_a = [(n.pitch, round(n.start, 3), round(n.end, 3)) for n in extract_lane_notes(a)]
    notes_b = [(n.pitch, round(n.start, 3), round(n.end, 3)) for n in extract_lane_notes(b)]
    assert a != b or notes_a != notes_b


def test_seed_none_does_not_crash_baseline() -> None:
    data, preview = generate_bass(seed=None, **_BASE_KW)
    assert isinstance(data, bytes) and len(data) > 0
    assert isinstance(preview, str) and preview


def test_seed_none_does_not_crash_phrase_v2() -> None:
    data, preview = generate_bass_phrase_v2(
        tempo=100,
        bar_count=4,
        key="C",
        scale="natural_minor",
        bass_style="supportive",
        bass_instrument="finger_bass",
        bass_player="pino",
        session_preset=None,
        context=None,
        seed=None,
    )
    assert isinstance(data, bytes) and len(data) > 0
    assert isinstance(preview, str) and preview


def test_seed_none_keeps_global_random_behaviour_baseline() -> None:
    """seed=None must use the module-level random; calls under the same global
    seed should reproduce, calls under different global seeds should differ."""
    random.seed(20260502)
    a, _ = generate_bass(seed=None, **_BASE_KW)
    random.seed(20260502)
    b, _ = generate_bass(seed=None, **_BASE_KW)
    assert a == b

    random.seed(11111)
    c, _ = generate_bass(seed=None, **_BASE_KW)
    notes_a = [(n.pitch, round(n.start, 3)) for n in extract_lane_notes(a)]
    notes_c = [(n.pitch, round(n.start, 3)) for n in extract_lane_notes(c)]
    assert a != c or notes_a != notes_c


def test_seed_passthrough_via_generator_wrapper() -> None:
    a, _ = generator.generate_bass(seed=77, **_BASE_KW)
    b, _ = generator.generate_bass(seed=77, **_BASE_KW)
    assert a == b


def _source_conditioning_for_minor_riff(bar_count: int = 8):
    kick = [[0.08] * 16 for _ in range(bar_count)]
    pressure = [[0.2] * 16 for _ in range(bar_count)]
    snare = [[0.05] * 16 for _ in range(bar_count)]
    for bar in range(bar_count):
        kick[bar][0] = 0.9
        kick[bar][8] = 0.7
        kick[bar][7 if bar % 2 == 0 else 10] = 0.86
        pressure[bar][7 if bar % 2 == 0 else 10] = 0.8
    src = SourceAnalysis(
        source_lane="reference_audio",
        tempo=118,
        tempo_estimate_bpm=117.5,
        tempo_confidence=0.8,
        beat_grid_seconds=[(60.0 / 118.0) * i for i in range(bar_count * 4)],
        bar_starts_seconds=[(60.0 / 118.0) * 4.0 * i for i in range(bar_count)],
        beat_phase_offset_beats=0,
        beat_phase_scores=[1.0, 0.0, 0.0, 0.0],
        beat_phase_confidence=0.7,
        phase_offset_used_for_generation_beats=0,
        bar_start_anchor_used_seconds=0.0,
        generation_aligned_to_anchor=False,
        downbeat_guess_bar_index=0,
        downbeat_confidence=0.6,
        bar_start_confidence=0.65,
        tonal_center_pc_guess=6,
        tonal_center_confidence=0.5,
        scale_mode_guess="natural_minor",
        scale_mode_confidence=0.5,
        sections=[],
        bar_energy=[0.5] * bar_count,
        bar_accent_profile=[0.4] * bar_count,
        bar_confidence_profile=[0.6] * bar_count,
        source_kick_weight=kick,
        source_slot_pressure=pressure,
        source_snare_weight=snare,
        source_onset_weight=pressure,
        source_groove_confidence=[0.55] * bar_count,
    )
    groove = GrooveProfile(
        pocket_feel="steady",
        syncopation_score=0.35,
        density_per_bar_estimate=4.0,
        accent_strength=0.5,
        confidence=0.55,
    )
    harmony = HarmonyPlan(key_center="F#", scale="natural_minor", source="static_progression", bars=[])
    return build_unified_conditioning(
        session=SimpleNamespace(bar_count=bar_count, tempo=118),
        source=src,
        groove=groove,
        harmony=harmony,
        context=None,
    )


def test_same_seed_same_bytes_source_minor_riff_path() -> None:
    kw = dict(_BASE_KW)
    kw.update(
        tempo=118,
        bar_count=8,
        key="F#",
        scale="natural_minor",
        bass_player=None,
        chord_progression=["F#m"],
        conditioning=_source_conditioning_for_minor_riff(8),
    )
    a, _ = generate_bass(seed=8105, **kw)
    b, _ = generate_bass(seed=8105, **kw)
    assert a == b
