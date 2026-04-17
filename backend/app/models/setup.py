"""Pydantic models for saved band setups (local JSON store)."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.session import (
    BassInstrument,
    BassPlayer,
    BassStyle,
    ChordInstrument,
    ChordPlayer,
    ChordStyle,
    DrumKit,
    DrumPlayer,
    DrumStyle,
    LeadInstrument,
    LeadPlayer,
    LeadStyle,
    SessionPatch,
    SessionPreset,
)


def _coerce_instrument_defaults(data: object) -> object:
    if not isinstance(data, dict):
        return data
    defaults = {
        "lead_instrument": "flute",
        "bass_instrument": "finger_bass",
        "chord_instrument": "piano",
        "drum_kit": "standard",
    }
    for k, v in defaults.items():
        if data.get(k) is None:
            data[k] = v
    return data


class BandSetup(BaseModel):
    """A named band configuration for the UI / session styles."""

    name: str = Field(min_length=1, max_length=120)
    session_preset: SessionPreset | None = None
    drum_style: DrumStyle
    bass_style: BassStyle
    chord_style: ChordStyle
    lead_style: LeadStyle
    lead_instrument: LeadInstrument = Field(default=LeadInstrument.flute)
    lead_player: LeadPlayer | None = Field(
        default=None,
        description="Optional lead personality; older saved setups omit this field (None).",
    )
    bass_instrument: BassInstrument = Field(default=BassInstrument.finger_bass)
    bass_player: BassPlayer | None = Field(
        default=None,
        description="Optional bass personality; older saved setups omit this field (None).",
    )
    drum_player: DrumPlayer | None = Field(
        default=None,
        description="Optional drum personality; older saved setups omit this field (None).",
    )
    chord_player: ChordPlayer | None = Field(
        default=None,
        description="Optional chord personality; older saved setups omit this field (None).",
    )
    chord_instrument: ChordInstrument = Field(default=ChordInstrument.piano)
    drum_kit: DrumKit = Field(default=DrumKit.standard)
    tempo: int | None = Field(default=None, ge=40, le=240)
    key: str | None = Field(default=None, max_length=4)
    scale: str | None = Field(default=None, max_length=32)

    @model_validator(mode="before")
    @classmethod
    def _instruments_before_band(cls, data: object) -> object:
        return _coerce_instrument_defaults(data)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        t = v.strip()
        if not t:
            raise ValueError("name cannot be empty")
        return t

    @field_validator("key", "scale", mode="before")
    @classmethod
    def empty_str_to_none_band(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            t = v.strip()
            return t if t else None
        return v


class BandSetupCreate(BaseModel):
    """Request body for POST /api/setups."""

    @model_validator(mode="before")
    @classmethod
    def _instruments_before_create(cls, data: object) -> object:
        return _coerce_instrument_defaults(data)

    name: str = Field(min_length=1, max_length=120)
    session_preset: SessionPreset | None = None
    drum_style: DrumStyle
    bass_style: BassStyle
    chord_style: ChordStyle
    lead_style: LeadStyle
    lead_instrument: LeadInstrument = Field(default=LeadInstrument.flute)
    lead_player: LeadPlayer | None = Field(default=None, description="Optional lead personality.")
    bass_instrument: BassInstrument = Field(default=BassInstrument.finger_bass)
    bass_player: BassPlayer | None = Field(default=None, description="Optional bass personality.")
    drum_player: DrumPlayer | None = Field(default=None, description="Optional drum personality.")
    chord_player: ChordPlayer | None = Field(default=None, description="Optional chord personality.")
    chord_instrument: ChordInstrument = Field(default=ChordInstrument.piano)
    drum_kit: DrumKit = Field(default=DrumKit.standard)
    tempo: int | None = Field(default=None, ge=40, le=240)
    key: str | None = Field(default=None, max_length=4)
    scale: str | None = Field(default=None, max_length=32)

    @field_validator("name")
    @classmethod
    def strip_name(cls, v: str) -> str:
        t = v.strip()
        if not t:
            raise ValueError("name cannot be empty")
        return t

    @field_validator("key", "scale", mode="before")
    @classmethod
    def empty_str_to_none(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if isinstance(v, str):
            t = v.strip()
            return t if t else None
        return v


class BandSetupListResponse(BaseModel):
    setups: list[BandSetup]


class BandSetupCreated(BaseModel):
    setup: BandSetup


class BandSetupDeleted(BaseModel):
    ok: bool = True
    deleted: str


class SavedSetupAsSessionPatchResponse(BaseModel):
    """Canonical PATCH body for applying a saved setup to an existing session."""

    patch: SessionPatch
