"""Session CRUD, generation, regeneration, and MIDI export routes."""

from __future__ import annotations

import base64
import random
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Final

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.models.session import (
    AddPartToSuitBody,
    BassCandidateRun,
    BassCandidateTake,
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
    RegenerateSelectedBody,
    SessionCreate,
    SessionCreated,
    SessionPatch,
    SessionState,
    lane_styles_for_session_preset,
)
from app.services import generator
from app.services import bass_candidate_store
from app.services.conditioning import UnifiedConditioning, build_unified_conditioning
from app.services.audio_source_analysis import analyze_reference_audio
from app.services.bass_quality import analyze_bass_take
from app.services.midi_note_extract import extract_lane_notes
from app.services.lead_generator import normalize_lead_style
from app.services.midi_export import lane_midi_response, merge_lane_midis, zip_all_lanes
from app.services.source_analysis import build_groove_profile, build_harmony_plan, build_source_analysis
from app.services.session_context import SessionAnchorContext, build_session_context, normalize_anchor_lane
from app.utils import music_theory as mt

router = APIRouter()

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
    drum_player: str | None = None
    chord_instrument: str = _DEFAULT_CHORD_INSTRUMENT
    chord_player: str | None = None
    drum_kit: str = _DEFAULT_DRUM_KIT
    drum_bytes: bytes | None = None
    bass_bytes: bytes | None = None
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
    current_bass_candidate_run_id: str | None = None
    current_bass_candidate_take_id: str | None = None


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


def _to_state(s: StoredSession, message: str | None = None) -> SessionState:
    ctx = build_session_context(s)
    src = s.source_analysis_override if s.source_analysis_override is not None else build_source_analysis(s, context=ctx)
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
        lanes=_lane_states(s),
        message=message,
    )


def _get_session_or_404(session_id: str) -> StoredSession:
    s = _SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "session_not_found", "id": session_id})
    return s


def _copy_midi_bytes(data: bytes | None) -> bytes | None:
    """Return a distinct bytes object (CPython may intern ``bytes()`` / full slices)."""
    if data is None:
        return None
    return bytes(bytearray(data))


