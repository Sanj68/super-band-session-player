from __future__ import annotations

from types import SimpleNamespace

from app.services.generator import generate_drums
from app.services.session_context import build_session_context, normalize_anchor_lane


def test_normalize_anchor_lane_accepts_known_values() -> None:
    assert normalize_anchor_lane("Drums") == "drums"
    assert normalize_anchor_lane("bass") == "bass"
    assert normalize_anchor_lane("not_a_lane") is None
    assert normalize_anchor_lane(None) is None


def test_build_session_context_none_without_anchor_or_midi() -> None:
    s = SimpleNamespace(anchor_lane=None, tempo=120, bar_count=8, drum_bytes=None, bass_bytes=None, chords_bytes=None, lead_bytes=None)
    assert build_session_context(s) is None
    s.anchor_lane = "drums"
    assert build_session_context(s) is None


def test_build_session_context_structural_invariants_for_drum_anchor() -> None:
    drum_bytes, _ = generate_drums(tempo=110, bar_count=8, drum_style="straight", context=None)
    s = SimpleNamespace(
        anchor_lane="drums",
        tempo=110,
        bar_count=8,
        key="C",
        scale="major",
        drum_bytes=drum_bytes,
        bass_bytes=None,
        chords_bytes=None,
        lead_bytes=None,
    )
    ctx = build_session_context(s)
    assert ctx is not None
    assert ctx.anchor_lane == "drums"
    assert ctx.bar_count == 8
    assert len(ctx.density_per_bar) == 8
    assert len(ctx.slot_occupancy) == 8
    assert len(ctx.kick_slot_weight) == 8
    assert len(ctx.snare_slot_weight) == 8
    assert all(len(row) == 16 for row in ctx.slot_occupancy)
    assert all(len(row) == 16 for row in ctx.kick_slot_weight)
    assert all(len(row) == 16 for row in ctx.snare_slot_weight)
    assert 0.0 <= ctx.syncopation_score <= 1.0
    assert 0.0 <= ctx.beat_phase_confidence <= 1.0
    assert 0 <= ctx.beat_phase_offset_beats <= 3
    assert len(ctx.harmonic_target_pcs_per_bar) == 8
    assert len(ctx.harmonic_confidence_per_bar) == 8
