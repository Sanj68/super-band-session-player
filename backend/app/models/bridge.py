"""Pydantic models for the Logic AU analyser bridge contract (v0.7.1 spike)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _clamp01(v: float | int | None) -> float:
    if v is None:
        return 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        return 0.0
    if x != x:  # NaN
        return 0.0
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


class BridgeHeartbeatRequest(BaseModel):
    plugin_instance_id: str = Field(min_length=1, max_length=128)
    plugin_version: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=128)
    source_id: str | None = Field(default=None, max_length=128)


class BridgeTransportFrame(BaseModel):
    plugin_instance_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    host_tempo: float | None = Field(default=None, ge=20.0, le=400.0)
    sample_rate: float | None = Field(default=None, ge=4000.0, le=384000.0)
    playing: bool = False
    ppq_position: float | None = None
    bar_index: int | None = Field(default=None, ge=0)
    beat_index: int | None = Field(default=None, ge=0)


class BridgeSourceFeatureFrame(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    plugin_instance_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    sample_rate: float = Field(ge=4000.0, le=384000.0)
    host_tempo: float | None = Field(default=None, ge=20.0, le=400.0)
    playing: bool = False
    ppq_position: float | None = None
    bar_index: int = Field(ge=0)
    frame_start_seconds: float | None = Field(default=None, ge=0.0)
    duration_seconds: float = Field(gt=0.0)
    rms: float = 0.0
    low_band_energy: float = 0.0
    mid_band_energy: float = 0.0
    high_band_energy: float = 0.0
    onset_strength: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def _normalize_live_feature_aliases(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        if "host_tempo" not in out and "tempo" in out:
            out["host_tempo"] = out["tempo"]
        if "ppq_position" not in out and "bar_position" in out:
            try:
                bar_pos = float(out["bar_position"])
                bar_index = int(out.get("bar_index", 0) or 0)
            except (TypeError, ValueError):
                bar_pos = 0.0
                bar_index = 0
            out["ppq_position"] = (bar_index * 4.0) + (bar_pos * 4.0) if 0.0 <= bar_pos <= 1.0 else bar_pos
        if "rms" not in out and "RMS" in out:
            out["rms"] = out["RMS"]
        band_energy = out.get("band_energy")
        if band_energy is not None:
            if isinstance(band_energy, dict):
                out.setdefault("low_band_energy", band_energy.get("low", band_energy.get("low_band")))
                out.setdefault("mid_band_energy", band_energy.get("mid", band_energy.get("mid_band")))
                out.setdefault("high_band_energy", band_energy.get("high", band_energy.get("high_band")))
            elif isinstance(band_energy, (list, tuple)):
                vals = list(band_energy)
                if vals:
                    out.setdefault("low_band_energy", vals[0])
                if len(vals) > 1:
                    out.setdefault("mid_band_energy", vals[1])
                if len(vals) > 2:
                    out.setdefault("high_band_energy", vals[2])
            else:
                out.setdefault("low_band_energy", band_energy)
                out.setdefault("mid_band_energy", band_energy)
                out.setdefault("high_band_energy", band_energy)
        return out

    @field_validator("rms", "low_band_energy", "mid_band_energy", "high_band_energy", "onset_strength", mode="before")
    @classmethod
    def _clamp_unit(cls, v: Any) -> float:
        return _clamp01(v)


class BridgeStateResponse(BaseModel):
    connected: bool
    plugin_instance_id: str | None = None
    session_id: str | None = None
    source_id: str | None = None
    last_seen_at: str | None = None
    frame_count: int = 0
    last_transport: dict[str, Any] | None = None
