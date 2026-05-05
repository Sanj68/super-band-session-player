from __future__ import annotations

from types import SimpleNamespace

from app.models.session import GrooveProfile, HarmonyPlan, SourceAnalysis
from app.services.bass_vocabulary.candidates import (
    _CANDIDATE_TEMPLATE_IDS,
    _minor7_vocabulary_allowed_pitch_classes,
    generate_template_candidate_events,
    generate_vocabulary_candidates,
)
from app.services.bass_vocabulary.pitch_roles import SUPPORTED_PITCH_ROLES
from app.services.bass_vocabulary.profile import valid_lanes
from app.services.bass_vocabulary.templates import SUB_ONE_BASS_TEMPLATES, templates_by_id
from app.services.conditioning import build_unified_conditioning


def test_at_least_9_templates_exist() -> None:
    assert len(SUB_ONE_BASS_TEMPLATES) >= 9


def test_every_template_references_valid_lane() -> None:
    lanes = valid_lanes()
    assert all(template.lane in lanes for template in SUB_ONE_BASS_TEMPLATES)


def test_template_slots_are_within_one_bar() -> None:
    for template in SUB_ONE_BASS_TEMPLATES:
        assert all(0 <= slot <= 15 for slot in template.slots)
        assert tuple(sorted(set(template.slots))) == template.slots


def test_pitch_roles_length_matches_slots_length() -> None:
    for template in SUB_ONE_BASS_TEMPLATES:
        assert len(template.pitch_roles) == len(template.slots)
        assert set(template.pitch_roles).issubset(SUPPORTED_PITCH_ROLES)


def test_no_template_is_root_only() -> None:
    for template in SUB_ONE_BASS_TEMPLATES:
        assert set(template.pitch_roles) != {"root"}


def test_no_template_uses_only_slots_0_and_8() -> None:
    for template in SUB_ONE_BASS_TEMPLATES:
        assert set(template.slots) != {0, 8}


def test_density_energy_grit_and_improvisation_are_valid() -> None:
    for template in SUB_ONE_BASS_TEMPLATES:
        assert template.density in {"low", "medium", "high"}
        assert 1 <= template.energy <= 5
        assert 1 <= template.grit <= 5
        assert 1 <= template.improvisation <= 5


def test_labelled_vocabulary_templates_anchor_bar0_slot0_root() -> None:
    by_id = templates_by_id()
    for tid in _CANDIDATE_TEMPLATE_IDS:
        t = by_id[tid]
        assert t.slots[0] == 0
        assert t.pitch_roles[0] == "root"


def _source_analysis_evil_snare_downbeat(*, bar_count: int, tempo: int) -> SourceAnalysis:
    pressure = [[0.32] * 16 for _ in range(bar_count)]
    kick = [[0.08] * 16 for _ in range(bar_count)]
    snare = [[0.06] * 16 for _ in range(bar_count)]
    for row in range(bar_count):
        snare[row][0] = 0.93
        kick[row][0] = 0.05
        snare[row][14] = 0.91
        kick[row][14] = 0.06
        for slot in (3, 6, 10):
            pressure[row][slot] = 0.72
            kick[row][slot] = 0.55
    beat = 60.0 / float(tempo)
    return SourceAnalysis(
        source_lane="reference_audio",
        tempo=tempo,
        tempo_estimate_bpm=float(tempo),
        tempo_confidence=0.85,
        beat_grid_seconds=[beat * 0.25 * i for i in range(bar_count * 4)],
        bar_starts_seconds=[beat * 4.0 * i for i in range(bar_count)],
        beat_phase_offset_beats=0,
        beat_phase_scores=[1.0, 0.0, 0.0, 0.0],
        beat_phase_confidence=0.8,
        phase_offset_used_for_generation_beats=0,
        bar_start_anchor_used_seconds=0.0,
        generation_aligned_to_anchor=False,
        downbeat_guess_bar_index=0,
        downbeat_confidence=0.7,
        bar_start_confidence=0.8,
        tonal_center_pc_guess=6,
        tonal_center_confidence=0.7,
        scale_mode_guess="minor",
        scale_mode_confidence=0.7,
        sections=[],
        bar_energy=[0.55] * bar_count,
        bar_accent_profile=[0.55] * bar_count,
        bar_confidence_profile=[0.72] * bar_count,
        source_groove_resolution=16,
        source_onset_weight=pressure,
        source_kick_weight=kick,
        source_snare_weight=snare,
        source_slot_pressure=pressure,
        source_groove_confidence=[0.82] * bar_count,
    )


