from __future__ import annotations

import io
import json
from pathlib import Path
import shutil

import pretty_midi
import pytest

import research.render_fixed_bass_pocket_audition as audition_module
from research.render_fixed_bass_pocket_audition import (
    AuditionConfig,
    FAMILY_SPECS,
    render_audition,
)
from research.render_fusion_contract_demo import DemoConfig, render_demo

from app.services import generator


def _make_accompaniment_stem_distinct(path: Path) -> None:
    """Stand in for the historical BEFORE renderer in an isolated test."""

    midi = pretty_midi.PrettyMIDI(io.BytesIO(path.read_bytes()))
    note = next(
        note
        for instrument in midi.instruments
        for note in instrument.notes
    )
    note.velocity = max(1, min(127, int(note.velocity) - 1))
    buffer = io.BytesIO()
    midi.write(buffer)
    path.write_bytes(buffer.getvalue())


@pytest.fixture(scope="module")
def source_demo(tmp_path_factory: pytest.TempPathFactory) -> Path:
    source_dir = tmp_path_factory.mktemp("fixed-bass-source")
    render_demo(
        DemoConfig(
            output_dir=source_dir,
            tempo=116,
            bars=8,
            base_seed=7300,
            gap_bars=1,
        )
    )
    # The repository test builds its source with the current renderer, whereas
    # the real audition points at the preserved pre-fix benchmark.  Make only
    # the accompaniment fixtures distinct; approved bass and contracts stay
    # byte-for-byte canonical.
    for spec in FAMILY_SPECS:
        family_dir = source_dir / spec.source_directory_name
        _make_accompaniment_stem_distinct(family_dir / "drums.mid")
        _make_accompaniment_stem_distinct(family_dir / "rhodes.mid")
    return source_dir


def test_fixed_bass_audition_proves_identity_and_changes_only_accompaniment(
    source_demo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "audition"
    drum_contracts: list[dict[str, object]] = []
    keys_contracts: list[dict[str, object]] = []
    real_generate_drums = audition_module.generate_drums
    real_generate_chords = audition_module.generate_chords

    def spy_drums(**kwargs: object) -> tuple[bytes, str]:
        contract = kwargs["fusion_contract"]
        drum_contracts.append(contract.to_payload())  # type: ignore[union-attr]
        return real_generate_drums(**kwargs)  # type: ignore[arg-type]

    def spy_chords(**kwargs: object) -> tuple[bytes, str]:
        contract = kwargs["fusion_contract"]
        keys_contracts.append(contract.to_payload())  # type: ignore[union-attr]
        return real_generate_chords(**kwargs)  # type: ignore[arg-type]

    def forbidden_bass_generation(**_kwargs: object) -> tuple[bytes, str]:
        raise AssertionError("fixed-bass renderer must not generate bass")

    monkeypatch.setattr(audition_module, "generate_drums", spy_drums)
    monkeypatch.setattr(audition_module, "generate_chords", spy_chords)
    monkeypatch.setattr(generator, "generate_bass", forbidden_bass_generation)

    assert not hasattr(audition_module, "generate_bass")
    manifest = render_audition(
        AuditionConfig(
            source_dir=source_demo,
            output_dir=output_dir,
        )
    )

    assert manifest["sections"] == [
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
    ]
    assert manifest["bass_identity_proof"]["verified"] is True
    assert set(manifest["families"]) == {
        "anticipated_tumbao",
        "open_funk_reply",
    }

    for spec in FAMILY_SPECS:
        source_family = source_demo / spec.source_directory_name
        locked = output_dir / "locked_bass" / f"{spec.covenant_id}.mid"
        assert locked.read_bytes() == (source_family / "bass.mid").read_bytes()

        proof = manifest["bass_identity_proof"]["families"][
            spec.covenant_id
        ]
        assert proof["approved_raw_sha256"] == spec.approved_bass_sha256
        assert proof["pair_identical"] is True
        assert proof["sections"]["before"]["matches_source"] is True
        assert proof["sections"]["pocket_pass"]["matches_source"] is True
        assert (
            proof["sections"]["before"]["event_sha256"]
            == proof["sections"]["pocket_pass"]["event_sha256"]
            == proof["source_event_sha256"]
        )

        family = manifest["families"][spec.covenant_id]
        assert Path(family["before"]["drums"]["file"]).read_bytes() == (
            source_family / "drums.mid"
        ).read_bytes()
        assert Path(family["before"]["keys"]["file"]).read_bytes() == (
            source_family / "rhodes.mid"
        ).read_bytes()
        assert (
            family["before"]["drums"]["sha256"]
            != family["pocket_pass"]["drums"]["sha256"]
        )
        assert (
            family["before"]["keys"]["sha256"]
            != family["pocket_pass"]["keys"]["sha256"]
        )

    expected_contracts = [
        json.loads(
            (
                source_demo
                / spec.source_directory_name
                / "contract.json"
            ).read_text(encoding="utf-8")
        )
        for spec in FAMILY_SPECS
    ]
    assert json.loads(json.dumps(drum_contracts)) == expected_contracts
    assert json.loads(json.dumps(keys_contracts)) == expected_contracts

    combined_path = Path(manifest["continuous"]["combined"]["file"])
    combined = pretty_midi.PrettyMIDI(
        io.BytesIO(combined_path.read_bytes())
    )
    assert len(combined.instruments) == 3
    assert {instrument.name for instrument in combined.instruments} == set(
        manifest["tracks"]
    )
    bass = next(
        instrument
        for instrument in combined.instruments
        if instrument.name == "Bass - LOCKED performance"
    )
    assert len(bass.notes) == 2 * (32 + 36)
    assert len(bass.pitch_bends) == 2 * (13 + 13)
    assert len(bass.control_changes) == 2 * (6 + 6)
    for gap_bar in (8, 17, 26):
        gap_start = gap_bar * 4 * combined.resolution
        gap_end = (gap_bar + 1) * 4 * combined.resolution
        assert not any(
            combined.time_to_tick(note.start) < gap_end
            and combined.time_to_tick(note.end) > gap_start
            for instrument in combined.instruments
            for note in instrument.notes
        )
    assert Path(manifest["manifest"]).is_file()


def test_fixed_bass_audition_rejects_unapproved_bass(
    source_demo: Path,
    tmp_path: Path,
) -> None:
    changed_source = tmp_path / "changed-source"
    shutil.copytree(source_demo, changed_source)
    bass_path = changed_source / "01_anticipated_tumbao" / "bass.mid"
    bass_path.write_bytes(bass_path.read_bytes() + b"\x00")

    with pytest.raises(ValueError, match="approved bass SHA-256 mismatch"):
        render_audition(
            AuditionConfig(
                source_dir=changed_source,
                output_dir=tmp_path / "must-not-render",
            )
        )
