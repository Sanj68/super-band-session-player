"""Session CRUD, generation, regeneration, and MIDI export routes."""

from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import io
import json
import random
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Literal

import pretty_midi
from fastapi import APIRouter, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ValidationError

from app.models.session import (
    AddPartToSuitBody,
    BassCandidateRun,
    BassCandidateTake,
    BassPerformanceControls,
    EngineData,
    GenerateBassCandidatesBody,
    GenerateAroundAnchorBody,
    GenerateResult,
    LaneLocksPatch,
    LaneNote,
    LaneName,
    LaneState,
    ReferenceAudioState,
    RegenerateLaneResult,
    RegenerateBassBarsBody,
    RegenerateSelectedBody,
    SessionCreate,
    SessionCreated,
    SessionPatch,
    SessionState,
    SourceAnalysis,
    lane_styles_for_session_preset,
)
from app.routes.midi_routes import get_audition_player
from app.services import (
    bass_candidate_store,
    bass_history_store,
    bridge_store,
    generator,
)
from app.services.bass_bar_splice import splice_bass_bars
from app.services.bass_candidate_roles import (
    ROLE_ORDER,
    bass_candidate_role_for_index,
    bass_candidate_role_spec,
)
from app.services.bass_loop_boundary import normalize_bass_loop_bytes
from app.services.bass_vocabulary.candidates import generate_vocabulary_candidates
from app.services.bass_performance import (
    BassPerformanceNote,
    infer_bass_articulations,
)
from app.services.bass_performance_controls import (
    resolve_bass_performance_controls,
)
from app.services.bass_performance_render import render_performance_bass_midi
from app.services.bass_instrument_profiles import resolve_bass_articulation_focus
from app.services.conditioning import UnifiedConditioning, build_unified_conditioning
from app.services.audio_source_analysis import analyze_reference_audio
from app.services.bass_quality import analyze_bass_take, count_unsupported_structural_notes
from app.services.midi_note_extract import extract_lane_notes
from app.services.lead_generator import normalize_lead_style
from app.services.midi_export import (
    apply_loop_phase_offset,
    lane_midi_response,
    merge_lane_midis,
    zip_all_lanes,
)
from app.services.midi_audition import MidiOutputUnavailable
from app.services.source_analysis import build_groove_profile, build_harmony_plan, build_source_analysis
from app.services.source_analysis_fusion import fuse_source_and_groove
from app.services.session_context import SessionAnchorContext, build_session_context, normalize_anchor_lane
from app.utils import music_theory as mt

router = APIRouter()


class AuditionBassBody(BaseModel):
    output: str
    mode: Literal["clean", "performance"] | None = None


class AuditionBassResponse(BaseModel):
    status: str
    session_id: str
    mode: Literal["clean", "performance"]
    output: str
    duration_seconds: float

_LANE_REGENERATION_ORDER: Final[tuple[LaneName, ...]] = (
    LaneName.drums,
    LaneName.bass,
    LaneName.chords,
    LaneName.lead,
)


def _requested_lanes_stable(lanes: list[LaneName]) -> list[LaneName]:
    """Return the subset of ``lanes`` in fixed order: drums, bass, chords, lead."""
    wanted = frozenset(lanes)
    return [lane for lane in _LANE_REGENERATION_ORDER if lane in wanted]


_DEFAULT_LEAD_INSTRUMENT = "flute"
_DEFAULT_BASS_INSTRUMENT = "finger_bass"
_DEFAULT_CHORD_INSTRUMENT = "piano"
_DEFAULT_DRUM_KIT = "standard"
_REFERENCE_AUDIO_ROOT = Path(__file__).resolve().parents[2] / "data" / "reference_audio"
_ALLOWED_REFERENCE_EXTS: Final[set[str]] = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
_CANDIDATE_GENERATION_CONTEXT_VERSION: Final[int] = 4


def _new_bass_seed() -> int:
    return random.randint(1, 2_000_000_000)


@dataclass
class StoredSession:
    id: str
    tempo: int
    key: str
    scale: str
    bar_count: int
    session_preset: str | None = None
    lead_style: str = "melodic"
    bass_style: str = "supportive"
    chord_style: str = "simple"
    chord_progression: list[str] | None = None
    drum_style: str = "straight"
    lead_instrument: str = _DEFAULT_LEAD_INSTRUMENT
    lead_player: str | None = None
    bass_instrument: str = _DEFAULT_BASS_INSTRUMENT
    bass_player: str | None = None
    bass_engine: str = "baseline"
    bass_lock_to_groove: float | None = None
    bass_articulation_focus: str = "natural"
    bass_expression: float = 0.5
    bass_performance_controls: dict[str, float] | None = None
    bass_phase_offset_beats: float = 0.0
    bass_density_bias: float = 0.0
    bass_seed: int | None = None
    drum_player: str | None = None
    chord_instrument: str = _DEFAULT_CHORD_INSTRUMENT
    chord_player: str | None = None
    drum_kit: str = _DEFAULT_DRUM_KIT
    drum_bytes: bytes | None = None
    bass_bytes: bytes | None = None
    bass_performance_bytes: bytes | None = None
    chords_bytes: bytes | None = None
    lead_bytes: bytes | None = None
    drum_preview: str = ""
    bass_preview: str = ""
    chords_preview: str = ""
    lead_preview: str = ""
    drum_locked: bool = False
    bass_locked: bool = False
    chords_locked: bool = False
    lead_locked: bool = False
    anchor_lane: str | None = None
    reference_audio_path: str | None = None
    reference_audio_filename: str | None = None
    reference_audio_uploaded_at: str | None = None
    reference_audio_duration_seconds: float = 0.0
    reference_audio_head_trim_seconds: float = 0.0
    source_analysis_override: object | None = None
    groove_reference_audio_path: str | None = None
    groove_reference_audio_filename: str | None = None
    groove_reference_audio_uploaded_at: str | None = None
    groove_reference_audio_duration_seconds: float = 0.0
    groove_reference_audio_head_trim_seconds: float = 0.0
    groove_reference_analysis_override: object | None = None
    harmony_confirmation_required: bool = False
    harmony_key_confirmed_by_user: bool = False
    harmony_map_confirmation_required: bool = False
    suggested_chord_progression: list[str] | None = None
    suggested_chord_confidence: list[float] | None = None
    harmony_map_source: str = "none"
    current_bass_candidate_run_id: str | None = None
    current_bass_candidate_take_id: str | None = None
    # Live Logic analyser evidence is an in-memory overlay. These fields keep
    # the durable base available for persistence and candidate-staleness
    # checks until /commit-source-groove explicitly promotes the overlay.
    bridge_live_overlay_active: bool = False
    bridge_live_base_source_analysis_override: object | None = None
    bridge_live_base_key: str | None = None
    bridge_live_base_scale: str | None = None


def _durable_source_analysis(s: StoredSession) -> object | None:
    if s.bridge_live_overlay_active:
        return s.bridge_live_base_source_analysis_override
    return s.source_analysis_override


def _durable_key_scale(s: StoredSession) -> tuple[str, str]:
    if not s.bridge_live_overlay_active:
        return s.key, s.scale
    return (
        s.bridge_live_base_key or s.key,
        s.bridge_live_base_scale or s.scale,
    )


def _discard_live_bridge_overlay(s: StoredSession) -> None:
    """Restore the durable base and clear transient analyser evidence."""

    if not s.bridge_live_overlay_active:
        return
    s.source_analysis_override = s.bridge_live_base_source_analysis_override
    if s.bridge_live_base_key is not None:
        s.key = s.bridge_live_base_key
    if s.bridge_live_base_scale is not None:
        s.scale = s.bridge_live_base_scale
    s.bridge_live_overlay_active = False
    s.bridge_live_base_source_analysis_override = None
    s.bridge_live_base_key = None
    s.bridge_live_base_scale = None


def _durable_session_view(s: StoredSession) -> StoredSession:
    """Copy a session as it would be restored from the durable snapshot."""

    view = deepcopy(s)
    _discard_live_bridge_overlay(view)
    return view


def _promote_live_bridge_overlay(s: StoredSession) -> None:
    """Make the effective live context durable after successful consumption."""

    if not s.bridge_live_overlay_active:
        return
    s.bridge_live_overlay_active = False
    s.bridge_live_base_source_analysis_override = None
    s.bridge_live_base_key = None
    s.bridge_live_base_scale = None


def _publish_staged_session(
    current: StoredSession,
    staged: StoredSession,
) -> StoredSession:
    """Atomically replace one complete session revision in the live mapping."""

    if staged.id != current.id:
        raise ValueError("Cannot publish a staged session under a different id")
    _SESSIONS[current.id] = staged
    return staged


def _stable_candidate_value(value: object | None) -> object | None:
    """Return a deterministic JSON-compatible view of stored analysis overrides."""
    if value is None:
        return None
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (dict, list, tuple, str, int, float, bool)):
        return value
    attrs = getattr(value, "__dict__", None)
    if isinstance(attrs, dict):
        return attrs
    return str(value)


def _midi_context_digest(data: bytes | None) -> str | None:
    return hashlib.sha256(data).hexdigest() if data else None


def _candidate_generation_context_fingerprint(s: StoredSession) -> str:
    """Hash durable structural inputs that make a take safe to promote."""
    anchor_lane = normalize_anchor_lane(s.anchor_lane)
    durable_key, durable_scale = _durable_key_scale(s)
    payload = {
        "version": _CANDIDATE_GENERATION_CONTEXT_VERSION,
        "musical_settings": {
            "tempo": s.tempo,
            "key": durable_key,
            "scale": durable_scale,
            "bar_count": s.bar_count,
            "session_preset": s.session_preset,
            "chord_progression": list(s.chord_progression or []),
            "bass_style": s.bass_style,
            "bass_instrument": s.bass_instrument,
            "bass_player": s.bass_player,
            "bass_engine": s.bass_engine,
            "bass_lock_to_groove": s.bass_lock_to_groove,
            "bass_articulation_focus": s.bass_articulation_focus,
            "bass_density_bias": s.bass_density_bias,
            "bass_expression": s.bass_expression,
            "bass_performance_controls": (
                dict(s.bass_performance_controls)
                if s.bass_performance_controls is not None
                else None
            ),
            "anchor_lane": anchor_lane,
        },
        "lane_midi": {
            "drums": _midi_context_digest(s.drum_bytes),
            # Bass MIDI is an upstream input only when bass itself is the anchor.
            "bass_anchor": (
                _midi_context_digest(s.bass_bytes)
                if anchor_lane == LaneName.bass.value
                else None
            ),
            "chords": _midi_context_digest(s.chords_bytes),
            "lead": _midi_context_digest(s.lead_bytes),
        },
        "reference_audio": {
            "path": s.reference_audio_path,
            "filename": s.reference_audio_filename,
            "uploaded_at": s.reference_audio_uploaded_at,
            "duration_seconds": s.reference_audio_duration_seconds,
            "head_trim_seconds": s.reference_audio_head_trim_seconds,
            "analysis": _stable_candidate_value(
                _durable_source_analysis(s)
            ),
        },
        "groove_reference_audio": {
            "path": s.groove_reference_audio_path,
            "filename": s.groove_reference_audio_filename,
            "uploaded_at": s.groove_reference_audio_uploaded_at,
            "duration_seconds": s.groove_reference_audio_duration_seconds,
            "head_trim_seconds": s.groove_reference_audio_head_trim_seconds,
            "analysis": _stable_candidate_value(
                s.groove_reference_analysis_override
            ),
        },
        "harmony_gate": {
            "confirmation_required": s.harmony_confirmation_required,
            "key_confirmed_by_user": s.harmony_key_confirmed_by_user,
            "map_confirmation_required": s.harmony_map_confirmation_required,
            "suggested_progression": list(s.suggested_chord_progression or []),
            "suggested_confidence": list(s.suggested_chord_confidence or []),
            "map_source": s.harmony_map_source,
        },
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_generation_evidence_payload(
    s: StoredSession,
) -> dict[str, object | None]:
    """Freeze the exact tonal/analysis evidence used to render a run."""

    return {
        "key": s.key,
        "scale": s.scale,
        "source_analysis_override": _stable_candidate_value(
            s.source_analysis_override
        ),
        "groove_reference_analysis_override": _stable_candidate_value(
            s.groove_reference_analysis_override
        ),
    }


def _candidate_generation_evidence_fingerprint(
    payload: dict[str, object | None],
) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_SESSIONS: dict[str, StoredSession] = {}

_SUIT_PART_MESSAGES: dict[str, str] = {
    "solo": "Generated a solo lead to suit the current session.",
    "counter": "Generated a counter lead to suit the current session.",
    "sparse_fill": "Generated a sparse fill lead to suit the current session.",
}


def _notes_per_bar(data: bytes | None, bar_count: int) -> float:
    """Approximate note density (notes / bar) for context-aware lead ideas."""
    bc = max(bar_count, 1)
    n = len(extract_lane_notes(data))
    return n / float(bc)


def _lane_locked(s: StoredSession, lane: LaneName) -> bool:
    if lane == LaneName.drums:
        return s.drum_locked
    if lane == LaneName.bass:
        return s.bass_locked
    if lane == LaneName.chords:
        return s.chords_locked
    return s.lead_locked


def _lane_has_midi(s: StoredSession, lane: LaneName) -> bool:
    if lane == LaneName.drums:
        return bool(s.drum_bytes)
    if lane == LaneName.bass:
        return bool(s.bass_bytes)
    if lane == LaneName.chords:
        return bool(s.chords_bytes)
    return bool(s.lead_bytes)


def _lane_states(s: StoredSession) -> dict[str, LaneState]:
    def lane_state(
        name: LaneName,
        preview: str,
        data: bytes | None,
        locked: bool,
    ) -> LaneState:
        gen = data is not None
        return LaneState(
            name=name,
            preview=preview or "—",
            generated=gen,
            locked=locked,
            notes=extract_lane_notes(data) if gen else [],
        )

    return {
        "drums": lane_state(LaneName.drums, s.drum_preview or "—", s.drum_bytes, s.drum_locked),
        "bass": lane_state(LaneName.bass, s.bass_preview or "—", s.bass_bytes, s.bass_locked),
        "chords": lane_state(LaneName.chords, s.chords_preview or "—", s.chords_bytes, s.chords_locked),
        "lead": lane_state(LaneName.lead, s.lead_preview or "—", s.lead_bytes, s.lead_locked),
    }


def _groove_source_status(
    session_id: str,
) -> tuple[bool, int, str | None]:
    """Describe live beat evidence without confusing it with harmony capture."""

    bridge_state = bridge_store.get_bridge_state(session_id)
    try:
        frame_count = max(0, int(bridge_state.get("frame_count", 0)))
    except (TypeError, ValueError):
        frame_count = 0
    if frame_count:
        return True, frame_count, None
    return (
        False,
        0,
        (
            "No beat captured - put Session Player Bridge on the beat/drum "
            "track and play it. NEW BEAT / RESET can make a fresh phrase, "
            "but it cannot match that beat yet."
        ),
    )


def _to_state(s: StoredSession, message: str | None = None) -> SessionState:
    # Legacy harmony-gate hydration is a derived presentation concern. Never
    # let a GET or response serializer mutate the published durable object.
    s = replace(s)
    _sync_harmony_map_gate(s)
    ctx = build_session_context(s)
    musical_src = s.source_analysis_override if s.source_analysis_override is not None else build_source_analysis(s, context=ctx)
    src = fuse_source_and_groove(musical_src, s.groove_reference_analysis_override)
    groove = build_groove_profile(src, context=ctx)
    harmony = build_harmony_plan(s, src)
    _conditioning = build_unified_conditioning(
        session=s,
        source=src,
        groove=groove,
        harmony=harmony,
        context=ctx,
    )
    _ = _conditioning  # explicit creation keeps one shared conditioning source available during state assembly
    ref_audio: ReferenceAudioState | None = None
    if s.reference_audio_path and s.reference_audio_filename:
        ref_audio = ReferenceAudioState(
            filename=s.reference_audio_filename,
            stored_path=s.reference_audio_path,
            duration_seconds=float(max(0.0, s.reference_audio_duration_seconds)),
            head_trim_seconds=float(max(0.0, s.reference_audio_head_trim_seconds)),
            analyzed=s.source_analysis_override is not None,
        )
    groove_ref_audio: ReferenceAudioState | None = None
    if s.groove_reference_audio_path and s.groove_reference_audio_filename:
        groove_ref_audio = ReferenceAudioState(
            filename=s.groove_reference_audio_filename,
            stored_path=s.groove_reference_audio_path,
            duration_seconds=float(max(0.0, s.groove_reference_audio_duration_seconds)),
            head_trim_seconds=float(max(0.0, s.groove_reference_audio_head_trim_seconds)),
            analyzed=s.groove_reference_analysis_override is not None,
        )
    requested_touch, effective_touch, articulation_notice = (
        resolve_bass_articulation_focus(
            s.bass_articulation_focus,
            s.bass_instrument,
        )
    )
    performance_controls = resolve_bass_performance_controls(
        s.bass_performance_controls,
        focus=requested_touch,
        expression_amount=s.bass_expression,
        style=s.bass_style,
        instrument_family=s.bass_instrument,
    )
    (
        groove_source_ready,
        groove_source_frame_count,
        groove_source_notice,
    ) = _groove_source_status(s.id)
    return SessionState(
        id=s.id,
        tempo=s.tempo,
        key=s.key,
        scale=s.scale,
        bar_count=s.bar_count,
        session_preset=s.session_preset,
        lead_style=s.lead_style,
        bass_style=s.bass_style,
        chord_style=s.chord_style,
        chord_progression=s.chord_progression,
        drum_style=s.drum_style,
        lead_instrument=s.lead_instrument,
        lead_player=s.lead_player,
        bass_instrument=s.bass_instrument,
        bass_player=s.bass_player,
        bass_engine=s.bass_engine,
        bass_lock_to_groove=s.bass_lock_to_groove,
        groove_source_ready=groove_source_ready,
        groove_source_frame_count=groove_source_frame_count,
        groove_source_notice=groove_source_notice,
        bass_articulation_focus=requested_touch,
        bass_articulation_effective=effective_touch,
        bass_articulation_notice=articulation_notice,
        bass_expression=s.bass_expression,
        bass_performance_controls=BassPerformanceControls.model_validate(
            performance_controls.requested
        ),
        bass_performance_controls_effective=BassPerformanceControls.model_validate(
            performance_controls.effective
        ),
        bass_performance_controls_notice=performance_controls.notice,
        bass_density_bias=s.bass_density_bias,
        bass_phase_offset_beats=s.bass_phase_offset_beats,
        bass_performance_available=s.bass_performance_bytes is not None,
        bass_seed=s.bass_seed,
        drum_player=s.drum_player,
        chord_instrument=s.chord_instrument,
        chord_player=s.chord_player,
        drum_kit=s.drum_kit,
        anchor_lane=s.anchor_lane,
        current_bass_candidate_run_id=s.current_bass_candidate_run_id,
        current_bass_candidate_take_id=s.current_bass_candidate_take_id,
        engine_data=EngineData(
            source_analysis=src,
            groove_profile=groove,
            harmony_plan=harmony,
        ),
        reference_audio=ref_audio,
        groove_reference_audio=groove_ref_audio,
        harmony_confirmation_required=s.harmony_confirmation_required,
        harmony_map_confirmation_required=s.harmony_map_confirmation_required,
        suggested_chord_progression=(
            list(s.suggested_chord_progression)
            if s.suggested_chord_progression is not None
            else None
        ),
        suggested_chord_confidence=(
            list(s.suggested_chord_confidence)
            if s.suggested_chord_confidence is not None
            else None
        ),
        harmony_map_source=s.harmony_map_source,
        lanes=_lane_states(s),
        message=message,
    )


def _get_session_or_404(session_id: str) -> StoredSession:
    s = _SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "session_not_found", "id": session_id})
    return s


