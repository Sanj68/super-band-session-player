"""Bass take analysis and quality scoring for candidate ranking."""

from __future__ import annotations

from dataclasses import dataclass
import math

from app.models.session import LaneNote
from app.services.conditioning import (
    ConditioningHarmonicBar,
    UnifiedConditioning,
    has_source_groove,
    source_kick_weight,
    source_snare_weight,
    source_slot_pressure,
)
from app.services.session_context import SessionAnchorContext, drum_kick_weight, slot_pressure
from app.utils import music_theory as mt


@dataclass(frozen=True)
class BassTakeQuality:
    total: float
    scores: dict[str, float]
    reason: str
    signature: tuple[tuple[int, ...], ...]


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(v)))


def _slot_for_note(note: LaneNote, *, bar_start: float, sixteenth: float) -> int:
    if sixteenth <= 1e-9:
        return 0
    return max(0, min(15, int(round((float(note.start) - bar_start) / sixteenth))))


def _bar_for_note(note: LaneNote, *, bar_anchor: float, bar_len: float, bar_count: int) -> int:
    if bar_len <= 1e-9:
        return 0
    return max(0, min(bar_count - 1, int(math.floor((float(note.start) - bar_anchor) / bar_len))))


def _fallback_harmonic_bar(bar: int, *, key: str, scale: str) -> ConditioningHarmonicBar:
    degree = mt.progression_degrees_for_bars(bar + 1, scale)[bar]
    tones = mt.chord_tones_midi(key, scale, degree, octave=2, seventh=True)
    target = tuple(sorted({int(p) % 12 for p in tones[:3]}))
    scale_pcs = {(mt.key_root_pc(key) + x) % 12 for x in mt.scale_intervals(scale)}
    passing = tuple(sorted(pc for pc in scale_pcs if pc not in target))
    avoid = tuple(sorted(pc for pc in range(12) if pc not in scale_pcs))
    return ConditioningHarmonicBar(
        bar_index=bar,
        root_pc=int(tones[0]) % 12,
        target_pcs=target,
        passing_pcs=passing,
        avoid_pcs=avoid,
        confidence=0.25,
        source="static_progression",
    )


def _harmonic_bar(
    bar: int,
    *,
    conditioning: UnifiedConditioning | None,
    key: str,
    scale: str,
) -> ConditioningHarmonicBar:
    cbar = conditioning.harmonic_bar(bar) if conditioning is not None else None
    if cbar is not None and cbar.target_pcs:
        return cbar
    return _fallback_harmonic_bar(bar, key=key, scale=scale)


def _group_notes_by_bar(
    notes: list[LaneNote],
    *,
    bar_count: int,
    tempo: int,
    bar_anchor: float,
) -> list[list[LaneNote]]:
    spb = 60.0 / float(max(40, min(240, tempo)))
    bar_len = 4.0 * spb
    out: list[list[LaneNote]] = [[] for _ in range(max(1, bar_count))]
    for note in notes:
        bar = _bar_for_note(note, bar_anchor=bar_anchor, bar_len=bar_len, bar_count=len(out))
        out[bar].append(note)
    for row in out:
        row.sort(key=lambda n: (n.start, n.pitch))
    return out


def _rhythm_signature(rows: list[list[LaneNote]], *, tempo: int, bar_anchor: float) -> tuple[tuple[int, ...], ...]:
    spb = 60.0 / float(max(40, min(240, tempo)))
    sixteenth = spb / 4.0
    out: list[tuple[int, ...]] = []
    for bar, notes in enumerate(rows):
        start = bar_anchor + bar * 4.0 * spb
        slots = sorted({_slot_for_note(n, bar_start=start, sixteenth=sixteenth) for n in notes})
        out.append(tuple(slots))
    return tuple(out)


def _harmonic_score(rows: list[list[LaneNote]], *, conditioning: UnifiedConditioning | None, key: str, scale: str) -> tuple[float, float]:
    weighted = 0.0
    total = 0.0
    avoid_hits = 0.0
    for bar, notes in enumerate(rows):
        h = _harmonic_bar(bar, conditioning=conditioning, key=key, scale=scale)
        target = {int(x) % 12 for x in h.target_pcs}
        passing = {int(x) % 12 for x in h.passing_pcs}
        avoid = {int(x) % 12 for x in h.avoid_pcs}
        root = int(h.root_pc) % 12
        for idx, n in enumerate(notes):
            pc = int(n.pitch) % 12
            strong = idx == 0
            weight = 1.3 if strong else 1.0
            total += weight
            if pc == root:
                weighted += 1.0 * weight
            elif pc in target:
                weighted += 0.82 * weight
            elif pc in passing:
                weighted += 0.52 * weight
            elif pc in avoid:
                weighted += 0.06 * weight
                avoid_hits += weight
            else:
                weighted += 0.28 * weight
    if total <= 1e-9:
        return 0.0, 1.0
    avoid_rate = avoid_hits / total
    return _clamp(weighted / total), _clamp(1.0 - (avoid_rate * 2.2))


