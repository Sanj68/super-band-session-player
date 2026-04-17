"""Pydantic models for sessions and lanes."""

from __future__ import annotations

from enum import Enum
from pydantic import BaseModel, Field, field_validator, model_validator


class LaneName(str, Enum):
    drums = "drums"
    bass = "bass"
    chords = "chords"
    lead = "lead"


class SuitPartTarget(str, Enum):
    """V1: only ``lead`` is supported for add-part-to-suit."""

    lead = "lead"


class SuitPartMode(str, Enum):
    solo = "solo"
    counter = "counter"
    sparse_fill = "sparse_fill"


class LeadStyle(str, Enum):
    sparse = "sparse"
    sparse_emotional = "sparse_emotional"
    melodic = "melodic"
    rhythmic = "rhythmic"
    bluesy = "bluesy"
    fusion = "fusion"


class BassStyle(str, Enum):
    supportive = "supportive"
    melodic = "melodic"
    rhythmic = "rhythmic"
    slap = "slap"
    fusion = "fusion"


class ChordStyle(str, Enum):
    simple = "simple"
    jazzy = "jazzy"
    wide = "wide"
    dense = "dense"
    stabs = "stabs"
    warm_broken = "warm_broken"


class DrumStyle(str, Enum):
    straight = "straight"
    broken = "broken"
    shuffle = "shuffle"
    funk = "funk"
    latin = "latin"
    laid_back_soul = "laid_back_soul"


class LeadInstrument(str, Enum):
    flute = "flute"
    vibes = "vibes"
    guitar = "guitar"
    synth_lead = "synth_lead"


class LeadPlayer(str, Enum):
    """Optional phrase personalities for the lead lane (inspired-by, not impersonations)."""

    coltrane = "coltrane"
    cal_tjader = "cal_tjader"
    soul_sparse = "soul_sparse"
    funk_phrasing = "funk_phrasing"


class BassInstrument(str, Enum):
    finger_bass = "finger_bass"
    slap_bass = "slap_bass"
    synth_bass = "synth_bass"


class BassPlayer(str, Enum):
    """Optional groove personalities for the bass lane (inspired-by, not impersonations)."""

    bootsy = "bootsy"
    marcus = "marcus"
    pino = "pino"


class DrumPlayer(str, Enum):
    """Optional groove personalities for the drum lane (inspired-by, not impersonations)."""

    stubblefield = "stubblefield"
    questlove = "questlove"
    dilla = "dilla"


class ChordPlayer(str, Enum):
    """Optional voicing personalities for the chord lane (inspired-by, not impersonations)."""

    herbie = "herbie"
    barry_miles = "barry_miles"
    soul_keys = "soul_keys"
    funk_stabs = "funk_stabs"


class ChordInstrument(str, Enum):
    piano = "piano"
    rhodes = "rhodes"
    organ = "organ"
    pad = "pad"


class DrumKit(str, Enum):
    standard = "standard"
    dry = "dry"
    percussion = "percussion"


class SessionPreset(str, Enum):
    latin_jazz = "latin_jazz"
    fusion = "fusion"
    cool_modal = "cool_modal"
    dusty_broken = "dusty_broken"
    soulful_funk = "soulful_funk"
    rare_groove_soul = "rare_groove_soul"


def lane_styles_for_session_preset(preset: SessionPreset) -> tuple[str, str, str, str]:
    """Returns (drum_style, bass_style, chord_style, lead_style) for a session preset."""
    mapping: dict[SessionPreset, tuple[DrumStyle, BassStyle, ChordStyle, LeadStyle]] = {
        SessionPreset.latin_jazz: (
            DrumStyle.latin,
            BassStyle.supportive,
            ChordStyle.jazzy,
            LeadStyle.melodic,
        ),
        SessionPreset.fusion: (
            DrumStyle.funk,
            BassStyle.fusion,
            ChordStyle.wide,
            LeadStyle.fusion,
        ),
        SessionPreset.cool_modal: (
            DrumStyle.shuffle,
            BassStyle.melodic,
            ChordStyle.wide,
            LeadStyle.sparse,
        ),
        SessionPreset.dusty_broken: (
            DrumStyle.broken,
            BassStyle.supportive,
            ChordStyle.stabs,
            LeadStyle.bluesy,
        ),
        SessionPreset.soulful_funk: (
            DrumStyle.funk,
            BassStyle.slap,
            ChordStyle.dense,
            LeadStyle.rhythmic,
        ),
        SessionPreset.rare_groove_soul: (
            DrumStyle.laid_back_soul,
            BassStyle.supportive,
            ChordStyle.warm_broken,
            LeadStyle.sparse_emotional,
        ),
    }
    d, b, c, l = mapping[preset]
    return (d.value, b.value, c.value, l.value)


