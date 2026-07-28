"""In-memory bridge store for v0.7.1 contract spike (no persistence)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from app.models.bridge import BridgeHarmonicFrame, BridgeHeartbeatRequest, BridgeSourceFeatureFrame, BridgeTransportFrame
from app.models.groove_frame import GROOVE_SLOTS, GrooveFrame


@dataclass
class _BridgeState:
    plugin_instance_id: str | None = None
    plugin_version: str | None = None
    source_id: str | None = None
    harmonic_plugin_instance_id: str | None = None
    harmonic_source_id: str | None = None
    last_seen_at: str | None = None
    last_transport: dict[str, Any] | None = None
    feature_frames: list[BridgeSourceFeatureFrame] = field(default_factory=list)
    harmonic_frames: list[BridgeHarmonicFrame] = field(default_factory=list)
    source_epoch: int = 0
    harmonic_epoch: int = 0
    source_identity: tuple[str, str] | None = None
    harmonic_identity: tuple[str, str] | None = None
    source_capture_epoch: int | None = None
    harmonic_capture_epoch: int | None = None
    source_last_ppq: float | None = None
    harmonic_last_ppq: float | None = None
    source_last_bar: int | None = None
    harmonic_last_bar: int | None = None
    source_origin_bar: int | None = None
    harmonic_origin_bar: int | None = None
    source_epoch_closed: bool = False
    harmonic_epoch_closed: bool = False


_STATES: dict[str, _BridgeState] = {}
_MAX_FRAMES_PER_SESSION = 4096
_LOCK = RLock()
_POSITION_EPSILON = 1.0e-6
_BAR_START_TOLERANCE_BEATS = 0.25


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_for_unlocked(session_id: str) -> _BridgeState:
    st = _STATES.get(session_id)
    if st is None:
        st = _BridgeState()
        _STATES[session_id] = st
    return st


def _source_identity(frame: BridgeSourceFeatureFrame) -> tuple[str, str]:
    return str(frame.plugin_instance_id), str(frame.source_id)


def _harmonic_identity(frame: BridgeHarmonicFrame) -> tuple[str, str]:
    return str(frame.plugin_instance_id), str(frame.source_id)


def _rewound(
    *,
    last_ppq: float | None,
    last_bar: int | None,
    ppq: float | None,
    bar: int,
) -> bool:
    if last_bar is not None and int(bar) < last_bar:
        return True
    if (
        ppq is not None
        and last_ppq is not None
        and float(ppq) < last_ppq - _POSITION_EPSILON
        and float(ppq)
        <= (int(bar) * 4.0) + _BAR_START_TOLERANCE_BEATS
    ):
        return True
    return False


def _reset_source_epoch_unlocked(
    st: _BridgeState,
    *,
    identity: tuple[str, str] | None = None,
) -> None:
    st.feature_frames.clear()
    st.source_epoch += 1
    st.source_identity = identity if identity is not None else st.source_identity
    st.source_capture_epoch = None
    st.source_last_ppq = None
    st.source_last_bar = None
    st.source_origin_bar = None
    st.source_epoch_closed = False


def _reset_harmonic_epoch_unlocked(
    st: _BridgeState,
    *,
    identity: tuple[str, str] | None = None,
) -> None:
    st.harmonic_frames.clear()
    st.harmonic_epoch += 1
    st.harmonic_identity = (
        identity if identity is not None else st.harmonic_identity
    )
    st.harmonic_capture_epoch = None
    st.harmonic_last_ppq = None
    st.harmonic_last_bar = None
    st.harmonic_origin_bar = None
    st.harmonic_epoch_closed = False


def record_heartbeat(req: BridgeHeartbeatRequest) -> None:
    sid = req.session_id or "_pending_"
    with _LOCK:
        st = _state_for_unlocked(sid)
        st.plugin_instance_id = req.plugin_instance_id
        st.plugin_version = req.plugin_version
        if req.source_id:
            st.source_id = req.source_id
        st.last_seen_at = _now_iso()


def record_transport(frame: BridgeTransportFrame) -> bool:
    """Record transport and report when prior live evidence was invalidated."""

    with _LOCK:
        st = _state_for_unlocked(frame.session_id)
        had_transport = st.last_transport is not None
        had_capture = bool(st.feature_frames or st.harmonic_frames)
        was_playing = bool(
            st.last_transport
            and st.last_transport.get("playing", False)
        )
        st.plugin_instance_id = frame.plugin_instance_id
        st.last_seen_at = _now_iso()
        st.last_transport = frame.model_dump()
        if not frame.playing:
            st.source_epoch_closed = True
            st.harmonic_epoch_closed = True
        elif not was_playing:
            # A transport restart is an explicit take boundary. Keep source
            # identities so the next frame can continue without looking like
            # a second identity reset, but discard all prior-take evidence.
            _reset_source_epoch_unlocked(st)
            _reset_harmonic_epoch_unlocked(st)
        return bool(
            frame.playing
            and not was_playing
            and (had_transport or had_capture)
        )


def record_source_frame(frame: BridgeSourceFeatureFrame) -> bool:
    """Record a playing source frame; return whether it began a new epoch."""

    with _LOCK:
        st = _state_for_unlocked(frame.session_id)
        identity = _source_identity(frame)
        rewind = _rewound(
            last_ppq=st.source_last_ppq,
            last_bar=st.source_last_bar,
            ppq=frame.ppq_position,
            bar=frame.bar_index,
        )
        capture_epoch_changed = (
            frame.capture_epoch is not None
            and st.source_capture_epoch is not None
            and frame.capture_epoch != st.source_capture_epoch
        )
        reset_epoch = False
        if st.source_identity is None:
            st.source_identity = identity
            if st.source_epoch == 0:
                st.source_epoch = 1
            st.source_epoch_closed = False
        elif (
            st.source_identity != identity
            or st.source_epoch_closed
            or capture_epoch_changed
            or rewind
        ):
            _reset_source_epoch_unlocked(st, identity=identity)
            reset_epoch = True
        st.source_capture_epoch = frame.capture_epoch
        if st.source_origin_bar is None:
            st.source_origin_bar = int(frame.bar_index)
        st.plugin_instance_id = frame.plugin_instance_id
        st.source_id = frame.source_id
        st.last_seen_at = _now_iso()
        st.feature_frames.append(frame)
        st.source_last_ppq = (
            float(frame.ppq_position)
            if frame.ppq_position is not None
            else st.source_last_ppq
        )
        st.source_last_bar = int(frame.bar_index)
        if len(st.feature_frames) > _MAX_FRAMES_PER_SESSION:
            excess = len(st.feature_frames) - _MAX_FRAMES_PER_SESSION
            del st.feature_frames[:excess]
        return reset_epoch


def record_harmonic_frame(frame: BridgeHarmonicFrame) -> bool:
    """Record a playing harmonic frame; return whether it began a new epoch."""

    with _LOCK:
        st = _state_for_unlocked(frame.session_id)
        identity = _harmonic_identity(frame)
        rewind = _rewound(
            last_ppq=st.harmonic_last_ppq,
            last_bar=st.harmonic_last_bar,
            ppq=frame.ppq_position,
            bar=frame.bar_index,
        )
        capture_epoch_changed = (
            frame.capture_epoch is not None
            and st.harmonic_capture_epoch is not None
            and frame.capture_epoch != st.harmonic_capture_epoch
        )
        reset_epoch = False
        if st.harmonic_identity is None:
            st.harmonic_identity = identity
            if st.harmonic_epoch == 0:
                st.harmonic_epoch = 1
            st.harmonic_epoch_closed = False
        elif (
            st.harmonic_identity != identity
            or st.harmonic_epoch_closed
            or capture_epoch_changed
            or rewind
        ):
            _reset_harmonic_epoch_unlocked(st, identity=identity)
            reset_epoch = True
        st.harmonic_capture_epoch = frame.capture_epoch
        if st.harmonic_origin_bar is None:
            st.harmonic_origin_bar = int(frame.bar_index)
        st.harmonic_plugin_instance_id = frame.plugin_instance_id
        st.harmonic_source_id = frame.source_id
        st.last_seen_at = _now_iso()
        st.harmonic_frames.append(frame)
        st.harmonic_last_ppq = (
            float(frame.ppq_position)
            if frame.ppq_position is not None
            else st.harmonic_last_ppq
        )
        st.harmonic_last_bar = int(frame.bar_index)
        if len(st.harmonic_frames) > _MAX_FRAMES_PER_SESSION:
            excess = len(st.harmonic_frames) - _MAX_FRAMES_PER_SESSION
            del st.harmonic_frames[:excess]
        return reset_epoch


def close_source_epoch(
    session_id: str,
    *,
    plugin_instance_id: str,
    source_id: str,
    capture_epoch: int | None = None,
) -> None:
    """Close only the active source epoch, preserving legacy epoch-less stops."""

    with _LOCK:
        st = _state_for_unlocked(session_id)
        identity = (str(plugin_instance_id), str(source_id))
        if st.source_identity not in {None, identity}:
            return
        if (
            st.source_capture_epoch is not None
            and capture_epoch is not None
            and capture_epoch != st.source_capture_epoch
        ):
            return
        st.source_epoch_closed = True


def close_harmonic_epoch(
    session_id: str,
    *,
    plugin_instance_id: str,
    source_id: str,
    capture_epoch: int | None = None,
) -> None:
    """Close only the active harmonic epoch, preserving legacy epoch-less stops."""

    with _LOCK:
        st = _state_for_unlocked(session_id)
        identity = (str(plugin_instance_id), str(source_id))
        if st.harmonic_identity not in {None, identity}:
            return
        if (
            st.harmonic_capture_epoch is not None
            and capture_epoch is not None
            and capture_epoch != st.harmonic_capture_epoch
        ):
            return
        st.harmonic_epoch_closed = True


def get_bridge_state(session_id: str) -> dict[str, Any]:
    with _LOCK:
        st = _STATES.get(session_id)
        if st is None:
            return {
                "connected": False,
                "plugin_instance_id": None,
                "session_id": session_id,
                "source_id": None,
                "source_plugin_instance_id": None,
                "harmonic_plugin_instance_id": None,
                "harmonic_source_id": None,
                "last_seen_at": None,
                "frame_count": 0,
                "harmonic_frame_count": 0,
                "source_epoch": 0,
                "harmonic_epoch": 0,
                "last_transport": None,
            }
        plugin_instance_id = (
            st.plugin_instance_id
            if st.plugin_instance_id is not None
            else st.harmonic_plugin_instance_id
        )
        source_id = (
            st.source_id
            if st.source_id is not None
            else st.harmonic_source_id
        )
        return {
            "connected": plugin_instance_id is not None,
            "plugin_instance_id": plugin_instance_id,
            "session_id": session_id,
            "source_id": source_id,
            "source_plugin_instance_id": st.plugin_instance_id,
            "harmonic_plugin_instance_id": st.harmonic_plugin_instance_id,
            "harmonic_source_id": st.harmonic_source_id,
            "last_seen_at": st.last_seen_at,
            "frame_count": len(st.feature_frames),
            "harmonic_frame_count": len(st.harmonic_frames),
            "source_epoch": st.source_epoch,
            "harmonic_epoch": st.harmonic_epoch,
            "last_transport": (
                dict(st.last_transport)
                if st.last_transport is not None
                else None
            ),
        }


def clear_bridge_state(session_id: str | None = None) -> None:
    with _LOCK:
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


def summarize_frames_to_groove_frames(
    session_id: str,
    *,
    bar_count: int | None = None,
) -> list[GrooveFrame]:
    """Compact stored feature frames into one GrooveFrame per bar.

    Logic reports absolute project bars, while Session Player parts are
    loop-local. When ``bar_count`` is provided, the first bar in the current
    capture epoch becomes loop bar 0 and later host bars wrap inside the
    session. This also keeps the mapping stable after the bounded frame buffer
    drops its oldest frames.
    """
    with _LOCK:
        st = _STATES.get(session_id)
        if st is None or not st.feature_frames:
            return []
        frames = list(st.feature_frames)
        origin_bar = (
            int(st.source_origin_bar)
            if st.source_origin_bar is not None
            else int(frames[0].bar_index)
        )

    by_bar: dict[int, list[BridgeSourceFeatureFrame]] = {}
    for f in frames:
        host_bar = int(f.bar_index)
        if bar_count is not None and int(bar_count) > 0:
            loop_bar = (host_bar - origin_bar) % int(bar_count)
        else:
            loop_bar = host_bar
        by_bar.setdefault(loop_bar, []).append(f)

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
                    "capture_origin_host_bar": origin_bar,
                    "loop_bar_index": bar_index,
                },
            )
        )
    return out


def harmonic_frames_for_session(session_id: str) -> list[BridgeHarmonicFrame]:
    with _LOCK:
        st = _STATES.get(session_id)
        if st is None:
            return []
        return list(st.harmonic_frames)


def summarize_harmonic_frames(
    session_id: str,
    *,
    bar_count: int | None = None,
) -> dict[str, Any] | None:
    with _LOCK:
        st = _STATES.get(session_id)
        if st is None or not st.harmonic_frames:
            return None
        frames = list(st.harmonic_frames)
        origin_bar = (
            int(st.harmonic_origin_bar)
            if st.harmonic_origin_bar is not None
            else int(frames[0].bar_index)
        )

    by_bar: dict[int, list[BridgeHarmonicFrame]] = {}
    for frame in frames:
        host_bar = int(frame.bar_index)
        if bar_count is not None and int(bar_count) > 0:
            loop_bar = (host_bar - origin_bar) % int(bar_count)
        else:
            loop_bar = host_bar
        by_bar.setdefault(loop_bar, []).append(frame)

    bars: list[dict[str, Any]] = []
    global_chroma = [0.0] * 12
    global_weight = 0.0
    key_votes: dict[tuple[int, str], float] = {}
    tempo_rows: list[tuple[float, float]] = []

    for bar_index in sorted(by_bar):
        rows = by_bar[bar_index]
        chroma = [0.0] * 12
        weight_sum = 0.0
        for row in rows:
            conf = max(0.05, min(1.0, 0.5 * float(row.key_confidence) + 0.5 * float(row.scale_confidence)))
            dur = max(0.001, float(row.duration_seconds))
            weight = conf * dur
            for i, value in enumerate(row.chroma):
                chroma[i] += float(value) * weight
                global_chroma[i] += float(value) * weight
            weight_sum += weight
            global_weight += weight
            if row.key_pc is not None:
                scale = str(row.scale or "major")
                key_votes[(int(row.key_pc) % 12, scale)] = key_votes.get((int(row.key_pc) % 12, scale), 0.0) + weight
            tempo = row.tempo_bpm if row.tempo_bpm is not None else row.host_tempo
            if tempo is not None:
                tempo_rows.append((float(tempo), max(0.05, float(row.tempo_confidence))))
        if weight_sum > 1e-9:
            chroma = [round(float(x / weight_sum), 6) for x in chroma]
        else:
            chroma = [0.0] * 12
        last = rows[-1]
        bars.append(
            {
                "bar_index": int(bar_index),
                "chroma": chroma,
                "key_pc": int(last.key_pc) if last.key_pc is not None else None,
                "key": last.key,
                "scale": last.scale,
                "key_confidence": float(last.key_confidence),
                "scale_confidence": float(last.scale_confidence),
                "cadence": last.cadence,
                "cadence_confidence": float(last.cadence_confidence),
                "frame_count": len(rows),
                "capture_origin_host_bar": origin_bar,
                "host_bar_indices": sorted({int(row.bar_index) for row in rows}),
            }
        )

    if global_weight > 1e-9:
        global_chroma = [round(float(x / global_weight), 6) for x in global_chroma]
    best_key = max(key_votes.items(), key=lambda kv: kv[1])[0] if key_votes else (None, None)
    if tempo_rows:
        tw = sum(w for _t, w in tempo_rows)
        tempo_bpm = round(sum(t * w for t, w in tempo_rows) / max(1e-9, tw), 3)
        tempo_conf = round(min(1.0, tw / max(1.0, len(tempo_rows))), 4)
    else:
        tempo_bpm = None
        tempo_conf = 0.0

    return {
        "source": "logic_au_harmonic_listener",
        "frame_count": len(frames),
        "bar_count": len(bars),
        "chroma": global_chroma,
        "key_pc": best_key[0],
        "scale": best_key[1],
        "tempo_bpm": tempo_bpm,
        "tempo_confidence": tempo_conf,
        "bars": bars,
    }