def _sync_harmony_map_gate(s: StoredSession) -> None:
    """Harden restored pre-gate audio sessions and hydrate stored suggestions."""
    if not s.reference_audio_path:
        return
    if s.chord_progression:
        s.harmony_map_confirmation_required = False
        if s.harmony_map_source == "none":
            s.harmony_map_source = "confirmed_user"
        return
    if s.source_analysis_override is not None and not s.suggested_chord_progression:
        metadata = getattr(s.source_analysis_override, "source_metadata", {}) or {}
        suggestion = metadata.get("harmony_suggestions", {})
        chords = suggestion.get("chords", []) if isinstance(suggestion, dict) else []
        confidence = suggestion.get("confidence", []) if isinstance(suggestion, dict) else []
        if isinstance(chords, list):
            clean = [str(chord).strip() for chord in chords if str(chord).strip()]
            s.suggested_chord_progression = clean or None
        if isinstance(confidence, list):
            s.suggested_chord_confidence = [
                max(0.0, min(1.0, float(value))) for value in confidence
            ] or None
    s.harmony_map_confirmation_required = True
    s.harmony_map_source = (
        "uploaded_audio_chroma_tentative"
        if s.suggested_chord_progression
        else "none"
    )


def _require_confirmed_harmony(s: StoredSession) -> None:
    gate_view = replace(s)
    _sync_harmony_map_gate(gate_view)
    if gate_view.harmony_confirmation_required:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "harmony_confirmation_required",
                "message": "Confirm or correct the tentative key and scale before generating harmonic parts.",
            },
        )
    if gate_view.harmony_map_confirmation_required:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "harmony_map_confirmation_required",
                "message": (
                    "Confirm or correct the tentative bar-level chord map before "
                    "generating harmonic parts from uploaded audio."
                ),
                "suggested_chord_progression": list(
                    gate_view.suggested_chord_progression or []
                ),
            },
        )


def _copy_midi_bytes(data: bytes | None) -> bytes | None:
    """Return a distinct bytes object (CPython may intern ``bytes()`` / full slices)."""
    if data is None:
        return None
    return bytes(bytearray(data))


_DEFAULT_BASS_PROGRAM = 33  # Electric Bass (finger), GM


def _bass_program_from_clean_bytes(clean_bytes: bytes) -> int:
    try:
        pm = pretty_midi.PrettyMIDI(io.BytesIO(clean_bytes))
    except Exception:
        return _DEFAULT_BASS_PROGRAM
    for inst in pm.instruments:
        if not inst.is_drum:
            return int(inst.program)
    return _DEFAULT_BASS_PROGRAM


def _resolved_bass_performance_controls(
    s: "StoredSession",
) -> dict[str, float]:
    return resolve_bass_performance_controls(
        s.bass_performance_controls,
        focus=s.bass_articulation_focus,
        expression_amount=s.bass_expression,
        style=s.bass_style,
        instrument_family=s.bass_instrument,
    ).effective


def _explicit_bass_performance_controls(
    s: "StoredSession",
) -> dict[str, float | None]:
    """Return independent amounts without changing legacy Touch/Character.

    A session with no explicit performance mix must continue to use the
    generator and renderer's established planners. Once the producer moves any
    independent control, all six performance axes become explicit.
    """

    if s.bass_performance_controls is None:
        return {
            "ghost": None,
            "mute": None,
            "slide": None,
            "legato": None,
            "timing_humanize": None,
            "velocity_humanize": None,
        }
    resolved = _resolved_bass_performance_controls(s)
    return dict(resolved)


def _render_bass_performance_bytes(
    *,
    clean_bytes: bytes,
    perf_notes: tuple[BassPerformanceNote, ...],
    tempo: int,
    expression_amount: float = 0.5,
    articulation_focus: str | None = "natural",
    timing_humanize: float | None = None,
    velocity_humanize: float | None = None,
    instrument_family: str | None = None,
    conditioning: UnifiedConditioning | None = None,
) -> bytes:
    program = _bass_program_from_clean_bytes(clean_bytes)
    return render_performance_bass_midi(
        perf_notes,
        tempo=int(tempo),
        program=program,
        expression_amount=expression_amount,
        articulation_focus=articulation_focus,
        timing_humanize=timing_humanize,
        velocity_humanize=velocity_humanize,
        instrument_family=instrument_family,
        source_kick_per_bar=conditioning.source_kick_weight if conditioning else None,
        source_snare_per_bar=conditioning.source_snare_weight if conditioning else None,
        source_pressure_per_bar=conditioning.source_slot_pressure if conditioning else None,
    )


def _bass_normalization_root_pc(s: "StoredSession") -> int | None:
    """First-chord root pitch class for boundary normalization, or None."""
    progression = list(getattr(s, "chord_progression", []) or [])
    if progression:
        try:
            chords = mt.progression_chords_for_bars(progression, max(1, int(s.bar_count)))
            if chords:
                return int(chords[0].root_pc) % 12
        except (ValueError, AttributeError):
            return None
    return int(mt.key_root_pc(s.key)) % 12


def _normalize_bass_bytes_for_session(
    midi_bytes: bytes | None,
    s: "StoredSession",
) -> bytes:
    if not midi_bytes:
        return midi_bytes or b""
    return normalize_bass_loop_bytes(
        midi_bytes,
        tempo=int(s.tempo),
        bar_count=int(s.bar_count),
        harmonic_root_pc=_bass_normalization_root_pc(s),
    )


def _duplicate_stored_session(src: StoredSession, new_id: str) -> StoredSession:
    """Deep-copy settings and lane MIDI bytes/previews into a new StoredSession."""
    # A variation starts from the durable musical state. Live analyser
    # evidence may still change or disappear, and candidate run ids are scoped
    # to the source session id rather than portable provenance.
    src = _durable_session_view(src)
    return StoredSession(
        id=new_id,
        tempo=src.tempo,
        key=src.key,
        scale=src.scale,
        bar_count=src.bar_count,
        session_preset=src.session_preset,
        lead_style=src.lead_style,
        bass_style=src.bass_style,
        chord_style=src.chord_style,
        chord_progression=list(src.chord_progression) if src.chord_progression is not None else None,
        drum_style=src.drum_style,
        lead_instrument=src.lead_instrument,
        lead_player=src.lead_player,
        bass_instrument=src.bass_instrument,
        bass_player=src.bass_player,
        bass_engine=src.bass_engine,
        bass_lock_to_groove=src.bass_lock_to_groove,
        bass_articulation_focus=src.bass_articulation_focus,
        bass_expression=src.bass_expression,
        bass_performance_controls=(
            dict(src.bass_performance_controls)
            if src.bass_performance_controls is not None
            else None
        ),
        bass_phase_offset_beats=src.bass_phase_offset_beats,
        bass_density_bias=src.bass_density_bias,
        bass_seed=src.bass_seed,
        drum_player=src.drum_player,
        chord_instrument=src.chord_instrument,
        chord_player=src.chord_player,
        drum_kit=src.drum_kit,
        drum_bytes=_copy_midi_bytes(src.drum_bytes),
        bass_bytes=_copy_midi_bytes(src.bass_bytes),
        bass_performance_bytes=_copy_midi_bytes(src.bass_performance_bytes),
        chords_bytes=_copy_midi_bytes(src.chords_bytes),
        lead_bytes=_copy_midi_bytes(src.lead_bytes),
        drum_preview=src.drum_preview,
        bass_preview=src.bass_preview,
        chords_preview=src.chords_preview,
        lead_preview=src.lead_preview,
        drum_locked=src.drum_locked,
        bass_locked=src.bass_locked,
        chords_locked=src.chords_locked,
        lead_locked=src.lead_locked,
        anchor_lane=src.anchor_lane,
        reference_audio_path=src.reference_audio_path,
        reference_audio_filename=src.reference_audio_filename,
        reference_audio_uploaded_at=src.reference_audio_uploaded_at,
        reference_audio_duration_seconds=src.reference_audio_duration_seconds,
        reference_audio_head_trim_seconds=src.reference_audio_head_trim_seconds,
        source_analysis_override=src.source_analysis_override,
        groove_reference_audio_path=src.groove_reference_audio_path,
        groove_reference_audio_filename=src.groove_reference_audio_filename,
        groove_reference_audio_uploaded_at=src.groove_reference_audio_uploaded_at,
        groove_reference_audio_duration_seconds=src.groove_reference_audio_duration_seconds,
        groove_reference_audio_head_trim_seconds=src.groove_reference_audio_head_trim_seconds,
        groove_reference_analysis_override=src.groove_reference_analysis_override,
        harmony_confirmation_required=src.harmony_confirmation_required,
        harmony_key_confirmed_by_user=src.harmony_key_confirmed_by_user,
        harmony_map_confirmation_required=src.harmony_map_confirmation_required,
        suggested_chord_progression=(
            list(src.suggested_chord_progression)
            if src.suggested_chord_progression is not None
            else None
        ),
        suggested_chord_confidence=(
            list(src.suggested_chord_confidence)
            if src.suggested_chord_confidence is not None
            else None
        ),
        harmony_map_source=src.harmony_map_source,
        current_bass_candidate_run_id=None,
        current_bass_candidate_take_id=None,
    )


def _safe_filename(name: str) -> str:
    base = Path(name).name
    clean = "".join(ch for ch in base if ch.isalnum() or ch in ("-", "_", "."))
    return clean or "upload.bin"


def _reference_audio_ext(name: str) -> str:
    ext = Path(name).suffix.lower()
    if ext in _ALLOWED_REFERENCE_EXTS:
        return ext
    return ""