class SessionPatch(BaseModel):
    """Partial session update. Provide at least one field; no automatic lane regeneration."""

    lead_style: LeadStyle | None = Field(
        default=None,
        description="When set, updates stored lead preset (regenerate lead to apply).",
    )
    lead_player: LeadPlayer | None = Field(
        default=None,
        description="When set, updates stored lead player profile (regenerate lead to apply). Send null to clear.",
    )
    bass_style: BassStyle | None = Field(
        default=None,
        description="When set, updates stored bass preset (regenerate bass to apply).",
    )
    chord_style: ChordStyle | None = Field(
        default=None,
        description="When set, updates stored chord preset (regenerate chords to apply).",
    )
    chord_player: ChordPlayer | None = Field(
        default=None,
        description="When set, updates stored chord player profile (regenerate chords to apply). Send null to clear.",
    )
    drum_style: DrumStyle | None = Field(
        default=None,
        description="When set, updates stored drum preset (regenerate drums to apply).",
    )
    session_preset: SessionPreset | None = Field(
        default=None,
        description="When set, updates stored preset and all lane styles to preset defaults; explicit style fields in the same request override after. Instrument fields are preserved unless also sent in this request.",
    )
    lead_instrument: LeadInstrument | None = Field(
        default=None,
        description="When set, updates stored lead instrument (regenerate lead to apply).",
    )
    bass_instrument: BassInstrument | None = Field(
        default=None,
        description="When set, updates stored bass instrument (regenerate bass to apply).",
    )
    bass_player: BassPlayer | None = Field(
        default=None,
        description="When set, updates stored bass player profile (regenerate bass to apply). Send null to clear.",
    )
    drum_player: DrumPlayer | None = Field(
        default=None,
        description="When set, updates stored drum player profile (regenerate drums to apply). Send null to clear.",
    )
    chord_instrument: ChordInstrument | None = Field(
        default=None,
        description="When set, updates stored chord instrument (regenerate chords to apply).",
    )
    drum_kit: DrumKit | None = Field(
        default=None,
        description="When set, updates stored drum kit (regenerate drums to apply).",
    )
    anchor_lane: LaneName | None = Field(
        default=None,
        description="Lane to treat as rhythmic/harmonic anchor for complementary generation; send null to clear.",
    )

    @model_validator(mode="after")
    def at_least_one_field(self) -> SessionPatch:
        if not self.model_fields_set:
            raise ValueError(
                "Provide at least one of: lead_style, lead_player, bass_style, bass_player, chord_style, chord_player, "
                "drum_style, drum_player, session_preset, lead_instrument, bass_instrument, chord_instrument, drum_kit, "
                "anchor_lane"
            )
        return self


class SessionCreate(BaseModel):
    tempo: int = Field(ge=40, le=240, description="BPM")
    key: str = Field(min_length=1, max_length=4, examples=["C", "F#", "Bb"])
    scale: str = Field(
        min_length=1,
        max_length=32,
        examples=["major", "natural_minor", "pentatonic_major", "blues"],
    )
    bar_count: int = Field(ge=1, le=128, description="Number of 4/4 bars")
    lead_style: LeadStyle | None = Field(
        default=None,
        description="Lead expression preset; omit for melodic.",
    )
    lead_player: LeadPlayer | None = Field(
        default=None,
        description="Optional lead personality; omit for style-only generation.",
    )
    bass_style: BassStyle | None = Field(
        default=None,
        description="Bass groove preset; omit for supportive.",
    )
    chord_style: ChordStyle | None = Field(
        default=None,
        description="Chord voicing preset; omit for simple.",
    )
    chord_player: ChordPlayer | None = Field(
        default=None,
        description="Optional chord personality; omit for style-only generation.",
    )
    drum_style: DrumStyle | None = Field(
        default=None,
        description="Drum groove preset; omit for straight.",
    )
    session_preset: SessionPreset | None = Field(
        default=None,
        description="Optional session vibe preset; when set, initializes lane styles unless overridden per lane.",
    )
    lead_instrument: LeadInstrument | None = Field(
        default=None,
        description="Lead timbre; omit for flute.",
    )
    bass_instrument: BassInstrument | None = Field(
        default=None,
        description="Bass timbre; omit for finger bass.",
    )
    bass_player: BassPlayer | None = Field(
        default=None,
        description="Optional bass personality; omit for style-only generation.",
    )
    drum_player: DrumPlayer | None = Field(
        default=None,
        description="Optional drum personality; omit for style-only generation.",
    )
    chord_instrument: ChordInstrument | None = Field(
        default=None,
        description="Chord timbre; omit for piano.",
    )
    drum_kit: DrumKit | None = Field(
        default=None,
        description="Drum kit character; omit for standard kit.",
    )
    anchor_lane: LaneName | None = Field(
        default=None,
        description="Optional anchor lane (drums, bass, chords, lead) for anchor-first / context-aware generation.",
    )


