from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.session import BassEngine
from app.models.setup import BandSetup, BandSetupCreate
from app.services import setup_store
from app.services.setup_apply import (
    band_setup_to_session_patch,
    band_setup_to_session_patch_payload,
)


def _fusion_setup_payload() -> dict[str, object]:
    return {
        "name": "Shared Fusion Pocket",
        "session_preset": "fusion",
        "drum_style": "latin",
        "bass_style": "fusion",
        "bass_engine": "baseline",
        "bass_player": "bootsy",
        "chord_style": "simple",
        "lead_style": "fusion",
    }


@pytest.mark.parametrize("model_type", [BandSetupCreate, BandSetup])
def test_fusion_setup_models_normalize_baseline_to_phrase_v2(
    model_type: type[BandSetupCreate] | type[BandSetup],
) -> None:
    setup = model_type.model_validate(_fusion_setup_payload())

    assert setup.bass_engine == BassEngine.phrase_v2
    assert setup.bass_player is None
    assert setup.drum_style.value == "funk"
    assert setup.chord_style.value == "wide"
    assert setup.model_dump(mode="json")["bass_engine"] == "phrase_v2"


def test_legacy_saved_fusion_setup_loads_and_resaves_as_phrase_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setups_file = tmp_path / "band_setups.json"
    setups_file.write_text(
        json.dumps({"setups": [_fusion_setup_payload()]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(setup_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(setup_store, "_SETUPS_FILE", setups_file)

    setups = setup_store.load_setups()

    assert len(setups) == 1
    assert setups[0].bass_engine == BassEngine.phrase_v2
    assert setups[0].bass_player is None
    setup_store.save_setups(setups)
    persisted = json.loads(setups_file.read_text(encoding="utf-8"))
    assert persisted["setups"][0]["bass_engine"] == "phrase_v2"
    assert persisted["setups"][0]["bass_player"] is None
    assert persisted["setups"][0]["drum_style"] == "funk"
    assert persisted["setups"][0]["chord_style"] == "wide"


def test_fusion_setup_apply_defensively_forces_phrase_v2() -> None:
    normalized = BandSetup.model_validate(_fusion_setup_payload())
    stale = normalized.model_copy(
        update={"bass_engine": BassEngine.baseline},
    )

    payload = band_setup_to_session_patch_payload(stale)
    patch = band_setup_to_session_patch(stale)

    assert payload["bass_engine"] == "phrase_v2"
    assert payload["bass_player"] is None
    assert payload["drum_style"] == "funk"
    assert payload["chord_style"] == "wide"
    assert patch.bass_engine == BassEngine.phrase_v2
    assert patch.bass_player is None
