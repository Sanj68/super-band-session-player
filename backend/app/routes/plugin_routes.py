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

import copy
import io
from dataclasses import asdict
from typing import Annotated, Literal

import pretty_midi
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.models.session import (
    BassArticulationFocus,
    BassInstrument,
    BassPerformanceControls,
    BassPlayer,
    BassStyle,
    LaneName,
)
from app.routes import session_routes
from app.services import bass_history_store
from app.services.bass_instrument_profiles import (
    public_bass_instrument_profiles,
    resolve_bass_articulation_focus,
)
from app.services.bass_journey_advisor import build_bass_journey_advice
from app.services.bass_performance_controls import (
    resolve_bass_performance_controls,
)

router = APIRouter()


def _history_unavailable(exc: bass_history_store.BassHistoryStoreError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "bass_history_unavailable",
            "message": (
                "Bass idea history could not be read safely. The original file "
                "was preserved for recovery; retry the action to start fresh."
            ),
        },
    )


def _capture_history_or_503(
    session: session_routes.StoredSession,
    *,
    kept: bool = False,
) -> dict[str, object]:
    try:
        return bass_history_store.capture(
            session_routes._durable_session_view(session),  # noqa: SLF001
            kept=kept,
        )
    except bass_history_store.BassHistoryStoreError as exc:
        raise _history_unavailable(exc) from exc


def _history_state_or_503(
    session: session_routes.StoredSession,
) -> dict[str, object]:
    try:
        return bass_history_store.history_state(
            session_routes._durable_session_view(session)  # noqa: SLF001
        )
    except bass_history_store.BassHistoryStoreError as exc:
        raise _history_unavailable(exc) from exc


def _commit_recalled_bass(
    destination: session_routes.StoredSession,
    recalled: session_routes.StoredSession,
) -> session_routes.StoredSession:
    """Publish the fully recalled durable revision in one mapping swap."""

    session_routes._discard_live_bridge_overlay(recalled)  # noqa: SLF001
    return session_routes._publish_staged_session(  # noqa: SLF001
        destination,
        recalled,
    )


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


class PluginPitchBend(BaseModel):
    type: Literal["pitch_bend"] = "pitch_bend"
    channel: Literal[1] = 1
    beat: float
    value: int = Field(ge=-8192, le=8191)


class PluginControlChange(BaseModel):
    type: Literal["control_change"] = "control_change"
    channel: Literal[1] = 1
    beat: float
    controller: int = Field(ge=0, le=127)
    value: int = Field(ge=0, le=127)


PluginAutomationEvent = Annotated[
    PluginPitchBend | PluginControlChange,
    Field(discriminator="type"),
]

_PITCH_RANGE_CC_ORDER = {
    101: 0,  # RPN MSB
    100: 1,  # RPN LSB
    99: 2,  # NRPN MSB
    98: 3,  # NRPN LSB
    6: 4,  # data entry MSB
    38: 5,  # data entry LSB
}


class PluginBassPart(BaseModel):
    version: Literal[2] = 2
    session_id: str
    tempo: int
    key: str
    scale: str
    bar_count: int
    beats_per_bar: int = 4
    source: str = Field(description="clean or performance render")
    preview: str
    lock_to_groove: float | None
    groove_source_ready: bool
    groove_source_frame_count: int = Field(ge=0)
    groove_source_notice: str | None
    bass_articulation_focus: str
    bass_articulation_effective: str
    bass_articulation_notice: str | None
    bass_expression: float
    bass_density_bias: float
    bass_performance_controls: BassPerformanceControls
    bass_performance_controls_effective: BassPerformanceControls
    bass_performance_controls_notice: str | None
    bass_style: str
    bass_instrument: str
    bass_player: str | None
    phase_offset_beats: float = 0.0
    notes: list[PluginNote]
    automation: list[PluginAutomationEvent] = Field(default_factory=list)


