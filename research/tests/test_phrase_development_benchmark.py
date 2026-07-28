from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pretty_midi
import pytest

from research.phrase_development_benchmark import (
    BenchmarkConfig,
    GATE_THRESHOLDS,
    MidiNoteEvent,
    analyze_features,
    batch_assessment,
    cross_seed_metrics,
    ending_metrics,
    extract_take_features,
    form_rhythm_metrics,
    main,
    motif_metrics,
    parse_chord,
    parse_chord_progression,
    parse_note_name,
    percentile,
    quantize_notes,
    same_harmony_rhythm_metrics,
    section_skeleton_metrics,
)


def _config(
    *,
    bars: int = 16,
    chords: str = "F|Gm|Dm|Bb",
) -> BenchmarkConfig:
    return BenchmarkConfig(
        tempo=120.0,
        chords=parse_chord_progression(chords),
        bars=bars,
        tonic_pc=parse_note_name("D"),
    )


def _features_from_rows(
    rows: list[set[int]],
    *,
    label: str = "take.mid",
    config: BenchmarkConfig | None = None,
):
    config = config or _config(bars=len(rows))
    notes = []
    for bar, slots in enumerate(rows):
        root = config.chords[bar % len(config.chords)].root_pc
        for slot in sorted(slots):
            notes.append(
                MidiNoteEvent(
                    start_beats=bar * config.beats_per_bar
                    + slot / config.subdivisions_per_beat,
                    pitch=36 + root,
                )
            )
    return extract_take_features(
        label=label,
        path=f"/tmp/{label}",
        notes=notes,
        config=config,
    )


def test_chord_parser_covers_benchmark_progression_and_common_qualities() -> None:
    progression = parse_chord_progression("F | Gm, Dm Bb")
    assert [chord.root_pc for chord in progression] == [5, 7, 2, 10]
    assert [chord.quality for chord in progression] == [
        "major",
        "minor",
        "minor",
        "major",
    ]
    assert parse_chord("F#m7b5").pitch_classes == (0, 4, 6, 9)
    assert parse_chord("Bb/D").root_pc == 10


def test_quantization_is_half_up_and_obeys_analysis_window() -> None:
    config = _config(bars=1, chords="Dm")
    notes = [
        MidiNoteEvent(-0.01, 38),
        MidiNoteEvent(0.124, 38),
        MidiNoteEvent(0.125, 40),
        MidiNoteEvent(3.999, 41),
        MidiNoteEvent(4.0, 43),
    ]
    quantized = quantize_notes(notes, config)
    assert [(note.slot, note.pitch) for note in quantized] == [
        (0, 38),
        (1, 40),
    ]


def test_skeleton_is_first_note_per_beat_and_chord_relative() -> None:
    config = _config(bars=2, chords="F|Gm")
    notes = [
        MidiNoteEvent(0.0, 41),  # F: relative root.
        MidiNoteEvent(0.5, 45),  # Later in beat 1, ignored by skeleton.
        MidiNoteEvent(1.0, 45),  # A over F: relative major third.
        MidiNoteEvent(4.0, 43),  # G over Gm: relative root.
        MidiNoteEvent(5.0, 46),  # Bb over Gm: relative minor third.
    ]
    features = extract_take_features(
        label="skeleton.mid",
        path="/tmp/skeleton.mid",
        notes=notes,
        config=config,
    )
    assert features.beat_skeleton[:6] == (0, 4, None, None, 0, 3)


def test_same_harmony_metrics_count_exact_rows_separately() -> None:
    config = _config(bars=4, chords="F")
    rows = [
        {0, 4},
        {0, 8},
        {0, 4},
        {2, 6},
    ]
    result = same_harmony_rhythm_metrics(
        _features_from_rows(rows, config=config), config
    )
    assert result["comparison_count"] == 6
    assert result["mean_similarity"] == pytest.approx(0.2778)
    assert result["exact_copy_count"] == 1
    assert result["exact_copy_ratio"] == pytest.approx(0.1667)


def test_section_skeleton_recognizes_transposed_chord_relative_restatement() -> None:
    config = _config(bars=8)
    rows = [{0, 4, 8, 12} for _ in range(8)]
    features = _features_from_rows(rows, config=config)
    result = section_skeleton_metrics(features, config)
    assert result["section_count"] == 2
    assert result["comparison_count"] == 1
    assert result["mean_similarity"] == 1.0
    assert result["maximum_similarity"] == 1.0


def test_four_bar_form_reports_restatement_contrast_and_final_return() -> None:
    config = _config()
    a = [{0, 4} for _ in range(4)]
    aprime = [{0, 8}, {0, 4}, {0, 4}, {0, 4}]
    b = [{0, 8} for _ in range(4)]
    final = [{0, 8}, {0, 8}, {0, 4}, {0, 4}]
    features = _features_from_rows(a + aprime + b + final, config=config)
    result = form_rhythm_metrics(features, config)
    assert result["a_to_aprime"] == pytest.approx(0.7778)
    assert result["aprime_to_b"] == pytest.approx(0.4545)
    assert result["a_aprime_minus_aprime_b"] == pytest.approx(0.3232)
    assert result["b_to_final"] == pytest.approx(0.6)


