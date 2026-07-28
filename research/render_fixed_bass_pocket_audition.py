"""Render a BEFORE / POCKET PASS audition around two approved Fusion bass takes.

This research utility deliberately freezes the two performance-bass MIDI files
that passed the listening gate.  It never imports or calls a bass generator.
The BEFORE sections reuse the original drum and Rhodes stems byte-for-byte.
The POCKET PASS sections are the production drum and keys renderers reading
each original serialized Fusion contract.  There is no research-only musical
post-processing, so the audition is an honest listen to the code that ships.

The continuous audition is intentionally easy to judge in one play-through:

* bars 1-8: anticipated_tumbao, BEFORE accompaniment
* bar 9: silence
* bars 10-17: anticipated_tumbao, POCKET PASS accompaniment
* bar 18: silence
* bars 19-26: open_funk_reply, BEFORE accompaniment
* bar 27: silence
* bars 28-35: open_funk_reply, POCKET PASS accompaniment

Every expressive bass event is copied into both readings.  The renderer fails
unless the raw approved bass files match their pinned SHA-256 digests, and it
normalizes the repeated sections back to local MIDI ticks to prove that every
note, pitch bend, and control change is identical.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import io
import json
from pathlib import Path
import random
import shutil
import sys
from typing import Any, Mapping, Sequence

import pretty_midi


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.fusion_contract import FusionGrooveContract
from app.services.generator import generate_chords, generate_drums
from app.services.midi_export import merge_lane_midis


DEFAULT_SOURCE_DIR = Path(
    "/Users/sub/Music/Session Player Benchmarks/"
    "Fusion Contract 2026-07-28"
)
DEFAULT_OUTPUT_DIR = Path("/tmp/session-player-fixed-bass-pocket-audition")

SCHEMA_VERSION = 1
BARS_PER_READING = 8
GAP_BARS = 1
BEATS_PER_BAR = 4


@dataclass(frozen=True, slots=True)
class FamilySpec:
    covenant_id: str
    source_index: int
    approved_bass_sha256: str
    before_start_bar: int
    pocket_start_bar: int

    @property
    def source_directory_name(self) -> str:
        return f"{self.source_index:02d}_{self.covenant_id}"


FAMILY_SPECS: tuple[FamilySpec, ...] = (
    FamilySpec(
        covenant_id="anticipated_tumbao",
        source_index=1,
        approved_bass_sha256=(
            "5c72fb71051f45e56a60f8660bf95090"
            "f1be481ec28e98cbc27e38e08515506c"
        ),
        before_start_bar=0,
        pocket_start_bar=9,
    ),
    FamilySpec(
        covenant_id="open_funk_reply",
        source_index=4,
        approved_bass_sha256=(
            "c273011cc517015e64dc49bc481e5ff3"
            "759de55f3db919c33cb8c91dcb3e135d"
        ),
        before_start_bar=18,
        pocket_start_bar=27,
    ),
)


@dataclass(frozen=True, slots=True)
class AuditionConfig:
    source_dir: Path = DEFAULT_SOURCE_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR

    def __post_init__(self) -> None:
        source = self.source_dir.expanduser().resolve()
        output = self.output_dir.expanduser().resolve()
        if source == output:
            raise ValueError("source_dir and output_dir must be different")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_sha256(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return _sha256(encoded)


def _single_instrument(data: bytes, *, label: str) -> tuple[
    pretty_midi.PrettyMIDI,
    pretty_midi.Instrument,
]:
    midi = pretty_midi.PrettyMIDI(io.BytesIO(data))
    if len(midi.instruments) != 1:
        raise ValueError(f"{label} must contain exactly one MIDI instrument")
    return midi, midi.instruments[0]


def _concatenate_at_bars(
    segments: Sequence[tuple[bytes, int]],
    *,
    tempo: int,
    instrument_name: str,
) -> bytes:
    parsed = [
        (_single_instrument(data, label=instrument_name), int(start_bar))
        for data, start_bar in segments
    ]
    resolution = max(
        (int(midi.resolution) for (midi, _instrument), _bar in parsed),
        default=220,
    )
    result = pretty_midi.PrettyMIDI(
        initial_tempo=float(tempo),
        resolution=resolution,
    )
    first_instrument = parsed[0][0][1] if parsed else None
    output = pretty_midi.Instrument(
        program=int(first_instrument.program) if first_instrument else 0,
        is_drum=bool(first_instrument.is_drum) if first_instrument else False,
        name=instrument_name,
    )
    result.instruments.append(output)
    seconds_per_bar = BEATS_PER_BAR * 60.0 / float(tempo)

    for (midi, _source_instrument), start_bar in parsed:
        offset = int(start_bar) * seconds_per_bar
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


def _event_document(
    midi: pretty_midi.PrettyMIDI,
    instrument: pretty_midi.Instrument,
    *,
    start_tick: int = 0,
    end_tick: int | None = None,
) -> dict[str, Any]:
    def included(tick: int) -> bool:
        return tick >= start_tick and (end_tick is None or tick < end_tick)

    notes = []
    for note in instrument.notes:
        note_start = int(midi.time_to_tick(float(note.start)))
        if not included(note_start):
            continue
        notes.append(
            (
                int(note.pitch),
                int(note.velocity),
                note_start - start_tick,
                int(midi.time_to_tick(float(note.end))) - start_tick,
            )
        )
    bends = []
    for bend in instrument.pitch_bends:
        tick = int(midi.time_to_tick(float(bend.time)))
        if included(tick):
            bends.append((int(bend.pitch), tick - start_tick))
    controls = []
    for event in instrument.control_changes:
        tick = int(midi.time_to_tick(float(event.time)))
        if included(tick):
            controls.append(
                (
                    int(event.number),
                    int(event.value),
                    tick - start_tick,
                )
            )
    return {
        "resolution": int(midi.resolution),
        "program": int(instrument.program),
        "is_drum": bool(instrument.is_drum),
        "notes": sorted(notes),
        "pitch_bends": sorted(bends),
        "control_changes": sorted(controls),
    }


def _source_event_document(data: bytes) -> dict[str, Any]:
    midi, instrument = _single_instrument(data, label="locked bass")
    return _event_document(midi, instrument)


def _bass_identity_proof(
    combined: bytes,
    *,
    locked_bass: Mapping[str, bytes],
) -> dict[str, Any]:
    midi = pretty_midi.PrettyMIDI(io.BytesIO(combined))
    bass_tracks = [
        instrument
        for instrument in midi.instruments
        if instrument.name == "Bass - LOCKED performance"
    ]
    if len(bass_tracks) != 1:
        raise ValueError("combined audition must contain one locked bass track")
    bass_track = bass_tracks[0]
    ticks_per_bar = int(midi.resolution) * BEATS_PER_BAR
    result: dict[str, Any] = {
        "verified": True,
        "normalization": (
            "MIDI ticks relative to each 8-bar section; includes program, "
            "notes, pitch bends, and control changes"
        ),
        "families": {},
    }

    for spec in FAMILY_SPECS:
        source_document = _source_event_document(locked_bass[spec.covenant_id])
        source_fingerprint = _json_sha256(source_document)
        section_documents: dict[str, dict[str, Any]] = {}
        for reading, start_bar in (
            ("before", spec.before_start_bar),
            ("pocket_pass", spec.pocket_start_bar),
        ):
            start_tick = start_bar * ticks_per_bar
            document = _event_document(
                midi,
                bass_track,
                start_tick=start_tick,
                end_tick=start_tick + BARS_PER_READING * ticks_per_bar,
            )
            if document != source_document:
                raise ValueError(
                    f"{spec.covenant_id} {reading} bass differs from locked source"
                )
            section_documents[reading] = {
                "start_bar": start_bar + 1,
                "end_bar": start_bar + BARS_PER_READING,
                "event_sha256": _json_sha256(document),
                "matches_source": True,
            }
        if (
            section_documents["before"]["event_sha256"]
            != section_documents["pocket_pass"]["event_sha256"]
        ):
            raise ValueError(
                f"{spec.covenant_id} BEFORE and POCKET PASS bass differ"
            )
        result["families"][spec.covenant_id] = {
            "approved_raw_sha256": spec.approved_bass_sha256,
            "source_event_sha256": source_fingerprint,
            "event_counts": {
                "notes": len(source_document["notes"]),
                "pitch_bends": len(source_document["pitch_bends"]),
                "control_changes": len(source_document["control_changes"]),
            },
            "sections": section_documents,
            "pair_identical": True,
        }
    return result


def _midi_summary(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    midi = pretty_midi.PrettyMIDI(io.BytesIO(data))
    return {
        "file": str(path),
        "sha256": _sha256(data),
        "instrument_count": len(midi.instruments),
        "note_count": sum(
            len(instrument.notes) for instrument in midi.instruments
        ),
        "pitch_bend_count": sum(
            len(instrument.pitch_bends) for instrument in midi.instruments
        ),
        "control_change_count": sum(
            len(instrument.control_changes)
            for instrument in midi.instruments
        ),
    }


def _load_source_configuration(source_dir: Path) -> dict[str, Any]:
    manifest_path = source_dir / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    configuration = payload.get("configuration")
    if not isinstance(configuration, dict):
        raise ValueError("source manifest has no configuration object")
    if int(configuration.get("bars_per_family", 0)) != BARS_PER_READING:
        raise ValueError("source audition must contain eight bars per family")
    return configuration


def _render_revised_accompaniment(
    *,
    contract: FusionGrooveContract,
    tempo: int,
    key: str,
    scale: str,
    chord_progression: Sequence[str],
) -> tuple[bytes, bytes, dict[str, str]]:
    random.seed(int(contract.seed) ^ 0xD12A)
    revised_drums, drum_preview = generate_drums(
        tempo=tempo,
        bar_count=BARS_PER_READING,
        drum_style="latin",
        drum_kit="percussion",
        fusion_contract=contract,
    )

    random.seed(int(contract.seed) ^ 0x4B45)
    revised_keys, keys_preview = generate_chords(
        tempo=tempo,
        bar_count=BARS_PER_READING,
        key=key,
        scale=scale,
        chord_style="warm_broken",
        chord_instrument="rhodes",
        chord_progression=list(chord_progression),
        fusion_contract=contract,
    )
    return revised_drums, revised_keys, {
        "drums": drum_preview,
        "keys": keys_preview,
    }


def render_audition(config: AuditionConfig) -> dict[str, Any]:
    source_dir = config.source_dir.expanduser().resolve()
    output_dir = config.output_dir.expanduser().resolve()
    source_configuration = _load_source_configuration(source_dir)
    tempo = int(source_configuration["tempo"])
    key = str(source_configuration["key"])
    scale = str(source_configuration["scale"])
    chord_progression = tuple(
        str(chord) for chord in source_configuration["chord_progression"]
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    locked_dir = output_dir / "locked_bass"
    stems_dir = output_dir / "stems"
    continuous_dir = output_dir / "continuous"
    for directory in (locked_dir, stems_dir, continuous_dir):
        directory.mkdir(parents=True, exist_ok=True)

    family_records: dict[str, dict[str, Any]] = {}
    locked_bass: dict[str, bytes] = {}
    lane_sections: dict[str, list[tuple[bytes, int]]] = {
        "drums": [],
        "bass": [],
        "keys": [],
    }

    for spec in FAMILY_SPECS:
        family_dir = source_dir / spec.source_directory_name
        bass_path = family_dir / "bass.mid"
        bass_data = bass_path.read_bytes()
        actual_bass_sha = _sha256(bass_data)
        if actual_bass_sha != spec.approved_bass_sha256:
            raise ValueError(
                f"{spec.covenant_id} approved bass SHA-256 mismatch: "
                f"expected {spec.approved_bass_sha256}, got {actual_bass_sha}"
            )

        contract_path = family_dir / "contract.json"
        contract = FusionGrooveContract.from_payload(
            json.loads(contract_path.read_text(encoding="utf-8"))
        )
        if contract.covenant_id != spec.covenant_id:
            raise ValueError(
                f"{contract_path} contains {contract.covenant_id}, "
                f"expected {spec.covenant_id}"
            )
        if int(contract.bar_count) != BARS_PER_READING:
            raise ValueError(
                f"{spec.covenant_id} contract must contain eight bars"
            )

        old_drums_path = family_dir / "drums.mid"
        old_keys_path = family_dir / "rhodes.mid"
        old_drums = old_drums_path.read_bytes()
        old_keys = old_keys_path.read_bytes()
        _single_instrument(old_drums, label=f"{spec.covenant_id} BEFORE drums")
        _single_instrument(old_keys, label=f"{spec.covenant_id} BEFORE keys")

        locked_path = locked_dir / f"{spec.covenant_id}.mid"
        shutil.copyfile(bass_path, locked_path)
        if locked_path.read_bytes() != bass_data:
            raise OSError(f"locked bass copy failed for {spec.covenant_id}")
        locked_bass[spec.covenant_id] = bass_data

        before_drums_path = stems_dir / (
            f"{spec.covenant_id}_before_drums.mid"
        )
        before_keys_path = stems_dir / f"{spec.covenant_id}_before_keys.mid"
        shutil.copyfile(old_drums_path, before_drums_path)
        shutil.copyfile(old_keys_path, before_keys_path)

        revised_drums, revised_keys, previews = _render_revised_accompaniment(
            contract=contract,
            tempo=tempo,
            key=key,
            scale=scale,
            chord_progression=chord_progression,
        )
        pocket_drums_path = stems_dir / (
            f"{spec.covenant_id}_pocket_pass_drums.mid"
        )
        pocket_keys_path = stems_dir / (
            f"{spec.covenant_id}_pocket_pass_keys.mid"
        )
        pocket_drums_path.write_bytes(revised_drums)
        pocket_keys_path.write_bytes(revised_keys)

        if _sha256(old_drums) == _sha256(revised_drums):
            raise ValueError(
                f"{spec.covenant_id} POCKET PASS drums did not change"
            )
        if _sha256(old_keys) == _sha256(revised_keys):
            raise ValueError(
                f"{spec.covenant_id} POCKET PASS keys did not change"
            )

        lane_sections["drums"].extend(
            (
                (old_drums, spec.before_start_bar),
                (revised_drums, spec.pocket_start_bar),
            )
        )
        lane_sections["bass"].extend(
            (
                (bass_data, spec.before_start_bar),
                (bass_data, spec.pocket_start_bar),
            )
        )
        lane_sections["keys"].extend(
            (
                (old_keys, spec.before_start_bar),
                (revised_keys, spec.pocket_start_bar),
            )
        )

        family_records[spec.covenant_id] = {
            "contract_id": contract.contract_id,
            "contract_file": str(contract_path),
            "contract_sha256": _sha256(contract_path.read_bytes()),
            "approved_bass_source": str(bass_path),
            "approved_bass_sha256": actual_bass_sha,
            "locked_bass": _midi_summary(locked_path),
            "before": {
                "start_bar": spec.before_start_bar + 1,
                "end_bar": spec.before_start_bar + BARS_PER_READING,
                "drums": _midi_summary(before_drums_path),
                "keys": _midi_summary(before_keys_path),
            },
            "pocket_pass": {
                "start_bar": spec.pocket_start_bar + 1,
                "end_bar": spec.pocket_start_bar + BARS_PER_READING,
                "drums": _midi_summary(pocket_drums_path),
                "keys": _midi_summary(pocket_keys_path),
                "generator_previews": previews,
            },
        }

    continuous = {
        "drums": _concatenate_at_bars(
            lane_sections["drums"],
            tempo=tempo,
            instrument_name="Drums - BEFORE / POCKET PASS",
        ),
        "bass": _concatenate_at_bars(
            lane_sections["bass"],
            tempo=tempo,
            instrument_name="Bass - LOCKED performance",
        ),
        "keys": _concatenate_at_bars(
            lane_sections["keys"],
            tempo=tempo,
            instrument_name="Keys - BEFORE / POCKET PASS",
        ),
    }
    continuous["combined"] = merge_lane_midis(
        tempo=tempo,
        lanes={
            "drums": continuous["drums"],
            "bass": continuous["bass"],
            "keys": continuous["keys"],
        },
    )
    continuous_files: dict[str, dict[str, Any]] = {}
    for lane_name, data in continuous.items():
        path = continuous_dir / f"{lane_name}.mid"
        path.write_bytes(data)
        continuous_files[lane_name] = _midi_summary(path)

    bass_proof = _bass_identity_proof(
        continuous["combined"],
        locked_bass=locked_bass,
    )
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "title": "Fusion fixed-bass BEFORE / POCKET PASS audition",
        "source_demo": str(source_dir),
        "configuration": {
            "tempo": tempo,
            "key": key,
            "scale": scale,
            "chord_progression": list(chord_progression),
            "bars_per_reading": BARS_PER_READING,
            "gap_bars": GAP_BARS,
            "total_bars": 35,
        },
        "sections": [
            {
                "bars": "1-8",
                "covenant_id": "anticipated_tumbao",
                "reading": "before",
            },
            {
                "bars": "10-17",
                "covenant_id": "anticipated_tumbao",
                "reading": "pocket_pass",
            },
            {
                "bars": "19-26",
                "covenant_id": "open_funk_reply",
                "reading": "before",
            },
            {
                "bars": "28-35",
                "covenant_id": "open_funk_reply",
                "reading": "pocket_pass",
            },
        ],
        "tracks": [
            "Drums - BEFORE / POCKET PASS",
            "Bass - LOCKED performance",
            "Keys - BEFORE / POCKET PASS",
        ],
        "families": family_records,
        "continuous": continuous_files,
        "bass_identity_proof": bass_proof,
    }
    manifest_path = output_dir / "manifest.json"
    manifest["manifest"] = str(manifest_path)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Freeze two approved Fusion bass takes and audition original "
            "versus pocket-pass drums and keys."
        )
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help=f"Existing Fusion contract demo (default: {DEFAULT_SOURCE_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Destination (default: {DEFAULT_OUTPUT_DIR})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        config = AuditionConfig(
            source_dir=args.source_dir,
            output_dir=args.output_dir,
        )
        manifest = render_audition(config)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print("Rendered fixed-bass BEFORE / POCKET PASS audition.")
    print(f"Combined MIDI: {manifest['continuous']['combined']['file']}")
    print(
        "Bass identity proof: "
        + (
            "VERIFIED"
            if manifest["bass_identity_proof"]["verified"]
            else "FAILED"
        )
    )
    print(f"Manifest: {manifest['manifest']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