def _duplicate_stored_session(src: StoredSession, new_id: str) -> StoredSession:
    """Deep-copy settings and lane MIDI bytes/previews into a new StoredSession."""
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
        drum_player=src.drum_player,
        chord_instrument=src.chord_instrument,
        chord_player=src.chord_player,
        drum_kit=src.drum_kit,
        drum_bytes=_copy_midi_bytes(src.drum_bytes),
        bass_bytes=_copy_midi_bytes(src.bass_bytes),
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
        current_bass_candidate_run_id=src.current_bass_candidate_run_id,
        current_bass_candidate_take_id=src.current_bass_candidate_take_id,
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
        drum_player=dp_ins,
        chord_instrument=ci_ins,
        chord_player=cp_ins,
        drum_kit=dk_ins,
        anchor_lane=anchor_ins,
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
    s.reference_audio_path = str(target)
    s.reference_audio_filename = filename
    s.reference_audio_uploaded_at = datetime.now(timezone.utc).isoformat()
    s.reference_audio_duration_seconds = 0.0
    s.reference_audio_head_trim_seconds = 0.0
    s.source_analysis_override = None
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
    try:
        result = analyze_reference_audio(
            audio_path=audio_path,
            session_tempo=s.tempo,
            bar_count=s.bar_count,
            session_key=s.key,
            session_scale=s.scale,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=422,
            detail={"error": "audio_analysis_failed", "message": str(exc)},
        ) from exc
    s.source_analysis_override = result.source_analysis
    s.reference_audio_duration_seconds = result.duration_seconds
    s.reference_audio_head_trim_seconds = result.head_trim_seconds
    return _to_state(s, message="Reference audio analyzed and source analysis updated.")


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
    parts: list[str] = []
    if body.tempo is not None:
        s.tempo = int(body.tempo)
        parts.append("Tempo updated")
    if body.key is not None:
        try:
            s.key = mt.normalize_key(body.key)
        except ValueError as e:
            raise HTTPException(status_code=422, detail={"error": "invalid_key", "message": str(e)}) from e
        parts.append("Key updated")
    if body.scale is not None:
        s.scale = mt.describe_scale(body.scale)
        parts.append("Scale updated")
    if body.bar_count is not None:
        s.bar_count = int(body.bar_count)
        parts.append("Bar count updated")
    if body.session_preset is not None:
        s.session_preset = body.session_preset.value
        ds, bs, cs, ls = lane_styles_for_session_preset(body.session_preset)
        s.drum_style, s.bass_style, s.chord_style, s.lead_style = ds, bs, cs, normalize_lead_style(ls)
        parts.append(
            f"Session preset updated to {body.session_preset.value}; lane styles set to preset defaults"
        )
    if body.lead_style is not None:
        s.lead_style = normalize_lead_style(body.lead_style.value)
        parts.append("Lead style updated")
    if "lead_player" in body.model_dump(exclude_unset=True):
        s.lead_player = body.lead_player.value if body.lead_player is not None else None
        parts.append("Lead player updated")
    if body.bass_style is not None:
        s.bass_style = body.bass_style.value
        parts.append("Bass style updated")
    if body.chord_style is not None:
        s.chord_style = body.chord_style.value
        parts.append("Chord style updated")
    if "chord_progression" in body.model_dump(exclude_unset=True):
        s.chord_progression = list(body.chord_progression) if body.chord_progression else None
        parts.append("Chord progression updated")
    if "chord_player" in body.model_dump(exclude_unset=True):
        s.chord_player = body.chord_player.value if body.chord_player is not None else None
        parts.append("Chord player updated")
    if body.drum_style is not None:
        s.drum_style = body.drum_style.value
        parts.append("Drum style updated")
    if body.lead_instrument is not None:
        s.lead_instrument = body.lead_instrument.value
        parts.append("Lead instrument updated")
    if body.bass_instrument is not None:
        s.bass_instrument = body.bass_instrument.value
        parts.append("Bass instrument updated")
    if "bass_player" in body.model_dump(exclude_unset=True):
        s.bass_player = body.bass_player.value if body.bass_player is not None else None
        parts.append("Bass player updated")
    if body.bass_engine is not None:
        s.bass_engine = body.bass_engine.value
        parts.append("Bass engine updated")
    if "drum_player" in body.model_dump(exclude_unset=True):
        s.drum_player = body.drum_player.value if body.drum_player is not None else None
        parts.append("Drum player updated")
    if body.chord_instrument is not None:
        s.chord_instrument = body.chord_instrument.value
        parts.append("Chord instrument updated")
    if body.drum_kit is not None:
        s.drum_kit = body.drum_kit.value
        parts.append("Drum kit updated")
    if "anchor_lane" in body.model_dump(exclude_unset=True):
        s.anchor_lane = body.anchor_lane.value if body.anchor_lane is not None else None
        parts.append("Anchor lane updated")
    msg = ". ".join(parts) + ". Regenerate affected lane(s) to rebuild MIDI."
    return _to_state(s, message=msg)


@router.patch("/{session_id}/lane-locks", response_model=SessionState)
def patch_lane_locks(session_id: str, body: LaneLocksPatch) -> SessionState:
    """Update which lanes are locked against multi-lane regenerate (partial body allowed)."""
    s = _get_session_or_404(session_id)
    if body.drums is not None:
        s.drum_locked = body.drums
    if body.bass is not None:
        s.bass_locked = body.bass
    if body.chords is not None:
        s.chords_locked = body.chords
    if body.lead is not None:
        s.lead_locked = body.lead
    return _to_state(s, message="Lane locks updated.")


@router.post("/{session_id}/generate", response_model=GenerateResult)
def generate_session(session_id: str) -> GenerateResult:
    s = _get_session_or_404(session_id)
    _generate_all_lanes(s)
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
        b_bytes, b_prev = generator.generate_bass(
            tempo=s.tempo,
            bar_count=s.bar_count,
            key=s.key,
            scale=s.scale,
            bass_style=s.bass_style,
            bass_instrument=s.bass_instrument,
            bass_player=s.bass_player,
            bass_engine=s.bass_engine,
            chord_progression=s.chord_progression,
            session_preset=s.session_preset,
            context=context,
            conditioning=cond,
        )
        s.bass_bytes = b_bytes
        s.bass_preview = b_prev
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
    src = s.source_analysis_override if s.source_analysis_override is not None else build_source_analysis(s, context=ctx)
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


