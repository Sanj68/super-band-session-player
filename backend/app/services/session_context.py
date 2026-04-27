"""Anchor lane analysis for coherent multi-lane generation (density, timing, space, register, groove)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final

from app.services.midi_note_extract import extract_lane_notes
from app.utils import music_theory as mt

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
    """Estimated beat-phase offset (0..3) for anchor downbeat relative to timeline start."""
    beat_phase_offset_beats: int
    """Confidence in beat-phase estimate."""
    beat_phase_confidence: float
    """Absolute bar-start anchor used by context-aware generation."""
    bar_start_anchor_sec: float
    """Per-bar harmonic root pitch-class guess (0=C)."""
    harmonic_root_pc_per_bar: tuple[int, ...]
    """Per-bar stable target pitch classes."""
    harmonic_target_pcs_per_bar: tuple[tuple[int, ...], ...]
    """Per-bar allowed passing pitch classes."""
    harmonic_passing_pcs_per_bar: tuple[tuple[int, ...], ...]
    """Per-bar avoid pitch classes for structural tones."""
    harmonic_avoid_pcs_per_bar: tuple[tuple[int, ...], ...]
    """Per-bar confidence for harmonic targeting."""
    harmonic_confidence_per_bar: tuple[float, ...]
    """Per-bar source tag (evidence or scale_fallback)."""
    harmonic_source_per_bar: tuple[str, ...]


_KICK_GM: Final[frozenset[int]] = frozenset({35, 36})
_SNARE_GM: Final[frozenset[int]] = frozenset({37, 38, 39, 40})


def _estimate_anchor_phase(notes: list[Any], beat_len: float) -> tuple[int, float]:
    if beat_len <= 1e-9 or not notes:
        return 0, 0.0
    scores = [0.0, 0.0, 0.0, 0.0]
    for n in notes:
        onset_beat = int(round(float(n.start) / beat_len))
        phase = onset_beat % 4
        vel = float(max(1, min(127, n.velocity))) / 127.0
        dur = max(0.04, float(n.end - n.start))
        scores[phase] += vel * min(1.5, 0.5 + dur)
    best = max(range(4), key=lambda i: scores[i])
    total = sum(scores)
    ordered = sorted(scores, reverse=True)
    if total <= 1e-9:
        return best, 0.0
    sep = (ordered[0] - ordered[1]) / total if len(ordered) > 1 else 0.0
    conf = min(1.0, 0.25 + (1.8 * max(0.0, sep)))
    return best, conf


def _stable_tones_from_root(root_pc: int, key_pc: int, scale_intervals: list[int]) -> tuple[int, ...]:
    if not scale_intervals:
        return (root_pc % 12,)
    root_rel = (root_pc - key_pc) % 12
    if root_rel not in scale_intervals:
        return (root_pc % 12,)
    i = scale_intervals.index(root_rel)
    n = len(scale_intervals)
    pcs = (
        (key_pc + scale_intervals[i]) % 12,
        (key_pc + scale_intervals[(i + 2) % n]) % 12,
        (key_pc + scale_intervals[(i + 4) % n]) % 12,
    )
    return tuple(dict.fromkeys(pcs))


def _build_harmonic_targets(
    session: Any,
    *,
    bar_count: int,
    bar_len: float,
    bar_start_anchor_sec: float,
) -> tuple[tuple[int, ...], tuple[tuple[int, ...], ...], tuple[tuple[int, ...], ...], tuple[tuple[int, ...], ...], tuple[float, ...], tuple[str, ...]]:
    key = str(getattr(session, "key", "C") or "C")
    scale = str(getattr(session, "scale", "major") or "major")
    key_pc = mt.key_root_pc(key)
    scale_intervals = mt.scale_intervals(scale)
    scale_pcs = tuple((key_pc + x) % 12 for x in scale_intervals)

    lane_weights: tuple[tuple[str, float], ...] = (("chords", 2.2), ("bass", 1.4), ("lead", 0.6))
    roots: list[int] = []
    targets: list[tuple[int, ...]] = []
    passings: list[tuple[int, ...]] = []
    avoids: list[tuple[int, ...]] = []
    confs: list[float] = []
    sources: list[str] = []

    for bar in range(bar_count):
        hist: dict[int, float] = {}
        for lane, lane_w in lane_weights:
            raw = _lane_midi_bytes(session, lane)
            if not raw:
                continue
            for n in extract_lane_notes(raw):
                bi = int((n.start - bar_start_anchor_sec) / bar_len) if bar_len > 1e-9 else 0
                if bi != bar:
                    continue
                pc = int(n.pitch) % 12
                vel_w = float(max(1, min(127, n.velocity))) / 127.0
                dur_w = max(0.05, float(n.end - n.start))
                hist[pc] = hist.get(pc, 0.0) + (lane_w * vel_w * dur_w)

        # Honest fallback: static tonic when evidence is absent/weak.
        source = "scale_fallback"
        conf = 0.2
        root_pc = key_pc
        if hist:
            total = sum(hist.values())
            ranked = sorted(hist.items(), key=lambda kv: kv[1], reverse=True)
            best_pc, best_w = ranked[0]
            second_w = ranked[1][1] if len(ranked) > 1 else 0.0
            # Prefer in-scale evidence; if top is out-of-scale and weak, pull to tonic.
            in_scale_bonus = 0.08 if best_pc in scale_pcs else -0.08
            sep = (best_w - second_w) / max(best_w, 1e-9)
            ratio = best_w / max(total, 1e-9)
            if best_pc in scale_pcs or ratio >= 0.5:
                root_pc = best_pc
            source = "evidence"
            conf = max(0.15, min(1.0, 0.5 * ratio + 0.35 * sep + in_scale_bonus))

        stable = _stable_tones_from_root(root_pc, key_pc, scale_intervals)
        passing = tuple(pc for pc in scale_pcs if pc not in stable)
        avoid = tuple(pc for pc in range(12) if pc not in scale_pcs)
        roots.append(int(root_pc))
        targets.append(stable)
        passings.append(passing)
        avoids.append(avoid)
        confs.append(round(float(conf), 4))
        sources.append(source)

    return tuple(roots), tuple(targets), tuple(passings), tuple(avoids), tuple(confs), tuple(sources)


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
    phase_offset_beats, phase_conf = _estimate_anchor_phase(notes, beat_len)
    bar_start_anchor_sec = float(phase_offset_beats) * beat_len
    harmonic_roots, harmonic_targets, harmonic_passings, harmonic_avoids, harmonic_confs, harmonic_sources = _build_harmonic_targets(
        session,
        bar_count=bar_count,
        bar_len=bar_len,
        bar_start_anchor_sec=bar_start_anchor_sec,
    )

    by_bar: list[list[float]] = [[] for _ in range(bar_count)]
    pitches: list[int] = []
    for n in notes:
        pitches.append(n.pitch)
        bi = int((n.start - bar_start_anchor_sec) / bar_len) if bar_len > 1e-9 else 0
        if bi < 0 or bi >= bar_count:
            continue
        rel = (n.start - bar_start_anchor_sec - bi * bar_len) / bar_len
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
            bi = int((n.start - bar_start_anchor_sec) / bar_len) if bar_len > 1e-9 else 0
            if bi < 0 or bi >= bar_count:
                continue
            rel = (n.start - bar_start_anchor_sec - bi * bar_len) / bar_len
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
        beat_phase_offset_beats=phase_offset_beats,
        beat_phase_confidence=round(phase_conf, 4),
        bar_start_anchor_sec=round(bar_start_anchor_sec, 6),
        harmonic_root_pc_per_bar=harmonic_roots,
        harmonic_target_pcs_per_bar=harmonic_targets,
        harmonic_passing_pcs_per_bar=harmonic_passings,
        harmonic_avoid_pcs_per_bar=harmonic_avoids,
        harmonic_confidence_per_bar=harmonic_confs,
        harmonic_source_per_bar=harmonic_sources,
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
