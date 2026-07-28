"""Render a deterministic MIDI audition of every shared Fusion covenant.

This is an isolated research utility.  It exercises the production
``FusionGrooveContract`` through the existing drum, Phrase-v2 bass, and chord
generators, but it does not create or mutate a Session Player session.

Run from the repository root:

    backend/.venv/bin/python research/render_fusion_contract_demo.py

Or choose an explicit destination:

    backend/.venv/bin/python research/render_fusion_contract_demo.py \
        --output-dir /tmp/fusion-contract-audition \
        --bars 8 --tempo 116

The destination receives one folder per covenant with individual MIDI stems,
a performance-bass combined MIDI, and the exact symbolic contract.  A final
``all_families`` folder places the four readings end-to-end for quick A/B/C/D
auditioning.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import random
import sys
from typing import Any, Sequence

import pretty_midi


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.bass_performance_controls import expressive_fusion_controls
from app.services.bass_performance_render import render_performance_bass_midi
from app.services.fusion_contract import (
    FusionGrooveContract,
    build_fusion_contract,
    covenant_ids,
)
from app.services.generator import generate_bass, generate_chords, generate_drums
from app.services.midi_export import merge_lane_midis


DEFAULT_OUTPUT_DIR = Path("/tmp/session-player-fusion-contract-demo")
DEFAULT_TEMPO = 116
DEFAULT_BARS = 8
DEFAULT_BASE_SEED = 7300
DEFAULT_GAP_BARS = 1
KEY = "D"
SCALE = "natural_minor"
CHORD_PROGRESSION = ("Dm7", "Gm7", "Bbmaj7", "A7")
MANIFEST_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class DemoConfig:
    output_dir: Path = DEFAULT_OUTPUT_DIR
    tempo: int = DEFAULT_TEMPO
    bars: int = DEFAULT_BARS
    base_seed: int = DEFAULT_BASE_SEED
    gap_bars: int = DEFAULT_GAP_BARS

    def __post_init__(self) -> None:
        if not 40 <= int(self.tempo) <= 240:
            raise ValueError("tempo must be between 40 and 240 BPM")
        if not 8 <= int(self.bars) <= 16:
            raise ValueError("bars must be between 8 and 16")
        if int(self.base_seed) < 0:
            raise ValueError("base_seed must be non-negative")
        if not 0 <= int(self.gap_bars) <= 4:
            raise ValueError("gap_bars must be between 0 and 4")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _midi_summary(data: bytes) -> dict[str, Any]:
    midi = pretty_midi.PrettyMIDI(io.BytesIO(data))
    notes = [
        note
        for instrument in midi.instruments
        for note in instrument.notes
    ]
    return {
        "instrument_count": len(midi.instruments),
        "note_count": len(notes),
        "duration_seconds": round(
            max((float(note.end) for note in notes), default=0.0),
            6,
        ),
        "sha256": _sha256(data),
    }


def _write_midi(path: Path, data: bytes) -> dict[str, Any]:
    path.write_bytes(data)
    summary = _midi_summary(data)
    summary["file"] = str(path)
    return summary


def _bass_program(clean_midi: bytes) -> int:
    midi = pretty_midi.PrettyMIDI(io.BytesIO(clean_midi))
    if not midi.instruments:
        return 33
    return int(midi.instruments[0].program)


def _performance_bass(
    clean_midi: bytes,
    performance_notes: tuple[Any, ...],
    *,
    tempo: int,
    controls: dict[str, float],
) -> bytes:
    return render_performance_bass_midi(
        performance_notes,
        tempo=tempo,
        program=_bass_program(clean_midi),
        expression_amount=0.72,
        articulation_focus="natural",
        timing_humanize=controls["timing_humanize"],
        velocity_humanize=controls["velocity_humanize"],
        instrument_family="finger_bass",
    )


def _concatenate_segments(
    segments: Sequence[bytes],
    *,
    tempo: int,
    bars_per_segment: int,
    gap_bars: int,
    instrument_name: str,
) -> bytes:
    """Place one-instrument MIDI segments end-to-end on a single lane."""

    parsed = [pretty_midi.PrettyMIDI(io.BytesIO(data)) for data in segments]
    resolution = max(
        (int(midi.resolution) for midi in parsed),
        default=220,
    )
    result = pretty_midi.PrettyMIDI(
        initial_tempo=float(tempo),
        resolution=resolution,
    )
    first_instrument = next(
        (
            instrument
            for midi in parsed
            for instrument in midi.instruments
        ),
        None,
    )
    output = pretty_midi.Instrument(
        program=int(first_instrument.program) if first_instrument else 0,
        is_drum=bool(first_instrument.is_drum) if first_instrument else False,
        name=instrument_name,
    )
    result.instruments.append(output)

    seconds_per_bar = 4.0 * 60.0 / float(tempo)
    segment_stride = (int(bars_per_segment) + int(gap_bars)) * seconds_per_bar
    for segment_index, midi in enumerate(parsed):
        offset = float(segment_index) * segment_stride
        for instrument in midi.instruments:
            for note in instrument.notes:
                output.notes.append(
                    pretty_midi.Note(
                        velocity=int(note.velocity),
                        pitch=int(note.pitch),
                        start=float(note.start) + offset,
                        end=float(note.end) + offset,
                    )
                )
            for bend in instrument.pitch_bends:
                output.pitch_bends.append(
                    pretty_midi.PitchBend(
                        pitch=int(bend.pitch),
                        time=float(bend.time) + offset,
                    )
                )
            for event in instrument.control_changes:
                output.control_changes.append(
                    pretty_midi.ControlChange(
                        number=int(event.number),
                        value=int(event.value),
                        time=float(event.time) + offset,
                    )
                )

    output.notes.sort(
        key=lambda note: (
            float(note.start),
            int(note.pitch),
            float(note.end),
            int(note.velocity),
        )
    )
    output.pitch_bends.sort(
        key=lambda bend: (float(bend.time), int(bend.pitch))
    )
    output.control_changes.sort(
        key=lambda event: (
            float(event.time),
            int(event.number),
            int(event.value),
        )
    )
    buffer = io.BytesIO()
    result.write(buffer)
    return buffer.getvalue()


def _family_seed(base_seed: int, family_index: int) -> int:
    return int(base_seed) + int(family_index) * 97


def _render_family(
    *,
    config: DemoConfig,
    family_id: str,
    family_index: int,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    family_seed = _family_seed(config.base_seed, family_index)
    contract = build_fusion_contract(
        seed=family_seed,
        bar_count=config.bars,
        covenant_id=family_id,
    )
    performance_controls = expressive_fusion_controls()

    # The contract path itself is deterministic.  Seeding each wrapper call as
    # well guards this research render against unrelated future generators
    # consuming module-global randomness before entering their contract branch.
    random.seed(family_seed ^ 0xD12A)
    drum_midi, drum_preview = generate_drums(
        tempo=config.tempo,
        bar_count=config.bars,
        drum_style="latin",
        drum_kit="percussion",
        fusion_contract=contract,
    )
    random.seed(family_seed ^ 0xBA55)
    bass_result = generate_bass(
        tempo=config.tempo,
        bar_count=config.bars,
        key=KEY,
        scale=SCALE,
        bass_style="fusion",
        bass_instrument="finger_bass",
        bass_engine="phrase_v2",
        chord_progression=list(CHORD_PROGRESSION),
        seed=family_seed,
        return_performance_notes=True,
        density_bias=0.0,
        expression_amount=0.72,
        bass_articulation_focus="natural",
        ghost_amount=performance_controls["ghost"],
        mute_amount=performance_controls["mute"],
        slide_amount=performance_controls["slide"],
        legato_amount=performance_controls["legato"],
        fusion_contract=contract,
    )
    clean_bass_midi, bass_preview, performance_notes = bass_result
    bass_midi = _performance_bass(
        clean_bass_midi,
        performance_notes,
        tempo=config.tempo,
        controls=performance_controls,
    )
    random.seed(family_seed ^ 0x4B45)
    keys_midi, keys_preview = generate_chords(
        tempo=config.tempo,
        bar_count=config.bars,
        key=KEY,
        scale=SCALE,
        chord_style="warm_broken",
        chord_instrument="rhodes",
        chord_progression=list(CHORD_PROGRESSION),
        fusion_contract=contract,
    )
    combined_midi = merge_lane_midis(
        tempo=config.tempo,
        lanes={
            "drums": drum_midi,
            "bass": bass_midi,
            "rhodes": keys_midi,
        },
    )

    family_dir = config.output_dir / f"{family_index + 1:02d}_{family_id}"
    family_dir.mkdir(parents=True, exist_ok=True)
    contract_path = family_dir / "contract.json"
    contract_path.write_text(
        json.dumps(contract.to_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    files = {
        "drums": _write_midi(family_dir / "drums.mid", drum_midi),
        "bass": _write_midi(family_dir / "bass.mid", bass_midi),
        "bass_clean": _write_midi(
            family_dir / "bass_clean.mid",
            clean_bass_midi,
        ),
        "rhodes": _write_midi(family_dir / "rhodes.mid", keys_midi),
        "combined": _write_midi(
            family_dir / "combined.mid",
            combined_midi,
        ),
    }
    files["contract"] = {
        "file": str(contract_path),
        "sha256": _sha256(contract_path.read_bytes()),
    }
    summary = {
        "family_index": family_index + 1,
        "covenant_id": family_id,
        "contract_id": contract.contract_id,
        "seed": family_seed,
        "bass_performance_intent": {
            "articulations": dict(
                sorted(
                    Counter(
                        str(note.articulation)
                        for note in performance_notes
                    ).items()
                )
            ),
            "semantic_roles": dict(
                sorted(
                    Counter(
                        str(note.role)
                        for note in performance_notes
                    ).items()
                )
            ),
        },
        "phrase_roles": [
            contract.bar(bar).phrase_role
            for bar in range(config.bars)
        ],
        "files": files,
        "previews": {
            "drums": drum_preview,
            "bass": bass_preview,
            "rhodes": keys_preview,
        },
    }
    lanes = {
        "drums": drum_midi,
        "bass": bass_midi,
        "rhodes": keys_midi,
    }
    return summary, lanes


def _write_readme(config: DemoConfig, manifest: dict[str, Any]) -> Path:
    family_lines = []
    for family in manifest["families"]:
        family_lines.append(
            f"{family['family_index']}. `{family['covenant_id']}` "
            f"(seed {family['seed']})"
        )
    gap_note = (
        f"The continuous audition leaves {config.gap_bars} silent bar(s) "
        "between families."
        if config.gap_bars
        else "The continuous audition changes family with no silent gap."
    )
    text = f"""# Session Player Fusion Contract audition