@router.post("/{session_id}/generate-around-anchor", response_model=SessionState)
def generate_around_anchor(session_id: str, body: GenerateAroundAnchorBody = GenerateAroundAnchorBody()) -> SessionState:
    """Regenerate non-anchor lanes using timing/density context from the anchor lane (respects lane locks)."""
    s = _get_session_or_404(session_id)
    if body.anchor_lane is not None:
        s.anchor_lane = body.anchor_lane.value
    av = normalize_anchor_lane(s.anchor_lane)
    if not av:
        raise HTTPException(
            status_code=400,
            detail={"error": "anchor_not_set", "message": "Set anchor_lane (PATCH session) or send anchor_lane in the request body."},
        )
    anchor_lane = LaneName(av)
    if not _lane_has_midi(s, anchor_lane):
        raise HTTPException(
            status_code=400,
            detail={"error": "anchor_not_generated", "lane": av, "message": "Generate the anchor lane first."},
        )
    ctx = build_session_context(s)
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
        if _lane_locked(s, lane):
            skipped_locked.append(lane)
            continue
        _regenerate_lane_on_stored_session(s, lane, context=ctx)
        regen.append(lane)
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
    to_run: list[LaneName] = []
    skipped_locked: list[LaneName] = []
    for lane in ordered:
        if _lane_locked(s, lane):
            skipped_locked.append(lane)
        else:
            to_run.append(lane)
    for lane in to_run:
        _regenerate_lane_on_stored_session(s, lane, context=_context_for_lane_regeneration(s, lane))
    parts: list[str] = []
    if to_run:
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
    to_run: list[LaneName] = []
    kept_locked: list[LaneName] = []
    for lane in _LANE_REGENERATION_ORDER:
        if _lane_locked(s, lane):
            kept_locked.append(lane)
        else:
            to_run.append(lane)
    anchor_v = normalize_anchor_lane(s.anchor_lane)
    done: set[LaneName] = set()
    if anchor_v:
        anchor_lane = LaneName(anchor_v)
        if anchor_lane in to_run:
            _regenerate_lane_on_stored_session(s, anchor_lane, context=None)
            done.add(anchor_lane)
        ctx = build_session_context(s)
        for lane in _LANE_REGENERATION_ORDER:
            if lane not in to_run or lane in done:
                continue
            _regenerate_lane_on_stored_session(s, lane, context=ctx)
    else:
        for lane in to_run:
            _regenerate_lane_on_stored_session(s, lane, context=None)
    if not to_run:
        return _to_state(s, message="All lanes are locked. No lanes were regenerated.")
    regen = "Regenerated unlocked lanes: " + ", ".join(lane.value for lane in to_run) + "."
    if kept_locked:
        kept = " Locked lanes kept: " + ", ".join(lane.value for lane in kept_locked) + "."
        return _to_state(s, message=regen + kept)
    return _to_state(s, message=regen)


@router.post("/{session_id}/add-part-to-suit", response_model=SessionState)
def add_part_to_suit(session_id: str, body: AddPartToSuitBody) -> SessionState:
    """Replace the lead lane with a new context-aware idea (ignores lead lock)."""
    s = _get_session_or_404(session_id)
    mode_v = body.mode.value
    l_bytes, l_prev = generator.generate_lead(
        tempo=s.tempo,
        bar_count=s.bar_count,
        key=s.key,
        scale=s.scale,
        lead_style=s.lead_style,
        lead_instrument=s.lead_instrument,
        lead_player=s.lead_player,
        suit_mode=mode_v,
        suit_bass_density=_notes_per_bar(s.bass_bytes, s.bar_count),
        suit_chord_density=_notes_per_bar(s.chords_bytes, s.bar_count),
        suit_lead_density=_notes_per_bar(s.lead_bytes, s.bar_count),
        suit_chord_style=s.chord_style,
        suit_bass_style=s.bass_style,
        session_preset=s.session_preset,
    )
    s.lead_bytes = l_bytes
    s.lead_preview = l_prev
    msg = _SUIT_PART_MESSAGES.get(mode_v, "Generated a new lead to suit the current session.")
    return _to_state(s, message=msg)


@router.post("/{session_id}/lanes/{lane}/regenerate", response_model=RegenerateLaneResult)
def regenerate_lane(session_id: str, lane: LaneName) -> RegenerateLaneResult:
    s = _get_session_or_404(session_id)
    _regenerate_lane_on_stored_session(s, lane, context=_context_for_lane_regeneration(s, lane))
    return RegenerateLaneResult(session=_to_state(s, message=f"Lane {lane.value} regenerated."), lane=lane)


