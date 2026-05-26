"""16-step drum groove generation contracts."""

from __future__ import annotations

import io
import random

import pretty_midi
import pytest

from app.services.drum_generator import (
    normalize_drum_kit,
    normalize_drum_player,
    normalize_drum_style,
    generate_drums,
)


_TEMPO = 120
_SIXTEENTH = 60.0 / _TEMPO / 4.0
_BAR_SECONDS = 4.0 * 60.0 / _TEMPO


def _notes(data: bytes) -> list[pretty_midi.Note]:
    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    assert len(pm.instruments) == 1
    assert pm.instruments[0].is_drum is True
    return pm.instruments[0].notes


@pytest.mark.parametrize("style", ["straight", "broken", "funk", "latin", "laid_back_soul"])
def test_sixteen_step_styles_emit_notes_inside_requested_bars(style: str) -> None:
    random.seed(100)
    data, preview = generate_drums(tempo=_TEMPO, bar_count=2, drum_style=style)
    notes = _notes(data)

    assert notes
    assert f"Drums [{style}" in preview
    assert min(n.start for n in notes) >= 0.0
    assert max(n.end for n in notes) <= (2 * _BAR_SECONDS) + _SIXTEENTH
    assert {int(n.pitch) for n in notes}.issubset({36, 37, 38, 42, 43, 46})


def test_straight_engine_keeps_backbeat_on_sixteen_slot_grid() -> None:
    random.seed(3)
    data, _preview = generate_drums(tempo=_TEMPO, bar_count=1, drum_style="straight")
    snares = [n for n in _notes(data) if int(n.pitch) == 38 and n.velocity >= 80]
    snare_slots = {round(n.start / _SIXTEENTH) % 16 for n in snares}

    assert snare_slots & {3, 4, 5, 6}
    assert snare_slots & {11, 12, 13, 14}


def test_percussion_kit_adds_auxiliary_pitches_without_removing_core_drums() -> None:
    random.seed(11)
    data, preview = generate_drums(tempo=_TEMPO, bar_count=4, drum_style="latin", drum_kit="percussion")
    pitches = {int(n.pitch) for n in _notes(data)}

    assert "kit=percussion" in preview
    assert pitches & {36, 38, 42}
    assert pitches & {56, 60, 61, 62}


def test_dry_kit_reduces_velocity_against_same_random_stream() -> None:
    random.seed(2)
    standard, _ = generate_drums(tempo=_TEMPO, bar_count=1, drum_style="funk", drum_kit="standard")
    random.seed(2)
    dry, _ = generate_drums(tempo=_TEMPO, bar_count=1, drum_style="funk", drum_kit="dry")

    standard_velocities = sorted(n.velocity for n in _notes(standard))
    dry_velocities = sorted(n.velocity for n in _notes(dry))

    assert len(standard_velocities) == len(dry_velocities)
    assert sum(dry_velocities) < sum(standard_velocities)


def test_normalizers_fallback_to_safe_defaults() -> None:
    assert normalize_drum_style("unknown") == "straight"
    assert normalize_drum_style(" FUNK ") == "funk"
    assert normalize_drum_kit("bad") == "standard"
    assert normalize_drum_player("off") is None
    assert normalize_drum_player(" dilla ") == "dilla"