def _uc_vocabulary_phrase(*, bar_count: int, tempo: int):
    groove = GrooveProfile(
        pocket_feel="steady",
        syncopation_score=0.2,
        density_per_bar_estimate=4.0,
        accent_strength=0.5,
        confidence=0.85,
    )
    harmony = HarmonyPlan(key_center="F#", scale="minor", source="static_progression", bars=[])
    src = _source_analysis_evil_snare_downbeat(bar_count=bar_count, tempo=tempo)
    return build_unified_conditioning(
        session=SimpleNamespace(bar_count=bar_count, tempo=tempo),
        source=src,
        groove=groove,
        harmony=harmony,
        context=None,
    )


def test_vocabulary_candidates_phrase_boundaries_eight_bars() -> None:
    tempo = 117
    bar_count = 8
    root_midi = 42
    uc = _uc_vocabulary_phrase(bar_count=bar_count, tempo=tempo)
    by_id = templates_by_id()
    spb = 60.0 / float(tempo)
    sixteenth = spb / 4.0
    anchor = float(uc.bar_start_anchor_sec)
    bar_len = 4.0 * spb
    last_origin = anchor + (bar_count - 1) * bar_len
    loop_end = anchor + bar_count * bar_len
    root_pc = root_midi % 12
    minor7_pcs = _minor7_vocabulary_allowed_pitch_classes(root_pc)

    def slot_in_bar(note_start: float, bar_origin: float) -> int:
        return max(0, min(15, int(round((float(note_start) - bar_origin) / sixteenth))))

    for tid in _CANDIDATE_TEMPLATE_IDS:
        notes = tuple(
            generate_template_candidate_events(
                template=by_id[tid],
                tempo=tempo,
                bar_count=bar_count,
                root_midi=root_midi,
                chord_quality="minor",
                harmonic_root_pc=root_pc,
                conditioning=uc,
            )
        )
        assert notes
        ordered = sorted(notes, key=lambda n: (n.start, n.pitch))
        first = ordered[0]
        assert slot_in_bar(first.start, anchor) == 0
        assert first.pitch % 12 == root_pc
        last_notes = [n for n in notes if last_origin - 1e-6 <= n.start < loop_end]
        assert last_notes
        assert any(
            slot_in_bar(n.start, last_origin) >= 12 and n.pitch % 12 in minor7_pcs for n in last_notes
        )


def test_labelled_vocabulary_candidates_only_use_minor_seventh_pitch_classes() -> None:
    tempo = 117
    bar_count = 8
    root_pc_expected = 6  # F#
    minor7_expected = _minor7_vocabulary_allowed_pitch_classes(root_pc_expected)
    assert minor7_expected == {6, 9, 1, 4}
    uc = _uc_vocabulary_phrase(bar_count=bar_count, tempo=tempo)
    by_id = templates_by_id()
    for tid in _CANDIDATE_TEMPLATE_IDS:
        notes = generate_template_candidate_events(
            template=by_id[tid],
            tempo=tempo,
            bar_count=bar_count,
            root_midi=42,
            chord_quality="minor",
            harmonic_root_pc=root_pc_expected,
            conditioning=uc,
        )
        assert notes
        assert all(int(n.pitch) % 12 in minor7_expected for n in notes)


def test_vocabulary_candidates_follow_session_minor_root_even_if_harmony_plan_major() -> None:
    groove = GrooveProfile(
        pocket_feel="steady",
        syncopation_score=0.2,
        density_per_bar_estimate=4.0,
        accent_strength=0.5,
        confidence=0.85,
    )
    harmony = HarmonyPlan(key_center="C#", scale="major", source="static_progression", bars=[])
    tempo = 100
    bar_count = 4
    src = _source_analysis_evil_snare_downbeat(bar_count=bar_count, tempo=tempo)
    uc = build_unified_conditioning(
        session=SimpleNamespace(bar_count=bar_count, tempo=tempo),
        source=src,
        groove=groove,
        harmony=harmony,
        context=None,
    )
    labelled = generate_vocabulary_candidates(
        tempo=tempo,
        bar_count=bar_count,
        bass_style="supportive",
        chord_progression=["F#m7"] * bar_count,
        conditioning=uc,
        context=None,
        seed=707,
    )
    assert labelled
    want = _minor7_vocabulary_allowed_pitch_classes(6)
    for cand in labelled:
        assert all(int(n.pitch) % 12 in want for n in cand.notes)