def _render_bass_take_with_seed(
    s: StoredSession,
    *,
    seed: int,
    conditioning: UnifiedConditioning | None,
    context: SessionAnchorContext | None,
) -> tuple[bytes, str]:
    state = random.getstate()
    random.seed(int(seed))
    try:
        return generator.generate_bass(
            tempo=s.tempo,
            bar_count=s.bar_count,
            key=s.key,
            scale=s.scale,
            bass_style=s.bass_style,
            bass_instrument=s.bass_instrument,
            bass_player=s.bass_player,
            bass_engine=s.bass_engine,
            chord_progression=s.chord_progression,
            session_preset=s.session_preset,
            context=context,
            conditioning=conditioning,
        )
    finally:
        random.setstate(state)


def _public_candidate_run(raw: dict[str, object]) -> BassCandidateRun:
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
                    preview=str(t.get("preview", "") or ""),
                    quality_total=float(t.get("quality_total", 0.0) or 0.0),
                    quality_scores=dict(t.get("quality_scores", {}) if isinstance(t.get("quality_scores"), dict) else {}),
                    quality_reason=str(t.get("quality_reason", "") or ""),
                    selection_stage=(str(t["selection_stage"]) if t.get("selection_stage") is not None else None),
                    motif_family=(str(t["motif_family"]) if t.get("motif_family") is not None else None),
                    signature_distance=(float(t["signature_distance"]) if t.get("signature_distance") is not None else None),
                    quality_floor_cutoff=(float(t["quality_floor_cutoff"]) if t.get("quality_floor_cutoff") is not None else None),
                    top_pool_score=(float(t["top_pool_score"]) if t.get("top_pool_score") is not None else None),
                )
            )
    return BassCandidateRun(
        run_id=str(raw.get("run_id", "")),
        session_id=str(raw.get("session_id", "")),
        created_at=str(raw.get("created_at", "")),
        take_count=int(raw.get("take_count", len(takes))),
        bass_style=str(raw.get("bass_style", "supportive")),
        bass_engine=str(raw.get("bass_engine", "baseline")),
        bass_player=(str(raw.get("bass_player")) if raw.get("bass_player") is not None else None),
        bass_instrument=str(raw.get("bass_instrument", "finger_bass")),
        clip_id=(str(raw.get("clip_id")) if raw.get("clip_id") is not None else None),
        conditioning_tempo=int(raw.get("conditioning_tempo", 120)),
        conditioning_phase_offset=int(raw.get("conditioning_phase_offset", 0)),
        conditioning_phase_confidence=float(raw.get("conditioning_phase_confidence", 0.0)),
        conditioning_sections_count=int(raw.get("conditioning_sections_count", 0)),
        conditioning_harmonic_bar_count=int(raw.get("conditioning_harmonic_bar_count", 0)),
        takes=takes,
    )


def _find_take_payload(raw_run: dict[str, object], take_id: str) -> dict[str, object] | None:
    takes_raw = raw_run.get("takes")
    if not isinstance(takes_raw, list):
        return None
    target = str(take_id).strip()
    for t in takes_raw:
        if isinstance(t, dict) and str(t.get("take_id", "")).strip() == target:
            return t
    return None


def _take_bytes_or_400(raw_take: dict[str, object]) -> bytes:
    b64 = raw_take.get("midi_b64")
    if not isinstance(b64, str) or not b64:
        raise HTTPException(
            status_code=400,
            detail={"error": "candidate_take_payload_missing", "message": "Candidate take MIDI payload missing."},
        )
    try:
        return base64.b64decode(b64.encode("ascii"))
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "candidate_take_payload_invalid", "message": str(exc)},
        ) from exc


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