This render exercises the real shared Fusion contract through the existing
drum, Phrase-v2 bass, chord, performance-MIDI, and lane-merge paths.

- Tempo: {config.tempo} BPM
- Key/scale: {KEY} {SCALE}
- Harmony: {' | '.join(CHORD_PROGRESSION)}
- Bars per family: {config.bars}
- Combined MIDI uses the performance bass stem.
- `{config.output_dir / 'all_families' / 'combined.mid'}` is the quickest A/B/C/D audition.
- {gap_note}

Families:

{chr(10).join(family_lines)}

For Logic, import a `combined.mid`, assign a drum kit to **Drums**, a fingered
bass to **Bass (Performance)**, and a Rhodes/electric piano to **Chords**.
The files contain MIDI only; no third-party audio or copied phrase is bundled.
"""
    path = config.output_dir / "README.md"
    path.write_text(text, encoding="utf-8")
    return path


def render_demo(config: DemoConfig) -> dict[str, Any]:
    """Render every covenant and return the JSON-serializable manifest."""

    config.output_dir.mkdir(parents=True, exist_ok=True)
    family_summaries: list[dict[str, Any]] = []
    lane_segments: dict[str, list[bytes]] = {
        "drums": [],
        "bass": [],
        "rhodes": [],
    }
    for family_index, family_id in enumerate(covenant_ids()):
        family_summary, family_lanes = _render_family(
            config=config,
            family_id=family_id,
            family_index=family_index,
        )
        family_summaries.append(family_summary)
        for lane_name, data in family_lanes.items():
            lane_segments[lane_name].append(data)

    continuous = {
        lane_name: _concatenate_segments(
            segments,
            tempo=config.tempo,
            bars_per_segment=config.bars,
            gap_bars=config.gap_bars,
            instrument_name={
                "drums": "Fusion Contract Drums",
                "bass": "Fusion Contract Bass (Performance)",
                "rhodes": "Fusion Contract Rhodes",
            }[lane_name],
        )
        for lane_name, segments in lane_segments.items()
    }
    continuous["combined"] = merge_lane_midis(
        tempo=config.tempo,
        lanes={
            "drums": continuous["drums"],
            "bass": continuous["bass"],
            "rhodes": continuous["rhodes"],
        },
    )
    all_dir = config.output_dir / "all_families"
    all_dir.mkdir(parents=True, exist_ok=True)
    all_files = {
        lane_name: _write_midi(all_dir / f"{lane_name}.mid", data)
        for lane_name, data in continuous.items()
    }

    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "title": "Session Player Fusion Contract audition",
        "configuration": {
            "tempo": config.tempo,
            "bars_per_family": config.bars,
            "gap_bars": config.gap_bars,
            "base_seed": config.base_seed,
            "key": KEY,
            "scale": SCALE,
            "chord_progression": list(CHORD_PROGRESSION),
            "combined_bass_mode": "performance",
            "bass_performance_controls": expressive_fusion_controls(),
        },
        "families": family_summaries,
        "all_families": {
            "order": list(covenant_ids()),
            "files": all_files,
        },
    }
    readme_path = _write_readme(config, manifest)
    manifest["readme"] = str(readme_path)
    manifest_path = config.output_dir / "manifest.json"
    manifest["manifest"] = str(manifest_path)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render all four Session Player Fusion groove covenants as "
            "drums, bass, Rhodes, and combined MIDI."
        )
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Destination directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--tempo",
        type=int,
        default=DEFAULT_TEMPO,
        help=f"Constant audition tempo (default: {DEFAULT_TEMPO})",
    )
    parser.add_argument(
        "--bars",
        type=int,
        choices=range(8, 17),
        default=DEFAULT_BARS,
        metavar="8..16",
        help=f"Bars rendered per family (default: {DEFAULT_BARS})",
    )
    parser.add_argument(
        "--base-seed",
        type=int,
        default=DEFAULT_BASE_SEED,
        help=f"Deterministic base seed (default: {DEFAULT_BASE_SEED})",
    )
    parser.add_argument(
        "--gap-bars",
        type=int,
        choices=range(0, 5),
        default=DEFAULT_GAP_BARS,
        metavar="0..4",
        help=(
            "Silent bars between families in the continuous audition "
            f"(default: {DEFAULT_GAP_BARS})"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = DemoConfig(
            output_dir=args.output_dir.expanduser().resolve(),
            tempo=args.tempo,
            bars=args.bars,
            base_seed=args.base_seed,
            gap_bars=args.gap_bars,
        )
        manifest = render_demo(config)
    except (OSError, TypeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(f"Rendered {len(manifest['families'])} Fusion covenant families.")
    print(f"Output: {config.output_dir}")
    print(
        "Continuous audition: "
        + manifest["all_families"]["files"]["combined"]["file"]
    )
    print(f"Manifest: {manifest['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