def _groove_score(
    rows: list[list[LaneNote]],
    *,
    context: SessionAnchorContext | None,
    tempo: int,
    bar_anchor: float,
    conditioning: UnifiedConditioning | None = None,
) -> float:
    if not rows:
        return 0.0
    spb = 60.0 / float(max(40, min(240, tempo)))
    sixteenth = spb / 4.0
    score = 0.0
    total = 0.0
    for bar, notes in enumerate(rows):
        bar_start = bar_anchor + bar * 4.0 * spb
        slots = [_slot_for_note(n, bar_start=bar_start, sixteenth=sixteenth) for n in notes]
        if 0 in slots:
            score += 1.2
        if 8 in slots:
            score += 0.55
        total += 1.75
        for slot in slots:
            total += 1.0
            if context is not None and context.anchor_lane == "drums":
                kick = drum_kick_weight(context, bar, slot)
                pressure = slot_pressure(context, bar, slot)
                score += _clamp(0.35 + 0.75 * kick - 0.3 * max(0.0, pressure - kick))
            elif conditioning is not None and has_source_groove(conditioning) and (
                context is None or context.anchor_lane != "drums"
            ):
                sk = source_kick_weight(conditioning, bar, slot)
                pr = source_slot_pressure(conditioning, bar, slot)
                sn = source_snare_weight(conditioning, bar, slot)
                score += _clamp(0.35 + 0.42 * sk + 0.22 * pr - 0.2 * max(0.0, sn - sk))
            else:
                score += 0.9 if slot % 4 == 0 else 0.62
    return _clamp(score / max(total, 1e-9))


def _phrase_score(signature: tuple[tuple[int, ...], ...]) -> float:
    if not signature:
        return 0.0
    vals: list[float] = []
    for i, slots in enumerate(signature):
        role = i % 4
        count = len(slots)
        has_root = 0 in slots
        if role == 0:
            vals.append((0.55 if has_root else 0.0) + _clamp(1.0 - abs(count - 2.5) / 4.0) * 0.45)
        elif role == 1:
            prev = set(signature[i - 1]) if i > 0 else set()
            diff = len(set(slots).symmetric_difference(prev))
            vals.append((0.35 if has_root else 0.0) + _clamp(diff / 5.0) * 0.65)
        elif role == 2:
            vals.append((0.35 if has_root else 0.0) + _clamp((count - 2.0) / 4.0) * 0.65)
        else:
            late = any(s >= 12 for s in slots)
            vals.append((0.45 if has_root else 0.0) + (0.25 if late else 0.0) + _clamp(1.0 - abs(count - 3.0) / 5.0) * 0.3)
    return _clamp(sum(vals) / len(vals))


def _register_score(notes: list[LaneNote]) -> float:
    if not notes:
        return 0.0
    vals = []
    for n in notes:
        p = int(n.pitch)
        if 34 <= p <= 55:
            vals.append(1.0)
        elif 30 <= p <= 62:
            vals.append(0.72)
        elif 24 <= p <= 67:
            vals.append(0.38)
        else:
            vals.append(0.0)
    return _clamp(sum(vals) / len(vals))


def _repetition_variation_score(signature: tuple[tuple[int, ...], ...]) -> float:
    if len(signature) <= 1:
        return 0.65
    pairs = []
    for i in range(1, len(signature)):
        a = set(signature[i - 1])
        b = set(signature[i])
        union = len(a | b) or 1
        pairs.append(len(a & b) / union)
    avg_sim = sum(pairs) / len(pairs)
    four_bar_recall = 0.5
    if len(signature) >= 5:
        recalls = []
        for i in range(4, len(signature)):
            a = set(signature[i - 4])
            b = set(signature[i])
            recalls.append(len(a & b) / (len(a | b) or 1))
        four_bar_recall = sum(recalls) / len(recalls) if recalls else 0.5
    return _clamp((1.0 - abs(avg_sim - 0.48) / 0.48) * 0.55 + (1.0 - abs(four_bar_recall - 0.72) / 0.72) * 0.45)


