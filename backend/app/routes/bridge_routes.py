"""Logic AU analyser bridge routes (v0.7.1 contract spike — feature-flagged)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models.bridge import (
    BridgeHarmonicFrame,
    BridgeHeartbeatRequest,
    BridgeSourceFeatureFrame,
    BridgeStateResponse,
    BridgeTransportFrame,
)
from app.routes import session_routes
from app.services import bridge_store
from app.services.groove_frame import merge_groove_frames
from app.services.harmonic_analysis import infer_key_scale_from_chroma
from app.services.session_context import build_session_context
from app.services.source_analysis import build_source_analysis

router = APIRouter()

_FEATURE_FLAG_ENV = "SESSION_PLAYER_ENABLE_GROOVE_BRIDGE"


def is_bridge_enabled() -> bool:
    raw = os.environ.get(_FEATURE_FLAG_ENV, "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _require_enabled() -> None:
    if not is_bridge_enabled():
        # Hide the surface entirely when disabled (consistent with feature-gated routes).
        raise HTTPException(status_code=404, detail={"error": "bridge_disabled"})


def _require_session(session_id: str) -> session_routes.StoredSession:
    s = session_routes._SESSIONS.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail={"error": "session_not_found", "id": session_id})
    return s


def _apply_live_source_groove(
    s: session_routes.StoredSession,
    *,
    replace_existing: bool = False,
) -> int:
    frames = bridge_store.summarize_frames_to_groove_frames(s.id)
    if not frames:
        return 0
    base = s.source_analysis_override
    if base is None:
        ctx = build_session_context(s)
        base = build_source_analysis(s, context=ctx)
    s.source_analysis_override = merge_groove_frames(base, frames, replace_existing=replace_existing)
    return len(frames)


def _pc_name(pc: int) -> str:
    return ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")[int(pc) % 12]


def _apply_live_harmonic_context(s: session_routes.StoredSession) -> dict[str, Any]:
    summary = bridge_store.summarize_harmonic_frames(s.id)
    if summary is None:
        return {"live_harmonic_bar_count": 0}
    base = s.source_analysis_override
    if base is None:
        ctx = build_session_context(s)
        base = build_source_analysis(s, context=ctx)

    key_pc = summary.get("key_pc")
    scale = summary.get("scale")
    inferred_conf = 0.0
    if key_pc is None or not scale:
        key_pc, scale, inferred_conf = infer_key_scale_from_chroma(summary.get("chroma") or [0.0] * 12)
    key_conf = max(inferred_conf, max((float(b.get("key_confidence") or 0.0) for b in summary["bars"]), default=0.0))
    scale_conf = max(inferred_conf, max((float(b.get("scale_confidence") or 0.0) for b in summary["bars"]), default=0.0))
    tempo_bpm = summary.get("tempo_bpm") or base.tempo_estimate_bpm
    tempo_conf = max(float(summary.get("tempo_confidence") or 0.0), float(base.tempo_confidence))
    metadata = dict(base.source_metadata or {})
    metadata["live_harmonic_source_tag"] = "logic_au_harmonic_listener"
    metadata["bridge_harmonic"] = summary
    s.source_analysis_override = base.model_copy(
        update={
            "source_lane": base.source_lane if base.source_lane != "none" else "logic_au_harmonic_listener",
            "tempo_estimate_bpm": float(tempo_bpm),
            "tempo_confidence": max(0.0, min(1.0, tempo_conf)),
            "tonal_center_pc_guess": int(key_pc),
            "tonal_center_confidence": max(float(base.tonal_center_confidence), max(0.0, min(1.0, key_conf))),
            "scale_mode_guess": str(scale),
            "scale_mode_confidence": max(float(base.scale_mode_confidence), max(0.0, min(1.0, scale_conf))),
            "source_metadata": metadata,
        }
    )
    if key_conf >= 0.35:
        s.key = _pc_name(int(key_pc))
    if scale_conf >= 0.35 and scale:
        s.scale = str(scale)
    return {
        "live_harmonic_bar_count": int(summary["bar_count"]),
        "key": s.key,
        "scale": s.scale,
        "tempo_bpm": tempo_bpm,
    }


@router.post("/heartbeat")
def post_heartbeat(req: BridgeHeartbeatRequest) -> dict[str, Any]:
    _require_enabled()
    bridge_store.record_heartbeat(req)
    sid = req.session_id or "_pending_"
    return bridge_store.get_bridge_state(sid)


@router.post("/sessions/{session_id}/transport")
def post_transport(session_id: str, frame: BridgeTransportFrame) -> dict[str, Any]:
    _require_enabled()
    if frame.session_id != session_id:
        raise HTTPException(status_code=400, detail={"error": "session_id_mismatch"})
    _require_session(session_id)
    bridge_store.record_transport(frame)
    return bridge_store.get_bridge_state(session_id)


@router.post("/sessions/{session_id}/source-frames")
def post_source_frames(session_id: str, frames: list[BridgeSourceFeatureFrame]) -> dict[str, Any]:
    _require_enabled()
    s = _require_session(session_id)
    accepted = 0
    for f in frames:
        if f.session_id != session_id:
            raise HTTPException(status_code=400, detail={"error": "session_id_mismatch"})
        bridge_store.record_source_frame(f)
        accepted += 1
    live_bar_count = _apply_live_source_groove(s) if accepted else 0
    state = bridge_store.get_bridge_state(session_id)
    state["accepted"] = accepted
    state["live_source_groove_bar_count"] = live_bar_count
    return state


@router.post("/harmonic")
def post_harmonic_frames(frames: list[BridgeHarmonicFrame]) -> dict[str, Any]:
    _require_enabled()
    if not frames:
        raise HTTPException(status_code=400, detail={"error": "empty_harmonic_frames"})
    session_id = frames[0].session_id
    return post_session_harmonic_frames(session_id, frames)


@router.post("/sessions/{session_id}/harmonic")
def post_session_harmonic_frames(session_id: str, frames: list[BridgeHarmonicFrame]) -> dict[str, Any]:
    _require_enabled()
    s = _require_session(session_id)
    accepted = 0
    for f in frames:
        if f.session_id != session_id:
            raise HTTPException(status_code=400, detail={"error": "session_id_mismatch"})
        bridge_store.record_harmonic_frame(f)
        accepted += 1
    state = bridge_store.get_bridge_state(session_id)
    state["accepted"] = accepted
    state.update(_apply_live_harmonic_context(s) if accepted else {"live_harmonic_bar_count": 0})
    return state


@router.post("/sessions/{session_id}/commit-source-groove")
def post_commit_source_groove(session_id: str, replace_existing: bool = False) -> dict[str, Any]:
    _require_enabled()
    s = _require_session(session_id)
    committed_bar_count = _apply_live_source_groove(s, replace_existing=replace_existing)
    if committed_bar_count == 0:
        raise HTTPException(
            status_code=400,
            detail={"error": "no_bridge_frames", "message": "No bridge feature frames captured for this session."},
        )
    return {
        "session_id": session_id,
        "committed_bar_count": committed_bar_count,
        "replace_existing": bool(replace_existing),
        "groove_resolution": s.source_analysis_override.source_groove_resolution,
    }


@router.get("/sessions/{session_id}/state", response_model=BridgeStateResponse)
def get_state(session_id: str) -> BridgeStateResponse:
    _require_enabled()
    raw = bridge_store.get_bridge_state(session_id)
    return BridgeStateResponse(**raw)