@router.post("/", response_model=SessionCreated)
def create_session(body: SessionCreate) -> SessionCreated:
    try:
        mt.key_root_pc(body.key)
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"error": "invalid_key", "message": str(e)}) from e

    sid = str(uuid.uuid4())
    if body.session_preset is not None:
        ds, bs, cs, ls = lane_styles_for_session_preset(body.session_preset)
    else:
        ls = normalize_lead_style(body.lead_style.value if body.lead_style else None)
        bs = body.bass_style.value if body.bass_style else "supportive"
        cs = body.chord_style.value if body.chord_style else "simple"
        ds = body.drum_style.value if body.drum_style else "straight"
    if body.lead_style is not None:
        ls = normalize_lead_style(body.lead_style.value)
    if body.bass_style is not None:
        bs = body.bass_style.value
    if body.chord_style is not None:
        cs = body.chord_style.value
    if body.drum_style is not None:
        ds = body.drum_style.value
    preset_stored = body.session_preset.value if body.session_preset is not None else None
    li_ins = body.lead_instrument.value if body.lead_instrument is not None else _DEFAULT_LEAD_INSTRUMENT
    lp_ins = body.lead_player.value if body.lead_player is not None else None
    bi_ins = body.bass_instrument.value if body.bass_instrument is not None else _DEFAULT_BASS_INSTRUMENT
    bp_ins = body.bass_player.value if body.bass_player is not None else None
    be_ins = body.bass_engine.value if body.bass_engine is not None else "baseline"
    dp_ins = body.drum_player.value if body.drum_player is not None else None
    cp_ins = body.chord_player.value if body.chord_player is not None else None
    ci_ins = body.chord_instrument.value if body.chord_instrument is not None else _DEFAULT_CHORD_INSTRUMENT
    dk_ins = body.drum_kit.value if body.drum_kit is not None else _DEFAULT_DRUM_KIT
    anchor_ins = body.anchor_lane.value if body.anchor_lane is not None else None
    s = StoredSession(
        id=sid,
        tempo=body.tempo,
        key=mt.normalize_key(body.key),
        scale=mt.describe_scale(body.scale),
        bar_count=body.bar_count,
        session_preset=preset_stored,
        lead_style=ls,
        bass_style=bs,
        chord_style=cs,
        chord_progression=list(body.chord_progression) if body.chord_progression else None,
        drum_style=ds,
        lead_instrument=li_ins,
        lead_player=lp_ins,
        bass_instrument=bi_ins,
        bass_player=bp_ins,
        bass_engine=be_ins,
        bass_lock_to_groove=body.bass_lock_to_groove,
        bass_articulation_focus=body.bass_articulation_focus.value,
        bass_expression=body.bass_expression,
        bass_performance_controls=(
            body.bass_performance_controls.model_dump(mode="python")
            if body.bass_performance_controls is not None
            else None
        ),
        bass_phase_offset_beats=body.bass_phase_offset_beats,
        bass_density_bias=body.bass_density_bias,
        drum_player=dp_ins,
        chord_instrument=ci_ins,
        chord_player=cp_ins,
        drum_kit=dk_ins,
        anchor_lane=anchor_ins,
        # SessionCreate requires an explicit key/scale, so that harmony is
        # authoritative user input rather than a tentative analyser guess.
        harmony_key_confirmed_by_user=True,
        harmony_map_source=(
            "confirmed_user"
            if body.chord_progression
            else "none"
        ),
    )
    _SESSIONS[sid] = s
    return SessionCreated(session=_to_state(s, message="Session created. Call /generate to build lanes."))


@router.get("/{session_id}", response_model=SessionState)
def get_session(session_id: str) -> SessionState:
    return _to_state(_get_session_or_404(session_id))


@router.post("/{session_id}/reference-audio", response_model=SessionState)
async def upload_reference_audio(session_id: str, file: UploadFile = File(...)) -> SessionState:
    s = _get_session_or_404(session_id)
    filename = _safe_filename(file.filename or "")
    ext = _reference_audio_ext(filename)
    if not ext:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_audio_format",
                "message": f"Supported formats: {', '.join(sorted(_ALLOWED_REFERENCE_EXTS))}",
            },
        )
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail={"error": "empty_upload", "message": "Uploaded file is empty."})
    if len(payload) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail={"error": "file_too_large", "message": "Max upload size is 25MB."})

    target_dir = _REFERENCE_AUDIO_ROOT / s.id
    target_dir.mkdir(parents=True, exist_ok=True)
    blob_name = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    target = target_dir / blob_name
    target.write_bytes(payload)
    staged = replace(s)
    _discard_live_bridge_overlay(staged)
    staged.reference_audio_path = str(target)
    staged.reference_audio_filename = filename
    staged.reference_audio_uploaded_at = datetime.now(timezone.utc).isoformat()
    staged.reference_audio_duration_seconds = 0.0
    staged.reference_audio_head_trim_seconds = 0.0
    staged.source_analysis_override = None
    staged.harmony_confirmation_required = True
    staged.harmony_key_confirmed_by_user = False
    staged.harmony_map_confirmation_required = True
    staged.suggested_chord_progression = None
    staged.suggested_chord_confidence = None
    staged.harmony_map_source = "none"
    s = _publish_staged_session(s, staged)
    return _to_state(s, message="Reference audio uploaded. Call /analyze-audio to run DSP analysis.")


@router.post("/{session_id}/analyze-audio", response_model=SessionState)
def analyze_reference_audio_for_session(session_id: str) -> SessionState:
    s = _get_session_or_404(session_id)
    if not s.reference_audio_path:
        raise HTTPException(
            status_code=400,
            detail={"error": "reference_audio_missing", "message": "Upload reference audio first."},
        )
    audio_path = Path(s.reference_audio_path)
    if not audio_path.is_file():
        raise HTTPException(
            status_code=400,
            detail={"error": "reference_audio_not_found", "message": "Stored reference audio file is missing."},
        )
    analysis_key, analysis_scale = _durable_key_scale(s)
    try:
        result = analyze_reference_audio(
            audio_path=audio_path,
            session_tempo=s.tempo,
            bar_count=s.bar_count,
            session_key=analysis_key,
            session_scale=analysis_scale,
            source_filename=s.reference_audio_filename,
            trust_session_bar_count=False,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "audio_analysis_failed", "message": str(exc)},
        ) from exc
    preserve_confirmed_key = bool(
        s.harmony_key_confirmed_by_user
        or (
            s.chord_progression
            and s.harmony_map_source == "confirmed_user"
            and not s.harmony_confirmation_required
        )
    )
    preserve_confirmed_map = bool(
        s.chord_progression
        and s.harmony_map_source == "confirmed_user"
        and not s.harmony_map_confirmation_required
    )

    staged = replace(s)
    _discard_live_bridge_overlay(staged)
    staged.source_analysis_override = result.source_analysis
    filename_key = result.source_analysis.source_metadata.get("filename_hints", {}).get("key")
    if preserve_confirmed_key:
        # Reanalysis refreshes evidence, not an explicit user decision.
        staged.harmony_confirmation_required = False
        staged.harmony_key_confirmed_by_user = True
    else:
        staged.harmony_confirmation_required = (
            not bool(filename_key)
            and float(result.source_analysis.tonal_center_confidence) < 0.5
        )
    suggestion = result.source_analysis.source_metadata.get("harmony_suggestions", {})
    suggested_chords = suggestion.get("chords", []) if isinstance(suggestion, dict) else []
    suggested_confidence = suggestion.get("confidence", []) if isinstance(suggestion, dict) else []
    staged.suggested_chord_progression = (
        [str(chord) for chord in suggested_chords if str(chord).strip()]
        if isinstance(suggested_chords, list)
        else None
    )
    staged.suggested_chord_confidence = (
        [max(0.0, min(1.0, float(value))) for value in suggested_confidence]
        if isinstance(suggested_confidence, list)
        else None
    )
    if preserve_confirmed_map:
        staged.harmony_map_confirmation_required = False
        staged.harmony_map_source = "confirmed_user"
    else:
        staged.harmony_map_confirmation_required = True
        staged.harmony_map_source = "uploaded_audio_chroma_tentative"
    staged.reference_audio_duration_seconds = result.duration_seconds
    staged.reference_audio_head_trim_seconds = result.head_trim_seconds
    s = _publish_staged_session(s, staged)
    return _to_state(s, message="Reference audio analyzed and source analysis updated.")


@router.post("/{session_id}/groove-reference-audio", response_model=SessionState)
async def upload_groove_reference_audio(session_id: str, file: UploadFile = File(...)) -> SessionState:
    s = _get_session_or_404(session_id)
    filename = _safe_filename(file.filename or "")
    ext = _reference_audio_ext(filename)
    if not ext:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "unsupported_audio_format",
                "message": f"Supported formats: {', '.join(sorted(_ALLOWED_REFERENCE_EXTS))}",
            },
        )
    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail={"error": "empty_upload", "message": "Uploaded file is empty."})
    if len(payload) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail={"error": "file_too_large", "message": "Max upload size is 25MB."})

    target_dir = _REFERENCE_AUDIO_ROOT / s.id
    target_dir.mkdir(parents=True, exist_ok=True)
    blob_name = f"groove_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}{ext}"
    target = target_dir / blob_name
    target.write_bytes(payload)
    staged = replace(s)
    staged.groove_reference_audio_path = str(target)
    staged.groove_reference_audio_filename = filename
    staged.groove_reference_audio_uploaded_at = datetime.now(timezone.utc).isoformat()
    staged.groove_reference_audio_duration_seconds = 0.0
    staged.groove_reference_audio_head_trim_seconds = 0.0
    staged.groove_reference_analysis_override = None
    s = _publish_staged_session(s, staged)
    return _to_state(s, message="Groove reference uploaded. Analyse it to extract kick, snare and pocket.")


@router.post("/{session_id}/analyze-groove-reference", response_model=SessionState)
def analyze_groove_reference_for_session(session_id: str) -> SessionState:
    s = _get_session_or_404(session_id)
    if not s.groove_reference_audio_path:
        raise HTTPException(
            status_code=400,
            detail={"error": "groove_reference_missing", "message": "Upload a groove reference first."},
        )
    audio_path = Path(s.groove_reference_audio_path)
    if not audio_path.is_file():
        raise HTTPException(
            status_code=400,
            detail={"error": "groove_reference_not_found", "message": "Stored groove reference is missing."},
        )
    try:
        result = analyze_reference_audio(
            audio_path=audio_path,
            session_tempo=s.tempo,
            bar_count=s.bar_count,
            session_key=s.key,
            session_scale=s.scale,
            source_filename=s.groove_reference_audio_filename,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "groove_analysis_failed", "message": str(exc)},
        ) from exc
    staged = replace(s)
    staged.groove_reference_analysis_override = result.source_analysis.model_copy(
        update={"source_lane": "groove_reference_audio"}
    )
    staged.groove_reference_audio_duration_seconds = result.duration_seconds
    staged.groove_reference_audio_head_trim_seconds = result.head_trim_seconds
    s = _publish_staged_session(s, staged)
    return _to_state(s, message="Groove reference analysed. Bass can now follow its pocket.")


@router.get("/{session_id}/reference-audio")
def download_reference_audio(session_id: str):
    s = _get_session_or_404(session_id)
    if not s.reference_audio_path or not s.reference_audio_filename:
        raise HTTPException(
            status_code=404,
            detail={"error": "reference_audio_missing", "message": "No reference audio uploaded for this session."},
        )
    audio_path = Path(s.reference_audio_path)
    if not audio_path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"error": "reference_audio_not_found", "message": "Stored reference audio file is missing."},
        )
    return FileResponse(
        path=str(audio_path),
        filename=s.reference_audio_filename,
    )


@router.post("/{session_id}/duplicate", response_model=SessionState)
def duplicate_session(session_id: str) -> SessionState:
    """Clone session settings and lane data to a new id; no regeneration."""
    src = _get_session_or_404(session_id)
    new_id = str(uuid.uuid4())
    dup = _duplicate_stored_session(src, new_id)
    _SESSIONS[new_id] = dup
    return _to_state(dup, message="Session duplicated. You are now working on a variation.")


@router.patch("/{session_id}", response_model=SessionState)
def patch_session(session_id: str, body: SessionPatch) -> SessionState:
    """Update session fields (no automatic lane regeneration)."""
    s = _get_session_or_404(session_id)
    staged = replace(s)
    if any(
        value is not None
        for value in (body.tempo, body.key, body.scale, body.bar_count)
    ):
        _discard_live_bridge_overlay(staged)
    parts: list[str] = []
    if body.tempo is not None:
        staged.tempo = int(body.tempo)
        parts.append("Tempo updated")
    if body.key is not None:
        try:
            staged.key = mt.normalize_key(body.key)
        except ValueError as e:
            raise HTTPException(status_code=422, detail={"error": "invalid_key", "message": str(e)}) from e
        parts.append("Key updated")
    if body.scale is not None:
        staged.scale = mt.describe_scale(body.scale)
        parts.append("Scale updated")
    if body.key is not None or body.scale is not None:
        staged.harmony_confirmation_required = False
        staged.harmony_key_confirmed_by_user = True
    if body.bar_count is not None:
        staged.bar_count = int(body.bar_count)
        parts.append("Bar count updated")
    if body.session_preset is not None:
        staged.session_preset = body.session_preset.value
        ds, bs, cs, ls = lane_styles_for_session_preset(body.session_preset)
        staged.drum_style, staged.bass_style, staged.chord_style, staged.lead_style = ds, bs, cs, normalize_lead_style(ls)
        parts.append(
            f"Session preset updated to {body.session_preset.value}; lane styles set to preset defaults"
        )
    if body.lead_style is not None:
        staged.lead_style = normalize_lead_style(body.lead_style.value)
        parts.append("Lead style updated")
    if "lead_player" in body.model_dump(exclude_unset=True):
        staged.lead_player = body.lead_player.value if body.lead_player is not None else None
        parts.append("Lead player updated")
    if body.bass_style is not None:
        staged.bass_style = body.bass_style.value
        parts.append("Bass style updated")
    if body.chord_style is not None:
        staged.chord_style = body.chord_style.value
        parts.append("Chord style updated")
    if "chord_progression" in body.model_dump(exclude_unset=True):
        staged.chord_progression = list(body.chord_progression) if body.chord_progression else None
        if staged.chord_progression:
            staged.harmony_map_confirmation_required = False
            staged.harmony_map_source = "confirmed_user"
            parts.append("Bar-level harmony map confirmed")
        else:
            staged.harmony_map_confirmation_required = bool(staged.reference_audio_path)
            staged.harmony_map_source = (
                "uploaded_audio_chroma_tentative"
                if staged.suggested_chord_progression
                else "none"
            )
            parts.append("Chord progression cleared")
    if "chord_player" in body.model_dump(exclude_unset=True):
        staged.chord_player = body.chord_player.value if body.chord_player is not None else None
        parts.append("Chord player updated")
    if body.drum_style is not None:
        staged.drum_style = body.drum_style.value
        parts.append("Drum style updated")
    if body.lead_instrument is not None:
        staged.lead_instrument = body.lead_instrument.value
        parts.append("Lead instrument updated")
    if body.bass_instrument is not None:
        staged.bass_instrument = body.bass_instrument.value
        parts.append("Bass instrument updated")
    if "bass_player" in body.model_dump(exclude_unset=True):
        staged.bass_player = body.bass_player.value if body.bass_player is not None else None
        parts.append("Bass player updated")
    if body.bass_engine is not None:
        staged.bass_engine = body.bass_engine.value
        parts.append("Bass engine updated")
    if "bass_lock_to_groove" in body.model_dump(exclude_unset=True):
        staged.bass_lock_to_groove = body.bass_lock_to_groove
        parts.append("Bass lock-to-groove updated")
    if body.bass_articulation_focus is not None:
        staged.bass_articulation_focus = body.bass_articulation_focus.value
        parts.append("Bass touch updated")
    if body.bass_expression is not None:
        staged.bass_expression = float(body.bass_expression)
        parts.append("Bass expression updated")
    if "bass_performance_controls" in body.model_fields_set:
        staged.bass_performance_controls = (
            body.bass_performance_controls.model_dump(mode="python")
            if body.bass_performance_controls is not None
            else None
        )
        parts.append("Bass performance mix updated")
    if body.bass_density_bias is not None:
        staged.bass_density_bias = float(body.bass_density_bias)
        parts.append("Bass activity updated")
    if body.bass_phase_offset_beats is not None:
        staged.bass_phase_offset_beats = float(body.bass_phase_offset_beats)
        parts.append("Bass phase offset updated")
    if "drum_player" in body.model_dump(exclude_unset=True):
        staged.drum_player = body.drum_player.value if body.drum_player is not None else None
        parts.append("Drum player updated")
    if body.chord_instrument is not None:
        staged.chord_instrument = body.chord_instrument.value
        parts.append("Chord instrument updated")
    if body.drum_kit is not None:
        staged.drum_kit = body.drum_kit.value
        parts.append("Drum kit updated")
    if "anchor_lane" in body.model_dump(exclude_unset=True):
        staged.anchor_lane = body.anchor_lane.value if body.anchor_lane is not None else None
        parts.append("Anchor lane updated")
    msg = ". ".join(parts) + ". Regenerate affected lane(s) to rebuild MIDI."
    s = _publish_staged_session(s, staged)
    return _to_state(s, message=msg)