def _bass_part_for_session(s: session_routes.StoredSession) -> PluginBassPart:
    """Serialize one already-resolved session for the MIDI FX client."""

    raw = s.bass_performance_bytes or s.bass_bytes
    source = "performance" if s.bass_performance_bytes else "clean"
    assert raw is not None
    pm = pretty_midi.PrettyMIDI(io.BytesIO(raw))
    spb = 60.0 / float(max(1, s.tempo))
    notes: list[PluginNote] = []
    automation: list[PluginAutomationEvent] = []
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
        for cc in inst.control_changes:
            automation.append(
                PluginControlChange(
                    beat=round(float(cc.time) / spb, 6),
                    controller=int(cc.number),
                    value=int(cc.value),
                )
            )
        for bend in inst.pitch_bends:
            automation.append(
                PluginPitchBend(
                    beat=round(float(bend.time) / spb, 6),
                    value=int(bend.pitch),
                )
            )
    notes.sort(key=lambda n: n.start_beats)
    # pretty_midi keeps CC and pitch-bend streams separately, so their
    # cross-stream order at an identical timestamp is not recoverable. Emit
    # RPN/NRPN selection and data-entry setup before the bend value. Python's
    # stable sort preserves source order for ordinary same-time controllers.
    automation.sort(
        key=lambda event: (
            event.beat,
            0 if event.type == "control_change" else 1,
            (
                _PITCH_RANGE_CC_ORDER.get(event.controller, 6)
                if isinstance(event, PluginControlChange)
                else 0
            ),
        )
    )
    requested_touch, effective_touch, articulation_notice = (
        resolve_bass_articulation_focus(
            s.bass_articulation_focus,
            s.bass_instrument,
        )
    )
    performance_controls = resolve_bass_performance_controls(
        s.bass_performance_controls,
        focus=s.bass_articulation_focus,
        expression_amount=s.bass_expression,
        style=s.bass_style,
        instrument_family=s.bass_instrument,
    )
    (
        groove_source_ready,
        groove_source_frame_count,
        groove_source_notice,
    ) = session_routes._groove_source_status(s.id)  # noqa: SLF001
    return PluginBassPart(
        session_id=s.id,
        tempo=int(s.tempo),
        key=s.key,
        scale=s.scale,
        bar_count=int(s.bar_count),
        source=source,
        preview=s.bass_preview or "",
        lock_to_groove=s.bass_lock_to_groove,
        groove_source_ready=groove_source_ready,
        groove_source_frame_count=groove_source_frame_count,
        groove_source_notice=groove_source_notice,
        bass_articulation_focus=requested_touch,
        bass_articulation_effective=effective_touch,
        bass_articulation_notice=articulation_notice,
        bass_expression=float(s.bass_expression),
        bass_density_bias=float(s.bass_density_bias),
        bass_performance_controls=BassPerformanceControls.model_validate(
            performance_controls.requested
        ),
        bass_performance_controls_effective=BassPerformanceControls.model_validate(
            performance_controls.effective
        ),
        bass_performance_controls_notice=performance_controls.notice,
        bass_style=s.bass_style,
        bass_instrument=s.bass_instrument,
        bass_player=s.bass_player,
        phase_offset_beats=float(s.bass_phase_offset_beats),
        notes=notes,
        automation=automation,
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
    force_new_phrase: bool = Field(
        default=False,
        description=(
            "Bypass performance-only re-rendering and write a new clean bass "
            "phrase with a fresh seed."
        ),
    )
    host_tempo: float | None = Field(
        default=None,
        ge=40.0,
        le=240.0,
        description=(
            "Current DAW tempo. Applied only with force_new_phrase so beat "
            "positions and the regenerated MIDI stay coherent."
        ),
    )
    bass_style: BassStyle | None = Field(default=None)
    bass_instrument: BassInstrument | None = Field(default=None)
    bass_player: str | None = Field(
        default=None,
        max_length=64,
        description="Legacy internal profile id.",
    )
    lock_to_groove: float | None = Field(default=None, ge=0.0, le=1.0)
    bass_articulation_focus: BassArticulationFocus | None = Field(default=None)
    bass_expression: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="0 clean/restrained, 0.5 natural, 1 bold style-aware expression.",
    )
    bass_density_bias: float | None = Field(
        default=None,
        ge=-1.0,
        le=1.0,
        description="-1 more space, 0 balanced, 1 busier.",
    )
    bass_performance_controls: BassPerformanceControls | None = Field(
        default=None,
        description=(
            "Independent performance mix: ghosts, mutes, slides, legato, "
            "timing feel, and dynamics."
        ),
    )

    @field_validator("bass_player")
    @classmethod
    def _validate_bass_player(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized in {"", "none"}:
            return "none"
        allowed = {player.value for player in BassPlayer}
        if normalized not in allowed:
            raise ValueError(
                "bass_player must be 'none' or a supported Bass Player profile"
            )
        return normalized


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


def _bass_structure_signature(
    session: session_routes.StoredSession,
) -> tuple[object, ...]:
    """Settings that are allowed to rewrite the structural bass phrase."""

    lock = session.bass_lock_to_groove
    return (
        session.bass_style,
        session.bass_instrument,
        session.bass_player,
        session.bass_engine,
        None if lock is None else round(float(lock), 4),
        round(float(session.bass_density_bias), 4),
        round(float(session.bass_expression), 4),
    )


def _require_unlocked_bass(session: session_routes.StoredSession) -> None:
    if session.bass_locked:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "bass_lane_locked",
                "message": "Unlock the Bass lane before requesting a new take.",
            },
        )


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

    _require_unlocked_bass(s)
    # Commands may surprise musically, never destructively.
    _capture_history_or_503(s)
    staged = copy.deepcopy(s)
    if plan.density_delta:
        staged.bass_density_bias = float(
            max(-1.0, min(1.0, staged.bass_density_bias + plan.density_delta))
        )
    if plan.lock_set is not None:
        staged.bass_lock_to_groove = float(plan.lock_set)
    elif plan.lock_delta:
        current = (
            staged.bass_lock_to_groove
            if staged.bass_lock_to_groove is not None
            else 0.5
        )
        staged.bass_lock_to_groove = float(
            max(0.0, min(1.0, current + plan.lock_delta))
        )
    if plan.player is not None:
        staged.bass_player = None if plan.player == "none" else plan.player
        staged.bass_engine = _engine_for_player(staged.bass_player)
    if plan.style is not None:
        staged.bass_style = plan.style

    if plan.bar_ranges:
        from app.models.session import RegenerateBassBarsBody

        for start, end in plan.bar_ranges:
            session_routes._regenerate_bass_bars_on_stored_session(  # noqa: SLF001
                staged,
                RegenerateBassBarsBody(
                    bar_start=start,
                    bar_end=end,
                    operation=plan.bar_operation,
                ),
            )
    else:
        session_routes._regenerate_lane_on_stored_session(  # noqa: SLF001
            staged,
            LaneName.bass,
            context=session_routes._context_for_lane_regeneration(  # noqa: SLF001
                staged,
                LaneName.bass,
            ),
        )
    session_routes._promote_live_bridge_overlay(staged)  # noqa: SLF001
    session_routes._SESSIONS[s.id] = staged  # noqa: SLF001

    return PluginCommandResult(
        ok=True,
        applied=plan.applied,
        unrecognized=plan.unrecognized,
        message=" · ".join(plan.applied),
        part=_bass_part_for_session(staged),
    )


