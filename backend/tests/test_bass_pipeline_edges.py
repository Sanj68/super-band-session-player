"""Bass articulation and performance rendering edge coverage."""

from __future__ import annotations

import io

import pretty_midi
import pytest

from app.services import bass_phrase_engine_v2 as phrase_v2
from app.services.bass_articulation import ghost_eligibility, shape_note
from app.services.bass_performance import BassPerformanceNote, infer_bass_articulations
from app.services.bass_performance_render import render_performance_bass_midi
from app.services.session_context import SessionAnchorContext


def _read(data: bytes) -> pretty_midi.PrettyMIDI:
    return pretty_midi.PrettyMIDI(io.BytesIO(data))


def test_shape_note_supportive_anchor_extends_and_accents_downbeat() -> None:
    new_end, new_velocity = shape_note(
        start=0.0,
        end=0.25,
        velocity=80,
        slot=0,
        role="anchor",
        style="supportive",
        is_structural=True,
        cadence_bias=0.0,
        sustain_mult=1.5,
        bar_end=2.0,
    )

    assert new_end > 0.25
    assert new_velocity > 80


def test_shape_note_never_runs_past_bar_end() -> None:
    new_end, _velocity = shape_note(
        start=1.95,
        end=2.2,
        velocity=90,
        slot=15,
        role="release",
        style="fusion",
        is_structural=True,
        cadence_bias=1.0,
        sustain_mult=1.25,
        bar_end=2.0,
    )

    assert new_end == pytest.approx(1.9999)


def test_ghost_eligibility_rejects_non_ghost_styles_and_reduces_dense_cadences() -> None:
    assert ghost_eligibility(style="melodic", role="answer", cadence_bias=0.0, bar_density=2.0) == 0.0
    loose = ghost_eligibility(style="rhythmic", role="answer", cadence_bias=0.0, bar_density=4.0)
    dense = ghost_eligibility(style="rhythmic", role="answer", cadence_bias=1.0, bar_density=20.0)

    assert 0.0 < dense < loose <= 1.0


def test_infer_articulations_sorts_for_next_note_but_preserves_input_order() -> None:
    target = BassPerformanceNote(pitch=40, start=0.27, end=0.7, velocity=88)
    grace = BassPerformanceNote(pitch=39, start=0.18, end=0.23, velocity=50)

    inferred = infer_bass_articulations((target, grace), tempo=100)

    assert inferred[0].pitch == 40
    assert inferred[0].articulation == "normal"
    assert inferred[1].pitch == 39
    assert inferred[1].articulation == "grace"


def test_renderer_snare_without_kick_attenuates_velocity() -> None:
    note = BassPerformanceNote(
        pitch=40,
        start=0.25,
        end=0.75,
        velocity=80,
        role="anchor",
        bar_index=0,
        slot_index=4,
    )
    row = tuple(0.0 for _ in range(16))
    snare_row = tuple(1.0 if i == 4 else 0.0 for i in range(16))

    baseline = _read(render_performance_bass_midi((note,), tempo=120, program=33)).instruments[0].notes[0]
    shaped = _read(
        render_performance_bass_midi(
            (note,),
            tempo=120,
            program=33,
            source_kick_per_bar=(row,),
            source_snare_per_bar=(snare_row,),
        )
    ).instruments[0].notes[0]

    assert shaped.velocity < baseline.velocity


def test_renderer_clamps_source_grid_indices_and_values() -> None:
    note = BassPerformanceNote(
        pitch=40,
        start=0.0,
        end=0.5,
        velocity=80,
        role="anchor",
        bar_index=99,
        slot_index=99,
    )
    row = tuple([0.0] * 15 + [2.0])

    rendered = _read(
        render_performance_bass_midi(
            (note,),
            tempo=120,
            program=33,
            source_kick_per_bar=(row,),
        )
    ).instruments[0].notes[0]

    assert rendered.velocity > 80


