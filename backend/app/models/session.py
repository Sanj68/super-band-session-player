"""Pydantic models for sessions and lanes."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.utils import music_theory as mt


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


class BassEngine(str, Enum):
    baseline = "baseline"
    phrase_v2 = "phrase_v2"


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

    tempo: int | None = Field(
        default=None,
        ge=40,
        le=240,
        description="When set, updates session tempo (BPM).",
    )
    key: str | None = Field(
        default=None,
        min_length=1,
        max_length=4,
        description="When set, updates session key (e.g. C, F#, Bb).",
    )
    scale: str | None = Field(
        default=None,
        min_length=1,
        max_length=32,
        description="When set, updates session scale/mode label.",
    )
    bar_count: int | None = Field(
        default=None,
        ge=1,
        le=128,
        description="When set, updates number of bars in the session.",
    )
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
    chord_progression: list[str] | None = Field(
        default=None,
        description="Optional chord symbols for bass harmony, e.g. Am7 | D7 | Gmaj7 | Cmaj7. Send null or [] to clear.",
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
    bass_engine: BassEngine | None = Field(
        default=None,
        description="Bass generation engine mode (baseline or phrase_v2).",
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

    @field_validator("chord_progression")
    @classmethod
    def validate_chord_progression(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        clean = [str(chord).strip() for chord in v if str(chord).strip()]
        for chord in clean:
            mt.parse_chord_symbol(chord)
        return clean

    @model_validator(mode="after")
    def at_least_one_field(self) -> SessionPatch:
        if not self.model_fields_set:
            raise ValueError(
                "Provide at least one of: tempo, key, scale, bar_count, lead_style, lead_player, bass_style, bass_player, "
                "chord_style, chord_progression, chord_player, drum_style, drum_player, session_preset, "
                "lead_instrument, bass_instrument, chord_instrument, drum_kit, anchor_lane, bass_engine"
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
    chord_progression: list[str] | None = Field(
        default=None,
        description="Optional chord symbols for bass harmony, e.g. Am7 | D7 | Gmaj7 | Cmaj7.",
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
    bass_engine: BassEngine | None = Field(
        default=None,
        description="Bass engine mode; omit for baseline.",
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

    @field_validator("chord_progression")
    @classmethod
    def validate_chord_progression(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        clean = [str(chord).strip() for chord in v if str(chord).strip()]
        for chord in clean:
            mt.parse_chord_symbol(chord)
        return clean


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


class SectionSpan(BaseModel):
    label: str = Field(description="Simple section label (intro/verse/lift/chorus/bridge/outro).")
    start_bar: int = Field(ge=0)
    end_bar: int = Field(ge=0)


_GROOVE_SLOTS = 16


def _clamp01(x: float) -> float:
    if x != x:  # NaN
        return 0.0
    return max(0.0, min(1.0, float(x)))


def _ensure_groove_rows(rows: list[list[float]] | None, n_bars: int) -> list[list[float]]:
    """Exactly ``n_bars`` rows of ``_GROOVE_SLOTS`` floats in 0..1."""
    raw = rows or []
    out: list[list[float]] = []
    for i in range(n_bars):
        if i < len(raw) and raw[i] is not None:
            r = [_clamp01(float(v)) for v in raw[i]][:_GROOVE_SLOTS]
            r.extend([0.0] * (_GROOVE_SLOTS - len(r)))
        else:
            r = [0.0] * _GROOVE_SLOTS
        out.append(r)
    return out


def _ensure_groove_confidence(vals: list[float] | None, n_bars: int) -> list[float]:
    raw = vals or []
    out: list[float] = []
    for i in range(n_bars):
        if i < len(raw):
            out.append(_clamp01(float(raw[i])))
        else:
            out.append(0.0)
    return out


class SourceAnalysis(BaseModel):
    source_lane: str = Field(description="Lane used as analysis source (or 'none').")
    tempo: int = Field(ge=40, le=240)
    tempo_estimate_bpm: float = Field(ge=40.0, le=240.0, description="Evidence-derived tempo estimate from onset spacing.")
    tempo_confidence: float = Field(ge=0.0, le=1.0, description="Confidence in tempo estimate.")
    beat_grid_seconds: list[float] = Field(default_factory=list, description="Quarter-note beat start times in seconds.")
    bar_starts_seconds: list[float] = Field(default_factory=list, description="Bar start times in seconds.")
    beat_phase_offset_beats: int = Field(ge=0, le=3, description="Estimated downbeat phase offset in beats (0..3) relative to timeline start.")
    beat_phase_scores: list[float] = Field(default_factory=list, description="Relative support scores for beat-phase candidates [0,1,2,3].")
    beat_phase_confidence: float = Field(ge=0.0, le=1.0, description="Confidence in selected beat-phase/downbeat anchor.")
    phase_offset_used_for_generation_beats: int = Field(ge=0, le=3, description="Beat-phase offset currently used by context-aware generation.")
    bar_start_anchor_used_seconds: float = Field(ge=0.0, description="Absolute bar-start anchor currently used by context-aware generation.")
    generation_aligned_to_anchor: bool = Field(description="Whether generation was aligned to anchor timing for this analysis pass.")
    downbeat_guess_bar_index: int = Field(ge=0, description="Best-guess downbeat bar index.")
    downbeat_confidence: float = Field(ge=0.0, le=1.0, description="Confidence score for downbeat guess.")
    bar_start_confidence: float = Field(ge=0.0, le=1.0, description="Confidence in bar-start anchor/spacing estimate.")
    tonal_center_pc_guess: int = Field(ge=0, le=11, description="Best-guess tonal center pitch class (0=C).")
    tonal_center_confidence: float = Field(ge=0.0, le=1.0, description="Confidence in tonal-center estimate.")
    scale_mode_guess: str = Field(description="Best-guess scale mode from available tonal evidence.")
    scale_mode_confidence: float = Field(ge=0.0, le=1.0, description="Confidence in scale-mode estimate.")
    sections: list[SectionSpan] = Field(default_factory=list, description="Simple section segmentation over bars.")
    bar_energy: list[float] = Field(default_factory=list, description="Per-bar normalized energy 0..1.")
    bar_accent_profile: list[float] = Field(default_factory=list, description="Per-bar normalized first-beat accent strength 0..1.")
    bar_confidence_profile: list[float] = Field(default_factory=list, description="Per-bar confidence in rhythm/grid evidence 0..1.")
    source_groove_resolution: int = Field(
        default=16,
        ge=1,
        le=64,
        description="Subdivision slots per bar for source groove maps (16 = sixteenths).",
    )
    source_onset_weight: list[list[float]] = Field(
        default_factory=list,
        description="Per-bar sixteenth-slot percussive onset emphasis 0..1.",
    )
    source_kick_weight: list[list[float]] = Field(
        default_factory=list,
        description="Per-bar low-band percussive energy / onset proxy 0..1.",
    )
    source_snare_weight: list[list[float]] = Field(
        default_factory=list,
        description="Per-bar mid/high-band percussive energy / onset proxy 0..1.",
    )
    source_slot_pressure: list[list[float]] = Field(
        default_factory=list,
        description="Combined rhythmic slot pressure 0..1 for conditioning.",
    )
    source_groove_confidence: list[float] = Field(
        default_factory=list,
        description="Per-bar confidence in groove-slot maps 0..1.",
    )
    source_metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional small metadata bag for analysis versioning and diagnostics.",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_source_groove_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        bar_e = out.get("bar_energy") or []
        bar_s = out.get("bar_starts_seconds") or []
        bar_c = out.get("bar_confidence_profile") or []
        bar_a = out.get("bar_accent_profile") or []
        n_bars = max(len(bar_e), len(bar_s), len(bar_c), len(bar_a), 1)
        raw_res = out.get("source_groove_resolution", 16)
        if raw_res is None:
            raw_res = 16
        try:
            res = int(raw_res)
        except (TypeError, ValueError):
            res = 16
        out["source_groove_resolution"] = max(1, min(64, res))
        out["source_onset_weight"] = _ensure_groove_rows(out.get("source_onset_weight"), n_bars)
        out["source_kick_weight"] = _ensure_groove_rows(out.get("source_kick_weight"), n_bars)
        out["source_snare_weight"] = _ensure_groove_rows(out.get("source_snare_weight"), n_bars)
        out["source_slot_pressure"] = _ensure_groove_rows(out.get("source_slot_pressure"), n_bars)
        out["source_groove_confidence"] = _ensure_groove_confidence(out.get("source_groove_confidence"), n_bars)
        return out


class GrooveProfile(BaseModel):
    pocket_feel: str = Field(description="Coarse groove feel tag for future conditioning.")
    syncopation_score: float = Field(ge=0.0, le=1.0)
    density_per_bar_estimate: float = Field(ge=0.0, description="Estimated note density (notes/bar) for model conditioning.")
    accent_strength: float = Field(ge=0.0, le=1.0, description="Mean accent intensity signal from source analysis.")
    confidence: float = Field(ge=0.0, le=1.0, description="Overall confidence in groove-conditioning signals.")


class HarmonyPlanBar(BaseModel):
    bar_index: int = Field(ge=0)
    root_pc: int = Field(ge=0, le=11, description="Per-bar root pitch-class guess (0=C).")
    target_pcs: list[int] = Field(default_factory=list, description="Stable target pitch classes for this bar.")
    passing_pcs: list[int] = Field(default_factory=list, description="Allowed passing pitch classes for this bar.")
    avoid_pcs: list[int] = Field(default_factory=list, description="Pitch classes to avoid for structural tones.")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in per-bar harmonic target.")
    source: str = Field(description="Per-bar source: evidence or scale_fallback.")


class HarmonyPlan(BaseModel):
    key_center: str
    scale: str
    source: str = Field(description="Harmony source: static_session_key_scale or bar_level_targets")
    bars: list[HarmonyPlanBar] = Field(default_factory=list)


class EngineData(BaseModel):
    source_analysis: SourceAnalysis
    groove_profile: GrooveProfile
    harmony_plan: HarmonyPlan


class ReferenceAudioState(BaseModel):
    filename: str
    stored_path: str
    duration_seconds: float = Field(ge=0.0)
    head_trim_seconds: float = Field(ge=0.0)
    analyzed: bool = Field(
        default=False,
        description="True when /analyze-audio has produced source analysis from this reference file.",
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
    chord_progression: list[str] | None = Field(
        default=None,
        description="Optional chord symbols currently used for bass harmony.",
    )
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
    bass_engine: str = Field(
        default="baseline",
        description="Active bass engine mode (baseline or phrase_v2).",
    )
    bass_seed: int | None = Field(
        default=None,
        description="Seed used to render the current bass lane, when generated.",
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
    current_bass_candidate_run_id: str | None = Field(
        default=None,
        description="Run id of currently promoted bass candidate take, if lane came from candidate workflow.",
    )
    current_bass_candidate_take_id: str | None = Field(
        default=None,
        description="Take id of currently promoted bass candidate, if applicable.",
    )
    engine_data: EngineData | None = Field(
        default=None,
        description="Inspectable internal engine analysis used for structured musical context.",
    )
    reference_audio: ReferenceAudioState | None = Field(
        default=None,
        description="Optional uploaded reference audio metadata for audio-driven analysis.",
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


class RegenerateBassBarsBody(BaseModel):
    """Request body for POST /api/sessions/{id}/lanes/bass/regenerate-bars."""

    bar_start: int = Field(description="Inclusive zero-based start bar.")
    bar_end: int = Field(description="Exclusive zero-based end bar.")
    seed: int | None = Field(default=None, description="Optional seed for repeatable bar regeneration.")


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


class GenerateBassCandidatesBody(BaseModel):
    """Request body for POST /api/sessions/{id}/bass-candidates."""

    take_count: int = Field(default=4, ge=2, le=12, description="How many candidate takes to render.")
    seed: int | None = Field(default=None, description="Optional seed root for repeatable candidate sets.")
    clip_id: str | None = Field(
        default=None,
        description="Optional clip/reference id for evaluation bookkeeping.",
    )


class BassCandidateTake(BaseModel):
    take_id: str
    seed: int
    note_count: int = Field(ge=0)
    byte_length: int = Field(ge=0)
    preview: str = ""
    quality_total: float = Field(default=0.0, ge=0.0, le=1.0)
    quality_scores: dict[str, float] = Field(default_factory=dict)
    quality_reason: str = ""
    selection_stage: Literal["strict", "relaxed", "final_fill"] | None = None
    motif_family: str | None = None
    signature_distance: float | None = None
    quality_floor_cutoff: float | None = None
    top_pool_score: float | None = None


class BassCandidateRun(BaseModel):
    run_id: str
    session_id: str
    created_at: str
    take_count: int = Field(ge=1)
    bass_style: str
    bass_engine: str
    bass_player: str | None = None
    bass_instrument: str
    clip_id: str | None = None
    conditioning_tempo: int = Field(ge=40, le=240)
    conditioning_phase_offset: int = Field(ge=0, le=3)
    conditioning_phase_confidence: float = Field(ge=0.0, le=1.0)
    conditioning_sections_count: int = Field(ge=0)
    conditioning_harmonic_bar_count: int = Field(ge=0)
    takes: list[BassCandidateTake] = Field(default_factory=list)


class ExportInfo(BaseModel):
    """JSON response when not downloading binary."""

    session_id: str
    download_urls: dict[str, str]
    note: str = "GET each URL to download that lane's MIDI file."
