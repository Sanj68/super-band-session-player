from __future__ import annotations

import io
import json
import random
from pathlib import Path

import pretty_midi

from app.models.session import BassPlayer, GrooveProfile
from app.services.bass_generator import generate_bass
from app.services.conditioning import UnifiedConditioning
from app.services.style_adapter import StyleAdapter


def _reference_conditioning(
    *,
    tempo_confidence: float = 0.8,
    beat_phase_confidence: float = 0.8,
    bar_start_confidence: float = 0.8,
    groove_confidence: float = 0.8,
    pocket_feel: str = "syncopated",
    syncopation_score: float = 0.8,
    bar_energy: tuple[float, ...] = (0.8, 0.8, 0.8, 0.8),
    bar_confidence: tuple[float, ...] = (0.8, 0.8, 0.8, 0.8),
) -> UnifiedConditioning:
    bars = len(bar_energy)
    return UnifiedConditioning(
        tempo=100,
        bar_count=bars,
        beat_phase_offset_beats=0,
        beat_phase_confidence=beat_phase_confidence,
        bar_start_anchor_sec=0.0,
        beat_grid_seconds=tuple(i * 0.6 for i in range(bars * 4)),
        bar_starts_seconds=tuple(i * 2.4 for i in range(bars)),
        sections=(),
        groove_profile=GrooveProfile(
            pocket_feel=pocket_feel,
            syncopation_score=syncopation_score,
            density_per_bar_estimate=4.0,
            accent_strength=0.7,
            confidence=groove_confidence,
        ),
        harmonic_bars=(),
        tempo_confidence=tempo_confidence,
        bar_start_confidence=bar_start_confidence,
        bar_energy=bar_energy,
        bar_accent=tuple(0.6 for _ in range(bars)),
        bar_confidence=bar_confidence,
    )


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


def test_supportive_bass_custom_progression_adds_release_approach_note() -> None:
    random.seed(20260501)
    data, _preview = generate_bass(
        tempo=100,
        bar_count=5,
        key="C",
        scale="major",
        bass_style="supportive",
        chord_progression=["Am7", "D7", "Gmaj7", "Cmaj7"],
        context=None,
    )

    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes = sorted([n for inst in pm.instruments for n in inst.notes], key=lambda n: (n.start, n.pitch))
    spb = 60.0 / 100.0
    bar_len = spb * 4.0
    release_bar_start = 3 * bar_len
    release_bar_end = 4 * bar_len

    # Bar 4 releases into the looped next chord, Am7. The approach should sit
    # very late in the release bar and land a semitone around A.
    late_release_notes = [
        n for n in notes
        if release_bar_end - (spb / 4.0) <= n.start < release_bar_end and n.pitch % 12 in {8, 10}
    ]
    assert late_release_notes


def test_high_confidence_reference_guidance_changes_supportive_bass_output() -> None:
    random.seed(20260502)
    no_ref, no_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        context=None,
    )
    random.seed(20260502)
    with_ref, with_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        context=None,
        conditioning=_reference_conditioning(),
    )

    assert with_ref != no_ref
    assert with_ref_preview != no_ref_preview
    assert "(ref groove: syncopated, conf 0.80)" in with_ref_preview


def test_low_confidence_reference_guidance_matches_no_reference() -> None:
    random.seed(20260503)
    no_ref, no_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        context=None,
    )
    random.seed(20260503)
    low_ref, low_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        context=None,
        conditioning=_reference_conditioning(tempo_confidence=0.2),
    )

    assert low_ref == no_ref
    assert low_ref_preview == no_ref_preview


def test_reference_guidance_does_not_change_non_supportive_style() -> None:
    random.seed(20260504)
    no_ref, no_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="melodic",
        context=None,
    )
    random.seed(20260504)
    with_ref, with_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="melodic",
        context=None,
        conditioning=_reference_conditioning(),
    )

    assert with_ref == no_ref
    assert with_ref_preview == no_ref_preview


def test_reference_guidance_does_not_change_phrase_v2() -> None:
    random.seed(20260505)
    no_ref, no_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        bass_engine="phrase_v2",
        context=None,
    )
    random.seed(20260505)
    with_ref, with_ref_preview = generate_bass(
        tempo=100,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="supportive",
        bass_engine="phrase_v2",
        context=None,
        conditioning=_reference_conditioning(),
    )

    assert with_ref == no_ref
    assert with_ref_preview == no_ref_preview