def test_phrase_v2_normalizers_and_midi_programs_use_safe_fallbacks() -> None:
    assert phrase_v2.normalize_bass_style(None) == "supportive"
    assert phrase_v2.normalize_bass_style(" FUSION ") == "fusion"
    assert phrase_v2.normalize_bass_style("busy") == "supportive"
    assert phrase_v2.normalize_bass_player("off") is None
    assert phrase_v2.normalize_bass_player("unknown") is None
    assert phrase_v2.normalize_bass_instrument("bad") == "finger_bass"
    assert phrase_v2.bass_midi_program("slap_bass", "supportive") == 36
    assert phrase_v2.bass_midi_program("synth_bass", "supportive") == 38
    assert phrase_v2.bass_midi_program("finger_bass", "slap") == 36


def test_phrase_v2_slot_selection_keeps_zero_and_caps_density() -> None:
    assert phrase_v2._bar_role(0, 2) == "anchor"
    assert phrase_v2._bar_role(1, 2) == "answer"
    assert phrase_v2._bar_role(3, 4) == "release"

    slots = phrase_v2._phrase_slots("push", [15, 2, 9, 11])

    assert slots[0] == 0
    assert slots == sorted(set(slots))
    assert len(slots) <= 5
    assert all(0 <= slot <= 15 for slot in slots)


def test_phrase_v2_pick_pitch_prefers_roots_on_strong_or_anchor_slots() -> None:
    class Chooser:
        def random(self) -> float:
            return 0.0

        def choice(self, values: list[int]) -> int:
            return values[0]

    strong = phrase_v2._pick_pitch(
        4,
        "answer",
        root_pc=2,
        stable_pcs=[6],
        passing_pcs=[1],
        avoid_pcs=[],
        conf=1.0,
        rng=Chooser(),
    )
    offbeat = phrase_v2._pick_pitch(
        5,
        "answer",
        root_pc=2,
        stable_pcs=[6],
        passing_pcs=[1],
        avoid_pcs=[],
        conf=1.0,
        rng=Chooser(),
    )
    avoid = phrase_v2._pick_pitch(
        5,
        "answer",
        root_pc=2,
        stable_pcs=[6],
        passing_pcs=[],
        avoid_pcs=[6],
        conf=0.0,
        rng=Chooser(),
    )

    assert strong % 12 == 2
    assert offbeat % 12 == 1
    assert avoid % 12 == 2


def test_phrase_v2_harmonic_plan_reads_context_before_scale_fallback() -> None:
    ctx = SessionAnchorContext(
        tempo=120,
        bar_count=1,
        anchor_lane="drums",
        bar_len_sec=2.0,
        beat_len_sec=0.5,
        sixteenth_len_sec=0.125,
        density_per_bar=(4.0,),
        onsets_norm_per_bar=((0.0,),),
        gap_sec_per_bar=((),),
        mean_gap_sec_per_bar=(0.0,),
        pitch_min=36,
        pitch_max=38,
        pitch_span=2,
        syncopation_score=0.0,
        mean_density=4.0,
        slot_occupancy=((0.0,) * 16,),
        kick_slot_weight=((0.0,) * 16,),
        snare_slot_weight=((0.0,) * 16,),
        beat_phase_offset_beats=0,
        beat_phase_confidence=0.8,
        bar_start_anchor_sec=0.0,
        harmonic_root_pc_per_bar=(9,),
        harmonic_target_pcs_per_bar=((9, 0, 4),),
        harmonic_passing_pcs_per_bar=((11, 2),),
        harmonic_avoid_pcs_per_bar=((1, 3, 6, 8, 10),),
        harmonic_confidence_per_bar=(0.77,),
        harmonic_source_per_bar=("test",),
    )

    assert phrase_v2._harmonic_bar_plan(0, key="C", scale="major", context=ctx) == (
        9,
        [9, 0, 4],
        [11, 2],
        [1, 3, 6, 8, 10],
        0.77,
    )


def test_phrase_v2_generation_can_return_performance_notes() -> None:
    data, preview, notes = phrase_v2.generate_bass_phrase_v2(
        tempo=120,
        bar_count=2,
        key="C",
        scale="major",
        bass_style="supportive",
        seed=5,
        return_performance_notes=True,
    )

    assert isinstance(data, bytes)
    assert "phrase_v2" in preview
    assert notes
    assert {note.source for note in notes} == {"phrase_v2"}
    assert all(note.bar_index in (0, 1) for note in notes)
