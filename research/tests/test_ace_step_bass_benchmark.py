from __future__ import annotations

import pytest

from research.ace_step_bass_benchmark import (
    AnalysisConfig,
    ChordSpec,
    MidiNoteEvent,
    PhraseFeatures,
    binary_jaccard,
    chord_for_bar,
    counts_per_bar,
    infer_bar_count,
    midi_harmonic_metrics,
    nearest_event_alignment,
    parse_chord,
    parse_chord_progression,
    parse_note_name,
    phrase_pair_metrics,
    quantized_onset_vector,
    scale_pitch_classes,
    stable_pitch_segments_from_frames,
)


def _config(*, chords: tuple[ChordSpec | None, ...] = ()) -> AnalysisConfig:
    key_pc = parse_note_name("D")
    return AnalysisConfig(
        tempo=120.0,
        key_pc=key_pc,
        key_name="D",
        scale="natural_minor",
        scale_pcs=scale_pitch_classes(key_pc, "natural_minor"),
        chords=chords,
        bars=4,
        beats_per_bar=4,
        subdivisions_per_beat=4,
        kick_tolerance_seconds=0.075,
        min_f0_confidence=0.8,
        analysis_sample_rate=22_050,
    )


@pytest.mark.parametrize(
    ("name", "expected"),
    [("C", 0), ("B#", 0), ("D", 2), ("F#", 6), ("Gb", 6), ("Bb", 10)],
)
def test_parse_note_name_enharmonics(name: str, expected: int) -> None:
    assert parse_note_name(name) == expected


def test_parse_chords_and_cycle_progression() -> None:
    progression = parse_chord_progression("F|Gm|Dm|Bb")
    assert [chord.quality for chord in progression if chord is not None] == [
        "major",
        "minor",
        "minor",
        "major",
    ]
    assert chord_for_bar(progression, 5).symbol == "Gm"
    assert parse_chord("F#m7b5").pitch_classes == (0, 4, 6, 9)
    assert parse_chord("Bb/D").slash_bass_pc == 2
    assert parse_chord("N") is None


def test_infer_bar_count_tolerates_short_render_tail() -> None:
    assert infer_bar_count(16.2, 2.0) == 8
    assert infer_bar_count(16.6, 2.0) == 9
    assert infer_bar_count(0.0, 2.0) == 1


def test_bar_counts_obey_analysis_window() -> None:
    assert counts_per_bar(
        [-0.1, 0.0, 1.99, 2.0, 7.99, 8.0],
        bar_duration=2.0,
        bar_count=4,
    ) == [2, 1, 0, 1]


def test_quantized_phrase_vector_and_jaccard() -> None:
    left = quantized_onset_vector(
        [0.0, 0.5, 1.0],
        tempo=120,
        beats_per_bar=4,
        bar_count=1,
    )
    same_with_microtiming = quantized_onset_vector(
        [0.015, 0.49, 1.02],
        tempo=120,
        beats_per_bar=4,
        bar_count=1,
    )
    different = quantized_onset_vector(
        [0.25, 0.75, 1.25],
        tempo=120,
        beats_per_bar=4,
        bar_count=1,
    )
    assert binary_jaccard(left, same_with_microtiming) == 1.0
    assert binary_jaccard(left, different) == 0.0


def test_phrase_similarity_falls_back_when_audio_pitch_is_unreliable() -> None:
    onsets = (1.0, 0.0, 1.0, 0.0)
    audio = PhraseFeatures(
        "audio.wav", "bass_audio", onsets, None, pitch_reliable=False
    )
    midi = PhraseFeatures(
        "take.mid",
        "session_player_midi",
        onsets,
        (0.0,) * 12,
        pitch_reliable=True,
    )
    result = phrase_pair_metrics(audio, midi)
    assert result["phrase_similarity"] == 1.0
    assert result["phrase_diversity"] == 0.0
    assert result["pitch_class_similarity"] is None
    assert result["basis"] == "quantized_onsets_only"


def test_nearest_event_alignment_reports_tolerance_and_offset() -> None:
    result = nearest_event_alignment(
        [0.02, 0.55, 1.30],
        [0.0, 0.5, 1.0],
        tolerance_seconds=0.075,
    )
    assert result["matched_source_onset_count"] == 2
    assert result["matched_source_onset_pct"] == pytest.approx(66.67)
    assert result["median_nearest_absolute_offset_ms"] == pytest.approx(50.0)


def test_stable_pitch_segments_reject_low_confidence_and_unstable_groups() -> None:
    times = [index * 0.01 for index in range(12)]
    # A2 (110 Hz) is stable for four frames, then an unstable sweep, then
    # confidence drops below the conservative threshold.
    frequencies = [
        110.0,
        110.2,
        109.9,
        110.1,
        130.0,
        139.0,
        149.0,
        160.0,
        110.0,
        110.0,
        110.0,
        110.0,
    ]
    probabilities = [0.95] * 8 + [0.5] * 4
    segments, diagnostics = stable_pitch_segments_from_frames(
        frame_times=times,
        f0_hz=frequencies,
        voiced_probabilities=probabilities,
        frame_rms_dbfs=[-24.0] * 12,
        hop_duration=0.01,
        min_confidence=0.8,
    )
    assert len(segments) == 1
    assert segments[0].midi_note == 45
    assert segments[0].pitch_class == 9
    assert diagnostics["candidate_frame_count"] == 8
    assert diagnostics["rejected_short_group_count"] >= 1


def test_midi_harmonic_metrics_separate_passing_tones_from_strong_errors() -> None:
    chord = parse_chord("Dm")
    assert chord is not None
    config = _config(chords=(chord,))
    notes = [
        MidiNoteEvent(0.0, 0.4, 38, 100),  # D2, chord/scale tone.
        MidiNoteEvent(0.75, 0.9, 40, 80),  # E2 passing tone, in scale.
        MidiNoteEvent(1.0, 1.3, 39, 95),  # Eb2, strong-beat chord + scale error.
    ]
    result = midi_harmonic_metrics(notes, config=config)
    assert result["scale"]["out_of_scale_note_count"] == 1
    assert result["chords"]["all_note_non_chord_count"] == 2
    assert result["chords"]["strong_beat_non_chord_error_count"] == 1
    error = result["chords"]["strong_beat_error_events"][0]
    assert error["note_name"] == "Eb2"
    assert error["chord"] == "Dm"
