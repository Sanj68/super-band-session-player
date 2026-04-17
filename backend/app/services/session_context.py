"""Anchor lane analysis for coherent multi-lane generation (density, timing, space, register, groove)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from app.services.midi_note_extract import extract_lane_notes

_ANCHOR_LANES: Final[frozenset[str]] = frozenset({"drums", "bass", "chords", "lead"})


def normalize_anchor_lane(anchor_lane: str | None) -> str | None:
    if anchor_lane is None:
        return None
    s = str(anchor_lane).strip().lower()
    return s if s in _ANCHOR_LANES else None


def _lane_midi_bytes(session: Any, lane: str) -> bytes | None:
    if lane == "drums":
        return getattr(session, "drum_bytes", None)
    if lane == "bass":
        return getattr(session, "bass_bytes", None)
    if lane == "chords":
        return getattr(session, "chords_bytes", None)
    if lane == "lead":
        return getattr(session, "lead_bytes", None)
    return None


@dataclass(frozen=True)
class SessionAnchorContext:
    """Lightweight snapshot of the anchor lane for complementary generation."""

    tempo: int
    bar_count: int
    anchor_lane: str
    bar_len_sec: float
    beat_len_sec: float
    sixteenth_len_sec: float
    """Notes per bar (anchor lane)."""
    density_per_bar: tuple[float, ...]
    """Per bar: normalized onset positions in [0,1) within the bar (one entry per note start)."""
    onsets_norm_per_bar: tuple[tuple[float, ...], ...]
    """Per bar: gaps in seconds between consecutive sorted note starts (empty if <2 notes)."""
    gap_sec_per_bar: tuple[tuple[float, ...], ...]
    """Mean gap in seconds per bar (0 if no gaps)."""
    mean_gap_sec_per_bar: tuple[float, ...]
    pitch_min: int
    pitch_max: int
    pitch_span: int
    """Fraction of onsets that fall off quarter-note grid (rough syncopation / swing feel)."""
    syncopation_score: float
    """Mean notes per bar across session."""
    mean_density: float
    """16 slots × bar_count: occupancy 0..1 per sixteenth (note starts landing in that slot)."""
    slot_occupancy: tuple[tuple[float, ...], ...]
    """When anchor is drums: GM kick (35–36) onset weight per sixteenth slot, else zeros."""
    kick_slot_weight: tuple[tuple[float, ...], ...]
    """When anchor is drums: snare/rim/clap (37–40) onset weight per slot, else zeros."""
    snare_slot_weight: tuple[tuple[float, ...], ...]


_KICK_GM: Final[frozenset[int]] = frozenset({35, 36})
_SNARE_GM: Final[frozenset[int]] = frozenset({37, 38, 39, 40})


def build_session_context(session: Any) -> SessionAnchorContext | None:
    """
    Extract timing/density/space/register/groove hints from the session's anchor lane MIDI.

    Returns ``None`` when no anchor is set or anchor lane has no MIDI yet — callers keep
    legacy independent generation.
    """
    anchor = normalize_anchor_lane(getattr(session, "anchor_lane", None))
    if not anchor:
        return None
    tempo = int(getattr(session, "tempo", 120) or 120)
    bar_count = max(1, int(getattr(session, "bar_count", 8) or 8))
    raw = _lane_midi_bytes(session, anchor)
    if not raw:
        return None

    notes = extract_lane_notes(raw)
    if not notes:
        return None

    spb = 60.0 / float(max(40, min(240, tempo)))
    bar_len = 4.0 * spb
    beat_len = spb
    sixteenth = spb / 4.0

    by_bar: list[list[float]] = [[] for _ in range(bar_count)]
    pitches: list[int] = []
    for n in notes:
        pitches.append(n.pitch)
        bi = int(n.start / bar_len) if bar_len > 1e-9 else 0
        if bi < 0 or bi >= bar_count:
            continue
        rel = (n.start - bi * bar_len) / bar_len
        rel = rel - int(rel)
        if rel < 0:
            rel += 1.0
        by_bar[bi].append(rel)

    density: list[float] = []
    onsets_norm: list[tuple[float, ...]] = []
    gaps_per: list[tuple[float, ...]] = []
    mean_gaps: list[float] = []
    slot_occ_rows: list[tuple[float, ...]] = []
    kick_occ = [[0.0] * 16 for _ in range(bar_count)]
    snare_occ = [[0.0] * 16 for _ in range(bar_count)]

    for b in range(bar_count):
        starts = sorted(by_bar[b])
        density.append(float(len(starts)))
        onsets_norm.append(tuple(starts))
        if len(starts) < 2:
            gaps_per.append(())
            mean_gaps.append(0.0)
        else:
            g = [starts[i + 1] - starts[i] for i in range(len(starts) - 1)]
            gaps_per.append(tuple(max(0.0, x) * bar_len for x in g))
            mean_gaps.append(sum(gaps_per[-1]) / len(gaps_per[-1]) if gaps_per[-1] else 0.0)

        occ = [0.0] * 16
        for rel in starts:
            slot = min(15, int(rel * 16.0 + 1e-9))
            occ[slot] += 1.0
        mx = max(occ) if occ else 1.0
        slot_occ_rows.append(tuple(min(1.0, x / max(1.0, mx)) for x in occ))

    pmin = min(pitches) if pitches else 36
    pmax = max(pitches) if pitches else 60
    span = max(1, pmax - pmin)

    off_grid = 0
    total_onsets = 0
    for starts in by_bar:
        for rel in starts:
            total_onsets += 1
            in_beat = (rel * 4.0) % 1.0
            if in_beat > 0.08 and in_beat < 0.42:
                off_grid += 1
            elif in_beat > 0.58:
                off_grid += 1
    sync = (off_grid / total_onsets) if total_onsets else 0.0

    mean_d = sum(density) / float(bar_count) if bar_count else 0.0

    kick_rows: list[tuple[float, ...]] = []
    snare_rows: list[tuple[float, ...]] = []
    if anchor == "drums":
        for n in notes:
            bi = int(n.start / bar_len) if bar_len > 1e-9 else 0
            if bi < 0 or bi >= bar_count:
                continue
            rel = (n.start - bi * bar_len) / bar_len
            rel = rel - int(rel)
            if rel < 0:
                rel += 1.0
            slot = min(15, int(rel * 16.0 + 1e-9))
            p = int(n.pitch)
            if p in _KICK_GM:
                kick_occ[bi][slot] += 1.0
            elif p in _SNARE_GM:
                snare_occ[bi][slot] += 1.0
        for b in range(bar_count):
            mxk = max(kick_occ[b]) if kick_occ[b] and max(kick_occ[b]) > 0 else 0.0
            mxs = max(snare_occ[b]) if snare_occ[b] and max(snare_occ[b]) > 0 else 0.0
            kick_rows.append(
                tuple(min(1.0, x / max(1.0, mxk)) for x in kick_occ[b]) if mxk > 0 else tuple(0.0 for _ in range(16))
            )
            snare_rows.append(
                tuple(min(1.0, x / max(1.0, mxs)) for x in snare_occ[b]) if mxs > 0 else tuple(0.0 for _ in range(16))
            )
    else:
        z = tuple(0.0 for _ in range(16))
        kick_rows = [z for _ in range(bar_count)]
        snare_rows = [z for _ in range(bar_count)]

    return SessionAnchorContext(
        tempo=tempo,
        bar_count=bar_count,
        anchor_lane=anchor,
        bar_len_sec=bar_len,
        beat_len_sec=beat_len,
        sixteenth_len_sec=sixteenth,
        density_per_bar=tuple(density),
        onsets_norm_per_bar=tuple(onsets_norm),
        gap_sec_per_bar=tuple(gaps_per),
        mean_gap_sec_per_bar=tuple(mean_gaps),
        pitch_min=pmin,
        pitch_max=pmax,
        pitch_span=span,
        syncopation_score=min(1.0, max(0.0, sync)),
        mean_density=mean_d,
        slot_occupancy=tuple(slot_occ_rows),
        kick_slot_weight=tuple(kick_rows),
        snare_slot_weight=tuple(snare_rows),
    )


def slot_pressure(ctx: SessionAnchorContext, bar: int, slot_0_15: int) -> float:
    b = bar % ctx.bar_count
    row = ctx.slot_occupancy[b] if b < len(ctx.slot_occupancy) else (0.0,) * 16
    s = max(0, min(15, slot_0_15))
    return float(row[s]) if s < len(row) else 0.0


def density_for_bar(ctx: SessionAnchorContext, bar: int) -> float:
    b = bar % ctx.bar_count
    if b < len(ctx.density_per_bar):
        return float(ctx.density_per_bar[b])
    return ctx.mean_density


def drum_kick_weight(ctx: SessionAnchorContext, bar: int, slot_0_15: int) -> float:
    """Normalized kick emphasis at a sixteenth slot (0 for non-drum anchors)."""
    if ctx.anchor_lane != "drums":
        return 0.0
    b = bar % ctx.bar_count
    s = max(0, min(15, slot_0_15))
    row = ctx.kick_slot_weight[b] if b < len(ctx.kick_slot_weight) else (0.0,) * 16
    return float(row[s]) if s < len(row) else 0.0


def drum_snare_weight(ctx: SessionAnchorContext, bar: int, slot_0_15: int) -> float:
    """Normalized snare/rim emphasis at a sixteenth slot (0 for non-drum anchors)."""
    if ctx.anchor_lane != "drums":
        return 0.0
    b = bar % ctx.bar_count
    s = max(0, min(15, slot_0_15))
    row = ctx.snare_slot_weight[b] if b < len(ctx.snare_slot_weight) else (0.0,) * 16
    return float(row[s]) if s < len(row) else 0.0


def drum_kick_emphasis_max(ctx: SessionAnchorContext, bar: int, slot_0_15: int, radius: int = 2) -> float:
    """Max kick weight in a small window around ``slot_0_15`` (groove lock / neighbor bias)."""
    lo, hi = max(0, slot_0_15 - radius), min(15, slot_0_15 + radius)
    return max(drum_kick_weight(ctx, bar, j) for j in range(lo, hi + 1))