@router.patch("/{session_id}/lane-locks", response_model=SessionState)
def patch_lane_locks(session_id: str, body: LaneLocksPatch) -> SessionState:
    """Update which lanes are locked against multi-lane regenerate (partial body allowed)."""
    s = _get_session_or_404(session_id)
    staged = replace(s)
    if body.drums is not None:
        staged.drum_locked = body.drums
    if body.bass is not None:
        staged.bass_locked = body.bass
    if body.chords is not None:
        staged.chords_locked = body.chords
    if body.lead is not None:
        staged.lead_locked = body.lead
    s = _publish_staged_session(s, staged)
    return _to_state(s, message="Lane locks updated.")


@router.post("/{session_id}/generate", response_model=GenerateResult)
def generate_session(session_id: str) -> GenerateResult:
    s = _get_session_or_404(session_id)
    staged = replace(s)
    _require_confirmed_harmony(staged)
    _generate_all_lanes(staged)
    s = _commit_regenerated_lanes(
        s,
        staged,
        list(_LANE_REGENERATION_ORDER),
    )
    return GenerateResult(session=_to_state(s, message="All lanes generated."))


def _generate_all_lanes(s: StoredSession) -> None:
    """Fill all four lanes; anchor lane first without context, then others with anchor context when configured."""
    anchor_v = normalize_anchor_lane(s.anchor_lane)
    if anchor_v:
        anchor_lane = LaneName(anchor_v)
        _regenerate_lane_on_stored_session(s, anchor_lane, context=None)
        ctx = build_session_context(s)
        for lane in _LANE_REGENERATION_ORDER:
            if lane == anchor_lane:
                continue
            _regenerate_lane_on_stored_session(s, lane, context=ctx)
        return
    for lane in _LANE_REGENERATION_ORDER:
        _regenerate_lane_on_stored_session(s, lane, context=None)


def _regenerate_lane_on_stored_session(
    s: StoredSession,
    lane: LaneName,
    *,
    context: object | None = None,
) -> None:
    """Regenerate one lane in-place using current stored session settings."""
    if lane != LaneName.drums:
        _require_confirmed_harmony(s)
    cond = _conditioning_for_generation(s, context=context)
    if lane == LaneName.drums:
        d_bytes, d_prev = generator.generate_drums(
            tempo=s.tempo,
            bar_count=s.bar_count,
            drum_style=s.drum_style,
            drum_kit=s.drum_kit,
            drum_player=s.drum_player,
            session_preset=s.session_preset,
            context=context,
        )
        s.drum_bytes = d_bytes
        s.drum_preview = d_prev
    elif lane == LaneName.bass:
        base_seed = _new_bass_seed()
        performance_controls = _explicit_bass_performance_controls(s)
        strict_harmonic_guard = bool(
            s.bass_engine == "phrase_v2"
            and s.chord_progression
            and cond is not None
        )
        max_attempts = 6 if strict_harmonic_guard else 1
        rejected = 0
        for attempt in range(max_attempts):
            seed = base_seed + attempt
            b_bytes, b_prev, perf_notes = generator.generate_bass(
                tempo=s.tempo,
                bar_count=s.bar_count,
                key=s.key,
                scale=s.scale,
                bass_style=s.bass_style,
                bass_instrument=s.bass_instrument,
                bass_player=s.bass_player,
                bass_engine=s.bass_engine,
                lock_to_groove=s.bass_lock_to_groove,
                density_bias=s.bass_density_bias,
                expression_amount=s.bass_expression,
                bass_articulation_focus=s.bass_articulation_focus,
                ghost_amount=performance_controls["ghost"],
                mute_amount=performance_controls["mute"],
                slide_amount=performance_controls["slide"],
                legato_amount=performance_controls["legato"],
                chord_progression=s.chord_progression,
                session_preset=s.session_preset,
                context=context,
                conditioning=cond,
                seed=seed,
                return_performance_notes=True,
            )
            b_bytes = _normalize_bass_bytes_for_session(b_bytes, s)
            unsupported = count_unsupported_structural_notes(
                extract_lane_notes(b_bytes),
                tempo=s.tempo,
                conditioning=cond,
                style=s.bass_style,
            )
            if not strict_harmonic_guard or unsupported == 0:
                break
            rejected += 1
        else:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "bass_harmonic_guard_rejected",
                    "message": (
                        "Phrase-v2 could not produce a bass take supported by "
                        "the confirmed chord progression."
                    ),
                    "attempts": rejected,
                },
            )
        s.bass_bytes = b_bytes
        s.bass_preview = b_prev
        s.bass_seed = seed
        perf_bytes = _render_bass_performance_bytes(
            clean_bytes=b_bytes,
            perf_notes=perf_notes,
            tempo=s.tempo,
            expression_amount=s.bass_expression,
            articulation_focus=s.bass_articulation_focus,
            timing_humanize=performance_controls["timing_humanize"],
            velocity_humanize=performance_controls["velocity_humanize"],
            instrument_family=s.bass_instrument,
            conditioning=cond,
        )
        s.bass_performance_bytes = _normalize_bass_bytes_for_session(perf_bytes, s)
        s.current_bass_candidate_run_id = None
        s.current_bass_candidate_take_id = None
    elif lane == LaneName.chords:
        c_bytes, c_prev = generator.generate_chords(
            tempo=s.tempo,
            bar_count=s.bar_count,
            key=s.key,
            scale=s.scale,
            chord_style=s.chord_style,
            chord_instrument=s.chord_instrument,
            chord_player=s.chord_player,
            chord_progression=s.chord_progression,
            session_preset=s.session_preset,
            context=context,
        )
        s.chords_bytes = c_bytes
        s.chords_preview = c_prev
    else:
        l_bytes, l_prev = generator.generate_lead(
            tempo=s.tempo,
            bar_count=s.bar_count,
            key=s.key,
            scale=s.scale,
            lead_style=s.lead_style,
            lead_instrument=s.lead_instrument,
            lead_player=s.lead_player,
            session_preset=s.session_preset,
            context=context,
        )
        s.lead_bytes = l_bytes
        s.lead_preview = l_prev


def _conditioning_for_generation(
    s: StoredSession,
    *,
    context: object | None,
) -> UnifiedConditioning | None:
    ctx = context if isinstance(context, SessionAnchorContext) else build_session_context(s)
    musical_src = s.source_analysis_override if s.source_analysis_override is not None else build_source_analysis(s, context=ctx)
    src = fuse_source_and_groove(musical_src, s.groove_reference_analysis_override)
    groove = build_groove_profile(src, context=ctx)
    harmony = build_harmony_plan(s, src)
    return build_unified_conditioning(
        session=s,
        source=src,
        groove=groove,
        harmony=harmony,
        context=ctx,
    )


def _context_for_lane_regeneration(s: StoredSession, lane: LaneName) -> object | None:
    av = normalize_anchor_lane(s.anchor_lane)
    if not av or LaneName(av) == lane:
        return None
    return build_session_context(s)


def _lane_note_signature(data: bytes | None) -> tuple[tuple[int, float, float, int], ...]:
    return tuple(
        (
            int(note.pitch),
            round(float(note.start), 6),
            round(float(note.end), 6),
            int(note.velocity),
        )
        for note in extract_lane_notes(data)
    )


def _rerender_current_bass_performance(
    s: StoredSession,
    *,
    context: SessionAnchorContext | None,
) -> None:
    """Rebuild expression directly from clean MIDI, freezing written notes."""

    if not s.bass_bytes:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "bass_phrase_missing",
                "message": (
                    "Generate a bass idea before applying a performance-only change."
                ),
            },
        )
    conditioning = _conditioning_for_generation(s, context=context)
    try:
        clean_midi = pretty_midi.PrettyMIDI(io.BytesIO(s.bass_bytes))
    except Exception as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "bass_phrase_invalid",
                "message": "The current clean bass MIDI could not be re-rendered.",
            },
        ) from exc

    seconds_per_beat = 60.0 / float(max(1, int(s.tempo)))
    bar_seconds = seconds_per_beat * 4.0
    sixteenth = seconds_per_beat / 4.0
    phrase_roles = ("anchor", "answer", "push", "release")
    source = "phrase_v2" if s.bass_engine == "phrase_v2" else "baseline"
    clean_notes: list[BassPerformanceNote] = []
    for instrument in clean_midi.instruments:
        if instrument.is_drum:
            continue
        for note in instrument.notes:
            bar_index = max(0, int(float(note.start) / bar_seconds))
            bar_start = float(bar_index) * bar_seconds
            slot_index = int(round((float(note.start) - bar_start) / sixteenth))
            if slot_index >= 16:
                bar_index += slot_index // 16
                slot_index %= 16
            clean_notes.append(
                BassPerformanceNote(
                    pitch=int(note.pitch),
                    start=float(note.start),
                    end=float(note.end),
                    velocity=int(note.velocity),
                    role=phrase_roles[bar_index % len(phrase_roles)],
                    bar_index=bar_index,
                    slot_index=max(0, min(15, slot_index)),
                    source=source,
                )
            )
    clean_notes.sort(key=lambda note: (note.start, note.pitch, note.end))
    if not clean_notes:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "bass_phrase_empty",
                "message": "The current clean bass phrase contains no notes.",
            },
        )

    performance_controls = _explicit_bass_performance_controls(s)
    performance_notes = infer_bass_articulations(
        tuple(clean_notes),
        tempo=int(s.tempo),
        style=s.bass_style,
        source=source,
        expression_amount=s.bass_expression,
        instrument_family=s.bass_instrument,
        bass_articulation_focus=s.bass_articulation_focus,
        ghost_amount=performance_controls["ghost"],
        mute_amount=performance_controls["mute"],
        slide_amount=performance_controls["slide"],
        legato_amount=performance_controls["legato"],
    )
    rendered = _render_bass_performance_bytes(
        clean_bytes=s.bass_bytes,
        perf_notes=performance_notes,
        tempo=s.tempo,
        expression_amount=s.bass_expression,
        articulation_focus=s.bass_articulation_focus,
        timing_humanize=performance_controls["timing_humanize"],
        velocity_humanize=performance_controls["velocity_humanize"],
        instrument_family=s.bass_instrument,
        conditioning=conditioning,
    )
    s.bass_performance_bytes = _normalize_bass_bytes_for_session(rendered, s)
    s.current_bass_candidate_run_id = None
    s.current_bass_candidate_take_id = None


def _commit_regenerated_lanes(
    destination: StoredSession,
    staged: StoredSession,
    lanes: list[LaneName],
) -> StoredSession:
    """Publish one coherent staged revision after all requested work succeeds."""

    if staged.id != destination.id:
        raise ValueError("Cannot commit a staged session under a different id")
    if (
        any(lane != LaneName.drums for lane in lanes)
        and staged.bridge_live_overlay_active
    ):
        # The new MIDI was conditioned by the live context. Commit that exact
        # context alongside the output so a save/restart cannot pair the new
        # notes with an older key or groove map.
        _promote_live_bridge_overlay(staged)
    # Mapping replacement is atomic under CPython. Concurrent GET/plugin polls
    # therefore see either the complete prior revision or complete new one,
    # never clean MIDI from one take and performance/preview from another.
    _SESSIONS[destination.id] = staged
    return staged


@router.post("/{session_id}/generate-around-anchor", response_model=SessionState)
def generate_around_anchor(session_id: str, body: GenerateAroundAnchorBody = GenerateAroundAnchorBody()) -> SessionState:
    """Regenerate non-anchor lanes using timing/density context from the anchor lane (respects lane locks)."""
    s = _get_session_or_404(session_id)
    staged = replace(s)
    if body.anchor_lane is not None:
        staged.anchor_lane = body.anchor_lane.value
    av = normalize_anchor_lane(staged.anchor_lane)
    if not av:
        raise HTTPException(
            status_code=400,
            detail={"error": "anchor_not_set", "message": "Set anchor_lane (PATCH session) or send anchor_lane in the request body."},
        )
    anchor_lane = LaneName(av)
    if not _lane_has_midi(staged, anchor_lane):
        raise HTTPException(
            status_code=400,
            detail={"error": "anchor_not_generated", "lane": av, "message": "Generate the anchor lane first."},
        )
    ctx = build_session_context(staged)
    if ctx is None:
        raise HTTPException(
            status_code=400,
            detail={"error": "anchor_context_failed", "message": "Could not build context from anchor MIDI."},
        )
    regen: list[LaneName] = []
    skipped_locked: list[LaneName] = []
    for lane in _LANE_REGENERATION_ORDER:
        if lane == anchor_lane:
            continue
        if _lane_locked(staged, lane):
            skipped_locked.append(lane)
            continue
        regen.append(lane)
    if any(lane != LaneName.drums for lane in regen):
        _require_confirmed_harmony(staged)
    for lane in regen:
        _regenerate_lane_on_stored_session(staged, lane, context=ctx)
    s = _commit_regenerated_lanes(s, staged, regen)
    parts: list[str] = []
    if regen:
        parts.append("Regenerated around anchor (" + av + "): " + ", ".join(x.value for x in regen) + ".")
    if skipped_locked:
        parts.append("Skipped locked: " + ", ".join(x.value for x in skipped_locked) + ".")
    if not regen:
        parts.append("No non-anchor lanes were regenerated (all locked or anchor only).")
    return _to_state(s, message=" ".join(parts).strip())


