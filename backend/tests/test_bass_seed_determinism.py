"""v0.4a Step 1: explicit seed produces deterministic bass MIDI bytes.

The default behaviour (seed=None) must remain unchanged: the function uses
the global ``random`` module and is non-deterministic across calls.
"""

from __future__ import annotations

import random

from app.services import generator
from app.services.bass_generator import generate_bass
from app.services.bass_phrase_engine_v2 import generate_bass_phrase_v2
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
