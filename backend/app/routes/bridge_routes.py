"""Logic AU analyser bridge routes (v0.7.1 contract spike — feature-flagged)."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import logging
import os
from threading import Lock, Thread
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
from app.services import bridge_store, session_mutation_gate
from app.services.groove_frame import merge_groove_frames
from app.services.harmonic_analysis import infer_key_scale_from_chroma
from app.services.session_context import build_session_context
from app.services.source_analysis import (
    build_source_analysis,
    session_harmony_is_confirmed,
)

router = APIRouter()
logger = logging.getLogger(__name__)

_FEATURE_FLAG_ENV = "SESSION_PLAYER_ENABLE_GROOVE_BRIDGE"
_DEFERRED_OVERLAY_LOCK = Lock()
_DEFERRED_OVERLAYS: dict[str, dict[str, bool]] = {}
_DEFERRED_OVERLAY_WORKER_RUNNING = False


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


def _latest_session_id() -> str | None:
    """Resolve the newest created session for an analyser that is not bound yet."""

    if not session_routes._SESSIONS:
        return None
    return next(reversed(session_routes._SESSIONS))


def _apply_live_source_groove(
    s: session_routes.StoredSession,
    *,
    replace_existing: bool = False,
) -> int:
    frames = bridge_store.summarize_frames_to_groove_frames(
        s.id,
        bar_count=s.bar_count,
    )
    if not frames:
        return 0
    _begin_live_overlay(s)
    base = s.source_analysis_override
    if base is None:
        ctx = build_session_context(s)
        base = build_source_analysis(s, context=ctx)
    s.source_analysis_override = merge_groove_frames(base, frames, replace_existing=replace_existing)
    return len(frames)


def _begin_live_overlay(s: session_routes.StoredSession) -> None:
    if s.bridge_live_overlay_active:
        return
    s.bridge_live_base_source_analysis_override = deepcopy(
        s.source_analysis_override
    )
    s.bridge_live_base_key = s.key
    s.bridge_live_base_scale = s.scale
    s.bridge_live_overlay_active = True


def _commit_live_overlay(s: session_routes.StoredSession) -> None:
    s.bridge_live_overlay_active = False
    s.bridge_live_base_source_analysis_override = None
    s.bridge_live_base_key = None
    s.bridge_live_base_scale = None


def _commit_source_groove(
    s: session_routes.StoredSession,
    *,
    replace_existing: bool,
) -> int:
    """Promote only source-groove evidence, leaving Listener harmony transient."""

    frames = bridge_store.summarize_frames_to_groove_frames(
        s.id,
        bar_count=s.bar_count,
    )
    if not frames:
        return 0

    if s.bridge_live_overlay_active:
        durable_key = s.bridge_live_base_key or s.key
        durable_scale = s.bridge_live_base_scale or s.scale
        base = deepcopy(s.bridge_live_base_source_analysis_override)
    else:
        durable_key = s.key
        durable_scale = s.scale
        base = deepcopy(s.source_analysis_override)
    if base is None:
        durable_session = replace(
            s,
            key=durable_key,
            scale=durable_scale,
            source_analysis_override=None,
            bridge_live_overlay_active=False,
            bridge_live_base_source_analysis_override=None,
            bridge_live_base_key=None,
            bridge_live_base_scale=None,
        )
        ctx = build_session_context(durable_session)
        base = build_source_analysis(durable_session, context=ctx)

    s.source_analysis_override = merge_groove_frames(
        base,
        frames,
        replace_existing=replace_existing,
    )
    s.key = durable_key
    s.scale = durable_scale
    _commit_live_overlay(s)
    return len(frames)


def _has_persisted_bridge_groove(s: session_routes.StoredSession) -> bool:
    source = s.source_analysis_override
    if source is None:
        return False
    metadata = dict(source.source_metadata or {})
    return metadata.get("last_groove_source_tag") == "logic_au_bridge"


def _pc_name(pc: int) -> str:
    return ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")[int(pc) % 12]


def _apply_live_harmonic_context(s: session_routes.StoredSession) -> dict[str, Any]:
    summary = bridge_store.summarize_harmonic_frames(
        s.id,
        bar_count=s.bar_count,
    )
    if summary is None:
        return {"live_harmonic_bar_count": 0}
    harmonic_source_id = str(
        bridge_store.get_bridge_state(s.id).get("harmonic_source_id") or ""
    ).strip().lower()
    if (
        list(getattr(s, "chord_progression", []) or [])
        and harmonic_source_id == "session-player-listener"
    ):
        # A Listener on Session Player's own output is useful diagnostic
        # evidence, but once the user has supplied a chord map it is also
        # necessarily hearing the generated parts. Keep its raw Bridge frames
        # inspectable without turning that circular observation into a session
        # overlay that the next Generate action could accidentally promote.
        return {
            "live_harmonic_bar_count": int(summary["bar_count"]),
            "live_harmonic_applied": False,
            "live_harmonic_ignored_reason": "confirmed_chord_progression",
            "key": s.key,
            "scale": s.scale,
            "tempo_bpm": summary.get("tempo_bpm"),
        }
    _begin_live_overlay(s)
    base = s.source_analysis_override
    if base is None:
        ctx = build_session_context(s)
        base = build_source_analysis(s, context=ctx)

    key_pc = summary.get("key_pc")
    scale = summary.get("scale")
    inferred_conf = 0.0
    if key_pc is None or not scale:
        key_pc, scale, inferred_conf = infer_key_scale_from_chroma(summary.get("chroma") or [0.0] * 12)
    summary_bars = [
        row for row in summary["bars"]
        if isinstance(row, dict)
    ]
    matching_key_rows = [
        row
        for row in summary_bars
        if row.get("key_pc") is not None
        and int(row["key_pc"]) % 12 == int(key_pc) % 12
    ]
    matching_scale_rows = [
        row
        for row in summary_bars
        if str(row.get("scale") or "").strip().lower()
        == str(scale or "").strip().lower()
    ]
    key_conf = max(
        inferred_conf,
        max(
            (
                float(row.get("key_confidence") or 0.0)
                for row in matching_key_rows
            ),
            default=0.0,
        ),
    )
    scale_conf = max(
        inferred_conf,
        max(
            (
                float(row.get("scale_confidence") or 0.0)
                for row in matching_scale_rows
            ),
            default=0.0,
        ),
    )
    tempo_bpm = summary.get("tempo_bpm") or base.tempo_estimate_bpm
    tempo_conf = max(float(summary.get("tempo_confidence") or 0.0), float(base.tempo_confidence))
    bounded_key_conf = max(0.0, min(1.0, key_conf))
    bounded_scale_conf = max(0.0, min(1.0, scale_conf))
    same_key_hypothesis = (
        int(base.tonal_center_pc_guess) % 12 == int(key_pc) % 12
    )
    same_scale_hypothesis = (
        str(base.scale_mode_guess or "").strip().lower()
        == str(scale or "").strip().lower()
    )
    metadata = dict(base.source_metadata or {})
    metadata["live_harmonic_source_tag"] = "logic_au_harmonic_listener"
    metadata["bridge_harmonic"] = summary
    s.source_analysis_override = base.model_copy(
        update={
            "source_lane": base.source_lane if base.source_lane != "none" else "logic_au_harmonic_listener",
            "tempo_estimate_bpm": float(tempo_bpm),
            "tempo_confidence": max(0.0, min(1.0, tempo_conf)),
            "tonal_center_pc_guess": int(key_pc),
            "tonal_center_confidence": (
                max(float(base.tonal_center_confidence), bounded_key_conf)
                if same_key_hypothesis
                else bounded_key_conf
            ),
            "scale_mode_guess": str(scale),
            "scale_mode_confidence": (
                max(float(base.scale_mode_confidence), bounded_scale_conf)
                if same_scale_hypothesis
                else bounded_scale_conf
            ),
            "source_metadata": metadata,
        }
    )
    # A Listener estimate is evidence, not an instruction to replace harmony
    # the user has already confirmed or explicitly supplied. Keep the estimate
    # in source_analysis_override so it remains inspectable and useful to the
    # conditioning layer, but preserve the authoritative session key/map.
    if not session_harmony_is_confirmed(s):
        if key_conf >= 0.35:
            s.key = _pc_name(int(key_pc))
        if scale_conf >= 0.35 and scale:
            s.scale = str(scale)
    return {
        "live_harmonic_bar_count": int(summary["bar_count"]),
        "live_harmonic_applied": True,
        "key": s.key,
        "scale": s.scale,
        "tempo_bpm": tempo_bpm,
    }


def _queue_deferred_overlay(
    session_id: str,
    *,
    reset: bool = False,
    source: bool = False,
    source_force_replace: bool = False,
    source_reset_if_persisted: bool = False,
    harmonic: bool = False,
) -> None:
    """Coalesce a missed overlay and drain it after the durable gate releases."""

    global _DEFERRED_OVERLAY_WORKER_RUNNING

    should_start = False
    with _DEFERRED_OVERLAY_LOCK:
        pending = _DEFERRED_OVERLAYS.setdefault(
            session_id,
            {
                "reset": False,
                "source": False,
                "source_force_replace": False,
                "source_reset_if_persisted": False,
                "harmonic": False,
            },
        )
        pending["reset"] = pending["reset"] or reset
        pending["source"] = pending["source"] or source
        pending["source_force_replace"] = (
            pending["source_force_replace"] or source_force_replace
        )
        pending["source_reset_if_persisted"] = (
            pending["source_reset_if_persisted"]
            or source_reset_if_persisted
        )
        pending["harmonic"] = pending["harmonic"] or harmonic
        if not _DEFERRED_OVERLAY_WORKER_RUNNING:
            _DEFERRED_OVERLAY_WORKER_RUNNING = True
            should_start = True
    if should_start:
        Thread(
            target=_drain_deferred_overlays,
            name="session-player-bridge-overlay",
            daemon=True,
        ).start()


def _drain_deferred_overlays() -> None:
    """Apply deferred live evidence on a daemon worker, never on the AU request."""

    global _DEFERRED_OVERLAY_WORKER_RUNNING

    while True:
        with _DEFERRED_OVERLAY_LOCK:
            if not _DEFERRED_OVERLAYS:
                _DEFERRED_OVERLAY_WORKER_RUNNING = False
                return

        session_mutation_gate.acquire()
        try:
            with _DEFERRED_OVERLAY_LOCK:
                pending_batch = dict(_DEFERRED_OVERLAYS)
                _DEFERRED_OVERLAYS.clear()
            for session_id, pending in pending_batch.items():
                # Durable plugin commands swap staged objects into the mapping.
                # Always resolve after claiming the gate so we never update a
                # detached pre-swap object.
                s = session_routes._SESSIONS.get(session_id)
                if s is None:
                    continue
                staged = replace(s)
                try:
                    if pending["reset"]:
                        session_routes._discard_live_bridge_overlay(staged)  # noqa: SLF001
                    if pending["source"]:
                        replace_existing = pending["source_force_replace"] or (
                            pending["source_reset_if_persisted"]
                            and _has_persisted_bridge_groove(staged)
                        )
                        _apply_live_source_groove(
                            staged,
                            replace_existing=replace_existing,
                        )
                    if pending["harmonic"]:
                        _apply_live_harmonic_context(staged)
                    session_routes._publish_staged_session(  # noqa: SLF001
                        s,
                        staged,
                    )
                except Exception:
                    # Live evidence remains in bridge_store and can be retried
                    # by the next batch/explicit commit. Never terminate the
                    # coalescing worker because one session was malformed.
                    logger.exception(
                        "Could not apply deferred Bridge overlay for %s",
                        session_id,
                    )
        finally:
            session_mutation_gate.release()


@router.post("/heartbeat")
def post_heartbeat(req: BridgeHeartbeatRequest) -> dict[str, Any]:
    _require_enabled()
    resolved_session_id = req.session_id or _latest_session_id()
    resolved_req = req.model_copy(update={"session_id": resolved_session_id})
    bridge_store.record_heartbeat(resolved_req)
    sid = resolved_session_id or "_pending_"
    state = bridge_store.get_bridge_state(sid)
    # "_pending_" is an internal bucket, never a valid binding for an AU.
    state["session_id"] = resolved_session_id
    return state


@router.post("/sessions/{session_id}/transport")
def post_transport(session_id: str, frame: BridgeTransportFrame) -> dict[str, Any]:
    _require_enabled()
    if frame.session_id != session_id:
        raise HTTPException(status_code=400, detail={"error": "session_id_mismatch"})
    _require_session(session_id)
    restarted = bridge_store.record_transport(frame)
    overlay_deferred = False
    if restarted:
        if session_mutation_gate.try_acquire():
            try:
                current = _require_session(session_id)
                staged = replace(current)
                session_routes._discard_live_bridge_overlay(staged)  # noqa: SLF001
                session_routes._publish_staged_session(  # noqa: SLF001
                    current,
                    staged,
                )
            finally:
                session_mutation_gate.release()
        else:
            overlay_deferred = True
            _queue_deferred_overlay(session_id, reset=True)
    state = bridge_store.get_bridge_state(session_id)
    state["transport_restarted"] = restarted
    state["session_overlay_deferred"] = overlay_deferred
    return state


@router.post("/sessions/{session_id}/source-frames")
def post_source_frames(
    session_id: str,
    frames: list[BridgeSourceFeatureFrame],
) -> dict[str, Any]:
    _require_enabled()
    _require_session(session_id)
    if any(frame.session_id != session_id for frame in frames):
        # Validate the whole batch before recording any frame. A malformed
        # trailing row must not leave a partially accepted capture behind.
        raise HTTPException(status_code=400, detail={"error": "session_id_mismatch"})
    accepted = 0
    ignored_stopped = 0
    prior_state = bridge_store.get_bridge_state(session_id)
    first_source_batch = int(prior_state.get("frame_count", 0)) == 0
    capture_epoch_reset = False
    for f in frames:
        if not f.playing:
            bridge_store.close_source_epoch(
                session_id,
                plugin_instance_id=f.plugin_instance_id,
                source_id=f.source_id,
                capture_epoch=f.capture_epoch,
            )
            ignored_stopped += 1
            continue
        capture_epoch_reset = (
            bridge_store.record_source_frame(f)
            or capture_epoch_reset
        )
        accepted += 1
    overlay_deferred = False
    live_bar_count = 0
    if accepted:
        if session_mutation_gate.try_acquire():
            try:
                # A durable plugin request can replace the StoredSession object
                # while frames are being recorded. Resolve only after claiming
                # the gate so the overlay lands on the current mapping entry.
                current = _require_session(session_id)
                staged = replace(current)
                capture_epoch_reset = capture_epoch_reset or (
                    first_source_batch
                    and _has_persisted_bridge_groove(staged)
                )
                live_bar_count = _apply_live_source_groove(
                    staged,
                    replace_existing=capture_epoch_reset,
                )
                session_routes._publish_staged_session(  # noqa: SLF001
                    current,
                    staged,
                )
            finally:
                session_mutation_gate.release()
        else:
            overlay_deferred = True
            _queue_deferred_overlay(
                session_id,
                source=True,
                source_force_replace=capture_epoch_reset,
                source_reset_if_persisted=first_source_batch,
            )
    state = bridge_store.get_bridge_state(session_id)
    state["accepted"] = accepted
    state["ignored_stopped"] = ignored_stopped
    state["capture_epoch_reset"] = capture_epoch_reset
    state["live_source_groove_bar_count"] = live_bar_count
    state["session_overlay_deferred"] = overlay_deferred
    return state


@router.post("/harmonic")
def post_harmonic_frames(
    frames: list[BridgeHarmonicFrame],
) -> dict[str, Any]:
    _require_enabled()
    if not frames:
        raise HTTPException(status_code=400, detail={"error": "empty_harmonic_frames"})
    session_id = frames[0].session_id
    return post_session_harmonic_frames(session_id, frames)


@router.post("/sessions/{session_id}/harmonic")
def post_session_harmonic_frames(
    session_id: str,
    frames: list[BridgeHarmonicFrame],
) -> dict[str, Any]:
    _require_enabled()
    _require_session(session_id)
    if any(frame.session_id != session_id for frame in frames):
        # Keep a rejected batch atomic just like source-frame ingestion.
        raise HTTPException(status_code=400, detail={"error": "session_id_mismatch"})
    accepted = 0
    ignored_stopped = 0
    capture_epoch_reset = False
    for f in frames:
        if not f.playing:
            bridge_store.close_harmonic_epoch(
                session_id,
                plugin_instance_id=f.plugin_instance_id,
                source_id=f.source_id,
                capture_epoch=f.capture_epoch,
            )
            ignored_stopped += 1
            continue
        capture_epoch_reset = (
            bridge_store.record_harmonic_frame(f)
            or capture_epoch_reset
        )
        accepted += 1
    state = bridge_store.get_bridge_state(session_id)
    state["accepted"] = accepted
    state["ignored_stopped"] = ignored_stopped
    state["capture_epoch_reset"] = capture_epoch_reset
    overlay_deferred = False
    live_context: dict[str, Any] = {"live_harmonic_bar_count": 0}
    if accepted:
        if session_mutation_gate.try_acquire():
            try:
                # See source-frame route: durable plugin mutations use staged
                # object swaps, so re-resolve only after the gate is ours.
                current = _require_session(session_id)
                staged = replace(current)
                live_context = _apply_live_harmonic_context(staged)
                session_routes._publish_staged_session(  # noqa: SLF001
                    current,
                    staged,
                )
            finally:
                session_mutation_gate.release()
        else:
            overlay_deferred = True
            _queue_deferred_overlay(session_id, harmonic=True)
    state["session_overlay_deferred"] = overlay_deferred
    state.update(live_context)
    return state


@router.post("/sessions/{session_id}/commit-source-groove")
def post_commit_source_groove(session_id: str, replace_existing: bool = False) -> dict[str, Any]:
    _require_enabled()
    s = _require_session(session_id)
    staged = replace(s)
    committed_bar_count = _commit_source_groove(
        staged,
        replace_existing=replace_existing,
    )
    if committed_bar_count == 0:
        raise HTTPException(
            status_code=400,
            detail={"error": "no_bridge_frames", "message": "No bridge feature frames captured for this session."},
        )
    s = session_routes._publish_staged_session(s, staged)  # noqa: SLF001
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
