"""GrooveFrame model: clamping, normalization, and 16-slot guarantee."""

from __future__ import annotations

import math

from app.models.groove_frame import GROOVE_SLOTS, GrooveFrame


def test_groove_frame_pads_short_rows_to_sixteen() -> None:
    f = GrooveFrame(
        bar_index=0,
        kick_weight=[0.5, 0.5, 0.5],
        slot_pressure=[0.2],
    )
    assert len(f.kick_weight) == GROOVE_SLOTS
    assert len(f.slot_pressure) == GROOVE_SLOTS
    assert f.kick_weight[3] == 0.0
    assert f.slot_pressure[1] == 0.0


def test_groove_frame_truncates_long_rows() -> None:
    f = GrooveFrame(bar_index=2, onset_weight=[0.1] * 25)
    assert len(f.onset_weight) == GROOVE_SLOTS


def test_groove_frame_clamps_floats_and_handles_nan_none() -> None:
    f = GrooveFrame(
        bar_index=0,
        kick_weight=[-1.0, 2.0, math.nan, None, 0.5] + [0.0] * 11,
        confidence=2.5,
    )
    assert f.kick_weight[0] == 0.0
    assert f.kick_weight[1] == 1.0
    assert f.kick_weight[2] == 0.0
    assert f.kick_weight[3] == 0.0
    assert f.kick_weight[4] == 0.5
    assert f.confidence == 1.0


def test_groove_frame_defaults_when_nothing_provided() -> None:
    f = GrooveFrame(bar_index=0)
    assert f.resolution == GROOVE_SLOTS
    assert f.confidence == 0.0
    assert f.source_tag == "reference_audio"
    assert f.kick_weight == [0.0] * GROOVE_SLOTS
    assert f.snare_weight == [0.0] * GROOVE_SLOTS
    assert f.onset_weight == [0.0] * GROOVE_SLOTS
    assert f.slot_pressure == [0.0] * GROOVE_SLOTS


def test_groove_frame_resolution_is_clamped_but_rows_remain_sixteen_for_v071() -> None:
    f = GrooveFrame(bar_index=0, resolution=99, kick_weight=[0.5] * 32)
    assert 1 <= f.resolution <= 64
    assert f.resolution == 64
    assert len(f.kick_weight) == GROOVE_SLOTS
