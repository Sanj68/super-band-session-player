from __future__ import annotations

from app.models.session import LaneNote
from app.services.bass_quality import analyze_bass_take


def _make_note(pitch: int, start: float, dur: float = 0.22, velocity: int = 92) -> LaneNote:
    return LaneNote(pitch=pitch, start=start, end=start + dur, velocity=velocity)


def _simple_two_bar_phrase(*, root_pitch: int) -> list[LaneNote]:
    # Two bars at 100 BPM: bar length = 2.4s.
    starts = [0.0, 0.6, 1.2, 1.8, 2.4, 3.0, 3.6, 4.2]
    pattern = [0, 5, 7, 0, 0, 7, 5, 0]
    return [_make_note(root_pitch + step, t) for step, t in zip(pattern, starts, strict=False)]


def test_analyze_bass_take_returns_expected_shape_and_bounded_values() -> None:
    notes = _simple_two_bar_phrase(root_pitch=36)
    quality = analyze_bass_take(
        notes,
        tempo=100,
        bar_count=2,
        key="C",
        scale="major",
        style="supportive",
    )

    expected_keys = {
        "harmonic_fit",
        "groove_fit",
        "phrase_shape",
        "register_discipline",
        "repetition_variation",
        "style_match",
        "avoid_tone_control",
        "space_rest_quality",
    }
    assert set(quality.scores.keys()) == expected_keys
    assert 0.0 <= quality.total <= 1.0
    assert len(quality.signature) == 2
    assert isinstance(quality.reason, str) and quality.reason
    assert "strong " in quality.reason
    assert "watch " in quality.reason
    for value in quality.scores.values():
        assert 0.0 <= value <= 1.0


def test_analyze_bass_take_penalizes_register_abuse() -> None:
    good = _simple_two_bar_phrase(root_pitch=36)
    too_high = _simple_two_bar_phrase(root_pitch=76)

    good_q = analyze_bass_take(
        good,
        tempo=100,
        bar_count=2,
        key="C",
        scale="major",
        style="supportive",
    )
    high_q = analyze_bass_take(
        too_high,
        tempo=100,
        bar_count=2,
        key="C",
        scale="major",
        style="supportive",
    )

    assert good_q.scores["register_discipline"] > high_q.scores["register_discipline"]
    assert good_q.total > high_q.total
