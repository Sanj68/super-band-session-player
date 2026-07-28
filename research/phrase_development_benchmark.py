"""Measure written bass phrase development across repeated clean MIDI takes.

This research tool deliberately ignores performance detail such as velocity,
duration, microtiming, pitch bends, ghosts, and controller data.  It asks a
narrower question: does the *composition* develop over 16 bars, and do new
seeds produce related but materially different written phrases?

Example:

    backend/.venv/bin/python research/phrase_development_benchmark.py \
      --midi /path/to/take_1.mid \
      --midi /path/to/take_2.mid \
      --tempo 116 --chords 'F|Gm|Dm|Bb' --bars 16 --tonic D

At least eight takes are required for a batch-ready verdict.  JSON and
Markdown are written beneath ``research/benchmark_results/`` unless an output
directory is supplied.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
import json
import math
from pathlib import Path
import re
from statistics import mean, pstdev
from typing import Any, Iterable, Sequence

import pretty_midi


REPORT_SCHEMA_VERSION = 1
SECTION_BARS = 4
MINIMUM_BATCH_TAKES = 8
MINIMUM_INDIVIDUAL_PASS_RATIO = 0.75

NOTE_BASE_PCS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
PITCH_CLASS_NAMES = (
    "C",
    "C#",
    "D",
    "Eb",
    "E",
    "F",
    "F#",
    "G",
    "Ab",
    "A",
    "Bb",
    "B",
)

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


# These are evidence gates, not a claim that one numerical target defines good
# music.  They describe the useful middle ground requested for this iteration:
# recognizable material without literal looping, and contrast without random
# section replacement.
GATE_THRESHOLDS: dict[str, dict[str, float | int]] = {
    "same_harmony_rhythm_mean": {"minimum": 0.20, "maximum": 0.38},
    "same_harmony_exact_copy_ratio": {"maximum": 1.0 / 12.0},
    "section_skeleton_mean": {"minimum": 0.45, "maximum": 0.70},
    "section_skeleton_max": {"maximum": 0.80},
    "form_a_aprime": {"minimum": 0.45, "maximum": 0.80},
    "form_aprime_b": {"minimum": 0.30, "maximum": 0.65},
    "form_contrast_gap": {"minimum": 0.10},
    "form_b_final": {"minimum": 0.35, "maximum": 0.70},
    "ending_similarity_mean": {"minimum": 0.30, "maximum": 0.70},
    "ending_landing_pitch_classes": {"minimum": 2},
    "motif_reuse": {"minimum": 0.15, "maximum": 0.45},
    "duplicate_motif_ratio": {"minimum": 0.25, "maximum": 0.50},
    "cross_seed_rhythm_p50": {"minimum": 0.48, "maximum": 0.60},
    "cross_seed_rhythm_p90": {"maximum": 0.68},
    "cross_seed_skeleton_p50": {"minimum": 0.25, "maximum": 0.55},
    "cross_seed_skeleton_p90": {"maximum": 0.70},
    "note_count_cv": {"maximum": 0.12},
}


@dataclass(frozen=True)
class ChordSpec:
    symbol: str
    root_pc: int
    quality: str
    pitch_classes: tuple[int, ...]

    @property
    def identity(self) -> tuple[int, str, tuple[int, ...]]:
        return self.root_pc, self.quality, self.pitch_classes


@dataclass(frozen=True)
class BenchmarkConfig:
    tempo: float
    chords: tuple[ChordSpec, ...]
    bars: int
    tonic_pc: int | None = None
    beats_per_bar: int = 4
    subdivisions_per_beat: int = 4

    @property
    def slots_per_bar(self) -> int:
        return self.beats_per_bar * self.subdivisions_per_beat

    @property
    def total_slots(self) -> int:
        return self.bars * self.slots_per_bar

    @property
    def total_beats(self) -> int:
        return self.bars * self.beats_per_bar


@dataclass(frozen=True)
class MidiNoteEvent:
    """A note onset expressed in configured musical beats."""

    start_beats: float
    pitch: int
    velocity: int = 100


@dataclass(frozen=True)
class QuantizedNote:
    global_slot: int
    bar: int
    slot: int
    pitch: int
    start_beats: float


@dataclass(frozen=True)
class TakeFeatures:
    label: str
    path: str
    note_count: int
    bar_rhythms: tuple[frozenset[int], ...]
    full_rhythm: frozenset[int]
    beat_skeleton: tuple[int | None, ...]
    bar_landing_pitch_classes: tuple[int | None, ...]
    tempo_events: tuple[float, ...]
    has_pitch_bends: bool
    has_control_changes: bool


def round_float(value: float | int | None, digits: int = 4) -> float | int | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    if isinstance(value, int):
        return value
    return round(number, digits)


def parse_note_name(note: str) -> int:
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


def parse_chord(symbol: str) -> ChordSpec:
    cleaned = symbol.strip()
    match = re.fullmatch(r"([A-Ga-g])([#b]?)([^/]*)?(?:/[A-Ga-g][#b]?)?", cleaned)
    if not match:
        raise ValueError(f"unsupported chord symbol: {symbol!r}")
    letter, accidental, raw_quality = match.groups()
    root_pc = parse_note_name(f"{letter}{accidental}")
    quality_key = (raw_quality or "").strip().lower()
    quality = CHORD_QUALITY_ALIASES.get(quality_key)
    if quality is None:
        raise ValueError(f"unsupported chord quality in {symbol!r}")
    pitch_classes = tuple(
        sorted({(root_pc + interval) % 12 for interval in CHORD_INTERVALS[quality]})
    )
    return ChordSpec(cleaned, root_pc, quality, pitch_classes)


def parse_chord_progression(raw: str) -> tuple[ChordSpec, ...]:
    symbols = [item for item in re.split(r"\s*(?:\||,)\s*|\s+", raw.strip()) if item]
    if not symbols:
        raise ValueError("at least one chord is required")
    return tuple(parse_chord(symbol) for symbol in symbols)


def chord_for_bar(config: BenchmarkConfig, bar: int) -> ChordSpec:
    return config.chords[bar % len(config.chords)]


def _quantize_half_up(value: float) -> int:
    return int(math.floor(value + 0.5))


def quantize_notes(
    notes: Iterable[MidiNoteEvent], config: BenchmarkConfig
) -> tuple[QuantizedNote, ...]:
    output: list[QuantizedNote] = []
    for note in notes:
        if not math.isfinite(note.start_beats) or note.start_beats < 0:
            continue
        global_slot = _quantize_half_up(
            note.start_beats * config.subdivisions_per_beat
        )
        if not 0 <= global_slot < config.total_slots:
            continue
        bar, slot = divmod(global_slot, config.slots_per_bar)
        output.append(
            QuantizedNote(
                global_slot=global_slot,
                bar=bar,
                slot=slot,
                pitch=int(note.pitch),
                start_beats=float(note.start_beats),
            )
        )
    return tuple(
        sorted(
            output,
            key=lambda note: (
                note.global_slot,
                note.start_beats,
                note.pitch,
            ),
        )
    )


def extract_take_features(
    *,
    label: str,
    path: str,
    notes: Sequence[MidiNoteEvent],
    config: BenchmarkConfig,
    tempo_events: Sequence[float] = (),
    has_pitch_bends: bool = False,
    has_control_changes: bool = False,
) -> TakeFeatures:
    quantized = quantize_notes(notes, config)
    rows: list[set[int]] = [set() for _ in range(config.bars)]
    full_rhythm: set[int] = set()
    for note in quantized:
        rows[note.bar].add(note.slot)
        full_rhythm.add(note.global_slot)

    first_by_beat: dict[int, QuantizedNote] = {}
    for note in quantized:
        beat = note.global_slot // config.subdivisions_per_beat
        existing = first_by_beat.get(beat)
        if existing is None or (
            note.global_slot,
            note.start_beats,
            note.pitch,
        ) < (
            existing.global_slot,
            existing.start_beats,
            existing.pitch,
        ):
            first_by_beat[beat] = note

    skeleton: list[int | None] = [None] * config.total_beats
    for beat, note in first_by_beat.items():
        bar = beat // config.beats_per_bar
        chord = chord_for_bar(config, bar)
        skeleton[beat] = (note.pitch % 12 - chord.root_pc) % 12

    landing_pitch_classes: list[int | None] = [None] * config.bars
    for bar in range(config.bars):
        bar_notes = [note for note in quantized if note.bar == bar]
        if bar_notes:
            last_slot = max(note.global_slot for note in bar_notes)
            last_notes = [
                note for note in bar_notes if note.global_slot == last_slot
            ]
            # Bass composition MIDI should be monophonic.  If malformed input
            # stacks pitches at its final onset, the lowest pitch is the least
            # surprising bass landing and keeps the rule deterministic.
            landing_pitch_classes[bar] = min(note.pitch for note in last_notes) % 12

    return TakeFeatures(
        label=label,
        path=path,
        note_count=len(quantized),
        bar_rhythms=tuple(frozenset(row) for row in rows),
        full_rhythm=frozenset(full_rhythm),
        beat_skeleton=tuple(skeleton),
        bar_landing_pitch_classes=tuple(landing_pitch_classes),
        tempo_events=tuple(float(value) for value in tempo_events),
        has_pitch_bends=has_pitch_bends,
        has_control_changes=has_control_changes,
    )


def load_midi_take(path: Path, config: BenchmarkConfig) -> TakeFeatures:
    midi = pretty_midi.PrettyMIDI(str(path))
    notes = [
        MidiNoteEvent(
            start_beats=float(note.start) * config.tempo / 60.0,
            pitch=int(note.pitch),
            velocity=int(note.velocity),
        )
        for instrument in midi.instruments
        if not instrument.is_drum
        for note in instrument.notes
        if math.isfinite(float(note.start))
    ]
    notes.sort(key=lambda note: (note.start_beats, note.pitch, note.velocity))
    _, tempo_events = midi.get_tempo_changes()
    return extract_take_features(
        label=path.name,
        path=str(path.resolve()),
        notes=notes,
        config=config,
        tempo_events=tempo_events,
        has_pitch_bends=any(
            instrument.pitch_bends
            for instrument in midi.instruments
            if not instrument.is_drum
        ),
        has_control_changes=any(
            instrument.control_changes
            for instrument in midi.instruments
            if not instrument.is_drum
        ),
    )


def set_jaccard(left: frozenset[Any] | set[Any], right: frozenset[Any] | set[Any]) -> float | None:
    union = left | right
    if not union:
        return None
    return len(left & right) / len(union)


def _available_mean(values: Iterable[float | None]) -> float | None:
    available = [float(value) for value in values if value is not None]
    return mean(available) if available else None


def _section_rhythm(
    bar_rhythms: Sequence[frozenset[int]],
    *,
    start_bar: int,
    bar_count: int,
    slots_per_bar: int,
) -> frozenset[int]:
    return frozenset(
        relative_bar * slots_per_bar + slot
        for relative_bar, row in enumerate(
            bar_rhythms[start_bar : start_bar + bar_count]
        )
        for slot in row
    )


def _skeleton_tokens(
    skeleton: Sequence[int | None],
    *,
    start_beat: int = 0,
    beat_count: int | None = None,
) -> frozenset[tuple[int, int]]:
    stop = len(skeleton) if beat_count is None else min(
        len(skeleton), start_beat + beat_count
    )
    return frozenset(
        (beat - start_beat, int(skeleton[beat]))
        for beat in range(start_beat, stop)
        if skeleton[beat] is not None
    )


def same_harmony_rhythm_metrics(
    features: TakeFeatures, config: BenchmarkConfig
) -> dict[str, Any]:
    groups: dict[tuple[int, str, tuple[int, ...]], list[int]] = {}
    for bar in range(config.bars):
        groups.setdefault(chord_for_bar(config, bar).identity, []).append(bar)

    comparisons_out: list[dict[str, Any]] = []
    similarities: list[float] = []
    exact_copies = 0
    for bar_indices in groups.values():
        for left, right in combinations(bar_indices, 2):
            similarity = set_jaccard(
                features.bar_rhythms[left], features.bar_rhythms[right]
            )
            if similarity is None:
                continue
            exact = features.bar_rhythms[left] == features.bar_rhythms[right]
            similarities.append(similarity)
            exact_copies += int(exact)
            comparisons_out.append(
                {
                    "left_bar": left + 1,
                    "right_bar": right + 1,
                    "chord": chord_for_bar(config, left).symbol,
                    "similarity": round_float(similarity),
                    "exact_copy": exact,
                }
            )

    return {
        "comparison_count": len(similarities),
        "mean_similarity": round_float(mean(similarities)) if similarities else None,
        "exact_copy_count": exact_copies,
        "exact_copy_ratio": round_float(exact_copies / len(similarities))
        if similarities
        else None,
        "comparisons": comparisons_out,
    }


def section_skeleton_metrics(
    features: TakeFeatures, config: BenchmarkConfig
) -> dict[str, Any]:
    section_count = config.bars // SECTION_BARS
    beats_per_section = SECTION_BARS * config.beats_per_bar
    sections = [
        _skeleton_tokens(
            features.beat_skeleton,
            start_beat=index * beats_per_section,
            beat_count=beats_per_section,
        )
        for index in range(section_count)
    ]
    rows: list[dict[str, Any]] = []
    values: list[float] = []
    for left, right in combinations(range(section_count), 2):
        similarity = set_jaccard(sections[left], sections[right])
        if similarity is None:
            continue
        values.append(similarity)
        rows.append(
            {
                "left_section": left + 1,
                "right_section": right + 1,
                "similarity": round_float(similarity),
            }
        )
    return {
        "section_count": section_count,
        "comparison_count": len(values),
        "mean_similarity": round_float(mean(values)) if values else None,
        "maximum_similarity": round_float(max(values)) if values else None,
        "comparisons": rows,
    }


def form_rhythm_metrics(
    features: TakeFeatures, config: BenchmarkConfig
) -> dict[str, Any]:
    if config.bars < 16:
        return {
            "applicable": False,
            "reason": "requires_at_least_16_bars",
            "a_to_aprime": None,
            "aprime_to_b": None,
            "a_aprime_minus_aprime_b": None,
            "b_to_final": None,
        }

    starts = (0, 4, 8, config.bars - 4)
    a, aprime, b, final = [
        _section_rhythm(
            features.bar_rhythms,
            start_bar=start,
            bar_count=SECTION_BARS,
            slots_per_bar=config.slots_per_bar,
        )
        for start in starts
    ]
    a_aprime = set_jaccard(a, aprime)
    aprime_b = set_jaccard(aprime, b)
    b_final = set_jaccard(b, final)
    contrast_gap = (
        a_aprime - aprime_b
        if a_aprime is not None and aprime_b is not None
        else None
    )
    return {
        "applicable": all(
            value is not None for value in (a_aprime, aprime_b, b_final)
        ),
        "sections": {
            "A": "bars 1-4",
            "A_prime": "bars 5-8",
            "B": "bars 9-12",
            "final": f"bars {config.bars - 3}-{config.bars}",
        },
        "a_to_aprime": round_float(a_aprime),
        "aprime_to_b": round_float(aprime_b),
        "a_aprime_minus_aprime_b": round_float(contrast_gap),
        "b_to_final": round_float(b_final),
    }


def ending_metrics(
    features: TakeFeatures, config: BenchmarkConfig
) -> dict[str, Any]:
    final_bar = config.bars - 1
    half_start = config.slots_per_bar // 2
    reference_bars = [bar for bar in (3, 7, 11) if bar < final_bar]
    final_ending = frozenset(
        slot - half_start
        for slot in features.bar_rhythms[final_bar]
        if slot >= half_start
    )
    comparisons_out: list[dict[str, Any]] = []
    similarities: list[float] = []
    for bar in reference_bars:
        ending = frozenset(
            slot - half_start
            for slot in features.bar_rhythms[bar]
            if slot >= half_start
        )
        similarity = set_jaccard(ending, final_ending)
        if similarity is None:
            continue
        similarities.append(similarity)
        comparisons_out.append(
            {
                "reference_bar": bar + 1,
                "final_bar": final_bar + 1,
                "similarity": round_float(similarity),
            }
        )

    landing_bars = (*reference_bars, final_bar)
    landing_pcs: list[int] = []
    for bar in landing_bars:
        pitch_class = features.bar_landing_pitch_classes[bar]
        if pitch_class is not None:
            landing_pcs.append(pitch_class)

    final_landing_pc = features.bar_landing_pitch_classes[final_bar]
    final_chord = chord_for_bar(config, final_bar)
    safe_pitch_classes = set(final_chord.pitch_classes)
    if config.tonic_pc is not None:
        safe_pitch_classes.add(config.tonic_pc)
    final_safe = (
        final_landing_pc in safe_pitch_classes
        if final_landing_pc is not None
        else None
    )

    return {
        "reference_bars": [bar + 1 for bar in reference_bars],
        "final_bar": final_bar + 1,
        "comparison_count": len(similarities),
        "mean_half_bar_rhythm_similarity": round_float(mean(similarities))
        if similarities
        else None,
        "comparisons": comparisons_out,
        "landing_pitch_classes": [pitch_class_name(pc) for pc in landing_pcs],
        "distinct_landing_pitch_class_count": len(set(landing_pcs)),
        "final_landing_pitch_class": pitch_class_name(final_landing_pc)
        if final_landing_pc is not None
        else None,
        "final_chord": final_chord.symbol,
        "tonic": pitch_class_name(config.tonic_pc)
        if config.tonic_pc is not None
        else None,
        "safe_pitch_classes": [
            pitch_class_name(pc) for pc in sorted(safe_pitch_classes)
        ],
        "final_chord_or_tonic_safe": final_safe,
        "landing_note_basis": "lowest pitch at each target bar's final onset",
    }


def motif_metrics(features: TakeFeatures) -> dict[str, Any]:
    similarities = [
        similarity
        for left, right in combinations(features.bar_rhythms, 2)
        if (similarity := set_jaccard(left, right)) is not None
    ]
    counts: dict[frozenset[int], int] = {}
    for signature in features.bar_rhythms:
        counts[signature] = counts.get(signature, 0) + 1
    duplicate_occurrences = sum(
        count for signature, count in counts.items() if signature and count > 1
    )
    bar_count = len(features.bar_rhythms)
    return {
        "motif_unit": "one_bar_sixteenth_note_onset_signature",
        "pair_count": len(similarities),
        "mean_pairwise_bar_similarity": round_float(mean(similarities))
        if similarities
        else None,
        "duplicate_motif_occurrence_count": duplicate_occurrences,
        "duplicate_motif_ratio": round_float(duplicate_occurrences / bar_count)
        if bar_count
        else None,
        "unique_nonempty_motif_count": len(
            {signature for signature in counts if signature}
        ),
    }


def _gate(
    gate_id: str,
    value: float | int | bool | None,
    *,
    minimum: float | int | None = None,
    maximum: float | int | None = None,
    expected: bool | None = None,
    reason_if_unavailable: str = "metric_unavailable",
) -> dict[str, Any]:
    if value is None:
        return {
            "id": gate_id,
            "status": "not_applicable",
            "passed": None,
            "value": None,
            "minimum": minimum,
            "maximum": maximum,
            "expected": expected,
            "reason": reason_if_unavailable,
        }
    passed = True
    if minimum is not None:
        passed = passed and float(value) >= float(minimum)
    if maximum is not None:
        passed = passed and float(value) <= float(maximum)
    if expected is not None:
        passed = passed and bool(value) is expected
    return {
        "id": gate_id,
        "status": "pass" if passed else "fail",
        "passed": passed,
        "value": round_float(value) if not isinstance(value, bool) else value,
        "minimum": round_float(minimum),
        "maximum": round_float(maximum),
        "expected": expected,
        "reason": None,
    }


def _threshold_gate(gate_id: str, value: float | int | None) -> dict[str, Any]:
    threshold = GATE_THRESHOLDS[gate_id]
    return _gate(
        gate_id,
        value,
        minimum=threshold.get("minimum"),
        maximum=threshold.get("maximum"),
    )


def analyze_take(features: TakeFeatures, config: BenchmarkConfig) -> dict[str, Any]:
    same_harmony = same_harmony_rhythm_metrics(features, config)
    skeleton = section_skeleton_metrics(features, config)
    form = form_rhythm_metrics(features, config)
    ending = ending_metrics(features, config)
    motif = motif_metrics(features)

    gates = [
        _threshold_gate(
            "same_harmony_rhythm_mean", same_harmony["mean_similarity"]
        ),
        _threshold_gate(
            "same_harmony_exact_copy_ratio", same_harmony["exact_copy_ratio"]
        ),
        _threshold_gate("section_skeleton_mean", skeleton["mean_similarity"]),
        _threshold_gate("section_skeleton_max", skeleton["maximum_similarity"]),
        _threshold_gate("form_a_aprime", form["a_to_aprime"]),
        _threshold_gate("form_aprime_b", form["aprime_to_b"]),
        _threshold_gate(
            "form_contrast_gap", form["a_aprime_minus_aprime_b"]
        ),
        _threshold_gate("form_b_final", form["b_to_final"]),
        _threshold_gate(
            "ending_similarity_mean",
            ending["mean_half_bar_rhythm_similarity"],
        ),
        _threshold_gate(
            "ending_landing_pitch_classes",
            ending["distinct_landing_pitch_class_count"],
        ),
        _gate(
            "ending_final_chord_or_tonic_safe",
            ending["final_chord_or_tonic_safe"],
            expected=True,
        ),
        _threshold_gate(
            "motif_reuse", motif["mean_pairwise_bar_similarity"]
        ),
        _threshold_gate("duplicate_motif_ratio", motif["duplicate_motif_ratio"]),
    ]
    applicable = [gate for gate in gates if gate["passed"] is not None]
    passed = [gate for gate in applicable if gate["passed"]]
    take_passed = bool(applicable) and len(passed) == len(applicable)

    tempo_warning = None
    if features.tempo_events and all(
        abs(tempo - config.tempo) > 0.25 for tempo in features.tempo_events
    ):
        tempo_warning = (
            f"configured tempo {config.tempo:g} BPM does not match MIDI tempo "
            f"events {', '.join(f'{tempo:g}' for tempo in features.tempo_events)}"
        )

    return {
        "label": features.label,
        "path": features.path,
        "note_count": features.note_count,
        "unique_quantized_onset_count": len(features.full_rhythm),
        "tempo_events_bpm": [
            round_float(tempo, 3) for tempo in features.tempo_events
        ],
        "tempo_warning": tempo_warning,
        "performance_data_detected": {
            "pitch_bends": features.has_pitch_bends,
            "control_changes": features.has_control_changes,
            "ignored_by_composition_metrics": True,
        },
        "metrics": {
            "same_harmony_bar_rhythm": same_harmony,
            "section_chord_relative_beat_skeleton": skeleton,
            "four_bar_form_rhythm": form,
            "ending_contrast": ending,
            "motif_reuse": motif,
        },
        "gates": gates,
        "gate_summary": {
            "applicable": len(applicable),
            "passed": len(passed),
            "failed": len(applicable) - len(passed),
            "pass_ratio": round_float(len(passed) / len(applicable))
            if applicable
            else None,
            "take_passed": take_passed,
            "take_pass_definition": "all applicable individual gates pass",
        },
    }


def percentile(values: Sequence[float], percent: float) -> float | None:
    """Deterministic NumPy-compatible linear percentile without NumPy."""

    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percent / 100.0
    lower = int(math.floor(rank))
    upper = int(math.ceil(rank))
    if lower == upper:
        return ordered[lower]
    weight = rank - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def distribution_summary(values: Sequence[float]) -> dict[str, Any]:
    return {
        "count": len(values),
        "mean": round_float(mean(values)) if values else None,
        "minimum": round_float(min(values)) if values else None,
        "p10": round_float(percentile(values, 10)),
        "p50": round_float(percentile(values, 50)),
        "p90": round_float(percentile(values, 90)),
        "maximum": round_float(max(values)) if values else None,
    }


def coefficient_of_variation(values: Sequence[int | float]) -> float | None:
    if not values:
        return None
    average = mean(values)
    if average <= 0:
        return None
    return pstdev(values) / average


def cross_seed_metrics(features: Sequence[TakeFeatures]) -> dict[str, Any]:
    rhythm_rows: list[dict[str, Any]] = []
    rhythm_values: list[float] = []
    skeleton_rows: list[dict[str, Any]] = []
    skeleton_values: list[float] = []
    for left, right in combinations(features, 2):
        rhythm = set_jaccard(left.full_rhythm, right.full_rhythm)
        if rhythm is not None:
            rhythm_values.append(rhythm)
            rhythm_rows.append(
                {
                    "left": left.label,
                    "right": right.label,
                    "similarity": round_float(rhythm),
                }
            )
        left_skeleton = _skeleton_tokens(left.beat_skeleton)
        right_skeleton = _skeleton_tokens(right.beat_skeleton)
        skeleton = set_jaccard(left_skeleton, right_skeleton)
        if skeleton is not None:
            skeleton_values.append(skeleton)
            skeleton_rows.append(
                {
                    "left": left.label,
                    "right": right.label,
                    "similarity": round_float(skeleton),
                }
            )

    note_counts = [feature.note_count for feature in features]
    return {
        "full_rhythm_similarity": {
            "distribution": distribution_summary(rhythm_values),
            "pairs": rhythm_rows,
        },
        "chord_relative_beat_skeleton_similarity": {
            "distribution": distribution_summary(skeleton_values),
            "pairs": skeleton_rows,
        },
        "note_counts": note_counts,
        "note_count_mean": round_float(mean(note_counts))
        if note_counts
        else None,
        "note_count_cv": round_float(coefficient_of_variation(note_counts)),
    }


def batch_assessment(
    features: Sequence[TakeFeatures],
    take_reports: Sequence[dict[str, Any]],
    cross_seed: dict[str, Any],
) -> dict[str, Any]:
    enough_takes = len(features) >= MINIMUM_BATCH_TAKES
    take_pass_count = sum(
        bool(report["gate_summary"]["take_passed"]) for report in take_reports
    )
    take_pass_ratio = take_pass_count / len(take_reports) if take_reports else 0.0

    rhythm_distribution = cross_seed["full_rhythm_similarity"]["distribution"]
    skeleton_distribution = cross_seed[
        "chord_relative_beat_skeleton_similarity"
    ]["distribution"]
    unavailable_reason = f"requires_at_least_{MINIMUM_BATCH_TAKES}_takes"

    def batch_metric(gate_id: str, value: float | int | None) -> dict[str, Any]:
        if not enough_takes:
            threshold = GATE_THRESHOLDS[gate_id]
            return _gate(
                gate_id,
                None,
                minimum=threshold.get("minimum"),
                maximum=threshold.get("maximum"),
                reason_if_unavailable=unavailable_reason,
            )
        return _threshold_gate(gate_id, value)

    gates = [
        _gate(
            "minimum_take_count",
            len(features),
            minimum=MINIMUM_BATCH_TAKES,
        ),
        _gate(
            "individual_take_pass_ratio",
            take_pass_ratio,
            minimum=MINIMUM_INDIVIDUAL_PASS_RATIO,
        ),
        batch_metric("cross_seed_rhythm_p50", rhythm_distribution["p50"]),
        batch_metric("cross_seed_rhythm_p90", rhythm_distribution["p90"]),
        batch_metric("cross_seed_skeleton_p50", skeleton_distribution["p50"]),
        batch_metric("cross_seed_skeleton_p90", skeleton_distribution["p90"]),
        batch_metric("note_count_cv", cross_seed["note_count_cv"]),
    ]
    ready = all(gate["passed"] is True for gate in gates)
    return {
        "ready": ready,
        "verdict": "READY" if ready else "NOT_READY",
        "take_count": len(features),
        "minimum_take_count": MINIMUM_BATCH_TAKES,
        "individual_take_pass_count": take_pass_count,
        "individual_take_pass_ratio": round_float(take_pass_ratio),
        "minimum_individual_take_pass_ratio": MINIMUM_INDIVIDUAL_PASS_RATIO,
        "readiness_definition": (
            "at least 8 takes, at least 75% of takes pass every applicable "
            "individual gate, and every applicable cross-seed gate passes"
        ),
        "gates": gates,
    }


def analyze_features(
    features: Sequence[TakeFeatures], config: BenchmarkConfig
) -> dict[str, Any]:
    take_reports = [analyze_take(feature, config) for feature in features]
    cross_seed = cross_seed_metrics(features)
    batch = batch_assessment(features, take_reports, cross_seed)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "tempo_bpm": config.tempo,
            "bars": config.bars,
            "beats_per_bar": config.beats_per_bar,
            "subdivisions_per_beat": config.subdivisions_per_beat,
            "chords": [chord.symbol for chord in config.chords],
            "progression_cycles": True,
            "tonic": pitch_class_name(config.tonic_pc)
            if config.tonic_pc is not None
            else None,
            "section_bars": SECTION_BARS,
            "minimum_batch_takes": MINIMUM_BATCH_TAKES,
        },
        "scope": {
            "included": (
                "quantized note-on rhythm and chord-relative first-note-per-beat "
                "pitch skeleton"
            ),
            "excluded": (
                "velocity, duration, microtiming, articulation, pitch bend, "
                "controller data, and audio quality"
            ),
        },
        "gate_thresholds": GATE_THRESHOLDS,
        "takes": take_reports,
        "cross_seed": cross_seed,
        "batch_assessment": batch,
    }


def run_analysis(
    midi_paths: Sequence[Path], config: BenchmarkConfig
) -> dict[str, Any]:
    return analyze_features(
        [load_midi_take(path, config) for path in midi_paths],
        config,
    )


def _gate_target(gate: dict[str, Any]) -> str:
    if gate.get("expected") is not None:
        return str(gate["expected"])
    minimum = gate.get("minimum")
    maximum = gate.get("maximum")
    if minimum is not None and maximum is not None:
        return f"{minimum}–{maximum}"
    if minimum is not None:
        return f"≥ {minimum}"
    if maximum is not None:
        return f"≤ {maximum}"
    return "n/a"


def render_markdown(report: dict[str, Any]) -> str:
    config = report["configuration"]
    batch = report["batch_assessment"]
    lines = [
        "# Session Player phrase-development benchmark",
        "",
        f"**Verdict: {batch['verdict']}**",
        "",
        (
            f"{batch['individual_take_pass_count']}/{batch['take_count']} takes "
            f"pass every applicable individual gate "
            f"({batch['individual_take_pass_ratio']:.1%}); "
            f"batch requirement is at least "
            f"{batch['minimum_individual_take_pass_ratio']:.0%} across at least "
            f"{batch['minimum_take_count']} takes."
        ),
        "",
        "## Configuration",
        "",
        f"- Tempo: {config['tempo_bpm']} BPM",
        f"- Form: {config['bars']} bars, {config['beats_per_bar']}/4",
        f"- Chords: {' | '.join(config['chords'])} (cycled)",
        f"- Tonic safety allowance: {config['tonic'] or 'not supplied'}",
        f"- Takes: {batch['take_count']}",
        "",
        "## Take summary",
        "",
        "| Take | Notes | Onsets | Gates | Result |",
        "|---|---:|---:|---:|---|",
    ]
    for take in report["takes"]:
        summary = take["gate_summary"]
        lines.append(
            f"| {take['label']} | {take['note_count']} "
            f"| {take['unique_quantized_onset_count']} "
            f"| {summary['passed']}/{summary['applicable']} "
            f"| {'PASS' if summary['take_passed'] else 'FAIL'} |"
        )

    lines.extend(
        [
            "",
            "## Individual gate results",
            "",
            "| Take | Gate | Value | Target | Status |",
            "|---|---|---:|---:|---|",
        ]
    )
    for take in report["takes"]:
        for gate in take["gates"]:
            value = "n/a" if gate["value"] is None else str(gate["value"])
            lines.append(
                f"| {take['label']} | `{gate['id']}` | {value} "
                f"| {_gate_target(gate)} | {gate['status'].upper()} |"
            )

    lines.extend(
        [
            "",
            "## Cross-seed distributions",
            "",
            "| Measure | P50 | P90 | Mean | Min | Max |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for label, key in (
        ("Full onset rhythm", "full_rhythm_similarity"),
        (
            "Chord-relative first-note-per-beat skeleton",
            "chord_relative_beat_skeleton_similarity",
        ),
    ):
        distribution = report["cross_seed"][key]["distribution"]
        lines.append(
            f"| {label} | {distribution['p50']} | {distribution['p90']} "
            f"| {distribution['mean']} | {distribution['minimum']} "
            f"| {distribution['maximum']} |"
        )
    lines.append(
        f"| Note-count CV | {report['cross_seed']['note_count_cv']} "
        "| n/a | n/a | n/a | n/a |"
    )

    lines.extend(
        [
            "",
            "### Batch gates",
            "",
            "| Gate | Value | Target | Status |",
            "|---|---:|---:|---|",
        ]
    )
    for gate in batch["gates"]:
        value = "n/a" if gate["value"] is None else str(gate["value"])
        lines.append(
            f"| `{gate['id']}` | {value} | {_gate_target(gate)} "
            f"| {gate['status'].upper()} |"
        )

    lines.extend(
        [
            "",
            "## Metric definitions",
            "",
            "- Rhythm similarity is binary Jaccard similarity on a sixteenth-note onset grid. Timing detail and repeated pitches at one onset do not change it.",
            "- Same-harmony rhythm compares every pair of bars carrying the same parsed chord. Exact-copy ratio is exact onset-row matches divided by valid same-harmony pairs.",
            "- The beat skeleton retains only the first note in each beat and expresses its pitch class relative to that bar's chord root. Section similarity is token Jaccard over position + relative pitch class.",
            "- Four-bar form compares A (bars 1–4), A′ (5–8), B (9–12), and the final four bars. The contrast gap is A/A′ minus A′/B.",
            "- Ending contrast compares the final half-bar rhythm with bars 4, 8, and 12. Landing pitch class is the lowest pitch at each target bar's final onset.",
            "- Motif reuse is mean pairwise one-bar rhythm similarity. Duplicate-motif ratio is the share of bars belonging to a repeated exact onset signature.",
            "- Cross-seed metrics compare complete-take rhythm and beat skeletons. Note-count CV is population standard deviation divided by mean.",
            "",
            "## Interpretation limits",
            "",
            "- These gates reject obvious sameness and randomness; they do not prove a phrase sounds musical.",
            "- Supply clean composition MIDI. Performance MIDI is accepted, but bend/controller data is detected and ignored while microtimed starts are quantized.",
            "- First-note-per-beat reduction intentionally misses inner-beat melodic detail. That detail belongs in a later benchmark once macro development is credible.",
            "- The final safety gate accepts final-chord tones plus an optional supplied tonic. It is a cadence guard, not a full harmonic-analysis claim.",
            "",
        ]
    )
    return "\n".join(lines)


def _existing_file(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file does not exist: {path}")
    return path


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Measure 16-bar written phrase development across repeated clean "
            "Session Player MIDI takes."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--midi",
        action="append",
        required=True,
        type=_existing_file,
        help="Clean bass MIDI take; repeat for multiple seeds.",
    )
    parser.add_argument("--tempo", required=True, type=float)
    parser.add_argument(
        "--chords",
        required=True,
        help="One chord per bar separated by |, comma, or spaces; cycles as needed.",
    )
    parser.add_argument("--bars", required=True, type=int)
    parser.add_argument(
        "--tonic",
        help="Optional tonic pitch class accepted by the final landing gate, e.g. D.",
    )
    parser.add_argument("--beats-per-bar", type=int, default=4)
    parser.add_argument("--subdivisions-per-beat", type=int, default=4)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("research/benchmark_results"),
    )
    parser.add_argument(
        "--output-stem",
        help="Stable report stem; defaults to a UTC timestamped name.",
    )
    parser.add_argument(
        "--fail-on-not-ready",
        action="store_true",
        help="Exit 1 when the batch readiness verdict is NOT_READY.",
    )
    return parser


def _validated_config(args: argparse.Namespace) -> BenchmarkConfig:
    if not math.isfinite(args.tempo) or args.tempo <= 0:
        raise ValueError("tempo must be finite and positive")
    if args.bars <= 0:
        raise ValueError("bars must be positive")
    if args.beats_per_bar <= 0:
        raise ValueError("beats-per-bar must be positive")
    if args.subdivisions_per_beat <= 0:
        raise ValueError("subdivisions-per-beat must be positive")
    return BenchmarkConfig(
        tempo=float(args.tempo),
        chords=parse_chord_progression(args.chords),
        bars=int(args.bars),
        tonic_pc=parse_note_name(args.tonic) if args.tonic else None,
        beats_per_bar=int(args.beats_per_bar),
        subdivisions_per_beat=int(args.subdivisions_per_beat),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)
    try:
        config = _validated_config(args)
        report = run_analysis(args.midi, config)
    except (ValueError, OSError, EOFError) as exc:
        parser.error(str(exc))

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = args.output_stem or f"phrase_development_{stamp}"
    json_path = output_dir / f"{stem}.json"
    markdown_path = output_dir / f"{stem}.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"JSON: {json_path.resolve()}")
    print(f"Markdown: {markdown_path.resolve()}")
    print(f"Verdict: {report['batch_assessment']['verdict']}")
    if args.fail_on_not_ready and not report["batch_assessment"]["ready"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
