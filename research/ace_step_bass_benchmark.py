"""Compare generated bass audio with Session Player MIDI without touching production.

The analyzer is intentionally conservative:

* kick locations are a low-frequency onset *proxy*, never claimed as ground truth;
* audio pitch classes are reported only when pYIN yields enough stable,
  high-confidence monophonic segments;
* non-chord MIDI notes are described separately from likely harmonic errors on
  strong beats, because passing and approach notes are musically valid.

Run from the repository root with the existing backend environment:

    backend/.venv/bin/python research/ace_step_bass_benchmark.py \
      --backing /path/to/owned_backing.wav \
      --bass-wav /path/to/ace_take_1.wav \
      --bass-wav /path/to/ace_take_2.wav \
      --midi /path/to/session_player_take_1.mid \
      --midi /path/to/session_player_take_2.mid \
      --tempo 116 --key D --scale natural_minor \
      --chords 'F|Gm|Dm|Bb' --bars 8

JSON and Markdown reports are written beneath ``research/benchmark_results/``
unless ``--output-dir`` is supplied.
"""

from __future__ import annotations

import argparse
from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from statistics import median
from typing import Any, Iterable, Sequence

import librosa
import numpy as np
import pretty_midi
from scipy.signal import find_peaks
import soundfile as sf


REPORT_SCHEMA_VERSION = 1
DEFAULT_ANALYSIS_SAMPLE_RATE = 22_050
DEFAULT_HOP_LENGTH = 256
DEFAULT_F0_FRAME_LENGTH = 4096
PITCH_CLASS_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")
NOTE_BASE_PCS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

SCALE_INTERVALS: dict[str, tuple[int, ...]] = {
    "major": (0, 2, 4, 5, 7, 9, 11),
    "natural_minor": (0, 2, 3, 5, 7, 8, 10),
    "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10),
    "lydian": (0, 2, 4, 6, 7, 9, 11),
    "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "locrian": (0, 1, 3, 5, 6, 8, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
    "melodic_minor": (0, 2, 3, 5, 7, 9, 11),
}
SCALE_ALIASES = {
    "ionian": "major",
    "maj": "major",
    "minor": "natural_minor",
    "min": "natural_minor",
    "aeolian": "natural_minor",
}

CHORD_INTERVALS: dict[str, tuple[int, ...]] = {
    "major": (0, 4, 7),
    "minor": (0, 3, 7),
    "dominant7": (0, 4, 7, 10),
    "major7": (0, 4, 7, 11),
    "minor7": (0, 3, 7, 10),
    "minor_major7": (0, 3, 7, 11),
    "diminished": (0, 3, 6),
    "diminished7": (0, 3, 6, 9),
    "half_diminished": (0, 3, 6, 10),
    "augmented": (0, 4, 8),
    "sus2": (0, 2, 7),
    "sus4": (0, 5, 7),
    "six": (0, 4, 7, 9),
    "minor6": (0, 3, 7, 9),
}
CHORD_QUALITY_ALIASES = {
    "": "major",
    "maj": "major",
    "major": "major",
    "m": "minor",
    "min": "minor",
    "minor": "minor",
    "7": "dominant7",
    "dom7": "dominant7",
    "maj7": "major7",
    "major7": "major7",
    "m7": "minor7",
    "min7": "minor7",
    "minor7": "minor7",
    "mmaj7": "minor_major7",
    "minmaj7": "minor_major7",
    "dim": "diminished",
    "o": "diminished",
    "dim7": "diminished7",
    "o7": "diminished7",
    "m7b5": "half_diminished",
    "ø": "half_diminished",
    "aug": "augmented",
    "+": "augmented",
    "sus": "sus4",
    "sus2": "sus2",
    "sus4": "sus4",
    "6": "six",
    "maj6": "six",
    "m6": "minor6",
    "min6": "minor6",
}


@dataclass(frozen=True)
class ChordSpec:
    """A parsed chord symbol used for deterministic MIDI checks."""

    symbol: str
    root_pc: int
    quality: str
    pitch_classes: tuple[int, ...]
    slash_bass_pc: int | None = None


@dataclass(frozen=True)
class MidiNoteEvent:
    """Small serializable MIDI event independent of pretty_midi."""

    start: float
    end: float
    pitch: int
    velocity: int
    instrument: str = ""


@dataclass(frozen=True)
class PitchSegment:
    """Stable, locally monophonic pitch evidence retained from pYIN."""

    start: float
    end: float
    midi_float: float
    midi_note: int
    pitch_class: int
    confidence: float
    frame_count: int
    tuning_error_cents: float


@dataclass(frozen=True)
class PhraseFeatures:
    """Comparable phrase representation shared by audio and MIDI takes."""

    label: str
    kind: str
    onset_vector: tuple[float, ...]
    pitch_class_weights: tuple[float, ...] | None
    pitch_reliable: bool


@dataclass(frozen=True)
class AnalysisConfig:
    tempo: float
    key_pc: int
    key_name: str
    scale: str
    scale_pcs: tuple[int, ...]
    chords: tuple[ChordSpec | None, ...]
    bars: int
    beats_per_bar: int
    subdivisions_per_beat: int
    kick_tolerance_seconds: float
    min_f0_confidence: float
    analysis_sample_rate: int

    @property
    def beat_duration(self) -> float:
        return 60.0 / self.tempo

    @property
    def bar_duration(self) -> float:
        return self.beats_per_bar * self.beat_duration

    @property
    def analysis_duration(self) -> float:
        return self.bars * self.bar_duration


def round_float(value: float | np.floating[Any] | None, digits: int = 4) -> float | None:
    """Return a JSON-safe rounded float, withholding non-finite values."""

    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return round(number, digits)


def ratio_percent(numerator: float, denominator: float) -> float | None:
    """Return a bounded percentage or ``None`` when no denominator exists."""

    if denominator <= 0:
        return None
    return round_float(100.0 * numerator / denominator, 2)


def amplitude_to_dbfs(value: float) -> float | None:
    """Convert a linear full-scale amplitude to dBFS."""

    if value <= 0 or not math.isfinite(value):
        return None
    return round_float(20.0 * math.log10(value), 3)


def parse_note_name(note: str) -> int:
    """Parse a pitch-class name such as D, F#, or Bb."""

    match = re.fullmatch(r"\s*([A-Ga-g])([#b]?)\s*", note)
    if not match:
        raise ValueError(f"invalid note name: {note!r}")
    letter, accidental = match.groups()
    pitch_class = NOTE_BASE_PCS[letter.upper()]
    if accidental == "#":
        pitch_class += 1
    elif accidental == "b":
        pitch_class -= 1
    return pitch_class % 12


def pitch_class_name(pitch_class: int) -> str:
    return PITCH_CLASS_NAMES[pitch_class % 12]