@router.post("/{session_id}/regenerate-selected", response_model=SessionState)
def regenerate_selected(session_id: str, body: RegenerateSelectedBody) -> SessionState:
    """Regenerate multiple lanes using stored session styles/instruments; other lanes unchanged."""
    s = _get_session_or_404(session_id)
    ordered = _requested_lanes_stable(body.lanes)
    if body.preserve_bass_phrase and ordered != [LaneName.bass]:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "invalid_performance_rerender_scope",
                "message": (
                    "preserve_bass_phrase can only be used when Bass is the "
                    "sole requested lane."
                ),
            },
        )
    to_run: list[LaneName] = []
    skipped_locked: list[LaneName] = []
    for lane in ordered:
        if _lane_locked(s, lane):
            skipped_locked.append(lane)
        else:
            to_run.append(lane)
    staged = replace(s)
    if any(lane != LaneName.drums for lane in to_run):
        _require_confirmed_harmony(staged)
    anchor_v = normalize_anchor_lane(staged.anchor_lane)
    done: set[LaneName] = set()
    preserved_bass_phrase = (
        body.preserve_bass_phrase and to_run == [LaneName.bass]
    )
    if preserved_bass_phrase:
        raw_context = _context_for_lane_regeneration(staged, LaneName.bass)
        _rerender_current_bass_performance(
            staged,
            context=(
                raw_context
                if isinstance(raw_context, SessionAnchorContext)
                else None
            ),
        )
    elif anchor_v:
        anchor_lane = LaneName(anchor_v)
        if anchor_lane in to_run:
            _regenerate_lane_on_stored_session(
                staged,
                anchor_lane,
                context=None,
            )
            done.add(anchor_lane)
        ctx = build_session_context(staged)
        for lane in to_run:
            if lane in done:
                continue
            _regenerate_lane_on_stored_session(
                staged,
                lane,
                context=ctx,
            )
    else:
        for lane in to_run:
            _regenerate_lane_on_stored_session(
                staged,
                lane,
                context=None,
            )
    s = _commit_regenerated_lanes(s, staged, to_run)
    parts: list[str] = []
    if preserved_bass_phrase:
        parts.append("Re-rendered Bass performance; written phrase preserved.")
    elif to_run:
        parts.append("Regenerated lanes: " + ", ".join(lane.value for lane in to_run) + ".")
    if skipped_locked:
        parts.append("Skipped locked lanes: " + ", ".join(lane.value for lane in skipped_locked) + ".")
    if not to_run:
        parts.append("No lanes were regenerated.")
    return _to_state(s, message=" ".join(parts).strip())


@router.post("/{session_id}/regenerate-unlocked", response_model=SessionState)
def regenerate_unlocked(session_id: str) -> SessionState:
    """Regenerate every lane that is not locked; locked lanes unchanged."""
    s = _get_session_or_404(session_id)
    staged = replace(s)
    to_run: list[LaneName] = []
    kept_locked: list[LaneName] = []
    for lane in _LANE_REGENERATION_ORDER:
        if _lane_locked(staged, lane):
            kept_locked.append(lane)
        else:
            to_run.append(lane)
    if not to_run:
        return _to_state(s, message="All lanes are locked. No lanes were regenerated.")
    if any(lane != LaneName.drums for lane in to_run):
        _require_confirmed_harmony(staged)
    anchor_v = normalize_anchor_lane(staged.anchor_lane)
    done: set[LaneName] = set()
    if anchor_v:
        anchor_lane = LaneName(anchor_v)
        if anchor_lane in to_run:
            _regenerate_lane_on_stored_session(staged, anchor_lane, context=None)
            done.add(anchor_lane)
        ctx = build_session_context(staged)
        for lane in _LANE_REGENERATION_ORDER:
            if lane not in to_run or lane in done:
                continue
            _regenerate_lane_on_stored_session(staged, lane, context=ctx)
    else:
        for lane in to_run:
            _regenerate_lane_on_stored_session(staged, lane, context=None)
    s = _commit_regenerated_lanes(s, staged, to_run)
    regen = "Regenerated unlocked lanes: " + ", ".join(lane.value for lane in to_run) + "."
    if kept_locked:
        kept = " Locked lanes kept: " + ", ".join(lane.value for lane in kept_locked) + "."
        return _to_state(s, message=regen + kept)
    return _to_state(s, message=regen)


@router.post("/{session_id}/add-part-to-suit", response_model=SessionState)
def add_part_to_suit(session_id: str, body: AddPartToSuitBody) -> SessionState:
    """Replace the lead lane with a new context-aware idea (ignores lead lock)."""
    s = _get_session_or_404(session_id)
    staged = replace(s)
    _require_confirmed_harmony(staged)
    mode_v = body.mode.value
    l_bytes, l_prev = generator.generate_lead(
        tempo=staged.tempo,
        bar_count=staged.bar_count,
        key=staged.key,
        scale=staged.scale,
        lead_style=staged.lead_style,
        lead_instrument=staged.lead_instrument,
        lead_player=staged.lead_player,
        suit_mode=mode_v,
        suit_bass_density=_notes_per_bar(staged.bass_bytes, staged.bar_count),
        suit_chord_density=_notes_per_bar(staged.chords_bytes, staged.bar_count),
        suit_lead_density=_notes_per_bar(staged.lead_bytes, staged.bar_count),
        suit_chord_style=staged.chord_style,
        suit_bass_style=staged.bass_style,
        session_preset=staged.session_preset,
    )
    staged.lead_bytes = l_bytes
    staged.lead_preview = l_prev
    if staged.bridge_live_overlay_active:
        _promote_live_bridge_overlay(staged)
    _SESSIONS[s.id] = staged
    s = staged
    msg = _SUIT_PART_MESSAGES.get(mode_v, "Generated a new lead to suit the current session.")
    return _to_state(s, message=msg)


@router.post("/{session_id}/lanes/{lane}/regenerate", response_model=RegenerateLaneResult)
def regenerate_lane(session_id: str, lane: LaneName) -> RegenerateLaneResult:
    s = _get_session_or_404(session_id)
    staged = replace(s)
    _regenerate_lane_on_stored_session(
        staged,
        lane,
        context=_context_for_lane_regeneration(staged, lane),
    )
    s = _commit_regenerated_lanes(s, staged, [lane])
    return RegenerateLaneResult(session=_to_state(s, message=f"Lane {lane.value} regenerated."), lane=lane)


def _regenerate_bass_bars_on_stored_session(
    s: StoredSession,
    body: RegenerateBassBarsBody,
) -> None:
    """Regenerate a bass range on the provided session object."""

    _require_confirmed_harmony(s)
    if body.bar_start < 0:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_bar_range", "message": "bar_start must be greater than or equal to 0."},
        )
    if body.bar_end <= body.bar_start:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_bar_range", "message": "bar_end must be greater than bar_start."},
        )
    if body.bar_end > s.bar_count:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_bar_range", "message": "bar_end must be less than or equal to session.bar_count."},
        )
    if body.operation == "turnaround" and body.bar_end != body.bar_start + 1:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_bar_range", "message": "A turnaround must target exactly one bar."},
        )
    if not s.bass_bytes:
        raise HTTPException(
            status_code=400,
            detail={"error": "bass_lane_missing", "message": "Generate the bass lane before regenerating selected bars."},
        )

    seed = int(body.seed) if body.seed is not None else _new_bass_seed()
    ctx = _context_for_lane_regeneration(s, LaneName.bass)
    cond = _conditioning_for_generation(s, context=ctx)
    performance_controls = _explicit_bass_performance_controls(s)
    replacement_bytes, replacement_preview, replacement_performance_notes = generator.generate_bass(
        tempo=s.tempo,
        bar_count=s.bar_count,
        key=s.key,
        scale=s.scale,
        bass_style=s.bass_style,
        bass_instrument=s.bass_instrument,
        bass_player=s.bass_player,
        bass_engine=s.bass_engine,
        lock_to_groove=s.bass_lock_to_groove,
        density_bias=s.bass_density_bias,
        expression_amount=s.bass_expression,
        bass_articulation_focus=s.bass_articulation_focus,
        ghost_amount=performance_controls["ghost"],
        mute_amount=performance_controls["mute"],
        slide_amount=performance_controls["slide"],
        legato_amount=performance_controls["legato"],
        chord_progression=s.chord_progression,
        session_preset=s.session_preset,
        context=ctx,
        conditioning=cond,
        seed=seed,
        return_performance_notes=True,
    )
    replacement_bytes = _normalize_bass_bytes_for_session(replacement_bytes, s)
    replacement_performance = _render_bass_performance_bytes(
        clean_bytes=replacement_bytes,
        perf_notes=replacement_performance_notes,
        tempo=s.tempo,
        expression_amount=s.bass_expression,
        articulation_focus=s.bass_articulation_focus,
        timing_humanize=performance_controls["timing_humanize"],
        velocity_humanize=performance_controls["velocity_humanize"],
        instrument_family=s.bass_instrument,
        conditioning=cond,
    )
    replacement_performance = _normalize_bass_bytes_for_session(
        replacement_performance,
        s,
    )
    if body.operation == "turnaround":
        from app.services.bass_turnaround import apply_bass_turnaround

        next_bar = body.bar_end % max(1, s.bar_count)
        harmonic_bar = cond.harmonic_bar(next_bar)
        if harmonic_bar is not None:
            next_root_pc = int(harmonic_bar.root_pc) % 12
        else:
            progression = mt.progression_chords_for_bars(s.chord_progression, s.bar_count)
            if progression:
                next_root_pc = int(progression[next_bar].root_pc) % 12
            else:
                degrees = mt.progression_degrees_for_bars(s.bar_count, s.scale)
                next_root_pc = int(mt.bass_root_midi(s.key, s.scale, degrees[next_bar], octave=2)) % 12
        replacement_bytes = apply_bass_turnaround(
            replacement_bytes,
            tempo=s.tempo,
            bar_index=body.bar_start,
            next_root_pc=next_root_pc,
        )
        replacement_performance = apply_bass_turnaround(
            replacement_performance,
            tempo=s.tempo,
            bar_index=body.bar_start,
            next_root_pc=next_root_pc,
        )
        replacement_preview += f" Explicit turnaround applied to bar {body.bar_start + 1}."
    spliced = splice_bass_bars(
        existing_midi=s.bass_bytes,
        replacement_midi=replacement_bytes,
        tempo=s.tempo,
        bar_start=body.bar_start,
        bar_end=body.bar_end,
        # Clean phrases carry generator micro-timing too. Classify their early
        # downbeats by musical bar, just as the performance lane does below.
        humanize_boundary_seconds=0.02,
    )
    s.bass_bytes = _normalize_bass_bytes_for_session(spliced, s)
    if s.bass_performance_bytes is not None:
        spliced_performance = splice_bass_bars(
            existing_midi=s.bass_performance_bytes,
            replacement_midi=replacement_performance,
            tempo=s.tempo,
            bar_start=body.bar_start,
            bar_end=body.bar_end,
            humanize_boundary_seconds=0.02,
        )
        s.bass_performance_bytes = _normalize_bass_bytes_for_session(
            spliced_performance,
            s,
        )
    s.bass_preview = replacement_preview
    s.bass_seed = seed
    s.current_bass_candidate_run_id = None
    s.current_bass_candidate_take_id = None


@router.post("/{session_id}/lanes/bass/regenerate-bars", response_model=SessionState)
def regenerate_bass_bars(session_id: str, body: RegenerateBassBarsBody) -> SessionState:
    s = _get_session_or_404(session_id)
    staged = replace(s)
    _regenerate_bass_bars_on_stored_session(staged, body)
    s = _commit_regenerated_lanes(s, staged, [LaneName.bass])
    return _to_state(
        s,
        message=f"Regenerated bass bars {body.bar_start}-{body.bar_end - 1}.",
    )


def _render_bass_take_with_seed(
    s: StoredSession,
    *,
    seed: int,
    conditioning: UnifiedConditioning | None,
    context: SessionAnchorContext | None,
    candidate_role: str | None = None,
) -> tuple[bytes, bytes, str]:
    performance_controls = _explicit_bass_performance_controls(s)
    raw_bytes, preview, performance_notes = generator.generate_bass(
        tempo=s.tempo,
        bar_count=s.bar_count,
        key=s.key,
        scale=s.scale,
        bass_style=s.bass_style,
        bass_instrument=s.bass_instrument,
        bass_player=s.bass_player,
        bass_engine=s.bass_engine,
        lock_to_groove=s.bass_lock_to_groove,
        density_bias=s.bass_density_bias,
        expression_amount=s.bass_expression,
        bass_articulation_focus=s.bass_articulation_focus,
        ghost_amount=performance_controls["ghost"],
        mute_amount=performance_controls["mute"],
        slide_amount=performance_controls["slide"],
        legato_amount=performance_controls["legato"],
        candidate_role=candidate_role,
        chord_progression=s.chord_progression,
        session_preset=s.session_preset,
        context=context,
        conditioning=conditioning,
        seed=int(seed),
        return_performance_notes=True,
    )
    clean = _normalize_bass_bytes_for_session(raw_bytes, s)
    performance = _render_bass_performance_bytes(
        clean_bytes=clean,
        perf_notes=performance_notes,
        tempo=s.tempo,
        expression_amount=s.bass_expression,
        articulation_focus=s.bass_articulation_focus,
        timing_humanize=performance_controls["timing_humanize"],
        velocity_humanize=performance_controls["velocity_humanize"],
        instrument_family=s.bass_instrument,
        conditioning=conditioning,
    )
    return (
        clean,
        _normalize_bass_bytes_for_session(performance, s),
        preview,
    )


def _unpack_candidate_render(
    result: tuple[bytes, bytes, str] | tuple[bytes, str],
) -> tuple[bytes, bytes, str]:
    """Accept legacy two-value test/adapter renderers as clean-only takes."""

    if len(result) == 2:
        clean, preview = result
        return clean, clean, preview
    clean, performance, preview = result
    return clean, performance, preview


def _candidate_store_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "error": "candidate_store_unavailable",
            "message": (
                "Bass candidate history could not be read or written safely. "
                "The existing store was left untouched."
            ),
        },
    )


def _append_candidate_run_or_503(run: dict[str, object]) -> None:
    try:
        bass_candidate_store.append_run(run)
    except bass_candidate_store.CandidateStoreError as exc:
        raise _candidate_store_unavailable() from exc


def _candidate_runs_for_session_or_503(
    session_id: str,
) -> list[dict[str, object]]:
    try:
        return bass_candidate_store.list_runs_for_session(session_id)
    except bass_candidate_store.CandidateStoreError as exc:
        raise _candidate_store_unavailable() from exc


def _candidate_run_for_session_or_503(
    session_id: str,
    run_id: str,
) -> dict[str, object] | None:
    try:
        return bass_candidate_store.get_run_for_session(session_id, run_id)
    except bass_candidate_store.CandidateStoreError as exc:
        raise _candidate_store_unavailable() from exc


def _invalid_candidate_metadata(
    raw: dict[str, object],
) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": "candidate_run_metadata_invalid",
            "run_id": str(raw.get("run_id", "")),
            "message": (
                "Stored candidate metadata is invalid. Generate a fresh "
                "candidate run."
            ),
        },
    )


