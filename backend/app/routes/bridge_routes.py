"""Logic AU analyser bridge routes (v0.7.1 contract spike — feature-flagged)."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException

from app.models.bridge import (
    BridgeHeartbeatRequest,
    BridgeSourceFeatureFrame,
    BridgeStateResponse,
    BridgeTransportFrame,
)
from app.routes import session_routes
from app.services import bridge_store
from app.services.groove_frame import merge_groove_frames
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
    _require_session(session_id)
    accepted = 0
    for f in frames:
        if f.session_id != session_id:
            raise HTTPException(status_code=400, detail={"error": "session_id_mismatch"})
        bridge_store.record_source_frame(f)
        accepted += 1
    state = bridge_store.get_bridge_state(session_id)
    state["accepted"] = accepted
    return state


@router.post("/sessions/{session_id}/commit-source-groove")
def post_commit_source_groove(session_id: str, replace_existing: bool = False) -> dict[str, Any]:
    _require_enabled()
    s = _require_session(session_id)
    frames = bridge_store.summarize_frames_to_groove_frames(session_id)
    if not frames:
        raise HTTPException(
            status_code=400,
            detail={"error": "no_bridge_frames", "message": "No bridge feature frames captured for this session."},
        )
    base = s.source_analysis_override
    if base is None:
        ctx = build_session_context(s)
        base = build_source_analysis(s, context=ctx)
    merged = merge_groove_frames(base, frames, replace_existing=replace_existing)
    s.source_analysis_override = merged
    return {
        "session_id": session_id,
        "committed_bar_count": len(frames),
        "replace_existing": bool(replace_existing),
        "groove_resolution": merged.source_groove_resolution,
    }


@router.get("/sessions/{session_id}/state", response_model=BridgeStateResponse)
def get_state(session_id: str) -> BridgeStateResponse:
    _require_enabled()
    raw = bridge_store.get_bridge_state(session_id)
    return BridgeStateResponse(**raw)