def test_labelled_render_skips_chromatic_roles_and_hides_passing_pitch_classes() -> None:
    """Chromatic template roles are not emitted; fourths remap off the undecorated template grid."""
    tempo = 100
    bar_count = 2
    uc = _uc_vocabulary_phrase(bar_count=bar_count, tempo=tempo)
    by_id = templates_by_id()
    for tid in ("dark_slinky_grit_01", "fusion_answer_01"):
        notes = generate_template_candidate_events(
            template=by_id[tid],
            tempo=tempo,
            bar_count=bar_count,
            root_midi=42,
            chord_quality="minor",
            harmonic_root_pc=6,
            conditioning=uc,
        )
        pcs = {int(n.pitch) % 12 for n in notes}
        assert 5 not in pcs  # F natural — chromatic slot below F#
        assert 7 not in pcs  # G — neighbour outside m7 chord vocabulary
        assert 8 not in pcs  # G# — chromatic above F#
        assert 11 not in pcs  # B — perfect fourth (remapped to chord tones)


def test_cinematic_template_rest_slot_emits_no_rest_lane_note() -> None:
    tempo = 100
    bar_count = 1
    uc = _uc_vocabulary_phrase(bar_count=bar_count, tempo=tempo)
    t = templates_by_id()["cinematic_sample_source_01"]
    notes = generate_template_candidate_events(
        template=t,
        tempo=tempo,
        bar_count=bar_count,
        root_midi=42,
        chord_quality="minor",
        harmonic_root_pc=6,
        conditioning=uc,
    )
    assert notes
    assert len(notes) == 3
    assert {int(n.pitch) % 12 for n in notes}.issubset({6, 9, 1, 4})


def test_dark_slinky_grit_is_audible_and_controlled() -> None:
    tempo = 100
    bar_count = 4
    root_pc = 6
    root_midi = 42
    uc = _uc_vocabulary_phrase(bar_count=bar_count, tempo=tempo)
    t = templates_by_id()["dark_slinky_grit_01"]
    assert t.rules.get("groove_feel") == "dark_slinky_swing"
    assert abs(float(t.rules.get("swing_amount", 0.0)) - 0.56) < 1e-9
    notes = generate_template_candidate_events(
        template=t,
        tempo=tempo,
        bar_count=bar_count,
        root_midi=root_midi,
        chord_quality="minor",
        harmonic_root_pc=root_pc,
        conditioning=uc,
    )
    assert notes
    notes_2 = generate_template_candidate_events(
        template=t,
        tempo=tempo,
        bar_count=bar_count,
        root_midi=root_midi,
        chord_quality="minor",
        harmonic_root_pc=root_pc,
        conditioning=uc,
    )
    assert [(n.pitch, round(n.start, 6), round(n.end, 6), n.velocity) for n in notes] == [
        (n.pitch, round(n.start, 6), round(n.end, 6), n.velocity) for n in notes_2
    ]
    # first anchor at bar 0 slot 0
    spb = 60.0 / float(tempo)
    sixteenth = spb / 4.0
    bar_len = 4.0 * spb
    first = min(notes, key=lambda n: (n.start, n.pitch))
    assert abs(float(first.start) - 0.0) <= 1e-6
    # guardrail still applies
    assert {int(n.pitch) % 12 for n in notes}.issubset({6, 9, 1, 4})
    # short articulations should be audible
    short_notes = [n for n in notes if (n.end - n.start) <= (sixteenth * 0.9)]
    assert short_notes
    assert min(int(n.velocity) for n in short_notes) >= 58
    assert min(float(n.end - n.start) for n in short_notes) >= sixteenth * 0.75
    # timing is tightened with deterministic swing quantize:
    # - slot 0/4/8/12 anchors on-grid
    # - dead/ghost slots bounded very tightly
    # - normal offbeats swing later than grid
    for n in notes:
        bar = max(0, min(bar_count - 1, int(float(n.start) // bar_len)))
        rel = float(n.start) - (bar * bar_len)
        slot = max(0, min(15, int(round(rel / sixteenth))))
        expected = (bar * bar_len) + (slot * sixteenth)
        offset = float(n.start) - expected
        if slot == 0:
            assert abs(offset) <= 1e-6
        elif slot in (4, 8, 12):
            assert abs(offset) <= 1e-6
        elif slot == 5 or (float(n.end) - float(n.start)) <= (sixteenth * 0.9):
            assert 0.0 <= offset <= 0.0025
        else:
            assert 0.0 <= offset <= 0.0205
        if slot in (11, 14):
            assert offset >= 0.006
    # density remains controlled (template count plus possible one boundary reinforcement)
    counts = [len([n for n in notes if (bar * bar_len) <= n.start < ((bar + 1) * bar_len)]) for bar in range(bar_count)]
    assert max(counts) <= 6
