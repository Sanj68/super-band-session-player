from __future__ import annotations

import io
import random

import pretty_midi

from app.services.bass_generator import generate_bass


def test_supportive_bass_seeded_smoke_is_repeatable_and_valid() -> None:
    random.seed(20260426)
    b1, preview1 = generate_bass(
        tempo=100,
        bar_count=8,
        key="B",
        scale="minor",
        bass_style="supportive",
        context=None,
    )
    random.seed(20260426)
    b2, preview2 = generate_bass(
        tempo=100,
        bar_count=8,
        key="B",
        scale="minor",
        bass_style="supportive",
        context=None,
    )
    assert b1 == b2
    assert preview1 == preview2

    pm = pretty_midi.PrettyMIDI(io.BytesIO(b1))
    notes = [n for inst in pm.instruments for n in inst.notes]
    assert len(notes) >= 8  # at least one note per bar on average
    total_len = (60.0 / 100.0) * 4.0 * 8.0
    eps = 1e-3
    for n in notes:
        assert 0.0 <= n.start < total_len
        assert 0.0 < n.end <= total_len + eps
        assert n.end > n.start
        assert 0 <= n.pitch <= 127
        assert 1 <= n.velocity <= 127