def midi_note_name(pitch: int) -> str:
    octave = (int(pitch) // 12) - 1
    return f"{pitch_class_name(int(pitch) % 12)}{octave}"


def normalize_scale_name(scale: str) -> str:
    normalized = scale.strip().lower().replace("-", "_").replace(" ", "_")
    normalized = SCALE_ALIASES.get(normalized, normalized)
    if normalized not in SCALE_INTERVALS:
        supported = ", ".join(sorted(SCALE_INTERVALS))
        raise ValueError(f"unsupported scale {scale!r}; choose one of: {supported}")
    return normalized


def scale_pitch_classes(key_pc: int, scale: str) -> tuple[int, ...]:
    normalized = normalize_scale_name(scale)
    return tuple(sorted((key_pc + interval) % 12 for interval in SCALE_INTERVALS[normalized]))


def parse_chord(symbol: str) -> ChordSpec | None:
    """Parse common lead-sheet chord symbols.

    ``N`` and ``NC`` produce ``None``. Slash bass notes are retained and also
    accepted as chord tones.
    """

    raw = symbol.strip()
    if raw.upper().replace(".", "") in {"N", "NC", "NOCHORD"}:
        return None
    match = re.fullmatch(r"([A-Ga-g])([#b]?)([^/]*)?(?:/([A-Ga-g][#b]?))?", raw)
    if not match:
        raise ValueError(f"unsupported chord symbol: {symbol!r}")
    letter, accidental, suffix, slash = match.groups()
    root_pc = parse_note_name(f"{letter}{accidental}")
    quality_token = (suffix or "").strip().lower().replace(" ", "").replace("−", "m")
    quality = CHORD_QUALITY_ALIASES.get(quality_token)
    if quality is None:
        supported = ", ".join(sorted(k for k in CHORD_QUALITY_ALIASES if k))
        raise ValueError(
            f"unsupported quality in chord {symbol!r}; supported suffixes include: {supported}"
        )
    pitch_classes = {(root_pc + interval) % 12 for interval in CHORD_INTERVALS[quality]}
    slash_pc = parse_note_name(slash) if slash else None
    if slash_pc is not None:
        pitch_classes.add(slash_pc)
    return ChordSpec(
        symbol=raw,
        root_pc=root_pc,
        quality=quality,
        pitch_classes=tuple(sorted(pitch_classes)),
        slash_bass_pc=slash_pc,
    )


def parse_chord_progression(raw: str | None) -> tuple[ChordSpec | None, ...]:
    """Parse comma, pipe, or whitespace separated chord symbols."""

    if raw is None or not raw.strip():
        return ()
    if "|" in raw or "," in raw:
        tokens = [token.strip() for token in re.split(r"[|,]", raw) if token.strip()]
    else:
        tokens = raw.split()
    if not tokens:
        return ()
    return tuple(parse_chord(token) for token in tokens)


def chord_for_bar(
    progression: Sequence[ChordSpec | None], bar_index: int
) -> ChordSpec | None:
    if not progression:
        return None
    return progression[bar_index % len(progression)]


def infer_bar_count(duration_seconds: float, bar_duration: float) -> int:
    """Infer musical bars while tolerating a short render/reverb tail."""

    if duration_seconds <= 0 or bar_duration <= 0:
        return 1
    exact = duration_seconds / bar_duration
    nearest = max(1, round(exact))
    if abs(exact - nearest) <= 0.25:
        return nearest
    return max(1, math.ceil(exact))


def counts_per_bar(
    times: Iterable[float], *, bar_duration: float, bar_count: int
) -> list[int]:
    counts = [0] * bar_count
    if bar_duration <= 0:
        return counts
    for raw_time in times:
        time = float(raw_time)
        if time < 0:
            continue
        bar = int(time // bar_duration)
        if 0 <= bar < bar_count:
            counts[bar] += 1
    return counts


def quantized_onset_vector(
    times: Iterable[float],
    *,
    tempo: float,
    beats_per_bar: int,
    bar_count: int,
    subdivisions_per_beat: int = 4,
) -> tuple[float, ...]:
    """Return binary phrase occupancy on a fixed musical grid."""

    total_slots = bar_count * beats_per_bar * subdivisions_per_beat
    vector = np.zeros(total_slots, dtype=float)
    seconds_per_slot = 60.0 / tempo / subdivisions_per_beat
    max_time = bar_count * beats_per_bar * 60.0 / tempo
    for raw_time in times:
        time = float(raw_time)
        if not math.isfinite(time) or time < 0 or time >= max_time:
            continue
        slot = int(round(time / seconds_per_slot))
        slot = min(max(slot, 0), total_slots - 1)
        vector[slot] = 1.0
    return tuple(float(value) for value in vector)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float | None:
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if a.shape != b.shape or a.size == 0:
        return None
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator <= 0:
        return None
    return float(np.clip(np.dot(a, b) / denominator, 0.0, 1.0))


def binary_jaccard(left: Sequence[float], right: Sequence[float]) -> float | None:
    a = np.asarray(left, dtype=float) > 0
    b = np.asarray(right, dtype=float) > 0
    if a.shape != b.shape or a.size == 0:
        return None
    union = int(np.count_nonzero(a | b))
    if union == 0:
        return None
    return float(np.count_nonzero(a & b) / union)


def phrase_pair_metrics(left: PhraseFeatures, right: PhraseFeatures) -> dict[str, Any]:
    """Compare two phrase representations without pretending audio pitch is exact."""

    rhythm_cosine = cosine_similarity(left.onset_vector, right.onset_vector)
    rhythm_jaccard = binary_jaccard(left.onset_vector, right.onset_vector)
    rhythm_parts = [value for value in (rhythm_cosine, rhythm_jaccard) if value is not None]
    rhythm_similarity = sum(rhythm_parts) / len(rhythm_parts) if rhythm_parts else None

    pitch_similarity = None
    if (
        left.pitch_reliable
        and right.pitch_reliable
        and left.pitch_class_weights is not None
        and right.pitch_class_weights is not None
    ):
        pitch_similarity = cosine_similarity(left.pitch_class_weights, right.pitch_class_weights)

    if rhythm_similarity is None and pitch_similarity is None:
        phrase_similarity = None
        basis = "insufficient_evidence"
    elif pitch_similarity is None:
        phrase_similarity = rhythm_similarity
        basis = "quantized_onsets_only"
    elif rhythm_similarity is None:
        phrase_similarity = pitch_similarity
        basis = "pitch_classes_only"
    else:
        phrase_similarity = 0.7 * rhythm_similarity + 0.3 * pitch_similarity
        basis = "70%_quantized_onsets_30%_pitch_classes"

    return {
        "left": left.label,
        "left_kind": left.kind,
        "right": right.label,
        "right_kind": right.kind,
        "rhythm_cosine": round_float(rhythm_cosine),
        "rhythm_jaccard": round_float(rhythm_jaccard),
        "rhythm_similarity": round_float(rhythm_similarity),
        "pitch_class_similarity": round_float(pitch_similarity),
        "phrase_similarity": round_float(phrase_similarity),
        "phrase_diversity": round_float(1.0 - phrase_similarity)
        if phrase_similarity is not None
        else None,
        "basis": basis,
    }


def pairwise_phrase_metrics(features: Sequence[PhraseFeatures]) -> list[dict[str, Any]]:
    return [
        phrase_pair_metrics(features[left], features[right])
        for left in range(len(features))
        for right in range(left + 1, len(features))
    ]


def summarize_pair_groups(comparisons: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[tuple[str, str], list[float]] = {}
    for comparison in comparisons:
        similarity = comparison.get("phrase_similarity")
        if similarity is None:
            continue
        key = tuple(sorted((str(comparison["left_kind"]), str(comparison["right_kind"]))))
        buckets.setdefault(key, []).append(float(similarity))
    output = []
    for (left_kind, right_kind), values in sorted(buckets.items()):
        mean_similarity = sum(values) / len(values)
        output.append(
            {
                "groups": [left_kind, right_kind],
                "pair_count": len(values),
                "mean_phrase_similarity": round_float(mean_similarity),
                "mean_phrase_diversity": round_float(1.0 - mean_similarity),
                "min_similarity": round_float(min(values)),
                "max_similarity": round_float(max(values)),
            }
        )
    return output


def nearest_event_alignment(
    source_times: Sequence[float],
    reference_times: Sequence[float],
    *,
    tolerance_seconds: float,
) -> dict[str, Any]:
    """Measure whether each source onset is near any reference onset."""

    sources = sorted(float(value) for value in source_times if math.isfinite(float(value)))
    references = sorted(float(value) for value in reference_times if math.isfinite(float(value)))
    if not sources:
        return {
            "status": "unavailable",
            "reason": "no_source_onsets",
            "source_onset_count": 0,
            "reference_onset_count": len(references),
        }
    if not references:
        return {
            "status": "unavailable",
            "reason": "no_reference_onsets",
            "source_onset_count": len(sources),
            "reference_onset_count": 0,
        }

    signed_offsets: list[float] = []
    for source in sources:
        position = bisect_left(references, source)
        candidates = []
        if position < len(references):
            candidates.append(references[position])
        if position > 0:
            candidates.append(references[position - 1])
        closest = min(candidates, key=lambda reference: abs(source - reference))
        signed_offsets.append(source - closest)

    matched = [offset for offset in signed_offsets if abs(offset) <= tolerance_seconds]
    return {
        "status": "available",
        "source_onset_count": len(sources),
        "reference_onset_count": len(references),
        "tolerance_ms": round_float(tolerance_seconds * 1000.0, 1),
        "matched_source_onset_count": len(matched),
        "matched_source_onset_pct": ratio_percent(len(matched), len(sources)),
        "median_nearest_absolute_offset_ms": round_float(
            median(abs(offset) for offset in signed_offsets) * 1000.0, 2
        ),
        "median_matched_signed_offset_ms": round_float(median(matched) * 1000.0, 2)
        if matched
        else None,
    }


def stable_pitch_segments_from_frames(
    *,
    frame_times: Sequence[float],
    f0_hz: Sequence[float],
    voiced_probabilities: Sequence[float],
    frame_rms_dbfs: Sequence[float],
    hop_duration: float,
    min_confidence: float = 0.8,
    rms_gate_dbfs: float = -55.0,
    min_frames: int = 3,
    max_neighbor_jump_semitones: float = 0.8,
    max_segment_mad_semitones: float = 0.3,
    max_tuning_error_semitones: float = 0.45,
) -> tuple[list[PitchSegment], dict[str, int]]:
    """Conservatively turn pYIN frames into stable, quantizable note evidence."""

    lengths = {
        len(frame_times),
        len(f0_hz),
        len(voiced_probabilities),
        len(frame_rms_dbfs),
    }
    if len(lengths) != 1:
        raise ValueError("pitch frame arrays must have equal lengths")

    candidate_frames: list[tuple[int, float, float, float]] = []
    for index, (time, frequency, probability, rms_dbfs) in enumerate(
        zip(frame_times, f0_hz, voiced_probabilities, frame_rms_dbfs)
    ):
        if (
            math.isfinite(float(frequency))
            and float(frequency) > 0
            and math.isfinite(float(probability))
            and float(probability) >= min_confidence
            and math.isfinite(float(rms_dbfs))
            and float(rms_dbfs) >= rms_gate_dbfs
        ):
            midi_float = 69.0 + 12.0 * math.log2(float(frequency) / 440.0)
            candidate_frames.append((index, float(time), midi_float, float(probability)))

    groups: list[list[tuple[int, float, float, float]]] = []
    current: list[tuple[int, float, float, float]] = []
    for frame in candidate_frames:
        if current:
            previous = current[-1]
            contiguous = frame[0] == previous[0] + 1
            pitch_contiguous = abs(frame[2] - previous[2]) <= max_neighbor_jump_semitones
            if not contiguous or not pitch_contiguous:
                groups.append(current)
                current = []
        current.append(frame)
    if current:
        groups.append(current)

    segments: list[PitchSegment] = []
    short_groups = 0
    unstable_groups = 0
    detuned_groups = 0
    for group in groups:
        if len(group) < min_frames:
            short_groups += 1
            continue
        midi_values = np.asarray([frame[2] for frame in group], dtype=float)
        median_midi = float(np.median(midi_values))
        mad = float(np.median(np.abs(midi_values - median_midi)))
        if mad > max_segment_mad_semitones:
            unstable_groups += 1
            continue
        midi_note = int(round(median_midi))
        tuning_error = median_midi - midi_note
        if abs(tuning_error) > max_tuning_error_semitones:
            detuned_groups += 1
            continue
        segments.append(
            PitchSegment(
                start=group[0][1],
                end=group[-1][1] + hop_duration,
                midi_float=median_midi,
                midi_note=midi_note,
                pitch_class=midi_note % 12,
                confidence=float(np.median([frame[3] for frame in group])),
                frame_count=len(group),
                tuning_error_cents=tuning_error * 100.0,
            )
        )

    diagnostics = {
        "total_frame_count": len(frame_times),
        "candidate_frame_count": len(candidate_frames),
        "candidate_group_count": len(groups),
        "accepted_segment_count": len(segments),
        "accepted_frame_count": sum(segment.frame_count for segment in segments),
        "rejected_short_group_count": short_groups,
        "rejected_unstable_group_count": unstable_groups,
        "rejected_detuned_group_count": detuned_groups,
    }
    return segments, diagnostics


def pitch_segment_compliance(
    segments: Sequence[PitchSegment], scale_pcs: Sequence[int]
) -> dict[str, Any]:
    """Summarize duration-weighted pitch-class evidence."""

    allowed = {int(pitch_class) % 12 for pitch_class in scale_pcs}
    weights = np.zeros(12, dtype=float)
    total_duration = 0.0
    in_scale_duration = 0.0
    out_of_scale = []
    for segment in segments:
        duration = max(0.0, float(segment.end - segment.start))
        weights[segment.pitch_class] += duration
        total_duration += duration
        if segment.pitch_class in allowed:
            in_scale_duration += duration
        else:
            out_of_scale.append(
                {
                    "start_seconds": round_float(segment.start),
                    "end_seconds": round_float(segment.end),
                    "midi_note": segment.midi_note,
                    "note_name": midi_note_name(segment.midi_note),
                    "confidence": round_float(segment.confidence),
                    "tuning_error_cents": round_float(segment.tuning_error_cents, 1),
                }
            )
    midi_notes = np.asarray([segment.midi_note for segment in segments], dtype=float)
    estimated_register = (
        {
            "status": "estimated_from_stable_f0_segments",
            "min_midi": int(np.min(midi_notes)),
            "min_note": midi_note_name(int(np.min(midi_notes))),
            "median_midi": round_float(np.median(midi_notes), 2),
            "max_midi": int(np.max(midi_notes)),
            "max_note": midi_note_name(int(np.max(midi_notes))),
            "span_semitones": int(np.max(midi_notes) - np.min(midi_notes)),
        }
        if segments
        else {"status": "unavailable", "reason": "no_stable_pitch_segments"}
    )
    return {
        "stable_segment_count": len(segments),
        "stable_duration_seconds": round_float(total_duration),
        "estimated_register": estimated_register,
        "in_scale_duration_pct": ratio_percent(in_scale_duration, total_duration),
        "out_of_scale_segment_count": len(out_of_scale),
        "out_of_scale_segments": out_of_scale[:100],
        "pitch_class_duration_weights": [round_float(value) for value in weights],
    }


def _strong_beat_distance(
    time_in_bar: float, *, beat_duration: float, beats_per_bar: int
) -> tuple[float, float]:
    beat_position = time_in_bar / beat_duration
    strong_beats = [float(beat) for beat in range(0, beats_per_bar, 2)]
    closest = min(strong_beats, key=lambda beat: abs(beat_position - beat))
    return closest, abs(beat_position - closest)


def midi_harmonic_metrics(
    notes: Sequence[MidiNoteEvent],
    *,
    config: AnalysisConfig,
    strong_beat_tolerance_beats: float = 0.125,
) -> dict[str, Any]:
    """Compute exact scale membership and conservative chord-error candidates."""

    scale_allowed = set(config.scale_pcs)
    total_duration = sum(max(0.0, note.end - note.start) for note in notes)
    out_scale_notes = [note for note in notes if note.pitch % 12 not in scale_allowed]
    out_scale_duration = sum(max(0.0, note.end - note.start) for note in out_scale_notes)

    strict_non_chord: list[tuple[MidiNoteEvent, int, ChordSpec]] = []
    strong_beat_errors: list[tuple[MidiNoteEvent, int, ChordSpec, float]] = []
    if config.chords:
        for note in notes:
            bar_index = int(note.start // config.bar_duration)
            if not 0 <= bar_index < config.bars:
                continue
            chord = chord_for_bar(config.chords, bar_index)
            if chord is None or note.pitch % 12 in chord.pitch_classes:
                continue
            strict_non_chord.append((note, bar_index, chord))
            time_in_bar = note.start - bar_index * config.bar_duration
            strong_beat, beat_distance = _strong_beat_distance(
                time_in_bar,
                beat_duration=config.beat_duration,
                beats_per_bar=config.beats_per_bar,
            )
            if beat_distance <= strong_beat_tolerance_beats:
                strong_beat_errors.append((note, bar_index, chord, strong_beat))

    def note_payload(note: MidiNoteEvent) -> dict[str, Any]:
        return {
            "start_seconds": round_float(note.start),
            "end_seconds": round_float(note.end),
            "midi_note": note.pitch,
            "note_name": midi_note_name(note.pitch),
            "velocity": note.velocity,
        }

    out_scale_payload = [note_payload(note) for note in out_scale_notes[:100]]
    chord_status = "configured" if config.chords else "not_configured"
    strict_payload = [
        {
            **note_payload(note),
            "bar": bar_index + 1,
            "chord": chord.symbol,
        }
        for note, bar_index, chord in strict_non_chord[:100]
    ]
    strong_payload = [
        {
            **note_payload(note),
            "bar": bar_index + 1,
            "chord": chord.symbol,
            "nearest_strong_beat": strong_beat + 1.0,
        }
        for note, bar_index, chord, strong_beat in strong_beat_errors[:100]
    ]

    return {
        "scale": {
            "status": "exact_midi_check",
            "note_count": len(notes),
            "out_of_scale_note_count": len(out_scale_notes),
            "out_of_scale_note_pct": ratio_percent(len(out_scale_notes), len(notes)),
            "out_of_scale_duration_pct": ratio_percent(out_scale_duration, total_duration),
            "out_of_scale_notes": out_scale_payload,
        },
        "chords": {
            "status": chord_status,
            "interpretation": (
                "All-note non-chord counts include valid passing/approach tones. "
                "Strong-beat errors are the conservative review queue."
            ),
            "all_note_non_chord_count": len(strict_non_chord) if config.chords else None,
            "all_note_non_chord_pct": ratio_percent(len(strict_non_chord), len(notes))
            if config.chords
            else None,
            "strong_beat_non_chord_error_count": len(strong_beat_errors)
            if config.chords
            else None,
            "strong_beat_non_chord_error_pct": ratio_percent(
                len(strong_beat_errors), len(notes)
            )
            if config.chords
            else None,
            "all_note_non_chord_events": strict_payload,
            "strong_beat_error_events": strong_payload,
        },
    }


def register_metrics(notes: Sequence[MidiNoteEvent]) -> dict[str, Any]:
    if not notes:
        return {"status": "unavailable", "reason": "no_notes"}
    pitches = np.asarray([note.pitch for note in notes], dtype=float)
    return {
        "status": "available",
        "min_midi": int(np.min(pitches)),
        "min_note": midi_note_name(int(np.min(pitches))),
        "p10_midi": round_float(np.percentile(pitches, 10), 2),
        "median_midi": round_float(np.median(pitches), 2),
        "p90_midi": round_float(np.percentile(pitches, 90), 2),
        "max_midi": int(np.max(pitches)),
        "max_note": midi_note_name(int(np.max(pitches))),
        "span_semitones": int(np.max(pitches) - np.min(pitches)),
    }


def load_audio(path: Path, analysis_sample_rate: int) -> tuple[np.ndarray, dict[str, Any]]:
    """Load audio for analysis while retaining exact source-level statistics."""

    samples, source_sample_rate = sf.read(path, dtype="float32", always_2d=True)
    channels = int(samples.shape[1])
    frame_count = int(samples.shape[0])
    duration = frame_count / source_sample_rate if source_sample_rate else 0.0
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64)))) if samples.size else 0.0
    peak_dbfs = amplitude_to_dbfs(peak)
    rms_dbfs = amplitude_to_dbfs(rms)
    crest_db = (
        round_float(float(peak_dbfs) - float(rms_dbfs), 3)
        if peak_dbfs is not None and rms_dbfs is not None
        else None
    )

    mono = np.mean(samples, axis=1, dtype=np.float32) if samples.size else np.zeros(0)
    if source_sample_rate != analysis_sample_rate and mono.size:
        mono = librosa.resample(
            mono,
            orig_sr=source_sample_rate,
            target_sr=analysis_sample_rate,
            res_type="soxr_hq",
        )
    metadata = {
        "duration_seconds": round_float(duration),
        "source_sample_rate": int(source_sample_rate),
        "channels": channels,
        "frame_count": frame_count,
        "peak_dbfs": peak_dbfs,
        "rms_dbfs": rms_dbfs,
        "crest_factor_db": crest_db,
        "clipped_sample_count": int(np.count_nonzero(np.abs(samples) >= 1.0)),
        "analysis_sample_rate": analysis_sample_rate,
    }
    return np.asarray(mono, dtype=np.float32), metadata


