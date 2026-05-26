"""Data-backed style/persona adapter for lane generators."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final


_PERSONAS_DIR: Final[Path] = Path(__file__).resolve().parents[1] / "personas"


class StyleAdapter:
    """Load persona JSON files and expose lane-specific traits."""

    def __init__(self, personas_dir: Path = _PERSONAS_DIR) -> None:
        self._personas_dir = personas_dir
        self._bass_personas: dict[str, dict[str, Any]] | None = None

    def bass_player_ids(self) -> frozenset[str]:
        return frozenset(self._load_bass_personas())

    def bass_profiles(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for persona_id, persona in self._load_bass_personas().items():
            profile = persona.get("profile")
            if isinstance(profile, dict):
                out[persona_id] = dict(profile)
        return out

    def bass_persona(self, persona_id: str) -> dict[str, Any] | None:
        return self._load_bass_personas().get(persona_id)

    def _load_bass_personas(self) -> dict[str, dict[str, Any]]:
        if self._bass_personas is not None:
            return self._bass_personas
        bass_dir = self._personas_dir / "bass"
        personas: dict[str, dict[str, Any]] = {}
        if bass_dir.exists():
            for path in sorted(bass_dir.glob("*.json")):
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    continue
                persona_id = str(raw.get("id") or path.stem).strip().lower()
                if persona_id:
                    personas[persona_id] = raw
        self._bass_personas = personas
        return personas


BASS_STYLE_ADAPTER: Final[StyleAdapter] = StyleAdapter()


__all__ = ["BASS_STYLE_ADAPTER", "StyleAdapter"]