def _public_candidate_run_unchecked(
    raw: dict[str, object],
) -> BassCandidateRun:
    takes_raw = raw.get("takes")
    takes: list[BassCandidateTake] = []
    if isinstance(takes_raw, list):
        for t in takes_raw:
            if not isinstance(t, dict):
                continue
            takes.append(
                BassCandidateTake(
                    take_id=str(t.get("take_id", "")),
                    seed=int(t.get("seed", 0)),
                    note_count=int(t.get("note_count", 0)),
                    byte_length=int(t.get("byte_length", 0)),
                    midi_sha256=str(t.get("midi_sha256", "") or ""),
                    performance_byte_length=int(
                        t.get("performance_byte_length", 0) or 0
                    ),
                    performance_midi_sha256=str(
                        t.get("performance_midi_sha256", "") or ""
                    ),
                    preview=str(t.get("preview", "") or ""),
                    label=(str(t.get("label")) if t.get("label") is not None else None),
                    template_id=(str(t.get("template_id")) if t.get("template_id") is not None else None),
                    quality_total=float(t.get("quality_total", 0.0) or 0.0),
                    quality_scores=dict(t.get("quality_scores", {}) if isinstance(t.get("quality_scores"), dict) else {}),
                    quality_reason=str(t.get("quality_reason", "") or ""),
                    selection_stage=(str(t["selection_stage"]) if t.get("selection_stage") is not None else None),
                    motif_family=(str(t["motif_family"]) if t.get("motif_family") is not None else None),
                    signature_distance=(float(t["signature_distance"]) if t.get("signature_distance") is not None else None),
                    quality_floor_cutoff=(float(t["quality_floor_cutoff"]) if t.get("quality_floor_cutoff") is not None else None),
                    top_pool_score=(float(t["top_pool_score"]) if t.get("top_pool_score") is not None else None),
                    candidate_role=(str(t["candidate_role"]) if t.get("candidate_role") is not None else None),
                    candidate_role_label=(
                        str(t["candidate_role_label"]) if t.get("candidate_role_label") is not None else None
                    ),
                    candidate_role_description=(
                        str(t["candidate_role_description"])
                        if t.get("candidate_role_description") is not None
                        else None
                    ),
                )
            )
    raw_performance_controls = raw.get("bass_performance_controls")
    if isinstance(raw_performance_controls, dict):
        public_performance_controls = {
            str(key): float(value)
            for key, value in raw_performance_controls.items()
        }
    else:
        # Legacy candidate runs remain inspectable. Promotion still fails
        # closed through the generation-context version check.
        public_performance_controls = resolve_bass_performance_controls(
            None,
            focus=str(raw.get("bass_articulation_focus", "natural")),
            expression_amount=float(raw.get("bass_expression", 0.5)),
            style=str(raw.get("bass_style", "supportive")),
            instrument_family=str(
                raw.get("bass_instrument", "finger_bass")
            ),
        ).requested

    return BassCandidateRun(
        run_id=str(raw.get("run_id", "")),
        session_id=str(raw.get("session_id", "")),
        created_at=str(raw.get("created_at", "")),
        generation_context_version=int(raw.get("generation_context_version", 0) or 0),
        generation_context_fingerprint=str(raw.get("generation_context_fingerprint", "") or ""),
        generation_evidence_fingerprint=str(
            raw.get("generation_evidence_fingerprint", "") or ""
        ),
        take_count=int(raw.get("take_count", len(takes))),
        bass_style=str(raw.get("bass_style", "supportive")),
        bass_engine=str(raw.get("bass_engine", "baseline")),
        bass_player=(str(raw.get("bass_player")) if raw.get("bass_player") is not None else None),
        bass_instrument=str(raw.get("bass_instrument", "finger_bass")),
        bass_articulation_focus=str(
            raw.get("bass_articulation_focus", "natural")
        ),
        bass_expression=float(raw.get("bass_expression", 0.5)),
        bass_performance_controls=public_performance_controls,
        bass_density_bias=float(raw.get("bass_density_bias", 0.0)),
        variation_mode=str(raw.get("variation_mode", "ranked")),
        clip_id=(str(raw.get("clip_id")) if raw.get("clip_id") is not None else None),
        conditioning_tempo=int(raw.get("conditioning_tempo", 120)),
        conditioning_phase_offset=int(raw.get("conditioning_phase_offset", 0)),
        conditioning_phase_confidence=float(raw.get("conditioning_phase_confidence", 0.0)),
        conditioning_sections_count=int(raw.get("conditioning_sections_count", 0)),
        conditioning_harmonic_bar_count=int(raw.get("conditioning_harmonic_bar_count", 0)),
        takes=takes,
    )


def _public_candidate_run(raw: dict[str, object]) -> BassCandidateRun:
    try:
        return _public_candidate_run_unchecked(raw)
    except (TypeError, ValueError, OverflowError, ValidationError) as exc:
        raise _invalid_candidate_metadata(raw) from exc


def _find_take_payload(raw_run: dict[str, object], take_id: str) -> dict[str, object] | None:
    takes_raw = raw_run.get("takes")
    if not isinstance(takes_raw, list):
        return None
    target = str(take_id).strip()
    for t in takes_raw:
        if isinstance(t, dict) and str(t.get("take_id", "")).strip() == target:
            return t
    return None


def _require_current_candidate_context(
    s: StoredSession,
    raw_run: dict[str, object],
) -> None:
    try:
        run_version = int(
            raw_run.get("generation_context_version", 0) or 0
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise _invalid_candidate_metadata(raw_run) from exc
    run_fingerprint = str(raw_run.get("generation_context_fingerprint", "") or "")
    current_fingerprint = _candidate_generation_context_fingerprint(s)
    if (
        run_version == _CANDIDATE_GENERATION_CONTEXT_VERSION
        and run_fingerprint
        and run_fingerprint == current_fingerprint
    ):
        return
    unverifiable = not run_fingerprint or run_version != _CANDIDATE_GENERATION_CONTEXT_VERSION
    raise HTTPException(
        status_code=409,
        detail={
            "error": "stale_candidate_generation_context",
            "message": (
                "This candidate run no longer matches the session's generation context. "
                "Generate a fresh candidate run before promotion."
            ),
            "reason": "legacy_or_unverifiable" if unverifiable else "session_context_changed",
            "run_context_version": run_version,
            "current_context_version": _CANDIDATE_GENERATION_CONTEXT_VERSION,
        },
    )


def _candidate_payload_bytes(
    raw_take: dict[str, object],
    *,
    b64_field: str,
    digest_field: str,
    length_field: str,
    required: bool,
) -> bytes | None:
    b64 = raw_take.get(b64_field)
    if not required and (b64 is None or b64 == ""):
        return None
    if not isinstance(b64, str) or not b64:
        raise HTTPException(
            status_code=400,
            detail={"error": "candidate_take_payload_missing", "message": "Candidate take MIDI payload missing."},
        )
    try:
        data = base64.b64decode(b64.encode("ascii"), validate=True)
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "candidate_take_payload_invalid", "message": str(exc)},
        ) from exc
    expected_digest = raw_take.get(digest_field)
    actual_digest = hashlib.sha256(data).hexdigest()
    if (
        not isinstance(expected_digest, str)
        or len(expected_digest) != 64
        or expected_digest != actual_digest
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "candidate_take_integrity_failed",
                "field": b64_field,
                "message": "Candidate MIDI failed its stored SHA-256 integrity check.",
            },
        )
    expected_length = raw_take.get(length_field)
    if (
        isinstance(expected_length, bool)
        or not isinstance(expected_length, int)
        or expected_length != len(data)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "candidate_take_integrity_failed",
                "field": length_field,
                "message": "Candidate MIDI byte length does not match its stored metadata.",
            },
        )
    return data


def _take_bytes_or_400(raw_take: dict[str, object]) -> bytes:
    data = _candidate_payload_bytes(
        raw_take,
        b64_field="midi_b64",
        digest_field="midi_sha256",
        length_field="byte_length",
        required=True,
    )
    assert data is not None
    return data


def _take_performance_bytes_or_400(
    raw_take: dict[str, object],
) -> bytes | None:
    return _candidate_payload_bytes(
        raw_take,
        b64_field="performance_midi_b64",
        digest_field="performance_midi_sha256",
        length_field="performance_byte_length",
        required=False,
    )


def _candidate_take_bytes_for_mode(
    raw_take: dict[str, object],
    mode: Literal["clean", "performance"],
) -> tuple[bytes, Literal["clean", "performance"]]:
    """Resolve a candidate preview payload without breaking legacy runs.

    Candidate downloads historically exposed clean composition MIDI. Keep
    that endpoint default, while allowing callers to request the exact frozen
    performance that promotion will install. Older runs without a frozen
    performance safely fall back to their verified clean payload.
    """

    if mode == "performance":
        performance = _take_performance_bytes_or_400(raw_take)
        if performance is not None:
            return performance, "performance"
    return _take_bytes_or_400(raw_take), "clean"


def _candidate_midi_payload(
    clean: bytes,
    performance: bytes,
) -> dict[str, object]:
    return {
        "midi_b64": base64.b64encode(clean).decode("ascii"),
        "midi_sha256": hashlib.sha256(clean).hexdigest(),
        "performance_midi_b64": base64.b64encode(performance).decode("ascii"),
        "performance_midi_sha256": hashlib.sha256(performance).hexdigest(),
        "performance_byte_length": len(performance),
    }


def _validated_candidate_generation_evidence(
    raw_run: dict[str, object],
) -> tuple[str, str, SourceAnalysis | None, SourceAnalysis | None]:
    raw_evidence = raw_run.get("generation_evidence")
    expected = raw_run.get("generation_evidence_fingerprint")
    if not isinstance(raw_evidence, dict) or not isinstance(expected, str):
        raise HTTPException(
            status_code=409,
            detail={
                "error": "candidate_generation_evidence_unverifiable",
                "message": "Candidate generation evidence is missing; generate a fresh run.",
            },
        )
    actual = _candidate_generation_evidence_fingerprint(raw_evidence)
    if len(expected) != 64 or expected != actual:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "candidate_generation_evidence_integrity_failed",
                "message": "Candidate generation evidence failed its integrity check.",
            },
        )
    try:
        key = mt.normalize_key(str(raw_evidence.get("key", "")))
        mt.key_root_pc(key)
        scale = mt.normalize_scale(str(raw_evidence.get("scale", "")))
        source_raw = raw_evidence.get("source_analysis_override")
        groove_raw = raw_evidence.get("groove_reference_analysis_override")
        source = (
            SourceAnalysis.model_validate(source_raw)
            if source_raw is not None
            else None
        )
        groove = (
            SourceAnalysis.model_validate(groove_raw)
            if groove_raw is not None
            else None
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "candidate_generation_evidence_invalid",
                "message": str(exc),
            },
        ) from exc
    return key, scale, source, groove


def _is_guarded_vocabulary_take(raw_take: dict[str, object]) -> bool:
    template_id = raw_take.get("template_id")
    label = raw_take.get("label")
    return isinstance(template_id, str) and bool(template_id.strip()) and isinstance(label, str) and bool(label.strip())


def _signature_distance(a: tuple[tuple[int, ...], ...], b: tuple[tuple[int, ...], ...]) -> float:
    aset = {(bar, int(slot)) for bar, row in enumerate(a) for slot in row}
    bset = {(bar, int(slot)) for bar, row in enumerate(b) for slot in row}
    if not aset and not bset:
        return 0.0
    return 1.0 - (len(aset & bset) / float(max(1, len(aset | bset))))


def _motif_family(signature: tuple[tuple[int, ...], ...], *, style: str) -> str:
    bars = max(1, len(signature))
    slots = [int(s) for row in signature for s in row]
    avg_hits = len(slots) / float(bars)
    offbeats = sum(1 for s in slots if s % 4 != 0)
    offbeat_rate = offbeats / float(max(1, len(slots)))
    has_late = any(s >= 12 for s in slots)
    has_mid = any(s in (6, 7, 9, 10) for s in slots)
    root_rate = sum(1 for row in signature if 0 in row) / float(bars)
    if style == "supportive":
        if avg_hits <= 2.5 and root_rate >= 0.75:
            return "supportive_pocket"
        if has_late:
            return "supportive_cadence_tail"
        return "supportive_answer"
    if style == "melodic":
        if offbeat_rate <= 0.35:
            return "melodic_beatline"
        if has_mid:
            return "melodic_contour"
        return "melodic_sync"
    if style == "slap":
        if avg_hits >= 4.8:
            return "slap_dense_pop"
        if has_late:
            return "slap_tail_pop"
        return "slap_thumb_space"
    if style == "rhythmic":
        return "rhythmic_sync" if offbeat_rate >= 0.48 else "rhythmic_grid"
    return "fusion_busy" if avg_hits >= 4.8 else "fusion_phrase"


def _style_diversity_gate(style: str) -> tuple[float, int]:
    if style == "supportive":
        return 0.3, 2
    if style == "melodic":
        return 0.28, 2
    if style == "slap":
        return 0.2, 3
    if style == "rhythmic":
        return 0.16, 3
    return 0.22, 3


def _style_floor_margin(style: str) -> float:
    # Max allowed drop from top hidden-pool quality in strict/relaxed passes.
    if style == "slap":
        return 0.11
    if style == "rhythmic":
        return 0.085
    if style == "fusion":
        return 0.075
    if style == "supportive":
        return 0.07
    return 0.08