def detect_bass_onsets(
    signal: np.ndarray, *, sample_rate: int, hop_length: int = DEFAULT_HOP_LENGTH
) -> tuple[list[float], dict[str, Any]]:
    if signal.size == 0 or float(np.max(np.abs(signal))) < 1e-7:
        return [], {"status": "unavailable", "reason": "silent_or_empty_audio"}
    envelope = librosa.onset.onset_strength(
        y=signal,
        sr=sample_rate,
        hop_length=hop_length,
        aggregate=np.median,
    )
    onset_frames = librosa.onset.onset_detect(
        onset_envelope=envelope,
        sr=sample_rate,
        hop_length=hop_length,
        units="frames",
        backtrack=False,
        normalize=True,
        pre_max=max(1, int(0.03 * sample_rate / hop_length)),
        post_max=max(1, int(0.03 * sample_rate / hop_length)),
        pre_avg=max(1, int(0.10 * sample_rate / hop_length)),
        post_avg=max(1, int(0.10 * sample_rate / hop_length)),
        wait=max(1, int(0.08 * sample_rate / hop_length)),
        delta=0.12,
    )
    times = list(
        librosa.frames_to_time(onset_frames, sr=sample_rate, hop_length=hop_length)
    )

    # Spectral-flux detectors often miss an immediate note at sample zero.
    initial_window = signal[: max(1, int(0.12 * sample_rate))]
    later_window = signal[
        max(1, int(0.12 * sample_rate)) : max(2, int(0.24 * sample_rate))
    ]
    initial_rms = float(np.sqrt(np.mean(np.square(initial_window)))) if initial_window.size else 0.0
    later_rms = float(np.sqrt(np.mean(np.square(later_window)))) if later_window.size else 0.0
    inserted_initial_onset = False
    if initial_rms > max(1e-5, later_rms * 1.35) and (not times or times[0] > 0.15):
        times.insert(0, 0.0)
        inserted_initial_onset = True

    return [float(time) for time in times], {
        "status": "available" if times else "unavailable",
        "reason": None if times else "no_confident_onsets",
        "detector": "librosa_spectral_flux",
        "inserted_initial_onset": inserted_initial_onset,
        "onset_count": len(times),
    }