def test_ending_metrics_use_actual_final_onset_and_harmonic_safety() -> None:
    config = _config()
    notes = [
        MidiNoteEvent(
            bar * 4 + slot / 4,
            46 if bar == 3 else 50 if bar in (7, 15) else 53 if bar == 11 else 38,
        )
        for bar in range(16)
        for slot in (
            ({0, 8, 12} if bar == 3 else
             {0, 8, 14} if bar == 7 else
             {0, 8, 12, 14} if bar == 11 else
             {0, 8, 12, 15} if bar == 15 else
             {0, 8})
        )
    ]
    features = extract_take_features(
        label="ending.mid",
        path="/tmp/ending.mid",
        notes=notes,
        config=config,
    )
    result = ending_metrics(features, config)
    assert result["mean_half_bar_rhythm_similarity"] == pytest.approx(0.4722)
    assert result["distinct_landing_pitch_class_count"] == 3
    assert result["final_landing_pitch_class"] == "D"
    assert result["final_chord_or_tonic_safe"] is True


def test_motif_metrics_distinguish_similarity_from_exact_duplication() -> None:
    config = _config(bars=6, chords="Dm")
    rows = [{0, 4}, {0, 4}, {0, 8}, {0, 8}, {2, 6}, {0, 12}]
    result = motif_metrics(_features_from_rows(rows, config=config))
    assert result["unique_nonempty_motif_count"] == 4
    assert result["duplicate_motif_occurrence_count"] == 4
    assert result["duplicate_motif_ratio"] == pytest.approx(0.6667)
    assert 0.0 < result["mean_pairwise_bar_similarity"] < 1.0


def test_linear_percentile_and_population_note_count_cv_are_deterministic() -> None:
    assert percentile([0.1, 0.2, 0.3, 0.4], 50) == pytest.approx(0.25)
    assert percentile([0.1, 0.2, 0.3, 0.4], 90) == pytest.approx(0.37)
    config = _config(bars=1, chords="Dm")
    features = [
        _features_from_rows([{0, 4}], label=f"take_{index}.mid", config=config)
        for index in range(3)
    ]
    features[1] = replace(features[1], note_count=4)
    features[2] = replace(features[2], note_count=6)
    result = cross_seed_metrics(features)
    assert result["note_counts"] == [2, 4, 6]
    assert result["note_count_mean"] == 4.0
    assert result["note_count_cv"] == pytest.approx(0.4082)


def test_batch_ready_requires_eight_takes_take_pass_ratio_and_cross_gates() -> None:
    config = _config(bars=1, chords="Dm")
    features = [
        _features_from_rows([{0, 4}], label=f"take_{index}.mid", config=config)
        for index in range(8)
    ]
    take_reports = [
        {"gate_summary": {"take_passed": index < 6}} for index in range(8)
    ]
    cross = {
        "full_rhythm_similarity": {
            "distribution": {"p50": 0.54, "p90": 0.64}
        },
        "chord_relative_beat_skeleton_similarity": {
            "distribution": {"p50": 0.40, "p90": 0.62}
        },
        "note_count_cv": 0.08,
    }
    result = batch_assessment(features, take_reports, cross)
    assert result["ready"] is True
    assert result["individual_take_pass_ratio"] == 0.75
    assert all(gate["passed"] for gate in result["gates"])

    failed_cross = {
        **cross,
        "full_rhythm_similarity": {
            "distribution": {"p50": 0.71, "p90": 0.80}
        },
    }
    assert batch_assessment(features, take_reports, failed_cross)["ready"] is False
    assert (
        batch_assessment(features[:7], take_reports[:7], cross)["gates"][2][
            "status"
        ]
        == "not_applicable"
    )


def test_analysis_exposes_every_grounded_threshold_as_a_gate() -> None:
    config = _config()
    rows = [{0, 4, 8} for _ in range(16)]
    report = analyze_features(
        [_features_from_rows(rows, label="single.mid", config=config)],
        config,
    )
    individual_ids = {gate["id"] for gate in report["takes"][0]["gates"]}
    batch_ids = {
        gate["id"] for gate in report["batch_assessment"]["gates"]
    }
    assert set(GATE_THRESHOLDS) <= individual_ids | batch_ids
    assert report["batch_assessment"]["verdict"] == "NOT_READY"


def test_cli_writes_json_and_markdown_without_failing_a_baseline(
    tmp_path: Path,
) -> None:
    midi_path = tmp_path / "take.mid"
    midi = pretty_midi.PrettyMIDI(initial_tempo=120)
    instrument = pretty_midi.Instrument(program=33, name="clean bass")
    instrument.notes.extend(
        [
            pretty_midi.Note(
                velocity=100,
                pitch=38,
                start=bar * 2.0,
                end=bar * 2.0 + 0.4,
            )
            for bar in range(16)
        ]
    )
    midi.instruments.append(instrument)
    midi.write(str(midi_path))

    output_dir = tmp_path / "reports"
    exit_code = main(
        [
            "--midi",
            str(midi_path),
            "--tempo",
            "120",
            "--chords",
            "Dm",
            "--bars",
            "16",
            "--tonic",
            "D",
            "--output-dir",
            str(output_dir),
            "--output-stem",
            "fixture",
        ]
    )
    assert exit_code == 0
    payload = json.loads((output_dir / "fixture.json").read_text())
    markdown = (output_dir / "fixture.md").read_text()
    assert payload["batch_assessment"]["verdict"] == "NOT_READY"
    assert "Session Player phrase-development benchmark" in markdown
    assert "`cross_seed_rhythm_p50`" in markdown
