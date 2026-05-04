"""In-memory bridge store for v0.7.1 contract spike (no persistence)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.models.bridge import BridgeHeartbeatRequest, BridgeSourceFeatureFrame, BridgeTransportFrame
from app.models.groove_frame import GROOVE_SLOTS, GrooveFrame


@dataclass
class _BridgeState:
    plugin_instance_id: str | None = None
    plugin_version: str | None = None
    source_id: str | None = None
    last_seen_at: str | None = None
    last_transport: dict[str, Any] | None = None
    feature_frames: list[BridgeSourceFeatureFrame] = field(default_factory=list)


_STATES: dict[str, _BridgeState] = {}
_MAX_FRAMES_PER_SESSION = 4096


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_for(session_id: str) -> _BridgeState:
    st = _STATES.get(session_id)
    if st is None:
        st = _BridgeState()
        _STATES[session_id] = st
    return st


def record_heartbeat(req: BridgeHeartbeatRequest) -> None:
    sid = req.session_id or "_pending_"
    st = _state_for(sid)
    st.plugin_instance_id = req.plugin_instance_id
    st.plugin_version = req.plugin_version
    if req.source_id:
        st.source_id = req.source_id
    st.last_seen_at = _now_iso()


def record_transport(frame: BridgeTransportFrame) -> None:
    st = _state_for(frame.session_id)
    st.plugin_instance_id = frame.plugin_instance_id
    st.last_seen_at = _now_iso()
    st.last_transport = frame.model_dump()


def record_source_frame(frame: BridgeSourceFeatureFrame) -> None:
    st = _state_for(frame.session_id)
    st.plugin_instance_id = frame.plugin_instance_id
    st.source_id = frame.source_id
    st.last_seen_at = _now_iso()
    st.feature_frames.append(frame)
    if len(st.feature_frames) > _MAX_FRAMES_PER_SESSION:
        # Drop oldest frames; cheap protection against unbounded growth.
        excess = len(st.feature_frames) - _MAX_FRAMES_PER_SESSION
        del st.feature_frames[:excess]


def get_bridge_state(session_id: str) -> dict[str, Any]:
    st = _STATES.get(session_id)
    if st is None:
        return {
            "connected": False,
            "plugin_instance_id": None,
            "session_id": session_id,
            "source_id": None,
            "last_seen_at": None,
            "frame_count": 0,
            "last_transport": None,
        }
    return {
        "connected": st.plugin_instance_id is not None,
        "plugin_instance_id": st.plugin_instance_id,
        "session_id": session_id,
        "source_id": st.source_id,
        "last_seen_at": st.last_seen_at,
        "frame_count": len(st.feature_frames),
        "last_transport": st.last_transport,
    }


def clear_bridge_state(session_id: str | None = None) -> None:
    if session_id is None:
        _STATES.clear()
        return
    _STATES.pop(session_id, None)


def _slot_for_frame(frame: BridgeSourceFeatureFrame, fallback_index: int, fallback_total: int) -> int:
    """Map a feature frame into a 16th-slot.

    Uses ppq_position fractional beat when available (4 beats/bar => 16 slots/bar);
    otherwise falls back to evenly distributing frames across the bar.
    """
    if frame.ppq_position is not None:
        beat_in_bar = float(frame.ppq_position) % 4.0
        slot = int(beat_in_bar * 4.0)
        return max(0, min(GROOVE_SLOTS - 1, slot))
    if fallback_total <= 0:
        return 0
    s = int((fallback_index / max(fallback_total, 1)) * GROOVE_SLOTS)
    return max(0, min(GROOVE_SLOTS - 1, s))


def _normalize_row_in_place(row: list[float]) -> list[float]:
    peak = max(row) if row else 0.0
    if peak <= 1e-9:
        return [0.0] * GROOVE_SLOTS
    return [max(0.0, min(1.0, v / peak)) for v in row]


def summarize_frames_to_groove_frames(session_id: str) -> list[GrooveFrame]:
    """Compact stored feature frames into one GrooveFrame per bar.

    Simple-by-design: this is the contract spike, not the final analyser.
    """
    st = _STATES.get(session_id)
    if st is None or not st.feature_frames:
        return []

    by_bar: dict[int, list[BridgeSourceFeatureFrame]] = {}
    for f in st.feature_frames:
        by_bar.setdefault(int(f.bar_index), []).append(f)

    out: list[GrooveFrame] = []
    for bar_index in sorted(by_bar.keys()):
        bar_frames = by_bar[bar_index]
        onset_row = [0.0] * GROOVE_SLOTS
        kick_row = [0.0] * GROOVE_SLOTS
        snare_row = [0.0] * GROOVE_SLOTS
        pressure_row = [0.0] * GROOVE_SLOTS
        slot_counts = [0] * GROOVE_SLOTS

        total = len(bar_frames)
        for i, frame in enumerate(bar_frames):
            slot = _slot_for_frame(frame, i, total)
            onset_v = float(frame.onset_strength)
            low = float(frame.low_band_energy)
            mid = float(frame.mid_band_energy)
            high = float(frame.high_band_energy)
            rms = float(frame.rms)

            kick_contrib = 0.6 * low + 0.4 * onset_v
            snare_contrib = 0.55 * (0.7 * mid + 0.3 * high) + 0.45 * onset_v
            # Soft beat-2/4 prior on slots 4 and 12 if ppq is available (already encoded by slot).
            if slot in (4, 12) and frame.ppq_position is not None:
                snare_contrib *= 1.08
            pressure_contrib = 0.4 * rms + 0.35 * onset_v + 0.25 * (low + mid + high) / 3.0

            onset_row[slot] = max(onset_row[slot], onset_v)
            kick_row[slot] = max(kick_row[slot], kick_contrib)
            snare_row[slot] = max(snare_row[slot], snare_contrib)
            pressure_row[slot] = max(pressure_row[slot], pressure_contrib)
            slot_counts[slot] += 1

        onset_row = _normalize_row_in_place(onset_row)
        kick_row = _normalize_row_in_place(kick_row)
        snare_row = _normalize_row_in_place(snare_row)
        pressure_row = _normalize_row_in_place(pressure_row)

        slots_filled = sum(1 for c in slot_counts if c > 0)
        density = slots_filled / float(GROOVE_SLOTS)
        coverage = min(1.0, total / 16.0)
        confidence = max(0.0, min(1.0, 0.4 * coverage + 0.6 * density))

        tempo_bpm: float | None = None
        for f in bar_frames:
            if f.host_tempo is not None:
                tempo_bpm = float(f.host_tempo)
                break

        source_tag = "logic_au_bridge"
        out.append(
            GrooveFrame(
                bar_index=bar_index,
                tempo_bpm=tempo_bpm,
                resolution=GROOVE_SLOTS,
                onset_weight=onset_row,
                kick_weight=kick_row,
                snare_weight=snare_row,
                slot_pressure=pressure_row,
                confidence=confidence,
                source_tag=source_tag,
                source_metadata={
                    "frame_count": total,
                    "slots_filled": slots_filled,
                },
            )
        )
    return out