def detect_kick_proxy(
    signal: np.ndarray,
    *,
    sample_rate: int,
    hop_length: int = DEFAULT_HOP_LENGTH,
    min_frequency: float = 35.0,
    max_frequency: float = 165.0,
) -> tuple[list[float], dict[str, Any]]:
    """Detect prominent low-frequency percussive changes in a backing track."""

    if signal.size < 2048 or float(np.max(np.abs(signal))) < 1e-7:
        return [], {
            "status": "unavailable",
            "reason": "silent_or_too_short",
            "interpretation": "No kick-alignment score was attempted.",
        }

    try:
        _, percussive = librosa.effects.hpss(signal, margin=(1.0, 2.0))
        stft = np.abs(
            librosa.stft(
                percussive,
                n_fft=2048,
                hop_length=hop_length,
                window="hann",
            )
        )
        frequencies = librosa.fft_frequencies(sr=sample_rate, n_fft=2048)
        low_mask = (frequencies >= min_frequency) & (frequencies <= max_frequency)
        if not np.any(low_mask):
            raise ValueError("analysis sample rate has no low-frequency bins")
        low_energy = np.mean(np.square(stft[low_mask, :], dtype=np.float64), axis=0)
        log_energy = np.log1p(low_energy * 1000.0)
        flux = np.maximum(0.0, np.diff(log_energy, prepend=log_energy[0]))
        if flux.size >= 5:
            flux = np.convolve(flux, np.ones(3) / 3.0, mode="same")

        median_flux = float(np.median(flux))
        mad = float(np.median(np.abs(flux - median_flux)))
        robust_scale = max(1e-10, 1.4826 * mad)
        height = median_flux + 1.5 * robust_scale
        prominence = max(robust_scale, float(np.std(flux)) * 0.35)
        min_distance = max(1, int(0.12 * sample_rate / hop_length))
        peaks, properties = find_peaks(
            flux,
            height=height,
            prominence=prominence,
            distance=min_distance,
        )
        times = librosa.frames_to_time(peaks, sr=sample_rate, hop_length=hop_length)
        duration = signal.size / sample_rate
        events_per_second = len(times) / duration if duration > 0 else 0.0
        peak_prominences = np.asarray(properties.get("prominences", []), dtype=float)
        median_prominence_z = (
            float(np.median(peak_prominences) / robust_scale)
            if peak_prominences.size
            else 0.0
        )
        plausible_density = 0.2 <= events_per_second <= 5.0
        enough_events = len(times) >= 2
        confidence_score = float(
            np.clip(
                0.5 * min(1.0, median_prominence_z / 4.0)
                + 0.3 * (1.0 if plausible_density else 0.0)
                + 0.2 * min(1.0, len(times) / 8.0),
                0.0,
                1.0,
            )
        )
        if not enough_events or confidence_score < 0.35:
            status = "unavailable"
            reason = "insufficient_low_frequency_peak_evidence"
            times_output: list[float] = []
        else:
            status = "available"
            reason = None
            times_output = [float(time) for time in times]
        return times_output, {
            "status": status,
            "reason": reason,
            "method": "HPSS_percussive_low_band_flux",
            "frequency_band_hz": [min_frequency, max_frequency],
            "proxy_event_count": len(times_output),
            "events_per_second": round_float(events_per_second),
            "confidence_score": round_float(confidence_score),
            "confidence_label": "medium"
            if confidence_score >= 0.65
            else "low",
            "interpretation": (
                "Prominent low-frequency percussive onsets; these can include toms "
                "or other low transients and are not verified kick labels."
            ),
        }
    except Exception as exc:  # Research report should fail soft, with evidence.
        return [], {
            "status": "unavailable",
            "reason": f"kick_proxy_failed: {type(exc).__name__}: {exc}",
            "interpretation": "No kick-alignment score was attempted.",
        }


