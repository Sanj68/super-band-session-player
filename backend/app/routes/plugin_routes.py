"""Tiny dedicated surface for the Session Player MIDI FX plugin (v0.6).

The plugin is intentionally dumb: it asks "what's the current bass part?"
and plays it in sync with the host transport, and it can ask for a
regenerate with a style / lock tweak. Contract kept minimal so the plugin
never grows session-management UI (the product surface is style options
and one knob — BUILD_NOTES §6b).

Session selection: the most recently CREATED session that has a bass
part. The producer's working session is, in practice, the newest one.
"""

from __future__ import annotations

import io
from dataclasses import asdict

import pretty_midi
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.session import BassInstrument, LaneName, RegenerateSelectedBody
from app.routes import session_routes
from app.services.bass_instrument_profiles import public_bass_instrument_profiles
from app.services.bass_journey_advisor import build_bass_journey_advice

router = APIRouter()


def _latest_bass_session() -> session_routes.StoredSession | None:
    candidates = [
        s for s in session_routes._SESSIONS.values()  # noqa: SLF001
        if s.bass_bytes or s.bass_performance_bytes
    ]
    if not candidates:
        return None
    # dict preserves insertion order; the last created wins
    return candidates[-1]


def _bass_session(session_id: str | None = None) -> session_routes.StoredSession | None:
    """Resolve an explicitly bound plugin session, or bind an unbound plugin once."""

    if session_id:
        candidate = session_routes._SESSIONS.get(session_id)  # noqa: SLF001
        if candidate is None or not (candidate.bass_bytes or candidate.bass_performance_bytes):
            return None
        return candidate
    return _latest_bass_session()


class PluginNote(BaseModel):
    pitch: int
    velocity: int
    start_beats: float
    dur_beats: float


class PluginBassPart(BaseModel):
    session_id: str
    tempo: int
    bar_count: int
    beats_per_bar: int = 4
    source: str = Field(description="clean or performance render")
    preview: str
    lock_to_groove: float | None
    bass_expression: float
    bass_style: str
    bass_instrument: str
    bass_player: str | None
    notes: list[PluginNote]


def _bass_part_for_session(s: session_routes.StoredSession) -> PluginBassPart:
    """Serialize one already-resolved session for the MIDI FX client."""

    raw = s.bass_performance_bytes or s.bass_bytes
    source = "performance" if s.bass_performance_bytes else "clean"
    assert raw is not None
    pm = pretty_midi.PrettyMIDI(io.BytesIO(raw))
    spb = 60.0 / float(max(1, s.tempo))
    notes: list[PluginNote] = []
    for inst in pm.instruments:
        for n in inst.notes:
            notes.append(
                PluginNote(
                    pitch=int(n.pitch),
                    velocity=int(n.velocity),
                    start_beats=round(float(n.start) / spb, 6),
                    dur_beats=round(max(0.01, float(n.end) - float(n.start)) / spb, 6),
                )
            )
    notes.sort(key=lambda n: n.start_beats)
    return PluginBassPart(
        session_id=s.id,
        tempo=int(s.tempo),
        bar_count=int(s.bar_count),
        source=source,
        preview=s.bass_preview or "",
        lock_to_groove=s.bass_lock_to_groove,
        bass_expression=float(s.bass_expression),
        bass_style=s.bass_style,
        bass_instrument=s.bass_instrument,
        bass_player=s.bass_player,
        notes=notes,
    )


@router.get("/bass-part", response_model=PluginBassPart)
def get_bass_part(session_id: str | None = None) -> PluginBassPart:
    s = _bass_session(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "no_bass_part"})
    return _bass_part_for_session(s)


class PluginRegenerateBody(BaseModel):
    session_id: str | None = Field(
        default=None,
        description="Persistent session binding owned by this plugin instance.",
    )
    bass_style: str | None = Field(default=None)
    bass_instrument: BassInstrument | None = Field(default=None)
    bass_player: str | None = Field(default=None, description="Legacy internal profile id.")
    lock_to_groove: float | None = Field(default=None, ge=0.0, le=1.0)
    bass_expression: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="0 clean/restrained, 0.5 natural, 1 bold style-aware expression.",
    )


# Persona -> engine routing. The two bass engines deliberately stay separate
# (BUILD_NOTES §6: no consolidation before it's provably painful), but their
# strengths differ: phrase_v2 carries the reference lock and the dedicated
# pino/chambers paths; the baseline engine carries the rich
# jamerson/jaco/bootsy/marcus vocabularies (measured 06-12: baseline
# jamerson = 14 notes / 5 pitch classes / syncopated 16ths vs 11/3/straight
# through phrase_v2's generic path — the "result is quite basic" report).
_PHRASE_V2_PERSONAS = {None, "pino", "paul_chambers"}


def _engine_for_player(player: str | None) -> str:
    return "phrase_v2" if player in _PHRASE_V2_PERSONAS else "baseline"


class PluginCommandBody(BaseModel):
    session_id: str | None = Field(
        default=None,
        description="Persistent session binding owned by this plugin instance.",
    )
    text: str = Field(min_length=1, max_length=400)


class PluginCommandResult(BaseModel):
    ok: bool
    applied: list[str]
    unrecognized: list[str]
    message: str
    part: PluginBassPart | None


