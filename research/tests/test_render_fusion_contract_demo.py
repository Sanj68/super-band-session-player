from __future__ import annotations

import io
from pathlib import Path

import pretty_midi

from research.render_fusion_contract_demo import (
    DemoConfig,
    covenant_ids,
    render_demo,
)


def _midi(path: Path) -> pretty_midi.PrettyMIDI:
    return pretty_midi.PrettyMIDI(io.BytesIO(path.read_bytes()))


def _note_count(path: Path) -> int:
    return sum(len(instrument.notes) for instrument in _midi(path).instruments)


def test_renderer_writes_four_complete_deterministic_auditions(
    tmp_path: Path,
) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first = render_demo(
        DemoConfig(
            output_dir=first_dir,
            tempo=116,
            bars=8,
            base_seed=7300,
            gap_bars=1,
        )
    )
    second = render_demo(
        DemoConfig(
            output_dir=second_dir,
            tempo=116,
            bars=8,
            base_seed=7300,
            gap_bars=1,
        )
    )

    assert tuple(
        family["covenant_id"] for family in first["families"]
    ) == covenant_ids()
    assert len(first["families"]) == 4
    articulation_union: set[str] = set()

    for left, right in zip(first["families"], second["families"], strict=True):
        assert left["contract_id"] == right["contract_id"]
        articulation_union.update(
            left["bass_performance_intent"]["articulations"]
        )
        assert sum(
            left["bass_performance_intent"]["articulations"].values()
        ) == int(left["files"]["bass"]["note_count"])
        assert left["phrase_roles"][:4] == ["statement"] * 4
        assert left["phrase_roles"][4:] == ["variation"] * 4
        for stem in ("drums", "bass", "bass_clean", "rhodes", "combined"):
            left_file = Path(left["files"][stem]["file"])
            right_file = Path(right["files"][stem]["file"])
            assert left_file.is_file()
            assert _note_count(left_file) > 0
            assert (
                left["files"][stem]["sha256"]
                == right["files"][stem]["sha256"]
            )
            assert right_file.is_file()

        combined = _midi(Path(left["files"]["combined"]["file"]))
        assert len(combined.instruments) == 3
        assert {instrument.name for instrument in combined.instruments} == {
            "Drums",
            "Bass (Performance)",
            "Chords",
        }

    assert {"ghost", "dead"}.issubset(articulation_union)
    assert articulation_union.intersection({"slide_to", "hammer"})

    all_combined = Path(first["all_families"]["files"]["combined"]["file"])
    assert all_combined.is_file()
    assert len(_midi(all_combined).instruments) == 3
    assert _note_count(all_combined) == sum(
        int(family["files"]["combined"]["note_count"])
        for family in first["families"]
    )
    assert Path(first["manifest"]).is_file()
    assert Path(first["readme"]).is_file()
