"""Pydantic models for the Logic AU analyser bridge contract (v0.7.1 spike)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


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