@router.post("/command", response_model=PluginCommandResult)
def plugin_command(body: PluginCommandBody) -> PluginCommandResult:
    """The prompting layer (v0.7): natural language -> engine operations.

    "make it busier" / "like jamerson" / "tighter" / "turnaround on the
    4th bar" / "new take". Deterministic grammar (command_parser); anything
    not understood is reported back honestly, never guessed at.
    """
    from app.services.command_parser import parse_command

    s = _bass_session(body.session_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "no_bass_part"})

    plan = parse_command(body.text, bar_count=int(s.bar_count))
    if not plan.has_ops:
        return PluginCommandResult(
            ok=False,
            applied=[],
            unrecognized=plan.unrecognized,
            message=(
                "didn't catch that — try: busier · more space · tighter · looser · "
                "like jamerson/pino/jaco/bootsy/marcus/chambers · funkier/smoother/slap · "
                "redo bar 2 · turnaround on the 4th bar · new take"
            ),
            part=None,
        )

    if plan.density_delta:
        s.bass_density_bias = float(max(-1.0, min(1.0, s.bass_density_bias + plan.density_delta)))
    if plan.lock_set is not None:
        s.bass_lock_to_groove = float(plan.lock_set)
    elif plan.lock_delta:
        current = s.bass_lock_to_groove if s.bass_lock_to_groove is not None else 0.5
        s.bass_lock_to_groove = float(max(0.0, min(1.0, current + plan.lock_delta)))
    if plan.player is not None:
        s.bass_player = None if plan.player == "none" else plan.player
        s.bass_engine = _engine_for_player(s.bass_player)
    if plan.style is not None:
        s.bass_style = plan.style

    if plan.bar_ranges:
        from app.models.session import RegenerateBassBarsBody

        for start, end in plan.bar_ranges:
            session_routes.regenerate_bass_bars(
                s.id,
                RegenerateBassBarsBody(
                    bar_start=start,
                    bar_end=end,
                    operation=plan.bar_operation,
                ),
            )
    else:
        session_routes.regenerate_selected(s.id, RegenerateSelectedBody(lanes=[LaneName.bass]))

    return PluginCommandResult(
        ok=True,
        applied=plan.applied,
        unrecognized=plan.unrecognized,
        message=" · ".join(plan.applied),
        part=_bass_part_for_session(s),
    )


@router.post("/regenerate", response_model=PluginBassPart)
def plugin_regenerate(body: PluginRegenerateBody) -> PluginBassPart:
    s = _bass_session(body.session_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "no_bass_part"})
    if body.bass_style is not None:
        s.bass_style = body.bass_style
    if body.bass_instrument is not None:
        s.bass_instrument = body.bass_instrument.value
    if body.bass_player is not None:
        s.bass_player = None if body.bass_player.strip().lower() in ("", "none") else body.bass_player
    if body.lock_to_groove is not None:
        s.bass_lock_to_groove = float(body.lock_to_groove)
    if body.bass_expression is not None:
        s.bass_expression = float(body.bass_expression)
    s.bass_engine = _engine_for_player(s.bass_player)
    session_routes.regenerate_selected(s.id, RegenerateSelectedBody(lanes=[LaneName.bass]))
    return _bass_part_for_session(s)


class PluginInstrumentProfile(BaseModel):
    id: str
    label: str
    description: str
    density_ceiling: float
    expression_ceiling: float
    connected_bias: float
    allow_ghost: bool
    allow_dead: bool
    allow_grace: bool
    sustain_multiplier: float
    timing_scale: float


class PluginSourceTrait(BaseModel):
    id: str
    label: str
    strength: float
    confidence: float
    evidence: str


class PluginStyleOrientation(BaseModel):
    id: str
    label: str
    strength: float
    confidence: float


class PluginJourneyPath(BaseModel):
    id: str
    label: str
    summary: str
    preserves: list[str]
    changes: list[str]
    suggested_style: str
    suggested_expression: float
    suggested_lock_to_groove: float
    suggested_density_bias: float


class PluginJourneyAdvice(BaseModel):
    summary: str
    confidence: float
    confidence_label: str
    instrument_profile: PluginInstrumentProfile
    traits: list[PluginSourceTrait]
    style_orientation: list[PluginStyleOrientation]
    paths: list[PluginJourneyPath]
    advisory_only: bool


@router.get("/instrument-profiles", response_model=list[PluginInstrumentProfile])
def plugin_instrument_profiles() -> list[PluginInstrumentProfile]:
    return [
        PluginInstrumentProfile.model_validate(asdict(profile))
        for profile in public_bass_instrument_profiles()
    ]


@router.get("/advice", response_model=PluginJourneyAdvice)
def plugin_journey_advice(
    session_id: str | None = None,
    bass_instrument: BassInstrument | None = None,
) -> PluginJourneyAdvice:
    """Return evidence-backed, non-destructive musical routes."""

    s = _bass_session(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "no_bass_part"})
    selected_instrument = (
        bass_instrument.value if bass_instrument is not None else s.bass_instrument
    )
    conditioning = session_routes._conditioning_for_generation(s, context=None)  # noqa: SLF001
    assert conditioning is not None
    advice = build_bass_journey_advice(
        conditioning,
        instrument_family=selected_instrument,
    )
    return PluginJourneyAdvice.model_validate(asdict(advice))
