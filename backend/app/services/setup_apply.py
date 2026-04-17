"""Map a saved band setup to a session PATCH body (no regeneration)."""

from __future__ import annotations

from typing import Any

from app.models.setup import BandSetup
from app.models.session import SessionPatch


def band_setup_to_session_patch_payload(setup: BandSetup) -> dict[str, Any]:
    """
    Build a JSON-serializable dict suitable for PATCH /api/sessions/{id}.

    When ``setup.session_preset`` is set, it is included so the server applies preset
    defaults first, then explicit style fields from the same setup (see patch order
    in session_routes). When there is no preset, ``session_preset`` is omitted so an
    existing session preset is not cleared implicitly.
    """
    out: dict[str, Any] = {
        "drum_style": setup.drum_style.value,
        "bass_style": setup.bass_style.value,
        "chord_style": setup.chord_style.value,
        "lead_style": setup.lead_style.value,
        "drum_kit": setup.drum_kit.value,
        "bass_instrument": setup.bass_instrument.value,
        "chord_instrument": setup.chord_instrument.value,
        "lead_instrument": setup.lead_instrument.value,
    }
    if setup.session_preset is not None:
        out["session_preset"] = setup.session_preset.value
    if setup.bass_player is not None:
        out["bass_player"] = setup.bass_player.value
    if setup.drum_player is not None:
        out["drum_player"] = setup.drum_player.value
    if setup.chord_player is not None:
        out["chord_player"] = setup.chord_player.value
    if setup.lead_player is not None:
        out["lead_player"] = setup.lead_player.value
    return out


def band_setup_to_session_patch(setup: BandSetup) -> SessionPatch:
    """Validate mapping as ``SessionPatch`` (raises on invalid combinations)."""
    return SessionPatch.model_validate(band_setup_to_session_patch_payload(setup))