def analyze_audio_pitch(
    signal: np.ndarray,
    *,
    sample_rate: int,
    scale_pcs: Sequence[int],
    overall_rms_dbfs: float | None,
    min_confidence: float,
    hop_length: int = DEFAULT_HOP_LENGTH,
) -> tuple[dict[str, Any], tuple[float, ...] | None]:
    """Extract stable pitch classes or explicitly withhold the result."""

    if signal.size < DEFAULT_F0_FRAME_LENGTH:
        return (
            {
                "status": "insufficient_confidence",
                "reason": "audio_too_short_for_conservative_f0",
                "estimated_register": {
                    "status": "unavailable",
                    "reason": "audio_too_short_for_conservative_f0",
                },
                "in_scale_duration_pct": None,
            },
            None,
        )
    try:
        f0, _, voiced_probability = librosa.pyin(
            signal,
            fmin=librosa.note_to_hz("B0"),
            fmax=librosa.note_to_hz("E4"),
            sr=sample_rate,
            frame_length=DEFAULT_F0_FRAME_LENGTH,
            hop_length=hop_length,
            center=True,
        )
        if voiced_probability is None:
            voiced_probability = np.zeros_like(f0)
        rms = librosa.feature.rms(
            y=signal,
            frame_length=DEFAULT_F0_FRAME_LENGTH,
            hop_length=hop_length,
            center=True,
        )[0]
        frame_count = min(len(f0), len(voiced_probability), len(rms))
        f0 = np.asarray(f0[:frame_count], dtype=float)
        voiced_probability = np.asarray(voiced_probability[:frame_count], dtype=float)
        rms = np.asarray(rms[:frame_count], dtype=float)
        rms_dbfs = 20.0 * np.log10(np.maximum(rms, 1e-12))
        times = librosa.frames_to_time(
            np.arange(frame_count), sr=sample_rate, hop_length=hop_length
        )
        relative_gate = (
            float(overall_rms_dbfs) - 30.0 if overall_rms_dbfs is not None else -55.0
        )
        rms_gate = max(-55.0, relative_gate)
        segments, diagnostics = stable_pitch_segments_from_frames(
            frame_times=times,
            f0_hz=f0,
            voiced_probabilities=voiced_probability,
            frame_rms_dbfs=rms_dbfs,
            hop_duration=hop_length / sample_rate,
            min_confidence=min_confidence,
            rms_gate_dbfs=rms_gate,
        )
        compliance = pitch_segment_compliance(segments, scale_pcs)
        accepted_frames = diagnostics["accepted_frame_count"]
        candidate_frames = diagnostics["candidate_frame_count"]
        acceptance_pct = ratio_percent(accepted_frames, candidate_frames)
        stable_duration = float(compliance["stable_duration_seconds"] or 0.0)
        enough_evidence = (
            len(segments) >= 3
            and stable_duration >= 0.4
            and candidate_frames > 0
            and accepted_frames / candidate_frames >= 0.25
        )
        result = {
            "status": "ok" if enough_evidence else "insufficient_confidence",
            "reason": None
            if enough_evidence
            else (
                "fewer_than_3_stable_segments"
                if len(segments) < 3
                else "too_little_stable_pitch_duration_or_frame_acceptance"
            ),
            "method": "pYIN_stable_monophonic_segments",
            "minimum_voiced_probability": min_confidence,
            "rms_gate_dbfs": round_float(rms_gate),
            "accepted_candidate_frame_pct": acceptance_pct,
            "diagnostics": diagnostics,
            **compliance,
        }
        if not enough_evidence:
            result["in_scale_duration_pct"] = None
            result["out_of_scale_segment_count"] = None
            result["out_of_scale_segments"] = []
            result["pitch_class_duration_weights"] = None
            result["estimated_register"] = {
                "status": "unavailable",
                "reason": "overall_audio_pitch_confidence_below_threshold",
            }
            return result, None
        weights = tuple(float(value) for value in compliance["pitch_class_duration_weights"])
        return result, weights
    except Exception as exc:
        return (
            {
                "status": "insufficient_confidence",
                "reason": f"pitch_analysis_failed: {type(exc).__name__}: {exc}",
                "estimated_register": {
                    "status": "unavailable",
                    "reason": "pitch_analysis_failed",
                },
                "in_scale_duration_pct": None,
            },
            None,
        )


