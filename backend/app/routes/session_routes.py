"""Session CRUD, generation, regeneration, and MIDI export routes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Final

from fastapi import APIRouter, HTTPException

from app.models.session import (
    AddPartToSuitBody,
    GenerateAroundAnchorBody,
    GenerateResult,
    LaneLocksPatch,
    LaneName,
    LaneState,
    RegenerateLaneResult,
    RegenerateSelectedBody,
    SessionCreate,
    SessionCreated,
    SessionPatch,
    SessionState,
    lane_styles_for_session_preset,
)
from app.services import generator
from app.services.midi_note_extract import extract_lane_notes
from app.services.lead_generator import normalize_lead_style
from app.services.midi_export import lane_midi_response, zip_all_lanes
from app.services.session_context import build_session_context, normalize_anchor_lane
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
    drum_style: str = "straight"
    lead_instrument: str = _DEFAULT_LEAD_INSTRUMENT
    lead_player: str | None = None
    bass_instrument: str = _DEFAULT_BASS_INSTRUMENT
    bass_player: str | None = None
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
        drum_style=s.drum_style,
        lead_instrument=s.lead_instrument,
        lead_player=s.lead_player,
        bass_instrument=s.bass_instrument,
        bass_player=s.bass_player,
        drum_player=s.drum_player,
        chord_instrument=s.chord_instrument,
        chord_player=s.chord_player,
        drum_kit=s.drum_kit,
        anchor_lane=s.anchor_lane,
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
        drum_style=src.drum_style,
        lead_instrument=src.lead_instrument,
        lead_player=src.lead_player,
        bass_instrument=src.bass_instrument,
        bass_player=src.bass_player,
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
    )


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
    dp_ins = body.drum_player.value if body.drum_player is not None else None
    cp_ins = body.chord_player.value if body.chord_player is not None else None
    ci_ins = body.chord_instrument.value if body.chord_instrument is not None else _DEFAULT_CHORD_INSTRUMENT
    dk_ins = body.drum_kit.value if body.drum_kit is not None else _DEFAULT_DRUM_KIT
    anchor_ins = body.anchor_lane.value if body.anchor_lane is not None else None
    s = StoredSession(
        id=sid,
        tempo=body.tempo,
        key=mt.normalize_key(body.key),
        scale=body.scale.strip(),
        bar_count=body.bar_count,
        session_preset=preset_stored,
        lead_style=ls,
        bass_style=bs,
        chord_style=cs,
        drum_style=ds,
        lead_instrument=li_ins,
        lead_player=lp_ins,
        bass_instrument=bi_ins,
        bass_player=bp_ins,
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
            session_preset=s.session_preset,
            context=context,
        )
        s.bass_bytes = b_bytes
        s.bass_preview = b_prev
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