def _generate_controlled_role_bass_candidates(
    *,
    s: StoredSession,
    body: GenerateBassCandidatesBody,
    context: SessionAnchorContext,
    conditioning: UnifiedConditioning,
    generation_context_fingerprint: str,
    generation_evidence: dict[str, object | None],
    generation_evidence_fingerprint: str,
    base_seed: int,
    run_id: str,
    created_at: str,
) -> BassCandidateRun:
    """Return purposeful alternatives that each change one musical dimension."""
    if s.bass_engine != "phrase_v2":
        raise HTTPException(
            status_code=409,
            detail={
                "error": "controlled_roles_requires_phrase_v2",
                "message": "Purposeful four-role comparison requires Phrase Engine v2.",
            },
        )
    if s.bass_player:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "controlled_roles_requires_neutral_player",
                "message": (
                    "Purposeful role comparison currently requires the neutral player profile; "
                    "turn the named player profile off first."
                ),
            },
        )

    requested = int(body.take_count)
    requested_roles = [bass_candidate_role_for_index(i) for i in range(requested)]
    attempts_per_role = max(3, min(9, (requested * 3 + len(ROLE_ORDER) - 1) // len(ROLE_ORDER)))
    strict_harmonic_guard = bool(s.reference_audio_path and s.chord_progression)
    harmonically_rejected = 0
    pools: dict[str, list[tuple[float, tuple[tuple[int, ...], ...], BassCandidateTake, dict[str, object]]]] = {
        role: [] for role in ROLE_ORDER
    }
    seed_cursor = 0

    for role in ROLE_ORDER:
        spec = bass_candidate_role_spec(role)
        assert spec is not None
        for _ in range(attempts_per_role):
            seed_i = int(base_seed) + seed_cursor
            seed_cursor += 1
            private_take_id = f"{run_id}_{role}_{seed_i}"
            data, performance_data, preview = _unpack_candidate_render(
                _render_bass_take_with_seed(
                    s,
                    seed=seed_i,
                    conditioning=conditioning,
                    context=context,
                    candidate_role=role,
                )
            )
            notes = extract_lane_notes(data)
            if strict_harmonic_guard and count_unsupported_structural_notes(
                notes,
                tempo=conditioning.tempo,
                conditioning=conditioning,
                style=s.bass_style,
            ):
                harmonically_rejected += 1
                continue
            quality = analyze_bass_take(
                notes,
                tempo=conditioning.tempo,
                bar_count=conditioning.bar_count,
                key=s.key,
                scale=s.scale,
                style=s.bass_style,
                conditioning=conditioning,
                context=context,
            )
            family = f"controlled_{role}"
            take = BassCandidateTake(
                take_id=private_take_id,
                seed=seed_i,
                note_count=len(notes),
                byte_length=len(data),
                midi_sha256=hashlib.sha256(data).hexdigest(),
                performance_byte_length=len(performance_data),
                performance_midi_sha256=hashlib.sha256(
                    performance_data
                ).hexdigest(),
                preview=preview,
                quality_total=quality.total,
                quality_scores=quality.scores,
                quality_reason=quality.reason,
                motif_family=family,
                candidate_role=role,
                candidate_role_label=spec.label,
                candidate_role_description=spec.description,
            )
            row: dict[str, object] = {
                "take_id": private_take_id,
                "seed": seed_i,
                "note_count": len(notes),
                "byte_length": len(data),
                "preview": preview,
                "quality_total": quality.total,
                "quality_scores": quality.scores,
                "quality_reason": quality.reason,
                "motif_family": family,
                "candidate_role": role,
                "candidate_role_label": spec.label,
                "candidate_role_description": spec.description,
                **_candidate_midi_payload(data, performance_data),
            }
            pools[role].append((quality.total, quality.signature, take, row))

    for pool in pools.values():
        pool.sort(key=lambda item: (item[0], -item[2].note_count), reverse=True)

    missing_roles = sorted({role for role in requested_roles if not pools[role]})
    if missing_roles:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "harmonic_candidate_shortfall",
                "message": (
                    "The confirmed chord map rejected every safe candidate for one or more "
                    "purposeful roles. No unsafe fallback candidates were returned."
                ),
                "missing_roles": missing_roles,
                "harmonically_rejected": harmonically_rejected,
            },
        )

    min_distance, _ = _style_diversity_gate(s.bass_style)
    selected_signatures: list[tuple[tuple[int, ...], ...]] = []
    role_offsets: dict[str, int] = {role: 0 for role in ROLE_ORDER}
    takes: list[BassCandidateTake] = []
    take_rows: list[dict[str, object]] = []
    hidden_pool_size = sum(len(pool) for pool in pools.values())

    for role in requested_roles:
        pool = pools[role]
        role_top_score = float(pool[0][0])
        floor_cutoff = max(0.0, role_top_score - _style_floor_margin(s.bass_style))
        start_at = role_offsets[role]
        eligible = [
            item
            for item in pool[start_at:]
            if float(item[0]) >= floor_cutoff
        ]
        if not eligible:
            eligible = pool[start_at:] or pool

        chosen = None
        if selected_signatures:
            for item in eligible:
                if all(
                    _signature_distance(item[1], existing) >= min_distance * 0.45
                    for existing in selected_signatures
                ):
                    chosen = item
                    break
        if chosen is None:
            chosen = eligible[0]

        score, signature, take, row = chosen
        role_offsets[role] = min(len(pool), pool.index(chosen) + 1)
        sig_dist = (
            min(_signature_distance(signature, existing) for existing in selected_signatures)
            if selected_signatures
            else None
        )
        stage = "strict" if sig_dist is None or sig_dist >= min_distance * 0.45 else "relaxed"
        selected_signatures.append(signature)
        public_take_id = f"{run_id}_t{len(takes) + 1}"
        take = take.model_copy(
            update={
                "take_id": public_take_id,
                "selection_stage": stage,
                "signature_distance": sig_dist,
                "quality_floor_cutoff": floor_cutoff,
                "top_pool_score": role_top_score,
            }
        )
        row = dict(row)
        row.update(
            {
                "take_id": public_take_id,
                "selection_stage": stage,
                "signature_distance": sig_dist,
                "quality_floor_cutoff": floor_cutoff,
                "top_pool_score": role_top_score,
                "rank": len(takes) + 1,
                "hidden_pool_size": hidden_pool_size,
            }
        )
        takes.append(take)
        take_rows.append(row)

    takes = [
        take.model_copy(
            update={
                "quality_reason": (
                    f"purposeful role {index + 1}/{len(takes)}; "
                    f"role={take.candidate_role}; {take.quality_reason}"
                )
            }
        )
        for index, take in enumerate(takes)
    ]
    for index, row in enumerate(take_rows):
        row["quality_reason"] = takes[index].quality_reason

    run = BassCandidateRun(
        run_id=run_id,
        session_id=s.id,
        created_at=created_at,
        generation_context_version=_CANDIDATE_GENERATION_CONTEXT_VERSION,
        generation_context_fingerprint=generation_context_fingerprint,
        generation_evidence_fingerprint=generation_evidence_fingerprint,
        take_count=len(takes),
        bass_style=s.bass_style,
        bass_engine=s.bass_engine,
        bass_player=s.bass_player,
        bass_instrument=s.bass_instrument,
        bass_articulation_focus=s.bass_articulation_focus,
        bass_expression=s.bass_expression,
        bass_performance_controls=BassPerformanceControls.model_validate(
            resolve_bass_performance_controls(
                s.bass_performance_controls,
                focus=s.bass_articulation_focus,
                expression_amount=s.bass_expression,
                style=s.bass_style,
                instrument_family=s.bass_instrument,
            ).requested
        ),
        bass_density_bias=s.bass_density_bias,
        variation_mode="controlled_roles",
        clip_id=body.clip_id,
        conditioning_tempo=conditioning.tempo,
        conditioning_phase_offset=conditioning.beat_phase_offset_beats,
        conditioning_phase_confidence=conditioning.beat_phase_confidence,
        conditioning_sections_count=len(conditioning.sections),
        conditioning_harmonic_bar_count=len(conditioning.harmonic_bars),
        takes=takes,
    )
    run_payload = run.model_dump(mode="json")
    run_payload["takes"] = take_rows
    run_payload["generation_evidence"] = generation_evidence
    _append_candidate_run_or_503(run_payload)
    return run


@router.post("/{session_id}/bass-candidates", response_model=BassCandidateRun)
def generate_bass_candidates(session_id: str, body: GenerateBassCandidatesBody = GenerateBassCandidatesBody()) -> BassCandidateRun:
    """
    Generate multiple comparable bass takes against one unified conditioning snapshot.
    This does not replace the current stored bass lane; it returns candidate metadata only.
    """
    s = _get_session_or_404(session_id)
    _require_confirmed_harmony(s)
    generation_context_fingerprint = _candidate_generation_context_fingerprint(s)
    generation_evidence = _candidate_generation_evidence_payload(s)
    generation_evidence_fingerprint = (
        _candidate_generation_evidence_fingerprint(generation_evidence)
    )
    ctx = build_session_context(s)
    cond = _conditioning_for_generation(s, context=ctx)
    base_seed = int(body.seed) if body.seed is not None else _new_bass_seed()
    run_id = f"cand_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    created_at = datetime.now(timezone.utc).isoformat()
    if body.variation_mode == "controlled_roles":
        return _generate_controlled_role_bass_candidates(
            s=s,
            body=body,
            context=ctx,
            conditioning=cond,
            generation_context_fingerprint=generation_context_fingerprint,
            generation_evidence=generation_evidence,
            generation_evidence_fingerprint=generation_evidence_fingerprint,
            base_seed=base_seed,
            run_id=run_id,
            created_at=created_at,
        )

    requested = int(body.take_count)
    hidden_count = max(requested, min(36, requested * 3))
    strict_harmonic_guard = bool(s.reference_audio_path and s.chord_progression)
    harmonically_rejected = 0
    scored_pool: list[tuple[float, tuple[tuple[int, ...], ...], str, BassCandidateTake, dict[str, object]]] = []
    for i in range(hidden_count):
        seed_i = base_seed + i
        take_id = f"{run_id}_p{i + 1}"
        data, performance_data, preview = _unpack_candidate_render(
            _render_bass_take_with_seed(
                s,
                seed=seed_i,
                conditioning=cond,
                context=ctx,
            )
        )
        notes = extract_lane_notes(data)
        if strict_harmonic_guard and count_unsupported_structural_notes(
            notes,
            tempo=cond.tempo if cond is not None else s.tempo,
            conditioning=cond,
            style=s.bass_style,
        ):
            harmonically_rejected += 1
            continue
        quality = analyze_bass_take(
            notes,
            tempo=cond.tempo if cond is not None else s.tempo,
            bar_count=cond.bar_count if cond is not None else s.bar_count,
            key=s.key,
            scale=s.scale,
            style=s.bass_style,
            conditioning=cond,
            context=ctx,
        )
        family = _motif_family(quality.signature, style=s.bass_style)
        take = BassCandidateTake(
            take_id=take_id,
            seed=seed_i,
            note_count=len(notes),
            byte_length=len(data),
            midi_sha256=hashlib.sha256(data).hexdigest(),
            performance_byte_length=len(performance_data),
            performance_midi_sha256=hashlib.sha256(
                performance_data
            ).hexdigest(),
            preview=preview,
            quality_total=quality.total,
            quality_scores=quality.scores,
            quality_reason=quality.reason,
        )
        row = {
            "take_id": take_id,
            "seed": seed_i,
            "note_count": len(notes),
            "byte_length": len(data),
            "preview": preview,
            "quality_total": quality.total,
            "quality_scores": quality.scores,
            "quality_reason": quality.reason,
            "motif_family": family,
            **_candidate_midi_payload(data, performance_data),
        }
        scored_pool.append((quality.total, quality.signature, family, take, row))

    scored_pool.sort(key=lambda item: (item[0], -item[3].note_count), reverse=True)
    vocabulary_pool: list[tuple[float, tuple[tuple[int, ...], ...], str, BassCandidateTake, dict[str, object]]] = []
    vocabulary_candidates = (
        generate_vocabulary_candidates(
            tempo=s.tempo,
            bar_count=s.bar_count,
            bass_style=s.bass_style,
            chord_progression=s.chord_progression,
            conditioning=cond,
            context=ctx,
            seed=base_seed,
        )
        if s.bass_articulation_focus == "natural"
        else ()
    )
    for vocab in vocabulary_candidates:
        vocab_bytes = _normalize_bass_bytes_for_session(vocab.midi_bytes, s)
        notes = extract_lane_notes(vocab_bytes)
        if strict_harmonic_guard and count_unsupported_structural_notes(
            notes,
            tempo=cond.tempo if cond is not None else s.tempo,
            conditioning=cond,
            style=s.bass_style,
        ):
            harmonically_rejected += 1
            continue
        quality = analyze_bass_take(
            notes,
            tempo=cond.tempo if cond is not None else s.tempo,
            bar_count=cond.bar_count if cond is not None else s.bar_count,
            key=s.key,
            scale=s.scale,
            style=s.bass_style,
            conditioning=cond,
            context=ctx,
        )
        family = f"sub_one_{vocab.template_id}"
        take_id = f"{run_id}_v{len(vocabulary_pool) + 1}"
        take = BassCandidateTake(
            take_id=take_id,
            seed=vocab.seed,
            note_count=len(notes),
            byte_length=len(vocab_bytes),
            midi_sha256=hashlib.sha256(vocab_bytes).hexdigest(),
            performance_byte_length=len(vocab_bytes),
            performance_midi_sha256=hashlib.sha256(
                vocab_bytes
            ).hexdigest(),
            preview=vocab.preview,
            label=vocab.label,
            template_id=vocab.template_id,
            quality_total=quality.total,
            quality_scores=quality.scores,
            quality_reason=quality.reason,
        )
        row = {
            "take_id": take_id,
            "seed": vocab.seed,
            "note_count": len(notes),
            "byte_length": len(vocab_bytes),
            "preview": vocab.preview,
            "label": vocab.label,
            "template_id": vocab.template_id,
            "quality_total": quality.total,
            "quality_scores": quality.scores,
            "quality_reason": quality.reason,
            "motif_family": family,
            **_candidate_midi_payload(vocab_bytes, vocab_bytes),
        }
        vocabulary_pool.append((quality.total, quality.signature, family, take, row))

    min_distance, max_family_count = _style_diversity_gate(s.bass_style)
    top_pool_score = float(scored_pool[0][0]) if scored_pool else 0.0
    if vocabulary_pool:
        top_pool_score = max(top_pool_score, max(float(item[0]) for item in vocabulary_pool))
    floor_cutoff = max(0.0, min(1.0, top_pool_score - _style_floor_margin(s.bass_style)))
    takes: list[BassCandidateTake] = []
    take_rows: list[dict[str, object]] = []
    selected_pool_ids: set[str] = set()
    selected_signatures: list[tuple[tuple[int, ...], ...]] = []
    family_counts: dict[str, int] = {}

    # Vocabulary pass: when eligible, keep the first Sub One labels visible in the run.
    for score, sig, family, take, row in vocabulary_pool:
        if len(takes) >= requested:
            break
        sig_dist = min(_signature_distance(sig, e) for e in selected_signatures) if selected_signatures else None
        selected_pool_ids.add(take.take_id)
        family_counts[family] = family_counts.get(family, 0) + 1
        selected_signatures.append(sig)
        public_take_id = f"{run_id}_t{len(takes) + 1}"
        take = take.model_copy(update={
            "take_id": public_take_id,
            "selection_stage": "strict",
            "motif_family": family,
            "signature_distance": sig_dist,
            "quality_floor_cutoff": floor_cutoff,
            "top_pool_score": top_pool_score,
        })
        row = dict(row)
        row["take_id"] = public_take_id
        row["selection_stage"] = "strict"
        row["signature_distance"] = sig_dist
        row["quality_floor_cutoff"] = floor_cutoff
        row["top_pool_score"] = top_pool_score
        takes.append(take)
        take_rows.append(row)

    # Strict pass: style-locked distance + motif-family spread.
    for score, sig, family, take, row in scored_pool:
        if len(takes) >= requested:
            break
        if score < floor_cutoff:
            continue
        if any(_signature_distance(sig, existing) < min_distance for existing in selected_signatures):
            continue
        if family_counts.get(family, 0) >= max_family_count:
            continue
        sig_dist = min(_signature_distance(sig, e) for e in selected_signatures) if selected_signatures else None
        selected_pool_ids.add(take.take_id)
        family_counts[family] = family_counts.get(family, 0) + 1
        selected_signatures.append(sig)
        public_take_id = f"{run_id}_t{len(takes) + 1}"
        take = take.model_copy(update={
            "take_id": public_take_id,
            "selection_stage": "strict",
            "motif_family": family,
            "signature_distance": sig_dist,
            "quality_floor_cutoff": floor_cutoff,
            "top_pool_score": top_pool_score,
        })
        row = dict(row)
        row["take_id"] = public_take_id
        row["selection_stage"] = "strict"
        row["signature_distance"] = sig_dist
        row["quality_floor_cutoff"] = floor_cutoff
        row["top_pool_score"] = top_pool_score
        takes.append(take)
        take_rows.append(row)

    # Relaxed pass: keep exact-dedupe protection, allow family overflow and looser distance.
    for score, sig, family, take, row in scored_pool:
        if len(takes) >= requested:
            break
        if take.take_id in selected_pool_ids:
            continue
        if score < floor_cutoff:
            continue
        if any(_signature_distance(sig, existing) < (min_distance * 0.6) for existing in selected_signatures):
            continue
        sig_dist = min(_signature_distance(sig, e) for e in selected_signatures) if selected_signatures else None
        selected_pool_ids.add(take.take_id)
        family_counts[family] = family_counts.get(family, 0) + 1
        selected_signatures.append(sig)
        public_take_id = f"{run_id}_t{len(takes) + 1}"
        take = take.model_copy(update={
            "take_id": public_take_id,
            "selection_stage": "relaxed",
            "motif_family": family,
            "signature_distance": sig_dist,
            "quality_floor_cutoff": floor_cutoff,
            "top_pool_score": top_pool_score,
        })
        row = dict(row)
        row["take_id"] = public_take_id
        row["selection_stage"] = "relaxed"
        row["signature_distance"] = sig_dist
        row["quality_floor_cutoff"] = floor_cutoff
        row["top_pool_score"] = top_pool_score
        takes.append(take)
        take_rows.append(row)

    # Final fill to guarantee requested count.
    for _score, sig, family, take, row in scored_pool:
        if len(takes) >= requested:
            break
        if take.take_id in selected_pool_ids:
            continue
        sig_dist = min(_signature_distance(sig, e) for e in selected_signatures) if selected_signatures else None
        selected_pool_ids.add(take.take_id)
        family_counts[family] = family_counts.get(family, 0) + 1
        selected_signatures.append(sig)
        public_take_id = f"{run_id}_t{len(takes) + 1}"
        take = take.model_copy(update={
            "take_id": public_take_id,
            "selection_stage": "final_fill",
            "motif_family": family,
            "signature_distance": sig_dist,
            "quality_floor_cutoff": floor_cutoff,
            "top_pool_score": top_pool_score,
        })
        row = dict(row)
        row["take_id"] = public_take_id
        row["selection_stage"] = "final_fill"
        row["signature_distance"] = sig_dist
        row["quality_floor_cutoff"] = floor_cutoff
        row["top_pool_score"] = top_pool_score
        takes.append(take)
        take_rows.append(row)

    if len(takes) < requested:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "harmonic_candidate_shortfall",
                "message": (
                    "The confirmed chord map rejected too many structurally unsupported "
                    "bass takes. No unsafe fallback candidates were returned."
                ),
                "requested": requested,
                "eligible": len(takes),
                "harmonically_rejected": harmonically_rejected,
            },
        )

    for idx, row in enumerate(take_rows):
        row["rank"] = idx + 1
        row["hidden_pool_size"] = hidden_count
    takes = [
        t.model_copy(update={"quality_reason": f"rank {i + 1}/{hidden_count}; family={take_rows[i].get('motif_family', 'unknown')}; {t.quality_reason}"})
        for i, t in enumerate(takes)
    ]
    for i, row in enumerate(take_rows):
        row["quality_reason"] = takes[i].quality_reason

    run = BassCandidateRun(
        run_id=run_id,
        session_id=s.id,
        created_at=created_at,
        generation_context_version=_CANDIDATE_GENERATION_CONTEXT_VERSION,
        generation_context_fingerprint=generation_context_fingerprint,
        generation_evidence_fingerprint=generation_evidence_fingerprint,
        take_count=len(takes),
        bass_style=s.bass_style,
        bass_engine=s.bass_engine,
        bass_player=s.bass_player,
        bass_instrument=s.bass_instrument,
        bass_articulation_focus=s.bass_articulation_focus,
        bass_expression=s.bass_expression,
        bass_performance_controls=BassPerformanceControls.model_validate(
            resolve_bass_performance_controls(
                s.bass_performance_controls,
                focus=s.bass_articulation_focus,
                expression_amount=s.bass_expression,
                style=s.bass_style,
                instrument_family=s.bass_instrument,
            ).requested
        ),
        bass_density_bias=s.bass_density_bias,
        variation_mode="ranked",
        clip_id=body.clip_id,
        conditioning_tempo=cond.tempo if cond is not None else s.tempo,
        conditioning_phase_offset=cond.beat_phase_offset_beats if cond is not None else 0,
        conditioning_phase_confidence=cond.beat_phase_confidence if cond is not None else 0.0,
        conditioning_sections_count=len(cond.sections) if cond is not None else 0,
        conditioning_harmonic_bar_count=len(cond.harmonic_bars) if cond is not None else 0,
        takes=takes,
    )
    run_payload = run.model_dump(mode="json")
    run_payload["takes"] = take_rows
    run_payload["generation_evidence"] = generation_evidence
    _append_candidate_run_or_503(run_payload)
    return run