def density_summary(counts: Sequence[int], *, beats_per_bar: int) -> dict[str, Any]:
    if not counts:
        return {
            "per_bar": [],
            "mean_per_bar": None,
            "standard_deviation_per_bar": None,
            "mean_per_beat": None,
        }
    values = np.asarray(counts, dtype=float)
    return {
        "per_bar": [int(value) for value in counts],
        "mean_per_bar": round_float(np.mean(values)),
        "standard_deviation_per_bar": round_float(np.std(values)),
        "mean_per_beat": round_float(np.mean(values) / beats_per_bar),
    }


def analyze_bass_audio(
    path: Path,
    *,
    config: AnalysisConfig,
    kick_proxy_times: Sequence[float],
    kick_proxy_available: bool,
) -> tuple[dict[str, Any], PhraseFeatures]:
    signal, level = load_audio(path, config.analysis_sample_rate)
    analysis_signal = signal[: int(round(config.analysis_duration * config.analysis_sample_rate))]
    onsets, onset_diagnostics = detect_bass_onsets(
        analysis_signal, sample_rate=config.analysis_sample_rate
    )
    counts = counts_per_bar(
        onsets, bar_duration=config.bar_duration, bar_count=config.bars
    )
    pitch, pitch_weights = analyze_audio_pitch(
        analysis_signal,
        sample_rate=config.analysis_sample_rate,
        scale_pcs=config.scale_pcs,
        overall_rms_dbfs=level["rms_dbfs"],
        min_confidence=config.min_f0_confidence,
    )
    if kick_proxy_available:
        alignment = nearest_event_alignment(
            onsets,
            kick_proxy_times,
            tolerance_seconds=config.kick_tolerance_seconds,
        )
        alignment["reference"] = "backing_low_frequency_onset_proxy"
    else:
        alignment = {
            "status": "unavailable",
            "reason": "backing_kick_proxy_unavailable",
            "source_onset_count": len(onsets),
        }
    vector = quantized_onset_vector(
        onsets,
        tempo=config.tempo,
        beats_per_bar=config.beats_per_bar,
        bar_count=config.bars,
        subdivisions_per_beat=config.subdivisions_per_beat,
    )
    feature = PhraseFeatures(
        label=path.name,
        kind="bass_audio",
        onset_vector=vector,
        pitch_class_weights=pitch_weights,
        pitch_reliable=pitch["status"] == "ok",
    )
    report = {
        "label": path.name,
        "path": str(path.resolve()),
        "level_and_duration": level,
        "onsets": {
            **onset_diagnostics,
            "times_seconds": [round_float(time) for time in onsets],
            "density": density_summary(counts, beats_per_bar=config.beats_per_bar),
        },
        "kick_proxy_alignment": alignment,
        "pitch_and_key_compliance": pitch,
    }
    return report, feature


def load_midi_notes(path: Path) -> tuple[list[MidiNoteEvent], dict[str, Any]]:
    midi = pretty_midi.PrettyMIDI(str(path))
    instruments = [instrument for instrument in midi.instruments if not instrument.is_drum]
    notes = [
        MidiNoteEvent(
            start=float(note.start),
            end=float(note.end),
            pitch=int(note.pitch),
            velocity=int(note.velocity),
            instrument=instrument.name or f"program_{instrument.program}",
        )
        for instrument in instruments
        for note in instrument.notes
    ]
    notes.sort(key=lambda note: (note.start, note.pitch, note.end))
    tempo_times, tempi = midi.get_tempo_changes()
    metadata = {
        "duration_seconds": round_float(midi.get_end_time()),
        "resolution_ticks_per_quarter": int(midi.resolution),
        "non_drum_instrument_count": len(instruments),
        "instruments": [
            {
                "name": instrument.name or f"program_{instrument.program}",
                "program": int(instrument.program),
                "note_count": len(instrument.notes),
            }
            for instrument in instruments
        ],
        "embedded_tempo_changes": [
            {"time_seconds": round_float(time), "bpm": round_float(tempo)}
            for time, tempo in zip(tempo_times, tempi)
        ],
    }
    return notes, metadata