def test_paul_chambers_persona_json_contains_walking_rules() -> None:
    path = Path(__file__).resolve().parents[1] / "app" / "personas" / "bass" / "paul_chambers.json"
    persona = json.loads(path.read_text(encoding="utf-8"))

    assert BassPlayer.paul_chambers.value == persona["id"]
    assert persona["swing_feel"]["quarter_note_walk"] is True
    assert persona["bebop_phrasing_rules"]["strong_beats"]["beats"] == [1, 3]
    assert "chromatic_from_below" in persona["approach_notes"]["types"]


def test_paul_chambers_baseline_walk_targets_quarters_and_chromatic_beat_four() -> None:
    data, preview = generate_bass(
        tempo=120,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="melodic",
        bass_player="paul_chambers",
        chord_progression=["Am7", "D7", "Gmaj7", "Cmaj7"],
        seed=1234,
        context=None,
    )

    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes = sorted([n for inst in pm.instruments for n in inst.notes], key=lambda n: (n.start, n.pitch))
    spb = 60.0 / 120.0
    assert "paul_chambers" in preview
    assert len(notes) == 16
    first_bar_slots = [round(n.start / spb) for n in notes[:4]]
    assert first_bar_slots == [0, 1, 2, 3]
    assert notes[0].pitch % 12 == 9
    assert notes[2].pitch % 12 in {4, 5}
    assert notes[3].pitch % 12 in {1, 3}


def test_paul_chambers_phrase_v2_walks_in_quarters() -> None:
    data, preview = generate_bass(
        tempo=120,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="melodic",
        bass_player="paul_chambers",
        bass_engine="phrase_v2",
        seed=1234,
        context=None,
    )

    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes = sorted([n for inst in pm.instruments for n in inst.notes], key=lambda n: (n.start, n.pitch))
    spb = 60.0 / 120.0
    assert "quarter-note walking" in preview
    assert len(notes) == 16
    assert [round(n.start / spb) for n in notes[:4]] == [0, 1, 2, 3]


def test_pino_persona_loads_with_neo_soul_vocabulary() -> None:
    personas_dir = Path(__file__).resolve().parents[1] / "app" / "personas"
    adapter = StyleAdapter(personas_dir=personas_dir)
    persona = adapter.bass_persona("pino")

    assert persona is not None
    assert BassPlayer.pino.value == persona["id"]
    assert persona["profile"]["base_style"] == "melodic"
    assert persona["profile"]["density_ceiling"] == 3
    assert "neo_soul" in persona["neo_soul_vocabulary"]["core_feel"]
    assert persona["rhythmic_language"]["placement"] == "laid_back"
    assert persona["groove_rules"]["max_notes_per_bar"] == 3


def test_pino_baseline_generates_valid_neo_soul_phrase() -> None:
    data, preview = generate_bass(
        tempo=92,
        bar_count=4,
        key="D",
        scale="minor",
        bass_style="melodic",
        bass_player="pino",
        seed=2468,
        context=None,
    )

    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes = sorted([n for inst in pm.instruments for n in inst.notes], key=lambda n: (n.start, n.pitch))
    assert "pino" in preview
    assert len(notes) >= 4
    assert all(34 <= n.pitch <= 55 for n in notes)
    assert all(n.end > n.start for n in notes)


def test_pino_phrase_v2_generates_spacious_sustained_phrase() -> None:
    data, preview = generate_bass(
        tempo=92,
        bar_count=4,
        key="D",
        scale="minor",
        bass_style="melodic",
        bass_player="pino",
        bass_engine="phrase_v2",
        seed=2468,
        context=None,
    )

    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes = sorted([n for inst in pm.instruments for n in inst.notes], key=lambda n: (n.start, n.pitch))
    spb = 60.0 / 92.0
    assert "pino" in preview
    assert "neo-soul pocket" in preview
    assert 4 <= len(notes) <= 12
    assert all(34 <= n.pitch <= 55 for n in notes)
    assert all(n.end > n.start for n in notes)
    assert any((n.end - n.start) >= spb for n in notes)
    assert notes[0].start > 0.0


