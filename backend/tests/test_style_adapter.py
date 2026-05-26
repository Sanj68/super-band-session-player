"""Style/persona adapter tests."""

from __future__ import annotations

import json
from pathlib import Path

from app.services.style_adapter import StyleAdapter


def _write_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_style_adapter_loads_bass_personas_with_lowercase_ids(tmp_path: Path) -> None:
    bass_dir = tmp_path / "bass"
    bass_dir.mkdir()
    _write_json(
        bass_dir / "player.json",
        {"id": "  Test_Player  ", "profile": {"base_style": "supportive", "density_ceiling": 3}},
    )

    adapter = StyleAdapter(personas_dir=tmp_path)

    assert adapter.bass_player_ids() == frozenset({"test_player"})
    assert adapter.bass_persona("test_player")["id"] == "  Test_Player  "
    assert adapter.bass_profiles()["test_player"]["density_ceiling"] == 3


def test_style_adapter_uses_filename_when_id_missing_and_ignores_non_objects(tmp_path: Path) -> None:
    bass_dir = tmp_path / "bass"
    bass_dir.mkdir()
    _write_json(bass_dir / "fallback.json", {"profile": {"base_style": "melodic"}})
    _write_json(bass_dir / "ignored.json", ["not", "a", "dict"])

    adapter = StyleAdapter(personas_dir=tmp_path)

    assert adapter.bass_player_ids() == frozenset({"fallback"})
    assert adapter.bass_profiles()["fallback"] == {"base_style": "melodic"}


def test_style_adapter_caches_loaded_personas(tmp_path: Path) -> None:
    bass_dir = tmp_path / "bass"
    bass_dir.mkdir()
    persona = bass_dir / "first.json"
    _write_json(persona, {"id": "first", "profile": {"base_style": "supportive"}})
    adapter = StyleAdapter(personas_dir=tmp_path)

    assert adapter.bass_player_ids() == frozenset({"first"})
    _write_json(persona, {"id": "second", "profile": {"base_style": "fusion"}})

    assert adapter.bass_player_ids() == frozenset({"first"})


def test_style_adapter_handles_missing_bass_directory(tmp_path: Path) -> None:
    adapter = StyleAdapter(personas_dir=tmp_path)

    assert adapter.bass_player_ids() == frozenset()
    assert adapter.bass_profiles() == {}
    assert adapter.bass_persona("missing") is None
