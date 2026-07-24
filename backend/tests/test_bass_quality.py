from __future__ import annotations

from app.models.session import GrooveProfile, LaneNote
from app.services.bass_quality import analyze_bass_take, count_unsupported_structural_notes
from app.services.conditioning import ConditioningHarmonicBar, UnifiedConditioning
from app.services.bass_phrase_engine_v2 import generate_bass_phrase_v2
from app.services.midi_note_extract import extract_lane_notes


def _make_note(pitch: int, start: float, dur: float = 0.22, velocity: int = 92) -> LaneNote:
    return LaneNote(pitch=pitch, start=start, end=start + dur, velocity=velocity)


def _simple_two_bar_phrase(*, root_pitch: int) -> list[LaneNote]:
    # Two bars at 100 BPM: bar length = 2.4s.
    starts = [0.0, 0.6, 1.2, 1.8, 2.4, 3.0, 3.6, 4.2]
    pattern = [0, 5, 7, 0, 0, 7, 5, 0]
    return [_make_note(root_pitch + step, t) for step, t in zip(pattern, starts, strict=False)]


def _confirmed_c_major_conditioning() -> UnifiedConditioning:
    return UnifiedConditioning(
        tempo=120,
        bar_count=1,
        beat_phase_offset_beats=0,
        beat_phase_confidence=1.0,
        bar_start_anchor_sec=0.0,
        beat_grid_seconds=(0.0, 0.5, 1.0, 1.5),
        bar_starts_seconds=(0.0,),
        sections=(),
        groove_profile=GrooveProfile(
            pocket_feel="straight",
            syncopation_score=0.0,
            density_per_bar_estimate=2.0,
            accent_strength=0.5,
            confidence=1.0,
        ),
        harmonic_bars=(
            ConditioningHarmonicBar(
                bar_index=0,
                root_pc=0,
                target_pcs=(0, 4, 7),
                passing_pcs=(2, 5, 9, 11),
                avoid_pcs=(1, 3, 6, 8, 10),
                confidence=1.0,
                source="confirmed_chord_progression",
            ),
        ),
    )


def test_confirmed_map_rejects_scale_legal_note_that_does_not_resolve_to_chord() -> None:
    conditioning = _confirmed_c_major_conditioning()
    notes = [
        _make_note(46, 0.875, dur=0.075),  # Bb on last sixteenth
        _make_note(43, 1.0),               # resolves by a minor third, not an approach
    ]

    assert count_unsupported_structural_notes(
        notes,
        tempo=120,
        conditioning=conditioning,
        style="supportive",
    ) == 1


def test_confirmed_map_allows_chord_tones_and_stepwise_sixteenth_approach() -> None:
    conditioning = _confirmed_c_major_conditioning()
    notes = [
        _make_note(36, 0.0),
        _make_note(47, 0.875, dur=0.075),  # B approaches C
        _make_note(48, 1.0),
        _make_note(43, 1.5),
    ]

    assert count_unsupported_structural_notes(
        notes,
        tempo=120,
        conditioning=conditioning,
        style="supportive",
    ) == 0


def test_supportive_phrase_engine_does_not_invent_scale_passing_over_confirmed_chart() -> None:
    conditioning = _confirmed_c_major_conditioning()

    for seed in range(1, 7):
        data, _preview = generate_bass_phrase_v2(
            tempo=120,
            bar_count=1,
            key="C",
            scale="major",
            bass_style="supportive",
            conditioning=conditioning,
            seed=seed,
        )
        notes = extract_lane_notes(data)
        assert notes
        assert {note.pitch % 12 for note in notes}.issubset({0, 4, 7})


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