def test_jaco_pastorius_persona_loads_with_fusion_vocabulary() -> None:
    personas_dir = Path(__file__).resolve().parents[1] / "app" / "personas"
    adapter = StyleAdapter(personas_dir=personas_dir)
    persona = adapter.bass_persona("jaco_pastorius")

    assert persona is not None
    assert BassPlayer.jaco_pastorius.value == persona["id"]
    assert persona["profile"]["base_style"] == "fusion"
    assert persona["profile"]["ghost_note_bias"] > 0.2
    assert "sixteenth_note_bursts" in persona["rhythmic_language"]["subdivision_mix"]
    assert "eleventh" in persona["fusion_vocabulary"]["upper_extensions"]
    assert "pinched_harmonic_color" in persona["percussive_elements"]["accent_devices"]


def test_jaco_pastorius_baseline_generates_valid_fusion_phrase() -> None:
    data, preview = generate_bass(
        tempo=96,
        bar_count=4,
        key="E",
        scale="minor",
        bass_style="fusion",
        bass_player="jaco_pastorius",
        seed=4321,
        context=None,
    )

    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes = sorted([n for inst in pm.instruments for n in inst.notes], key=lambda n: (n.start, n.pitch))
    assert "jaco_pastorius" in preview
    assert len(notes) >= 8
    assert all(34 <= n.pitch <= 67 for n in notes)
    assert all(n.end > n.start for n in notes)
    assert any(n.start % (60.0 / 96.0) > 1e-3 for n in notes)


def test_jaco_pastorius_phrase_v2_generates_valid_phrase() -> None:
    data, preview = generate_bass(
        tempo=96,
        bar_count=4,
        key="E",
        scale="minor",
        bass_style="fusion",
        bass_player="jaco_pastorius",
        bass_engine="phrase_v2",
        seed=4321,
        context=None,
    )

    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes = [n for inst in pm.instruments for n in inst.notes]
    assert "jaco_pastorius" in preview
    assert len(notes) >= 8
    assert all(0 <= n.pitch <= 127 for n in notes)
    assert all(n.end > n.start for n in notes)


def test_james_jamerson_persona_loads_with_motown_vocabulary() -> None:
    personas_dir = Path(__file__).resolve().parents[1] / "app" / "personas"
    adapter = StyleAdapter(personas_dir=personas_dir)
    persona = adapter.bass_persona("james_jamerson")

    assert persona is not None
    assert BassPlayer.james_jamerson.value == persona["id"]
    assert persona["profile"]["base_style"] == "rhythmic"
    assert persona["profile"]["syncopation_bias"] > 0.8
    assert "syncopated_16th_note_runs" in persona["rhythmic_language"]["subdivision_mix"]
    assert "chromatic_passing_tones" in persona["melodic_language"]["voice_leading"]
    assert persona["groove_rules"]["lock_to_kick"] is True


def test_james_jamerson_baseline_generates_valid_motown_phrase() -> None:
    data, preview = generate_bass(
        tempo=104,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="rhythmic",
        bass_player="james_jamerson",
        seed=5678,
        context=None,
    )

    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes = sorted([n for inst in pm.instruments for n in inst.notes], key=lambda n: (n.start, n.pitch))
    assert "james_jamerson" in preview
    assert len(notes) >= 8
    assert all(31 <= n.pitch <= 60 for n in notes)
    assert all(n.end > n.start for n in notes)
    assert any(n.start % (60.0 / 104.0) > 1e-3 for n in notes)


def test_james_jamerson_phrase_v2_generates_valid_phrase() -> None:
    data, preview = generate_bass(
        tempo=104,
        bar_count=4,
        key="C",
        scale="major",
        bass_style="rhythmic",
        bass_player="james_jamerson",
        bass_engine="phrase_v2",
        seed=5678,
        context=None,
    )

    pm = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes = [n for inst in pm.instruments for n in inst.notes]
    assert "james_jamerson" in preview
    assert len(notes) >= 8
    assert all(0 <= n.pitch <= 127 for n in notes)
    assert all(n.end > n.start for n in notes)