def analyze_midi_take(
    path: Path,
    *,
    config: AnalysisConfig,
    kick_proxy_times: Sequence[float],
    kick_proxy_available: bool,
) -> tuple[dict[str, Any], PhraseFeatures]:
    notes, metadata = load_midi_notes(path)
    included_notes = [
        note for note in notes if 0 <= note.start < config.analysis_duration
    ]
    onset_times = [note.start for note in included_notes]
    counts = counts_per_bar(
        onset_times, bar_duration=config.bar_duration, bar_count=config.bars
    )
    note_durations = [max(0.0, note.end - note.start) for note in included_notes]
    velocity_values = [note.velocity for note in included_notes]
    pitch_weights = np.zeros(12, dtype=float)
    for note in included_notes:
        pitch_weights[note.pitch % 12] += max(0.01, note.end - note.start)
    harmonic = midi_harmonic_metrics(included_notes, config=config)
    if kick_proxy_available:
        alignment = nearest_event_alignment(
            onset_times,
            kick_proxy_times,
            tolerance_seconds=config.kick_tolerance_seconds,
        )
        alignment["reference"] = "backing_low_frequency_onset_proxy"
    else:
        alignment = {
            "status": "unavailable",
            "reason": "backing_kick_proxy_unavailable",
            "source_onset_count": len(onset_times),
        }

    first_tempo = (
        metadata["embedded_tempo_changes"][0]["bpm"]
        if metadata["embedded_tempo_changes"]
        else None
    )
    tempo_warning = None
    if first_tempo is not None and abs(float(first_tempo) - config.tempo) > 0.5:
        tempo_warning = (
            f"embedded first tempo {first_tempo} BPM differs from configured "
            f"{config.tempo} BPM; bar/chord alignment may be invalid"
        )
    vector = quantized_onset_vector(
        onset_times,
        tempo=config.tempo,
        beats_per_bar=config.beats_per_bar,
        bar_count=config.bars,
        subdivisions_per_beat=config.subdivisions_per_beat,
    )
    feature = PhraseFeatures(
        label=path.name,
        kind="session_player_midi",
        onset_vector=vector,
        pitch_class_weights=tuple(float(value) for value in pitch_weights),
        pitch_reliable=bool(included_notes),
    )
    report = {
        "label": path.name,
        "path": str(path.resolve()),
        "file": metadata,
        "configured_tempo_warning": tempo_warning,
        "notes": {
            "total_note_count": len(notes),
            "analyzed_note_count": len(included_notes),
            "excluded_after_analysis_window": len(notes) - len(included_notes),
            "density": density_summary(counts, beats_per_bar=config.beats_per_bar),
            "mean_duration_seconds": round_float(np.mean(note_durations))
            if note_durations
            else None,
            "median_duration_seconds": round_float(np.median(note_durations))
            if note_durations
            else None,
            "mean_velocity": round_float(np.mean(velocity_values))
            if velocity_values
            else None,
            "velocity_standard_deviation": round_float(np.std(velocity_values))
            if velocity_values
            else None,
        },
        "register": register_metrics(included_notes),
        "kick_proxy_alignment": alignment,
        "harmonic_compliance": harmonic,
    }
    return report, feature


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return round_float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Path):
        return str(value)
    return value


def build_markdown_report(report: dict[str, Any]) -> str:
    config = report["configuration"]
    lines = [
        "# Bass Performance Benchmark",
        "",
        f"Generated: {report['generated_at_utc']}",
        "",
        "## Configuration",
        "",
        f"- Tempo: {config['tempo_bpm']} BPM",
        f"- Meter: {config['beats_per_bar']}/4",
        f"- Analysis window: {config['bars']} bars ({config['analysis_duration_seconds']} seconds)",
        f"- Key/scale: {config['key']} {config['scale']}",
        f"- Scale pitch classes: {', '.join(config['scale_pitch_classes'])}",
        f"- Chords: {' | '.join(config['chords']) if config['chords'] else 'not configured'}",
        "",
        "## Backing and kick proxy",
        "",
    ]
    backing = report["backing"]
    level = backing["level_and_duration"]
    proxy = backing["kick_proxy"]
    lines.extend(
        [
            f"- File: `{backing['label']}`",
            f"- Duration: {level['duration_seconds']} s; RMS: {level['rms_dbfs']} dBFS; peak: {level['peak_dbfs']} dBFS",
            f"- Proxy status: **{proxy['status']}**; events: {proxy.get('proxy_event_count', 0)}; confidence: {proxy.get('confidence_label', 'n/a')} ({proxy.get('confidence_score', 'n/a')})",
            f"- Interpretation: {proxy['interpretation']}",
            "",
            "## Generated bass audio",
            "",
            "| Take | Duration s | RMS dBFS | Onsets | Mean/bar | Kick-aligned % | Audio scale compliance |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report["bass_audio"]:
        item_level = item["level_and_duration"]
        onset = item["onsets"]
        alignment = item["kick_proxy_alignment"]
        pitch = item["pitch_and_key_compliance"]
        compliance = (
            f"{pitch.get('in_scale_duration_pct')}%"
            if pitch["status"] == "ok"
            else f"withheld ({pitch.get('reason', 'low confidence')})"
        )
        lines.append(
            f"| {item['label']} | {item_level['duration_seconds']} | {item_level['rms_dbfs']} "
            f"| {onset.get('onset_count', 0)} | {onset['density']['mean_per_bar']} "
            f"| {alignment.get('matched_source_onset_pct')} | {compliance} |"
        )

    lines.extend(
        [
            "",
            "## Session Player MIDI",
            "",
            "| Take | Notes | Mean/bar | Register | Scale errors | Strong-beat chord errors | Kick-aligned % |",
            "|---|---:|---:|---|---:|---:|---:|",
        ]
    )
    for item in report["session_player_midi"]:
        note_metrics = item["notes"]
        register = item["register"]
        harmonic = item["harmonic_compliance"]
        scale = harmonic["scale"]
        chords = harmonic["chords"]
        register_text = (
            f"{register['min_note']}–{register['max_note']}"
            if register["status"] == "available"
            else "n/a"
        )
        lines.append(
            f"| {item['label']} | {note_metrics['analyzed_note_count']} "
            f"| {note_metrics['density']['mean_per_bar']} | {register_text} "
            f"| {scale['out_of_scale_note_count']} "
            f"| {chords['strong_beat_non_chord_error_count']} "
            f"| {item['kick_proxy_alignment'].get('matched_source_onset_pct')} |"
        )
        if item.get("configured_tempo_warning"):
            lines.append(f"\n> {item['label']}: {item['configured_tempo_warning']}\n")

    lines.extend(
        [
            "",
            "## Phrase similarity and diversity",
            "",
            "| Left | Right | Rhythm | Pitch classes | Phrase similarity | Diversity | Basis |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for comparison in report["phrase_comparisons"]["pairs"]:
        lines.append(
            f"| {comparison['left']} | {comparison['right']} "
            f"| {comparison['rhythm_similarity']} | {comparison['pitch_class_similarity']} "
            f"| {comparison['phrase_similarity']} | {comparison['phrase_diversity']} "
            f"| {comparison['basis']} |"
        )
    if not report["phrase_comparisons"]["pairs"]:
        lines.append("| n/a | n/a | n/a | n/a | n/a | n/a | at least two takes required |")

    lines.extend(["", "### Group summary", ""])
    for group in report["phrase_comparisons"]["group_summary"]:
        lines.append(
            f"- {' vs '.join(group['groups'])}: mean similarity "
            f"{group['mean_phrase_similarity']}, diversity {group['mean_phrase_diversity']} "
            f"across {group['pair_count']} pair(s)."
        )
    if not report["phrase_comparisons"]["group_summary"]:
        lines.append("- Not enough comparable pairs.")

    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "- Audio onset detection is an estimate; legato notes can be missed and slap/ghost transients can be over-counted.",
            "- The backing kick track is not isolated. Low-frequency onset alignment is a proxy and can react to toms or other low transients.",
            "- Audio pitch compliance is duration-weighted only over stable high-confidence monophonic pYIN segments. A withheld result is not a failure or an out-of-key verdict.",
            "- MIDI scale membership is exact. All-note chord membership is deliberately not treated as an error because passing tones are valid; review the strong-beat error queue.",
            "- Phrase similarity uses a fixed sixteenth-note onset grid plus pitch-class weighting when reliable. It is a diagnostic, not a perceptual quality score.",
            "",
        ]
    )
    return "\n".join(lines)


def run_analysis(
    *,
    backing_path: Path,
    bass_wav_paths: Sequence[Path],
    midi_paths: Sequence[Path],
    config: AnalysisConfig,
) -> dict[str, Any]:
    backing_signal, backing_level = load_audio(
        backing_path, config.analysis_sample_rate
    )
    backing_analysis_signal = backing_signal[
        : int(round(config.analysis_duration * config.analysis_sample_rate))
    ]
    kick_times, kick_proxy = detect_kick_proxy(
        backing_analysis_signal, sample_rate=config.analysis_sample_rate
    )
    kick_available = kick_proxy["status"] == "available"

    bass_audio = []
    midi_takes = []
    features: list[PhraseFeatures] = []
    for path in bass_wav_paths:
        item, feature = analyze_bass_audio(
            path,
            config=config,
            kick_proxy_times=kick_times,
            kick_proxy_available=kick_available,
        )
        bass_audio.append(item)
        features.append(feature)
    for path in midi_paths:
        item, feature = analyze_midi_take(
            path,
            config=config,
            kick_proxy_times=kick_times,
            kick_proxy_available=kick_available,
        )
        midi_takes.append(item)
        features.append(feature)

    comparisons = pairwise_phrase_metrics(features)
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "tempo_bpm": config.tempo,
            "beats_per_bar": config.beats_per_bar,
            "subdivisions_per_beat": config.subdivisions_per_beat,
            "bars": config.bars,
            "analysis_duration_seconds": round_float(config.analysis_duration),
            "key": config.key_name,
            "scale": config.scale,
            "scale_pitch_classes": [
                pitch_class_name(pitch_class) for pitch_class in config.scale_pcs
            ],
            "chords": [
                chord.symbol if chord is not None else "N" for chord in config.chords
            ],
            "chord_progression_cycles_when_shorter_than_analysis": True,
            "kick_alignment_tolerance_ms": round_float(
                config.kick_tolerance_seconds * 1000.0, 1
            ),
            "minimum_audio_f0_confidence": config.min_f0_confidence,
            "analysis_sample_rate": config.analysis_sample_rate,
        },
        "backing": {
            "label": backing_path.name,
            "path": str(backing_path.resolve()),
            "level_and_duration": backing_level,
            "kick_proxy": {
                **kick_proxy,
                "times_seconds": [round_float(time) for time in kick_times],
                "density": density_summary(
                    counts_per_bar(
                        kick_times,
                        bar_duration=config.bar_duration,
                        bar_count=config.bars,
                    ),
                    beats_per_bar=config.beats_per_bar,
                ),
            },
        },
        "bass_audio": bass_audio,
        "session_player_midi": midi_takes,
        "phrase_comparisons": {
            "pairs": comparisons,
            "group_summary": summarize_pair_groups(comparisons),
        },
    }
    return _json_safe(report)