@router.post("/{session_id}/bass-candidates", response_model=BassCandidateRun)
def generate_bass_candidates(session_id: str, body: GenerateBassCandidatesBody = GenerateBassCandidatesBody()) -> BassCandidateRun:
    """
    Generate multiple comparable bass takes against one unified conditioning snapshot.
    This does not replace the current stored bass lane; it returns candidate metadata only.
    """
    s = _get_session_or_404(session_id)
    ctx = build_session_context(s)
    cond = _conditioning_for_generation(s, context=ctx)
    base_seed = int(body.seed) if body.seed is not None else random.randint(1, 2_000_000_000)
    run_id = f"cand_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
    created_at = datetime.now(timezone.utc).isoformat()

    requested = int(body.take_count)
    hidden_count = max(requested, min(36, requested * 3))
    scored_pool: list[tuple[float, tuple[tuple[int, ...], ...], str, BassCandidateTake, dict[str, object]]] = []
    for i in range(hidden_count):
        seed_i = base_seed + i
        take_id = f"{run_id}_p{i + 1}"
        data, preview = _render_bass_take_with_seed(
            s,
            seed=seed_i,
            conditioning=cond,
            context=ctx,
        )
        notes = extract_lane_notes(data)
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
            "midi_b64": base64.b64encode(data).decode("ascii"),
        }
        scored_pool.append((quality.total, quality.signature, family, take, row))

    scored_pool.sort(key=lambda item: (item[0], -item[3].note_count), reverse=True)
    min_distance, max_family_count = _style_diversity_gate(s.bass_style)
    top_pool_score = float(scored_pool[0][0]) if scored_pool else 0.0
    floor_cutoff = max(0.0, min(1.0, top_pool_score - _style_floor_margin(s.bass_style)))
    takes: list[BassCandidateTake] = []
    take_rows: list[dict[str, object]] = []
    selected_pool_ids: set[str] = set()
    selected_signatures: list[tuple[tuple[int, ...], ...]] = []
    family_counts: dict[str, int] = {}

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
        take_count=len(takes),
        bass_style=s.bass_style,
        bass_engine=s.bass_engine,
        bass_player=s.bass_player,
        bass_instrument=s.bass_instrument,
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
    bass_candidate_store.append_run(run_payload)
    return run


@router.get("/{session_id}/bass-candidates", response_model=list[BassCandidateRun])
def list_bass_candidates(session_id: str) -> list[BassCandidateRun]:
    _ = _get_session_or_404(session_id)
    rows = bass_candidate_store.list_runs_for_session(session_id)
    return [_public_candidate_run(r) for r in rows]


@router.get("/{session_id}/bass-candidates/{run_id}/{take_id}")
def download_bass_candidate_take(session_id: str, run_id: str, take_id: str):
    _ = _get_session_or_404(session_id)
    run = bass_candidate_store.get_run_for_session(session_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_run_not_found", "run_id": run_id})
    take = _find_take_payload(run, take_id)
    if take is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_take_not_found", "take_id": take_id})
    data = _take_bytes_or_400(take)
    return lane_midi_response(data, f"{session_id}_{run_id}_{take_id}_bass.mid")


@router.get("/{session_id}/bass-candidates/{run_id}/{take_id}/notes", response_model=list[LaneNote])
def get_bass_candidate_take_notes(session_id: str, run_id: str, take_id: str) -> list[LaneNote]:
    _ = _get_session_or_404(session_id)
    run = bass_candidate_store.get_run_for_session(session_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_run_not_found", "run_id": run_id})
    take = _find_take_payload(run, take_id)
    if take is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_take_not_found", "take_id": take_id})
    data = _take_bytes_or_400(take)
    return extract_lane_notes(data)


@router.post("/{session_id}/bass-candidates/{run_id}/{take_id}/promote", response_model=SessionState)
def promote_bass_candidate_take(session_id: str, run_id: str, take_id: str) -> SessionState:
    s = _get_session_or_404(session_id)
    run = bass_candidate_store.get_run_for_session(session_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_run_not_found", "run_id": run_id})
    take = _find_take_payload(run, take_id)
    if take is None:
        raise HTTPException(status_code=404, detail={"error": "candidate_take_not_found", "take_id": take_id})
    data = _take_bytes_or_400(take)
    s.bass_bytes = bytes(data)
    s.bass_preview = str(take.get("preview", "") or f"Promoted candidate take {take_id}.")
    s.current_bass_candidate_run_id = str(run_id)
    s.current_bass_candidate_take_id = str(take_id)
    return _to_state(s, message=f"Promoted bass candidate {take_id} into session bass lane.")


@router.get("/{session_id}/midi/{lane}")
def download_lane_midi(session_id: str, lane: LaneName):
    s = _get_session_or_404(session_id)
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
def download_session_midi(session_id: str):
    s = _get_session_or_404(session_id)
    lanes = {
        "drums": s.drum_bytes,
        "bass": s.bass_bytes,
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
    return lane_midi_response(data, f"session_{session_id}.mid")


@router.get("/{session_id}/export")
def export_all_midi(session_id: str):
    s = _get_session_or_404(session_id)
    if not (s.drum_bytes and s.bass_bytes and s.chords_bytes and s.lead_bytes):
        raise HTTPException(
            status_code=400,
            detail={"error": "incomplete_session", "message": "Generate all lanes before export."},
        )
    return zip_all_lanes(
        session_id=s.id,
        drums=s.drum_bytes,
        bass=s.bass_bytes,
        chords=s.chords_bytes,
        lead=s.lead_bytes,
    )