@router.get("/{session_id}/bass-candidates", response_model=list[BassCandidateRun])
def list_bass_candidates(session_id: str) -> list[BassCandidateRun]:
    _ = _get_session_or_404(session_id)
    rows = _candidate_runs_for_session_or_503(session_id)
    return [_public_candidate_run(r) for r in rows]


@router.get("/{session_id}/bass-candidates/{run_id}/{take_id}")
def download_bass_candidate_take(
    session_id: str,
    run_id: str,
    take_id: str,
    mode: Literal["clean", "performance"] = "clean",
):
    _ = _get_session_or_404(session_id)
    run = _candidate_run_for_session_or_503(session_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_run_not_found", "run_id": run_id})
    take = _find_take_payload(run, take_id)
    if take is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_take_not_found", "take_id": take_id})
    data, served_mode = _candidate_take_bytes_for_mode(take, mode)
    suffix = "_bass_performance.mid" if served_mode == "performance" else "_bass.mid"
    response = lane_midi_response(data, f"{session_id}_{run_id}_{take_id}{suffix}")
    response.headers["X-Bass-Candidate-Requested-Mode"] = mode
    response.headers["X-Bass-Candidate-Mode"] = served_mode
    return response


@router.get("/{session_id}/bass-candidates/{run_id}/{take_id}/notes", response_model=list[LaneNote])
def get_bass_candidate_take_notes(
    session_id: str,
    run_id: str,
    take_id: str,
    response: Response,
    mode: Literal["clean", "performance"] = "clean",
) -> list[LaneNote]:
    _ = _get_session_or_404(session_id)
    run = _candidate_run_for_session_or_503(session_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_run_not_found", "run_id": run_id})
    take = _find_take_payload(run, take_id)
    if take is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_take_not_found", "take_id": take_id})
    data, served_mode = _candidate_take_bytes_for_mode(take, mode)
    response.headers["X-Bass-Candidate-Requested-Mode"] = mode
    response.headers["X-Bass-Candidate-Mode"] = served_mode
    return extract_lane_notes(data)


@router.post("/{session_id}/bass-candidates/{run_id}/{take_id}/promote", response_model=SessionState)
def promote_bass_candidate_take(session_id: str, run_id: str, take_id: str) -> SessionState:
    s = _get_session_or_404(session_id)
    run = _candidate_run_for_session_or_503(session_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_run_not_found", "run_id": run_id})
    take = _find_take_payload(run, take_id)
    if take is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_take_not_found", "take_id": take_id})
    _require_current_candidate_context(s, run)
    evidence_key, evidence_scale, evidence_source, evidence_groove = (
        _validated_candidate_generation_evidence(run)
    )
    data = _take_bytes_or_400(take)
    performance_data = _take_performance_bytes_or_400(take)
    if performance_data is None:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "candidate_performance_payload_unverifiable",
                "message": (
                    "This candidate predates frozen performance rendering. "
                    "Generate a fresh candidate run before promotion."
                ),
            },
        )
    raw_seed = take.get("seed")
    if raw_seed is None:
        candidate_seed = None
    elif isinstance(raw_seed, int) and not isinstance(raw_seed, bool):
        candidate_seed = raw_seed
    else:
        exc = ValueError("Candidate seed must be an integer or null")
        raise _invalid_candidate_metadata(run) from exc
    normalized_take = _normalize_bass_bytes_for_session(bytes(data), s)
    normalized_performance = _normalize_bass_bytes_for_session(
        bytes(performance_data),
        s,
    )
    if s.bass_bytes:
        try:
            bass_history_store.capture(_durable_session_view(s))
        except bass_history_store.BassHistoryStoreError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "bass_history_unavailable",
                    "message": (
                        "The current bass idea could not be saved safely, so the "
                        "candidate was not promoted."
                    ),
                    "reason": str(exc),
                },
            ) from exc
    staged = replace(s)
    staged.key = evidence_key
    staged.scale = evidence_scale
    staged.source_analysis_override = evidence_source
    staged.groove_reference_analysis_override = evidence_groove
    staged.bridge_live_overlay_active = False
    staged.bridge_live_base_source_analysis_override = None
    staged.bridge_live_base_key = None
    staged.bridge_live_base_scale = None
    staged.bass_bytes = normalized_take
    staged.bass_performance_bytes = normalized_performance
    staged.bass_preview = str(
        take.get("preview", "") or f"Promoted candidate take {take_id}."
    )
    staged.bass_seed = candidate_seed
    staged.current_bass_candidate_run_id = str(run_id)
    staged.current_bass_candidate_take_id = str(take_id)
    s = _commit_regenerated_lanes(
        s,
        staged,
        [LaneName.bass],
    )
    return _to_state(s, message=f"Promoted bass candidate {take_id} into session bass lane.")


@router.post("/{session_id}/audition/bass", response_model=AuditionBassResponse)
def audition_bass(session_id: str, body: AuditionBassBody) -> AuditionBassResponse:
    s = _get_session_or_404(session_id)
    if not s.bass_bytes:
        raise HTTPException(
            status_code=400,
            detail={"error": "bass_lane_missing", "message": "Generate the bass lane before auditioning it."},
        )

    mode = body.mode or ("performance" if s.bass_performance_bytes else "clean")
    midi_bytes, _resolved_mode = _bass_midi_for_export(
        s,
        mode,
        allow_clean_fallback=False,
    )

    try:
        started = get_audition_player().start(
            session_id=session_id,
            mode=mode,
            output_id=body.output,
            midi_bytes=midi_bytes,
        )
    except MidiOutputUnavailable as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "midi_output_unavailable", "message": str(exc)},
        ) from exc

    return AuditionBassResponse(
        status=started.status,
        session_id=started.session_id,
        mode=mode,
        output=started.output,
        duration_seconds=started.duration_seconds,
    )


def _bass_midi_for_export(
    s: StoredSession,
    requested_mode: Literal["performance", "clean"],
    *,
    allow_clean_fallback: bool = False,
) -> tuple[bytes | None, Literal["performance", "clean"]]:
    """Select export bass and apply the MIDI FX phase only to performance."""
    if requested_mode == "clean":
        return s.bass_bytes, "clean"
    if not s.bass_performance_bytes:
        if allow_clean_fallback:
            return s.bass_bytes, "clean"
        reason = "invalidated" if s.bass_bytes else "missing"
        raise HTTPException(
            status_code=404,
            detail={
                "error": "performance_midi_unavailable",
                "reason": reason,
            },
        )
    assert s.bass_performance_bytes is not None
    return (
        apply_loop_phase_offset(
            s.bass_performance_bytes,
            tempo=s.tempo,
            bar_count=s.bar_count,
            phase_offset_beats=s.bass_phase_offset_beats,
        ),
        "performance",
    )


@router.get("/{session_id}/midi/{lane}")
def download_lane_midi(session_id: str, lane: LaneName, mode: str | None = None):
    s = _get_session_or_404(session_id)
    if mode is not None and mode not in ("clean", "performance"):
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_mode", "message": "mode must be 'clean' or 'performance'."},
        )
    if mode == "performance":
        if lane != LaneName.bass:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "performance_midi_unsupported_lane",
                    "lane": lane.value,
                    "message": "Performance MIDI is only available for the bass lane in v0.5.",
                },
            )
        if not s.bass_performance_bytes:
            reason = "invalidated" if s.bass_bytes else "missing"
            raise HTTPException(
                status_code=404,
                detail={"error": "performance_midi_unavailable", "reason": reason},
            )
        performance_bytes, _ = _bass_midi_for_export(s, "performance")
        assert performance_bytes is not None
        return lane_midi_response(
            performance_bytes,
            f"{session_id}_bass_performance.mid",
        )
    if lane == LaneName.drums:
        data, name = s.drum_bytes, f"{session_id}_drums.mid"
    elif lane == LaneName.bass:
        data, name = s.bass_bytes, f"{session_id}_bass.mid"
    elif lane == LaneName.chords:
        data, name = s.chords_bytes, f"{session_id}_chords.mid"
    else:
        data, name = s.lead_bytes, f"{session_id}_lead.mid"
    if not data:
        raise HTTPException(
            status_code=400,
            detail={"error": "lane_not_generated", "lane": lane.value},
        )
    return lane_midi_response(data, name)


@router.get("/{session_id}/midi")
def download_session_midi(
    session_id: str,
    bass_mode: Literal["performance", "clean"] | None = None,
):
    s = _get_session_or_404(session_id)
    bass_bytes, selected_bass_mode = _bass_midi_for_export(
        s,
        bass_mode or "performance",
        allow_clean_fallback=bass_mode is None,
    )
    lanes = {
        "drums": s.drum_bytes,
        "bass": bass_bytes,
        "chords": s.chords_bytes,
        "lead": s.lead_bytes,
    }
    if not any(lanes.values()):
        raise HTTPException(
            status_code=400,
            detail={"error": "session_not_generated", "message": "Generate at least one lane before session MIDI export."},
        )
    data = merge_lane_midis(tempo=s.tempo, lanes=lanes)
    if not data:
        raise HTTPException(
            status_code=400,
            detail={"error": "session_midi_empty", "message": "Session MIDI export produced no data."},
        )
    response = lane_midi_response(data, f"session_{session_id}.mid")
    response.headers["X-Session-Player-Bass-Mode"] = selected_bass_mode
    return response


@router.get("/{session_id}/export")
def export_all_midi(
    session_id: str,
    bass_mode: Literal["performance", "clean"] | None = None,
):
    s = _get_session_or_404(session_id)
    bass_bytes, selected_bass_mode = _bass_midi_for_export(
        s,
        bass_mode or "performance",
        allow_clean_fallback=bass_mode is None,
    )
    if not (s.drum_bytes and bass_bytes and s.chords_bytes and s.lead_bytes):
        raise HTTPException(
            status_code=400,
            detail={"error": "incomplete_session", "message": "Generate all lanes before export."},
        )
    response = zip_all_lanes(
        session_id=s.id,
        drums=s.drum_bytes,
        bass=bass_bytes,
        bass_mode=selected_bass_mode,
        chords=s.chords_bytes,
        lead=s.lead_bytes,
    )
    response.headers["X-Session-Player-Bass-Mode"] = selected_bass_mode
    return response