def _space_score(rows: list[list[LaneNote]], *, style: str) -> float:
    if not rows:
        return 0.0
    counts = [len(r) for r in rows]
    avg = sum(counts) / len(counts)
    target = {
        "supportive": 2.8,
        "melodic": 3.8,
        "rhythmic": 4.1,
        "slap": 4.8,
        "fusion": 4.9,
    }.get(style, 3.4)
    density_fit = 1.0 - min(1.0, abs(avg - target) / max(target, 1.0))
    emptyish = sum(1 for c in counts if c <= 1) / len(counts)
    clutter = sum(1 for c in counts if c >= 7) / len(counts)
    return _clamp((0.8 * density_fit) + (0.2 * (1.0 - clutter)) - (0.2 * emptyish))


def _style_score(rows: list[list[LaneNote]], signature: tuple[tuple[int, ...], ...], *, style: str) -> float:
    counts = [len(r) for r in rows] or [0]
    avg = sum(counts) / len(counts)
    offbeats = 0
    total = 0
    beat_hits = 0
    late_hits = 0
    for slots in signature:
        for s in slots:
            total += 1
            if s % 4 != 0:
                offbeats += 1
            else:
                beat_hits += 1
            if s >= 12:
                late_hits += 1
    off = offbeats / max(total, 1)
    beat = beat_hits / max(total, 1)
    late = late_hits / max(total, 1)
    if style == "supportive":
        return _clamp((1.0 - abs(avg - 2.7) / 4.0) * 0.55 + beat * 0.45)
    if style == "melodic":
        pitch_motion = _pitch_motion_score(rows)
        return _clamp((1.0 - abs(avg - 3.8) / 5.0) * 0.35 + pitch_motion * 0.45 + (1.0 - off) * 0.2)
    if style == "rhythmic":
        return _clamp((1.0 - abs(avg - 4.0) / 5.0) * 0.4 + off * 0.45 + late * 0.15)
    if style in ("slap", "fusion"):
        return _clamp((1.0 - abs(avg - 4.8) / 6.0) * 0.35 + off * 0.45 + late * 0.2)
    return 0.65


def _pitch_motion_score(rows: list[list[LaneNote]]) -> float:
    notes = [n for row in rows for n in row]
    if len(notes) < 2:
        return 0.5
    intervals = [abs(int(notes[i].pitch) - int(notes[i - 1].pitch)) for i in range(1, len(notes))]
    stepish = sum(1 for x in intervals if 1 <= x <= 5) / len(intervals)
    leaps = sum(1 for x in intervals if x >= 12) / len(intervals)
    return _clamp(stepish * 0.9 + (1.0 - leaps) * 0.1)


def analyze_bass_take(
    notes: list[LaneNote],
    *,
    tempo: int,
    bar_count: int,
    key: str,
    scale: str,
    style: str,
    conditioning: UnifiedConditioning | None = None,
    context: SessionAnchorContext | None = None,
) -> BassTakeQuality:
    bars = max(1, int(bar_count))
    anchor = float(conditioning.bar_start_anchor_sec) if conditioning is not None else (
        float(context.bar_start_anchor_sec) if context is not None else 0.0
    )
    rows = _group_notes_by_bar(notes, bar_count=bars, tempo=tempo, bar_anchor=anchor)
    signature = _rhythm_signature(rows, tempo=tempo, bar_anchor=anchor)
    harmonic, avoid = _harmonic_score(rows, conditioning=conditioning, key=key, scale=scale)
    groove = _groove_score(rows, context=context, tempo=tempo, bar_anchor=anchor, conditioning=conditioning)
    phrase = _phrase_score(signature)
    register = _register_score(notes)
    repvar = _repetition_variation_score(signature)
    space = _space_score(rows, style=style)
    style_score = _style_score(rows, signature, style=style)
    scores = {
        "harmonic_fit": round(harmonic, 4),
        "groove_fit": round(groove, 4),
        "phrase_shape": round(phrase, 4),
        "register_discipline": round(register, 4),
        "repetition_variation": round(repvar, 4),
        "style_match": round(style_score, 4),
        "avoid_tone_control": round(avoid, 4),
        "space_rest_quality": round(space, 4),
    }
    weights = {
        "harmonic_fit": 0.2,
        "groove_fit": 0.18,
        "phrase_shape": 0.15,
        "register_discipline": 0.1,
        "repetition_variation": 0.1,
        "style_match": 0.12,
        "avoid_tone_control": 0.08,
        "space_rest_quality": 0.07,
    }
    total = sum(scores[k] * weights[k] for k in weights)
    strongest = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:2]
    weakest = sorted(scores.items(), key=lambda kv: kv[1])[:2]
    reason = (
        "strong "
        + "/".join(k.replace("_", " ") for k, _ in strongest)
        + "; watch "
        + "/".join(k.replace("_", " ") for k, _ in weakest)
    )
    return BassTakeQuality(total=round(_clamp(total), 4), scores=scores, reason=reason, signature=signature)
