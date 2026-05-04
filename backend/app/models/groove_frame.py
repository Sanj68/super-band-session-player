"""GrooveFrame: per-bar 16th-slot map shared between upload analysis and future plug-in bridge."""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, model_validator

GROOVE_SLOTS = 16


def _to_clamped_float(value: Any) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(v):
        return 0.0
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _normalize_row(raw: Any) -> list[float]:
    if not isinstance(raw, (list, tuple)):
        out = [0.0] * GROOVE_SLOTS
        return out
    out = [_to_clamped_float(x) for x in list(raw)[:GROOVE_SLOTS]]
    if len(out) < GROOVE_SLOTS:
        out.extend([0.0] * (GROOVE_SLOTS - len(out)))
    return out


class GrooveFrame(BaseModel):
    """One bar of 16th-slot percussive evidence; identical shape regardless of source."""

    bar_index: int = Field(ge=0)
    tempo_bpm: float | None = Field(default=None, ge=20.0, le=400.0)
    resolution: int = Field(default=GROOVE_SLOTS, ge=1, le=64)
    onset_weight: list[float] = Field(default_factory=list)
    kick_weight: list[float] = Field(default_factory=list)
    snare_weight: list[float] = Field(default_factory=list)
    slot_pressure: list[float] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_tag: str = "reference_audio"
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        # Default + clamp resolution.
        raw_res = out.get("resolution", GROOVE_SLOTS)
        try:
            res = int(raw_res) if raw_res is not None else GROOVE_SLOTS
        except (TypeError, ValueError):
            res = GROOVE_SLOTS
        out["resolution"] = max(1, min(64, res))

        out["onset_weight"] = _normalize_row(out.get("onset_weight"))
        out["kick_weight"] = _normalize_row(out.get("kick_weight"))
        out["snare_weight"] = _normalize_row(out.get("snare_weight"))
        out["slot_pressure"] = _normalize_row(out.get("slot_pressure"))

        raw_conf = out.get("confidence", 0.0)
        out["confidence"] = _to_clamped_float(raw_conf)

        if "source_tag" not in out or not isinstance(out["source_tag"], str) or not out["source_tag"]:
            out["source_tag"] = "reference_audio"

        meta = out.get("source_metadata")
        if not isinstance(meta, dict):
            out["source_metadata"] = {}
        return out