@router.post("/regenerate", response_model=PluginBassPart)
def plugin_regenerate(body: PluginRegenerateBody) -> PluginBassPart:
    s = _bass_session(body.session_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "no_bass_part"})
    _require_unlocked_bass(s)
    # Preserve the exact playable part before changing controls or MIDI.
    _capture_history_or_503(s)
    staged = copy.deepcopy(s)
    prior_structure = _bass_structure_signature(staged)
    if body.bass_style is not None:
        staged.bass_style = body.bass_style.value
    if body.bass_instrument is not None:
        staged.bass_instrument = body.bass_instrument.value
    if body.bass_player is not None:
        staged.bass_player = (
            None
            if body.bass_player.strip().lower() in ("", "none")
            else body.bass_player
        )
    if body.lock_to_groove is not None:
        staged.bass_lock_to_groove = float(body.lock_to_groove)
    if body.bass_articulation_focus is not None:
        staged.bass_articulation_focus = body.bass_articulation_focus.value
    if body.bass_expression is not None:
        staged.bass_expression = float(body.bass_expression)
    if body.bass_density_bias is not None:
        staged.bass_density_bias = float(body.bass_density_bias)
    if body.bass_performance_controls is not None:
        staged.bass_performance_controls = (
            body.bass_performance_controls.model_dump(mode="python")
        )
    if body.force_new_phrase and body.host_tempo is not None:
        staged.tempo = int(round(float(body.host_tempo)))
    staged.bass_engine = _engine_for_player(staged.bass_player)
    raw_context = session_routes._context_for_lane_regeneration(  # noqa: SLF001
        staged,
        LaneName.bass,
    )
    if (
        not body.force_new_phrase
        and _bass_structure_signature(staged) == prior_structure
        and staged.bass_seed is not None
        and staged.bass_bytes
    ):
        session_routes._rerender_current_bass_performance(  # noqa: SLF001
            staged,
            context=(
                raw_context
                if isinstance(raw_context, session_routes.SessionAnchorContext)
                else None
            ),
        )
    else:
        session_routes._regenerate_lane_on_stored_session(  # noqa: SLF001
            staged,
            LaneName.bass,
            context=raw_context,
        )
    session_routes._promote_live_bridge_overlay(staged)  # noqa: SLF001
    session_routes._SESSIONS[s.id] = staged  # noqa: SLF001
    return _bass_part_for_session(staged)