def _existing_file(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file does not exist: {path}")
    return path


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze owned backing/bass WAVs and Session Player MIDI takes. "
            "Emits a machine-readable JSON report and a review-friendly Markdown report."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--backing", required=True, type=_existing_file)
    parser.add_argument(
        "--bass-wav",
        action="append",
        default=[],
        type=_existing_file,
        help="Generated isolated bass stem; repeat for multiple takes.",
    )
    parser.add_argument(
        "--midi",
        action="append",
        default=[],
        type=_existing_file,
        help="Session Player bass MIDI take; repeat for multiple takes.",
    )
    parser.add_argument("--tempo", required=True, type=float)
    parser.add_argument("--key", required=True, help="Root note, e.g. D, F#, or Bb.")
    parser.add_argument("--scale", default="natural_minor")
    parser.add_argument(
        "--chords",
        help="One chord per bar, separated by |, comma, or spaces; progression cycles.",
    )
    parser.add_argument(
        "--bars",
        type=int,
        help="Analysis bars. By default inferred from backing duration.",
    )
    parser.add_argument("--beats-per-bar", type=int, default=4)
    parser.add_argument("--subdivisions-per-beat", type=int, default=4)
    parser.add_argument("--kick-tolerance-ms", type=float, default=75.0)
    parser.add_argument("--min-f0-confidence", type=float, default=0.80)
    parser.add_argument(
        "--analysis-sample-rate", type=int, default=DEFAULT_ANALYSIS_SAMPLE_RATE
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("research/benchmark_results")
    )
    parser.add_argument(
        "--name",
        default="ace_step_bass_benchmark",
        help="Output filename stem (timestamp is appended).",
    )
    return parser


def validate_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.bass_wav and not args.midi:
        parser.error("supply at least one --bass-wav or --midi take")
    if args.tempo <= 0:
        parser.error("--tempo must be positive")
    if args.bars is not None and args.bars <= 0:
        parser.error("--bars must be positive")
    if args.beats_per_bar <= 0:
        parser.error("--beats-per-bar must be positive")
    if args.subdivisions_per_beat <= 0:
        parser.error("--subdivisions-per-beat must be positive")
    if not 0.0 <= args.min_f0_confidence <= 1.0:
        parser.error("--min-f0-confidence must be between 0 and 1")
    if args.kick_tolerance_ms < 0:
        parser.error("--kick-tolerance-ms cannot be negative")
    if args.analysis_sample_rate < 8_000:
        parser.error("--analysis-sample-rate must be at least 8000 Hz")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    validate_args(args, parser)

    try:
        key_pc = parse_note_name(args.key)
        scale = normalize_scale_name(args.scale)
        chords = parse_chord_progression(args.chords)
    except ValueError as exc:
        parser.error(str(exc))

    # A lightweight metadata read avoids doing the full resample twice.
    backing_info = sf.info(args.backing)
    bar_duration = args.beats_per_bar * 60.0 / args.tempo
    bars = args.bars or infer_bar_count(backing_info.duration, bar_duration)
    config = AnalysisConfig(
        tempo=float(args.tempo),
        key_pc=key_pc,
        key_name=args.key.strip(),
        scale=scale,
        scale_pcs=scale_pitch_classes(key_pc, scale),
        chords=chords,
        bars=bars,
        beats_per_bar=args.beats_per_bar,
        subdivisions_per_beat=args.subdivisions_per_beat,
        kick_tolerance_seconds=args.kick_tolerance_ms / 1000.0,
        min_f0_confidence=args.min_f0_confidence,
        analysis_sample_rate=args.analysis_sample_rate,
    )
    report = run_analysis(
        backing_path=args.backing,
        bass_wav_paths=args.bass_wav,
        midi_paths=args.midi,
        config=config,
    )
    markdown = build_markdown_report(report)

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", args.name).strip("._") or "benchmark"
    output_prefix = output_dir / f"{safe_name}_{timestamp}"
    json_path = output_prefix.with_suffix(".json")
    markdown_path = output_prefix.with_suffix(".md")
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(markdown, encoding="utf-8")

    print(f"JSON report: {json_path.resolve()}")
    print(f"Markdown report: {markdown_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