class LaneNote(BaseModel):
    """Single note from lane MIDI (seconds, MIDI pitch 0–127)."""

    pitch: int = Field(ge=0, le=127)
    start: float = Field(ge=0.0, description="Start time in seconds.")
    end: float = Field(ge=0.0, description="End time in seconds.")
    velocity: int = Field(ge=0, le=127)


class LaneState(BaseModel):
    name: LaneName
    preview: str
    generated: bool
    locked: bool = Field(
        default=False,
        description="When true, multi-lane regenerate skips this lane; single-lane regenerate still allowed.",
    )
    notes: list[LaneNote] = Field(
        default_factory=list,
        description="Note events parsed from lane MIDI when generated.",
    )


class SessionState(BaseModel):
    id: str
    tempo: int
    key: str
    scale: str
    bar_count: int
    session_preset: str | None = Field(
        default=None,
        description="Active session preset id, if any.",
    )
    lead_style: str = Field(default="melodic", description="Active lead preset for this session.")
    bass_style: str = Field(default="supportive", description="Active bass preset for this session.")
    chord_style: str = Field(default="simple", description="Active chord preset for this session.")
    drum_style: str = Field(default="straight", description="Active drum preset for this session.")
    lead_instrument: str = Field(default="flute", description="Active lead instrument for this session.")
    lead_player: str | None = Field(
        default=None,
        description="Optional lead player profile id (coltrane, cal_tjader, soul_sparse, funk_phrasing), or null when unset.",
    )
    bass_instrument: str = Field(default="finger_bass", description="Active bass instrument for this session.")
    bass_player: str | None = Field(
        default=None,
        description="Optional bass player profile id (bootsy, marcus, pino), or null when unset.",
    )
    drum_player: str | None = Field(
        default=None,
        description="Optional drum player profile id (stubblefield, questlove, dilla), or null when unset.",
    )
    chord_instrument: str = Field(default="piano", description="Active chord instrument for this session.")
    chord_player: str | None = Field(
        default=None,
        description="Optional chord player profile id (herbie, barry_miles, soul_keys, funk_stabs), or null when unset.",
    )
    drum_kit: str = Field(default="standard", description="Active drum kit for this session.")
    anchor_lane: str | None = Field(
        default=None,
        description="When set, full generate builds this lane first and others use its timing/density context.",
    )
    lanes: dict[str, LaneState]
    message: str | None = None


class SessionCreated(BaseModel):
    session: SessionState


class GenerateResult(BaseModel):
    session: SessionState


class RegenerateLaneBody(BaseModel):
    """Optional body for future flags; empty for V1."""

    pass


class LaneLocksPatch(BaseModel):
    """Partial update of per-lane lock flags (PATCH /lane-locks). At least one key required."""

    drums: bool | None = None
    bass: bool | None = None
    chords: bool | None = None
    lead: bool | None = None

    @model_validator(mode="after")
    @classmethod
    def at_least_one_lane(cls, v: LaneLocksPatch) -> LaneLocksPatch:
        if all(x is None for x in (v.drums, v.bass, v.chords, v.lead)):
            raise ValueError("Provide at least one of: drums, bass, chords, lead")
        return v


class AddPartToSuitBody(BaseModel):
    """Request body for POST /api/sessions/{id}/add-part-to-suit (V1: lead only)."""

    target_lane: SuitPartTarget
    mode: SuitPartMode

    @field_validator("target_lane", mode="after")
    @classmethod
    def lead_only_v1(cls, v: SuitPartTarget) -> SuitPartTarget:
        if v != SuitPartTarget.lead:
            raise ValueError("Only target_lane 'lead' is supported in V1")
        return v


class RegenerateSelectedBody(BaseModel):
    """Request body for POST /api/sessions/{id}/regenerate-selected."""

    lanes: list[LaneName] = Field(
        min_length=1,
        description=(
            "One or more lane names: drums, bass, chords, lead. Duplicates are removed. "
            "The server regenerates in fixed order: drums, bass, chords, lead."
        ),
    )

    @field_validator("lanes", mode="after")
    @classmethod
    def unique_preserve_request_order(cls, v: list[LaneName]) -> list[LaneName]:
        seen: set[LaneName] = set()
        out: list[LaneName] = []
        for lane in v:
            if lane not in seen:
                seen.add(lane)
                out.append(lane)
        return out


class RegenerateLaneResult(BaseModel):
    session: SessionState
    lane: LaneName


class GenerateAroundAnchorBody(BaseModel):
    """Optional body for POST /generate-around-anchor: pin anchor to a lane for this request."""

    anchor_lane: LaneName | None = Field(
        default=None,
        description="When set, updates stored anchor_lane before regenerating non-anchor lanes.",
    )


class ExportInfo(BaseModel):
    """JSON response when not downloading binary."""

    session_id: str
    download_urls: dict[str, str]
    note: str = "GET each URL to download that lane's MIDI file."