class PluginHistoryEntry(BaseModel):
    snapshot_id: str
    created_at: str
    kept: bool
    bass_style: str
    bass_instrument: str


class PluginHistoryState(BaseModel):
    session_id: str
    count: int
    kept_count: int
    current_index: int | None
    current_is_kept: bool
    can_previous: bool
    can_next: bool
    entries: list[PluginHistoryEntry]


class PluginHistoryActionResult(BaseModel):
    message: str
    history: PluginHistoryState
    part: PluginBassPart


class PluginHistoryBody(BaseModel):
    session_id: str | None = Field(
        default=None,
        description="Persistent session binding owned by this plugin instance.",
    )


class PluginHistoryNavigateBody(PluginHistoryBody):
    direction: Literal["previous", "next"]


class PluginHistoryRecallBody(PluginHistoryBody):
    snapshot_id: str = Field(min_length=1, max_length=128)


@router.get("/history", response_model=PluginHistoryState)
def plugin_history(session_id: str | None = None) -> PluginHistoryState:
    s = _bass_session(session_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "no_bass_part"})
    return PluginHistoryState.model_validate(_history_state_or_503(s))


@router.post("/keep", response_model=PluginHistoryActionResult)
def plugin_keep(body: PluginHistoryBody) -> PluginHistoryActionResult:
    s = _bass_session(body.session_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "no_bass_part"})
    _capture_history_or_503(s, kept=True)
    return PluginHistoryActionResult(
        message="Idea kept. You can explore and return to it.",
        history=PluginHistoryState.model_validate(
            _history_state_or_503(s)
        ),
        part=_bass_part_for_session(s),
    )


@router.post("/history/navigate", response_model=PluginHistoryActionResult)
def plugin_history_navigate(
    body: PluginHistoryNavigateBody,
) -> PluginHistoryActionResult:
    s = _bass_session(body.session_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "no_bass_part"})
    staged = session_routes._durable_session_view(s)  # noqa: SLF001
    try:
        bass_history_store.navigate(staged, body.direction)
    except bass_history_store.BassHistoryStoreError as exc:
        raise _history_unavailable(exc) from exc
    except IndexError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "history_boundary",
                "direction": body.direction,
                "message": f"No {body.direction} bass idea is available.",
            },
        ) from exc
    s = _commit_recalled_bass(s, staged)
    return PluginHistoryActionResult(
        message=(
            "Recalled earlier bass idea."
            if body.direction == "previous"
            else "Recalled later bass idea."
        ),
        history=PluginHistoryState.model_validate(
            _history_state_or_503(s)
        ),
        part=_bass_part_for_session(s),
    )


@router.post("/history/recall", response_model=PluginHistoryActionResult)
def plugin_history_recall(
    body: PluginHistoryRecallBody,
) -> PluginHistoryActionResult:
    s = _bass_session(body.session_id)
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "no_bass_part"})
    # Preserve an unlisted current state before jumping to a specific idea.
    _capture_history_or_503(s)
    staged = session_routes._durable_session_view(s)  # noqa: SLF001
    try:
        bass_history_store.recall(staged, body.snapshot_id)
    except bass_history_store.BassHistoryStoreError as exc:
        raise _history_unavailable(exc) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": "history_snapshot_not_found",
                "snapshot_id": body.snapshot_id,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "history_snapshot_invalid",
                "snapshot_id": body.snapshot_id,
            },
        ) from exc
    s = _commit_recalled_bass(s, staged)
    return PluginHistoryActionResult(
        message="Recalled kept bass idea.",
        history=PluginHistoryState.model_validate(
            _history_state_or_503(s)
        ),
        part=_bass_part_for_session(s),
    )


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
