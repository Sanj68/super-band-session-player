"""Rule-based lead lane: phrase shapes and pitch pools vary by ``lead_style``."""

from __future__ import annotations

import io
import random
from typing import Final, TypedDict

import pretty_midi

from app.services.anchor_lane_roles import lead_knobs_for_role, lead_role_for_anchor
from app.services.session_context import (
    SessionAnchorContext,
    density_for_bar,
    drum_kick_weight,
    drum_snare_weight,
    slot_pressure,
)
from app.utils import music_theory as mt

_LEAD_STYLES: Final[frozenset[str]] = frozenset(
    {"sparse", "sparse_emotional", "melodic", "rhythmic", "bluesy", "fusion"}
)
_LEAD_INSTRUMENTS: Final[frozenset[str]] = frozenset({"flute", "vibes", "guitar", "synth_lead"})
_LEAD_PLAYER_IDS: Final[frozenset[str]] = frozenset({"coltrane", "cal_tjader", "soul_sparse", "funk_phrasing"})


class LeadProfile(TypedDict):
    """Bias traits for a named lead personality (inspired-by, not a clone).

    ``base_style`` is a voice hint for preview copy only; ``lead_style`` always selects the engine.
    """

    base_style: str
    phrase_density: float
    burst_activity: float
    rest_preference: float
    contour_strength: float
    sequence_bias: float
    rhythmic_displacement_bias: float
    register_movement_bias: float
    articulation_length_bias: float
    repetition_strength: float
    resolution_bias: float


lead_profiles: dict[str, LeadProfile] = {
    "coltrane": {
        "base_style": "fusion",
        "phrase_density": 0.74,
        "burst_activity": 0.72,
        "rest_preference": 0.3,
        "contour_strength": 0.7,
        "sequence_bias": 0.64,
        "rhythmic_displacement_bias": 0.56,
        "register_movement_bias": 0.72,
        "articulation_length_bias": 0.54,
        "repetition_strength": 0.44,
        "resolution_bias": 0.8,
    },
    "cal_tjader": {
        "base_style": "melodic",
        "phrase_density": 0.52,
        "burst_activity": 0.34,
        "rest_preference": 0.4,
        "contour_strength": 0.64,
        "sequence_bias": 0.58,
        "rhythmic_displacement_bias": 0.46,
        "register_movement_bias": 0.44,
        "articulation_length_bias": 0.6,
        "repetition_strength": 0.66,
        "resolution_bias": 0.62,
    },
    "soul_sparse": {
        "base_style": "sparse_emotional",
        "phrase_density": 0.26,
        "burst_activity": 0.1,
        "rest_preference": 0.84,
        "contour_strength": 0.76,
        "sequence_bias": 0.42,
        "rhythmic_displacement_bias": 0.26,
        "register_movement_bias": 0.22,
        "articulation_length_bias": 0.74,
        "repetition_strength": 0.6,
        "resolution_bias": 0.46,
    },
    "funk_phrasing": {
        "base_style": "rhythmic",
        "phrase_density": 0.58,
        "burst_activity": 0.42,
        "rest_preference": 0.36,
        "contour_strength": 0.4,
        "sequence_bias": 0.36,
        "rhythmic_displacement_bias": 0.76,
        "register_movement_bias": 0.3,
        "articulation_length_bias": 0.36,
        "repetition_strength": 0.56,
        "resolution_bias": 0.54,
    },
}


def normalize_lead_player(lead_player: str | None) -> str | None:
    if lead_player is None:
        return None
    s = str(lead_player).strip().lower()
    if not s or s in ("none", "off", "null"):
        return None
    return s if s in _LEAD_PLAYER_IDS else None


def _phrase_bar_lead(traits: LeadProfile | None, bar: int) -> int:
    if traits and traits["repetition_strength"] > 0.58:
        return bar // 2
    return bar


# (start_16th_in_bar, duration_16ths, pool_slot_index — resolved with rotation % len(pool))
_M_MELODIC_EVEN: Final[tuple[tuple[int, int, int], ...]] = (
    (0, 3, 0),
    (8, 2, 2),
    (13, 3, 1),
)
_M_MELODIC_ODD: Final[tuple[tuple[int, int, int], ...]] = (
    (4, 4, 1),
    (11, 5, 0),
    (15, 1, 3),
)
_M_SPARSE_EVEN: Final[tuple[tuple[int, int, int], ...]] = ((2, 12, 0),)  # long tone, late entry
_M_SPARSE_ODD: Final[tuple[tuple[int, int, int], ...]] = ((6, 10, 2),)  # fewer, longer

_M_RHYTHMIC_EVEN: Final[tuple[tuple[int, int, int], ...]] = (
    (2, 1, 0),
    (5, 1, 0),
    (7, 1, 2),
    (10, 1, 1),
    (13, 1, 2),
    (15, 1, 0),
)
_M_RHYTHMIC_ODD: Final[tuple[tuple[int, int, int], ...]] = (
    (1, 1, 2),
    (4, 2, 0),
    (8, 1, 1),
    (11, 1, 0),
    (14, 1, 2),
)

_M_BLUESY_EVEN: Final[tuple[tuple[int, int, int], ...]] = (
    (0, 4, 0),
    (6, 2, 3),
    (10, 4, 1),
)
_M_BLUESY_ODD: Final[tuple[tuple[int, int, int], ...]] = (
    (3, 3, 2),
    (9, 3, 4),
    (14, 2, 5),
)

_M_FUSION_EVEN: Final[tuple[tuple[int, int, int], ...]] = (
    (1, 2, 4),
    (4, 1, 6),
    (7, 2, 2),
    (10, 1, 5),
    (13, 2, 0),
)
_M_FUSION_ODD: Final[tuple[tuple[int, int, int], ...]] = (
    (0, 1, 7),
    (3, 2, 1),
    (6, 1, 5),
    (9, 2, 3),
    (12, 1, 6),
    (15, 1, 0),
)

# Short arcs, long rests — contour implied by pool order (see ``_emotional_pool``).
_M_SPARSE_EMOTIONAL_EVEN: Final[tuple[tuple[int, int, int], ...]] = (
    (0, 4, 0),
    (10, 3, 2),
)
_M_SPARSE_EMOTIONAL_ODD: Final[tuple[tuple[int, int, int], ...]] = ((3, 9, 1),)

_MOTIF_BORROW: Final[dict[str, tuple[str, ...]]] = {
    "melodic": ("melodic", "rhythmic", "fusion"),
    "sparse": ("sparse", "melodic", "rhythmic"),
    "rhythmic": ("rhythmic", "melodic", "fusion"),
    "bluesy": ("bluesy", "melodic", "sparse"),
    "fusion": ("fusion", "rhythmic", "melodic"),
    "sparse_emotional": ("sparse_emotional",),
}


def normalize_lead_style(lead_style: str | None) -> str:
    if lead_style is None:
        return "melodic"
    s = str(lead_style).strip().lower()
    return s if s in _LEAD_STYLES else "melodic"


def _emotional_pool(key: str, scale: str, deg: int) -> list[int]:
    """Small pitch set ordered for gentle rise-and-fall (no dense scalar runs)."""
    c = mt.chord_tones_midi(key, scale, deg, octave=5, seventh=False)
    r, t, f = c[0], c[1], c[2]
    hi = min(88, max(t + 12, f))
    return [r, t, f, hi, t, r]


def normalize_lead_instrument(lead_instrument: str | None) -> str:
    if lead_instrument is None:
        return "flute"
    s = str(lead_instrument).strip().lower()
    return s if s in _LEAD_INSTRUMENTS else "flute"


def lead_midi_program(lead_instr: str) -> int:
    return {"flute": 73, "vibes": 11, "guitar": 25, "synth_lead": 81}.get(lead_instr, 73)


def _melodic_pool(key: str, scale: str, deg: int) -> list[int]:
    chord = mt.chord_tones_midi(key, scale, deg, octave=5, seventh=False)
    pool: list[int] = [chord[0], chord[1], chord[2], chord[0] + 12]
    color_deg = (deg % 7) + 2
    color = mt.bass_root_midi(key, scale, color_deg, octave=5)
    if color not in pool:
        pool.append(color)
    return pool


def _bluesy_pool(key: str, scale: str, deg: int) -> list[int]:
    """Chord tones + blues color tones (b3, #4/b5, m7) from the chord root when sensible."""
    pool = _melodic_pool(key, scale, deg)
    root = pool[0]
    for delta in (3, 6, 10):
        p = root + delta
        if p not in pool:
            pool.append(p)
    return list(dict.fromkeys(pool))


def _fusion_pool(key: str, scale: str, deg: int) -> list[int]:
    """
    Chord-scale base plus chromatic tensions (b2, maj7, 9) for offbeat lines;
    motifs alternate higher slot indices toward chord tones (implicit resolution).
    """
    pool = _melodic_pool(key, scale, deg)
    root, fifth = pool[0], pool[2]
    extras = [root + 1, root + 11, root + 14, fifth + 12, root + 7 + 12]
    for p in extras:
        if p not in pool:
            pool.append(p)
    return list(dict.fromkeys(pool))


def _style_motifs(style: str, bar: int) -> tuple[tuple[int, int, int], ...]:
    even = bar % 2 == 0
    if style == "sparse_emotional":
        return _M_SPARSE_EMOTIONAL_EVEN if even else _M_SPARSE_EMOTIONAL_ODD
    if style == "sparse":
        return _M_SPARSE_EVEN if even else _M_SPARSE_ODD
    if style == "rhythmic":
        return _M_RHYTHMIC_EVEN if even else _M_RHYTHMIC_ODD
    if style == "bluesy":
        return _M_BLUESY_EVEN if even else _M_BLUESY_ODD
    if style == "fusion":
        return _M_FUSION_EVEN if even else _M_FUSION_ODD
    return _M_MELODIC_EVEN if even else _M_MELODIC_ODD


def _motif_style_for_bar(
    style: str,
    lead_salt: int,
    suit_mode: str | None,
    traits: LeadProfile | None,
) -> str:
    """Pick which phrase family to read motifs from (may differ from harmony ``style``)."""
    if style == "sparse_emotional":
        return "sparse_emotional"
    if suit_mode == "sparse_fill":
        ws, wm = 0.78, 0.22
        if traits:
            ws = min(0.92, 0.52 + 0.38 * traits["rest_preference"])
            wm = 1.0 - ws
        return random.choices(["sparse", "melodic"], weights=[ws, wm], k=1)[0]
    if suit_mode == "counter":
        w = [0.55, 0.3, 0.15]
        if traits:
            w = [
                0.32 + 0.42 * traits["rest_preference"],
                0.22 + 0.28 * traits["phrase_density"],
                0.12 + 0.28 * traits["rhythmic_displacement_bias"],
            ]
            s = sum(w) or 1.0
            w = [x / s for x in w]
        return random.choices(["sparse", "melodic", "rhythmic"], weights=w, k=1)[0]
    if suit_mode == "solo":
        w = [0.32, 0.28, 0.22, 0.18]
        if traits:
            w = [
                0.2 + 0.28 * traits["rhythmic_displacement_bias"],
                0.16 + 0.28 * traits["phrase_density"],
                0.18 + 0.2 * traits["resolution_bias"],
                max(0.06, 0.2 - 0.12 * traits["phrase_density"]),
            ]
            s = sum(w) or 1.0
            w = [x / s for x in w]
        return random.choices(["rhythmic", "fusion", "melodic", style], weights=w, k=1)[0]
    borrow_p = 0.52
    if traits:
        borrow_p = min(0.84, max(0.26, 0.34 + 0.42 * traits["phrase_density"]))
    if random.random() < borrow_p:
        opts = list(_MOTIF_BORROW.get(style, (style,)))
        if traits:
            rp = traits["rest_preference"]
            rd = traits["rhythmic_displacement_bias"]
            pd = traits["phrase_density"]
            rs = traits["repetition_strength"]
            if rs > 0.6 and pd < 0.58 and random.random() < 0.38:
                return "melodic"
            if rp > 0.72:
                narrowed = [o for o in opts if o in ("sparse", "melodic", "sparse_emotional")]
                if narrowed:
                    return random.choice(narrowed)
            if rd > 0.68 and "rhythmic" in opts:
                return random.choices(opts, weights=[4 if o == "rhythmic" else 1 for o in opts], k=1)[0]
            if pd > 0.68 and "fusion" in opts:
                return random.choices(opts, weights=[4 if o == "fusion" else 1 for o in opts], k=1)[0]
        return random.choice(opts)
    return style


def _jitter_motif(
    motif: tuple[tuple[int, int, int], ...],
    *,
    max_start_nudge: int = 2,
    dur_scale: tuple[float, float] = (0.78, 1.22),
) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for start_16, dur_16, slot in motif:
        ns = min(15, max(0, start_16 + random.randint(-max_start_nudge, max_start_nudge)))
        d = max(1, int(round(dur_16 * random.uniform(dur_scale[0], dur_scale[1]))))
        out.append((ns, d, slot))
    return out


def _dur_factor(style: str) -> float:
    if style == "sparse_emotional":
        return 1.08
    if style == "sparse":
        return 1.0
    if style == "rhythmic":
        return 0.82
    if style == "fusion":
        return 0.88
    return 0.97


def _smoothing_pitch(prev: int | None, pitch: int, style: str, traits: LeadProfile | None = None) -> int:
    """Melodic: avoid wild leaps by nudging toward previous note (stepwise preference)."""
    if style not in ("melodic", "sparse_emotional") or prev is None:
        return pitch
    diff = pitch - prev
    max_leap = 7
    if traits:
        max_leap = max(4, min(9, int(10 - traits["contour_strength"] * 5)))
    if abs(diff) > max_leap:
        step = max_leap if diff > 0 else -max_leap
        return prev + step
    return pitch


def _mean_gap_for_bar(ctx: SessionAnchorContext, bar: int) -> float:
    b = bar % ctx.bar_count
    rows = ctx.mean_gap_sec_per_bar
    if b < len(rows) and rows[b] > 1e-6:
        return float(rows[b])
    return float(ctx.sixteenth_len_sec) * 2.5


def _gap_affinity(ctx: SessionAnchorContext, bar: int, slot: int) -> float:
    """High when anchor is quiet around this slot (good landing / answer zone)."""
    lo, hi = max(0, slot - 1), min(15, slot + 1)
    mx = max(slot_pressure(ctx, bar, s) for s in range(lo, hi + 1))
    return max(0.0, min(1.0, 1.0 - mx))


def _phrase_window_anchor_slot(ctx: SessionAnchorContext, bar: int) -> int | None:
    """Center of a short low-occupancy window (phrase placement hint)."""
    b = bar % ctx.bar_count
    if b >= len(ctx.slot_occupancy):
        return None
    row = ctx.slot_occupancy[b]
    best_s, best_c = None, 1.5
    for s in range(0, 13):
        c = sum(row[s + i] for i in range(4)) / 4.0
        if c < best_c:
            best_c, best_s = c, s + 1
    return best_s if best_c < 0.78 else None


def _onset_attack_slots_16(ctx: SessionAnchorContext, bar: int) -> set[int]:
    """Map anchor onsets (0–1 within bar) to sixteenth indices for attack-aware phrasing."""
    b = bar % ctx.bar_count
    on = ctx.onsets_norm_per_bar[b] if b < len(ctx.onsets_norm_per_bar) else ()
    return {min(15, max(0, int(t * 16.0 + 1e-9))) for t in on}


def _select_phrase_windows(
    ctx: SessionAnchorContext,
    bar: int,
    *,
    suit_mode: str | None,
    player_key: str | None,
    role_occ_bias: float = 0.0,
) -> list[tuple[int, int]]:
    """
    Choose 1–3 inclusive sixteenth ranges [start, end] where the lead should phrase.

    Windows are built from three signals:
    1. ``slot_occupancy`` — sliding windows (3–5 sixteenths) with the lowest average occupancy
       are treated as "room to speak".
    2. ``mean_gap_sec_per_bar`` — when the anchor leaves longer gaps, we allow more separate
       windows (up to 3); dense bars collapse toward one wider safe pocket.
    3. ``onsets_norm_per_bar`` — converted to attack slots; for ``counter`` mode and for
       chord/drum anchors we nudge window starts slightly *after* attacks so the line answers
       instead of doubling every stab.

    ``solo`` widens acceptable overlap (windows may include busier regions); ``sparse_fill``
    tightens thresholds so phrases stay short and gap-respecting.
    """
    b = bar % ctx.bar_count
    row = list(ctx.slot_occupancy[b] if b < len(ctx.slot_occupancy) else (0.0,) * 16)
    den = density_for_bar(ctx, bar)
    mg = _mean_gap_for_bar(ctx, bar)
    gap_ratio = min(1.2, mg / (ctx.bar_len_sec * 0.22 + 1e-6))

    if suit_mode == "sparse_fill":
        n_target = 1 if den > 9 else 2
    elif suit_mode == "solo":
        n_target = 3 if den < 7 and gap_ratio > 0.85 else min(3, 2 + int(gap_ratio > 0.65))
    elif suit_mode == "counter":
        n_target = 1 if den > 13 else 2
    else:
        n_target = 1 if den > 12 else 2 if den > 7 else 3

    occ_thresh = 0.52 + 0.12 * min(1.0, den / 14.0)
    if suit_mode == "sparse_fill":
        occ_thresh -= 0.06
    elif suit_mode == "solo":
        occ_thresh += 0.07
    if player_key == "soul_sparse":
        occ_thresh -= 0.05
    if player_key == "funk_phrasing":
        occ_thresh -= 0.04
    occ_thresh += role_occ_bias

    scored: list[tuple[float, int, int, int]] = []
    for win_w in (5, 4, 3):
        for s in range(0, 17 - win_w):
            e = s + win_w - 1
            cost = sum(row[s + i] for i in range(win_w)) / float(win_w)
            scored.append((cost, s, e, win_w))
    scored.sort(key=lambda x: x[0])

    attacks = _onset_attack_slots_16(ctx, bar)
    chosen: list[tuple[int, int]] = []
    used_cover = [False] * 16

    def overlap_too_much(s: int, e: int) -> bool:
        for i in range(s, e + 1):
            if used_cover[i]:
                return True
        return False

    def mark(s: int, e: int) -> None:
        for i in range(s, e + 1):
            used_cover[i] = True

    for cost, s, e, _ww in scored:
        if len(chosen) >= n_target:
            break
        if cost > occ_thresh + (0.14 if suit_mode != "solo" else 0.22):
            continue
        if overlap_too_much(s, e) and len(chosen) > 0:
            continue
        s1, e1 = s, e
        if suit_mode == "counter" or ctx.anchor_lane in ("chords", "drums"):
            if s1 in attacks and s1 < e1:
                s1 = min(e1, s1 + random.randint(1, 2))
            if ctx.anchor_lane == "chords" and (s1 + 1) in attacks and s1 < 13:
                s1 = min(e1, s1 + 1)
        if e1 - s1 < 2:
            continue
        chosen.append((s1, e1))
        mark(s, e)

    if not chosen:
        chosen = [(0, min(15, 4 + int(6 * gap_ratio)))]
    if len(chosen) < n_target and den < 10:
        for cost, s, e, _ww in scored:
            if len(chosen) >= n_target:
                break
            if (s, e) in chosen or cost > occ_thresh + 0.2:
                continue
            chosen.append((s, min(15, e)))
    return chosen[: max(1, min(3, n_target))]


def _snap_motif_to_phrase_windows(
    motif: tuple[tuple[int, int, int], ...],
    windows: list[tuple[int, int]],
    ctx: SessionAnchorContext,
    bar: int,
    *,
    suit_mode: str | None,
) -> tuple[tuple[int, int, int], ...]:
    """Clamp each motif onset into a phrase window so rolls read as intentional phrases."""
    if not motif or not windows:
        return motif
    out: list[tuple[int, int, int]] = []
    for ev_i, (s0, d0, slot) in enumerate(motif):
        ws, we = windows[min(ev_i, len(windows) - 1)]
        s_clamped = min(we, max(ws, s0))
        if ctx.anchor_lane == "drums" and suit_mode != "solo":
            if s_clamped in (4, 12):
                sw = drum_snare_weight(ctx, bar, s_clamped)
                if sw > 0.4:
                    s_clamped = min(we, s_clamped + random.randint(1, 2))
            if s_clamped in (0, 8) and drum_kick_weight(ctx, bar, s_clamped) > 0.55 and ev_i > 0:
                s_clamped = min(we, s_clamped + random.randint(0, 1))
        if ctx.anchor_lane == "bass" and ev_i > 0 and slot_pressure(ctx, bar, s_clamped) > 0.62:
            s_clamped = min(we, s_clamped + random.randint(1, 2))
        out.append((s_clamped, d0, slot))
    return tuple(out)


def _warp_motif_for_context(
    motif: tuple[tuple[int, int, int], ...],
    ctx: SessionAnchorContext,
    bar: int,
    *,
    suit_mode: str | None,
    player_key: str | None,
) -> tuple[tuple[int, int, int], ...]:
    """Nudge phrase starts toward low-occupancy windows; bounded shifts, rule-based."""
    if not motif:
        return motif
    anchor_slot = _phrase_window_anchor_slot(ctx, bar)
    out: list[tuple[int, int, int]] = []
    pull = 0.55 if suit_mode == "counter" else 0.42 if suit_mode == "solo" else 0.62
    if player_key == "cal_tjader":
        pull += 0.08
    if player_key == "funk_phrasing" and ctx.anchor_lane == "drums":
        pull += 0.06
    if player_key == "soul_sparse":
        pull -= 0.1
    pull = max(0.22, min(0.82, pull))
    for i, (s0, d0, sl) in enumerate(motif):
        s = s0
        if i == 0 and anchor_slot is not None:
            delta = anchor_slot - s0
            if abs(delta) <= 6:
                shift = int(round(delta * pull))
                shift = max(-3, min(3, shift))
                s = max(0, min(15, s0 + shift))
        elif i == 0 and ctx.anchor_lane == "drums":
            mg = _mean_gap_for_bar(ctx, bar)
            if mg > ctx.sixteenth_len_sec * 3.5 and random.random() < 0.4:
                s = min(15, s0 + random.randint(0, 1))
        if ctx.anchor_lane == "drums" and i > 0 and suit_mode != "solo":
            sn4, sn12 = drum_snare_weight(ctx, bar, 4), drum_snare_weight(ctx, bar, 12)
            if sn4 > 0.38 and s in (3, 4, 5) and random.random() < 0.55 + 0.12 * (sn4 + sn12):
                s = min(15, s + random.randint(1, 2))
            elif sn12 > 0.38 and s in (11, 12, 13) and random.random() < 0.5 + 0.1 * sn12:
                s = min(15, s + random.randint(1, 2))
            if suit_mode != "solo" and drum_kick_weight(ctx, bar, s) > 0.52 and drum_snare_weight(ctx, bar, s) > 0.35:
                s = min(15, s + random.randint(1, 2))
        elif ctx.anchor_lane == "chords" and i >= 0:
            pr = slot_pressure(ctx, bar, s)
            if pr > 0.58 and random.random() < 0.5 + 0.2 * pr:
                s = min(15, s + random.randint(1, 2))
        elif ctx.anchor_lane == "bass" and i > 0:
            if slot_pressure(ctx, bar, s) > 0.55 and _gap_affinity(ctx, bar, s) < 0.42:
                s = min(15, s + random.randint(1, 3))
        out.append((s, d0, sl))
    return tuple(out)


def _context_start_delay_16(
    ctx: SessionAnchorContext,
    bar: int,
    start_eff: int,
    *,
    suit_mode: str | None,
) -> int:
    """Extra sixteenth delay for answer-after-attack (chords / drums), bounded."""
    extra = 0
    if ctx.anchor_lane == "chords":
        pr = slot_pressure(ctx, bar, start_eff)
        mg = _mean_gap_for_bar(ctx, bar)
        if pr > 0.45:
            extra = 1 if pr < 0.72 else 2
            extra = min(2, extra + int(min(1.0, mg / (ctx.sixteenth_len_sec * 4.0 + 1e-6))))
        if suit_mode == "counter":
            extra = min(3, extra + 1)
        elif suit_mode == "sparse_fill":
            extra = min(2, extra)
    elif ctx.anchor_lane == "drums":
        kw = drum_kick_weight(ctx, bar, start_eff)
        sw = drum_snare_weight(ctx, bar, start_eff)
        if kw > 0.42 and sw < 0.22 and random.random() < 0.35:
            extra = 1
        if sw > 0.45 and start_eff in (4, 5, 12, 13):
            extra = max(extra, 1)
        if suit_mode == "counter":
            extra = min(3, extra + 1)
    return min(3, max(0, extra))


def _context_skip_note(
    ctx: SessionAnchorContext,
    bar: int,
    start_eff: int,
    ev_i: int,
    *,
    suit_mode: str | None,
    player_key: str | None,
    traits: LeadProfile | None,
) -> bool:
    """Return True to skip this motif event (bounded probability)."""
    if ev_i == 0:
        return False
    pr = slot_pressure(ctx, bar, start_eff)
    gap_aff = _gap_affinity(ctx, bar, start_eff)
    mg = _mean_gap_for_bar(ctx, bar)
    gap_boost = min(0.32, mg / (ctx.bar_len_sec + 1e-6))
    skip_p = 0.12 + 0.34 * pr - 0.3 * gap_aff - 0.14 * gap_boost
    den = density_for_bar(ctx, bar)
    if den > 10.0:
        skip_p += 0.06 * min(1.0, (den - 10.0) / 10.0)
    if ctx.anchor_lane == "drums":
        kw = drum_kick_weight(ctx, bar, start_eff)
        sw = drum_snare_weight(ctx, bar, start_eff)
        if start_eff in (6, 7, 14, 15) and sw > 0.28 and pr < 0.52:
            skip_p -= 0.16
        if kw > 0.48 and sw > 0.42:
            skip_p += 0.14
        if start_eff in (4, 12) and sw > 0.5 and kw < 0.25:
            skip_p += 0.12
    elif ctx.anchor_lane == "chords":
        if pr > 0.62:
            skip_p += 0.1
        if pr > 0.75 and start_eff % 4 == 0:
            skip_p += 0.08
    elif ctx.anchor_lane == "bass":
        if pr > 0.58:
            skip_p += 0.12
        if gap_aff > 0.58:
            skip_p -= 0.12
        if den > 9.0:
            skip_p += 0.08 * min(1.0, (den - 9.0) / 9.0)

    if suit_mode == "solo":
        skip_p *= 0.86
    elif suit_mode == "counter":
        skip_p *= 1.1
    elif suit_mode == "sparse_fill":
        skip_p *= 1.22

    if player_key == "coltrane":
        skip_p = skip_p * 1.05 if gap_aff < 0.45 else skip_p * 0.9
    elif player_key == "cal_tjader":
        skip_p *= 0.94
    elif player_key == "soul_sparse":
        skip_p *= 1.14
    elif player_key == "funk_phrasing":
        if ctx.anchor_lane == "drums":
            skip_p += 0.04 * (1.0 - drum_kick_weight(ctx, bar, max(0, start_eff - 1)))

    if traits is not None:
        skip_p += (traits["rest_preference"] - 0.5) * 0.08
    skip_p = max(0.04, min(0.82, skip_p))
    return random.random() < skip_p


def _player_context_dur_scale(player_key: str | None, ctx: SessionAnchorContext | None, bar: int) -> float:
    if not ctx or not player_key:
        return 1.0
    den = density_for_bar(ctx, bar)
    if player_key == "coltrane":
        return 0.94 + 0.06 * min(1.0, den / 14.0)
    if player_key == "cal_tjader":
        return 1.02
    if player_key == "soul_sparse":
        return 1.06 + 0.05 * min(1.0, den / 12.0)
    if player_key == "funk_phrasing":
        return 0.9 - 0.04 * min(1.0, den / 14.0)
    return 1.0


def _fusion_octave_jump(bar: int, slot: int, pitch: int, traits: LeadProfile | None) -> int:
    p = pitch
    if bar % 3 == 0 and slot % 3 == 0:
        p = pitch + 12
    elif bar % 5 == 0 and slot % 2 == 1:
        p = pitch - 12
    rj = 0.28 + (0.34 * traits["register_movement_bias"] if traits else 0.0)
    if random.random() < min(0.62, rj):
        p += random.choice((-12, 0, 0, 0, 12))
    return max(55, min(96, p))


def generate_lead(
    *,
    tempo: int,
    bar_count: int,
    key: str,
    scale: str,
    lead_style: str | None = None,
    lead_instrument: str | None = None,
    suit_mode: str | None = None,
    suit_bass_density: float = 0.0,
    suit_chord_density: float = 0.0,
    suit_lead_density: float = 0.0,
    suit_chord_style: str = "",
    suit_bass_style: str = "",
    session_preset: str | None = None,
    lead_player: str | None = None,
    context: SessionAnchorContext | None = None,
) -> tuple[bytes, str]:
    style = normalize_lead_style(lead_style)
    lead_instr = normalize_lead_instrument(lead_instrument)
    player_key = normalize_lead_player(lead_player)
    traits: LeadProfile | None = lead_profiles[player_key] if player_key else None
    lag_extra_16 = 0
    dur_scale_extra = 1.0
    sparse_fill_skip = False
    if suit_mode:
        cd = float(suit_chord_density) + (0.65 if suit_chord_style in ("dense", "stabs") else 0.0)
        bd = float(suit_bass_density) + (0.45 if suit_bass_style in ("slap", "rhythmic", "fusion") else 0.0)
        ld = float(suit_lead_density)
        if suit_mode == "sparse_fill":
            style = "sparse"
            sparse_fill_skip = True
        elif suit_mode == "solo":
            if style == "sparse":
                style = "melodic"
            if cd > 3.4:
                style = "rhythmic"
            elif style == "melodic" and cd > 2.2:
                style = "rhythmic"
            dur_scale_extra = random.uniform(0.7, 0.88)
        elif suit_mode == "counter":
            if ld > 3.8 or cd > 2.6 or bd > 2.0:
                style = "sparse"
            else:
                style = "melodic"
            lag_extra_16 = random.randint(2, 7)
    lead_role_name = lead_role_for_anchor(context.anchor_lane) if context else None
    lead_role_knobs = lead_knobs_for_role(lead_role_name or "melodic") if context else None
    if lead_role_knobs:
        lag_extra_16 = min(15, lag_extra_16 + lead_role_knobs.lag_add_16)
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    inst = pretty_midi.Instrument(program=lead_midi_program(lead_instr), name="Lead")
    spb = 60.0 / float(tempo)
    sixteenth = spb / 4.0
    degrees = mt.progression_degrees_for_bars(bar_count, scale)
    prev_pitch: int | None = None
    lead_salt = random.randint(0, 127)

    for bar, deg in enumerate(degrees):
        if style == "bluesy":
            pool = _bluesy_pool(key, scale, deg)
        elif style == "fusion":
            pool = _fusion_pool(key, scale, deg)
        elif style == "sparse_emotional":
            pool = _emotional_pool(key, scale, deg)
        else:
            pool = _melodic_pool(key, scale, deg)
        reg_bias = traits["register_movement_bias"] if traits else 0.35
        if style != "sparse_emotional" and random.random() < 0.38 + 0.22 * reg_bias:
            pool = list(pool)
            pool.append(min(96, pool[random.randint(0, len(pool) - 1)] + random.choice((12, -12))))

        motif_style = _motif_style_for_bar(style, lead_salt, suit_mode, traits)
        ph = _phrase_bar_lead(traits, bar)
        motif_base = _style_motifs(motif_style, ph + (lead_salt % 2))
        burst_x = 0.28 + (0.24 * traits["burst_activity"] if traits else 0.0)
        if context:
            dd_bar = density_for_bar(context, bar)
            burst_x *= 1.0 - 0.2 * min(1.0, max(0.0, (dd_bar - 5.0) / 14.0))
            if suit_mode == "sparse_fill":
                burst_x *= 0.8
            elif suit_mode == "counter":
                burst_x *= 0.9
            elif suit_mode == "solo" and dd_bar < 6.5:
                burst_x *= 1.07
        if lead_role_knobs:
            burst_x *= lead_role_knobs.burst_mult
        if style != "sparse_emotional" and random.random() < burst_x:
            motif_base = _style_motifs(motif_style, ph + 1 + (lead_salt % 3))
        rd = traits["rhythmic_displacement_bias"] if traits else 0.45
        if lead_role_knobs:
            rd = max(0.08, min(0.92, rd + lead_role_knobs.rhythmic_displacement_add))
        max_nudge = min(4, 2 + int(2.2 * rd))
        al = traits["articulation_length_bias"] if traits else 0.5
        dur_lo, dur_hi = 0.78 - 0.08 * al, 1.22 + 0.14 * al
        if lead_role_knobs:
            dur_lo *= lead_role_knobs.dur_lo_scale
            dur_hi *= lead_role_knobs.dur_hi_scale
        if style == "sparse_emotional":
            motif_tuples = tuple(_jitter_motif(motif_base, max_start_nudge=1, dur_scale=(0.94, 1.06)))
        else:
            motif_tuples = (
                tuple(_jitter_motif(motif_base, max_start_nudge=max_nudge, dur_scale=(dur_lo, dur_hi)))
                if random.random() < 0.84
                else motif_base
            )
        if context:
            phrase_windows = _select_phrase_windows(
                context,
                bar,
                suit_mode=suit_mode,
                player_key=player_key,
                role_occ_bias=lead_role_knobs.phrase_occ_bias if lead_role_knobs else 0.0,
            )
            motif_tuples = _snap_motif_to_phrase_windows(
                motif_tuples, phrase_windows, context, bar, suit_mode=suit_mode
            )
            motif_tuples = _warp_motif_for_context(
                motif_tuples, context, bar, suit_mode=suit_mode, player_key=player_key
            )
        bar_t0 = bar * 4 * spb
        bar_t1 = (bar + 1) * 4 * spb
        rot_mod = 3 if style == "rhythmic" else 2 if style == "sparse_emotional" else 4
        rot = ((ph // 2) + (lead_salt % rot_mod)) % rot_mod
        if style != "sparse_emotional":
            rot = (rot + random.randint(0, rot_mod)) % rot_mod

        for ev_i, (start_16, dur_16, slot) in enumerate(motif_tuples):
            if sparse_fill_skip and ev_i > 0 and (bar * 5 + ev_i * 2 + start_16 + (lead_salt % 5)) % 10 < 4:
                continue
            if traits and ev_i > 0:
                rest_roll = traits["rest_preference"] * 0.24 * (
                    lead_role_knobs.bar_rest_roll_mult if lead_role_knobs else 1.0
                )
                if random.random() < rest_roll:
                    continue
            start_eff = min(start_16 + lag_extra_16, 15)
            if context:
                start_eff = min(15, start_eff + _context_start_delay_16(context, bar, start_eff, suit_mode=suit_mode))
            if context and _context_skip_note(
                context,
                bar,
                start_eff,
                ev_i,
                suit_mode=suit_mode,
                player_key=player_key,
                traits=traits,
            ):
                continue
            late = sixteenth * (0.07 + (lead_salt % 5) / 140.0) if style == "sparse_emotional" else 0.0
            disp = (0.022 + 0.048 * rd) if style != "sparse_emotional" else (0.008 + 0.012 * rd)
            if context and player_key == "cal_tjader":
                disp *= 0.88
            t0 = bar_t0 + start_eff * sixteenth + late + random.uniform(0, disp) * spb
            if context:
                t0 += sixteenth * 0.04 * min(1.0, context.syncopation_score) * (0.35 if start_eff % 4 == 0 else 1.0)
                if context.anchor_lane == "chords" and slot_pressure(context, bar, start_eff) > 0.5:
                    t0 += sixteenth * random.uniform(0.35, 1.05) * min(1.0, slot_pressure(context, bar, start_eff))
                if context.anchor_lane == "drums" and suit_mode == "counter":
                    t0 += sixteenth * 0.45 * drum_snare_weight(context, bar, max(0, start_eff - 1))
            dur_j = random.uniform(0.94, 1.06) if style == "sparse_emotional" else random.uniform(0.82, 1.22)
            dur_mul = (
                _dur_factor(style)
                * (0.88 if lead_instr == "guitar" else 1.0)
                * dur_scale_extra
                * dur_j
                * (0.8 + 0.38 * al)
                * _player_context_dur_scale(player_key, context, bar)
            )
            if context and density_for_bar(context, bar) > 11.0:
                dur_mul *= 0.92 - 0.04 * min(1.0, (density_for_bar(context, bar) - 11.0) / 8.0)
            if context and player_key == "coltrane" and density_for_bar(context, bar) < 6.5:
                dur_mul *= 1.05
            t1 = t0 + dur_16 * sixteenth * dur_mul
            if t0 >= bar_t1 - 1e-4:
                continue
            if t1 > bar_t1:
                t1 = bar_t1 - 1e-4
            if t1 <= t0:
                continue
            seq_roll = 0.62 - (0.38 * traits["sequence_bias"] if traits else 0.0)
            if context and context.anchor_lane == "chords" and player_key == "cal_tjader":
                seq_roll = max(0.08, seq_roll - 0.08)
            if context and context.anchor_lane == "drums" and player_key == "funk_phrasing":
                seq_roll = min(0.78, seq_roll + 0.06)
            idx = (slot + rot + (random.randint(0, 2) if random.random() < seq_roll else 0)) % len(pool)
            if style == "fusion" and start_eff >= 10 and ev_i > 0:
                idx = idx % min(3, len(pool))
            is_last = ev_i == len(motif_tuples) - 1
            if is_last and traits and traits["resolution_bias"] > 0.52:
                idx = min(idx, max(0, (len(pool) + 1) // 2 - 1))
            if (
                is_last
                and context
                and player_key == "coltrane"
                and traits
                and density_for_bar(context, bar) > 9.0
            ):
                idx = min(idx, max(0, (len(pool) + 2) // 3))
            pitch = pool[idx]
            reg_nudge = 0.24 + (0.26 * reg_bias if traits else 0.0)
            if context and player_key == "funk_phrasing":
                reg_nudge *= 0.82
            if style not in ("fusion", "sparse_emotional") and random.random() < reg_nudge:
                pitch = max(55, min(96, pitch + random.choice((0, 0, 12, -12))))
            if style == "fusion":
                pitch = _fusion_octave_jump(bar, slot, pitch, traits)
            sm_style = "melodic" if style == "sparse_emotional" else style
            pitch = _smoothing_pitch(prev_pitch, pitch, sm_style, traits)
            if context and player_key == "soul_sparse" and prev_pitch is not None:
                mx_leap = 5 if density_for_bar(context, bar) > 8.0 else 6
                if abs(pitch - prev_pitch) > mx_leap:
                    pitch = prev_pitch + (mx_leap if pitch > prev_pitch else -mx_leap)
            on_beat = start_eff % 4 == 0
            offbeat = start_eff % 4 != 0
            if style == "rhythmic" and offbeat:
                vel = min(100, 92 + (start_eff % 3) * 2 + random.randint(-6, 6))
            elif style == "fusion" and offbeat:
                vel = min(104, 86 + (slot % 4) * 4 + random.randint(-6, 8))
            elif style == "sparse_emotional":
                vb = 78 if on_beat else max(54, 72 - start_eff)
                vel = max(44, min(96, vb + random.randint(-5, 5)))
            else:
                vb = 96 if on_beat else max(68, 88 - start_eff)
                vel = max(52, min(120, vb + random.randint(-8, 8)))
            if context and player_key == "soul_sparse" and density_for_bar(context, bar) > 10.5:
                vel = max(36, min(120, int(vel * 0.93)))
            inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))
            prev_pitch = pitch

    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    instr_lbl = {"flute": "flute", "vibes": "vibes", "guitar": "guitar", "synth_lead": "synth lead"}.get(
        lead_instr, lead_instr
    )
    spacious = ""
    if lead_instr in ("flute", "vibes") and style in ("sparse", "sparse_emotional"):
        spacious = " Open, airy phrasing with more space around the line."
    who = f", {player_key}" if player_key else ""
    player_bits = {
        "coltrane": "Lead player «coltrane» (directional intensity voice): arcs, bursts, sequence tilt, strong cadence.",
        "cal_tjader": "Lead player «cal_tjader» (lyrical syncopation voice): elegant motifs, medium density, clean repeats.",
        "soul_sparse": "Lead player «soul_sparse» (vocal restraint voice): rests, contour, no flashy runs.",
        "funk_phrasing": "Lead player «funk_phrasing» (groove-hook voice): short cells, pocket, less wandering.",
    }
    player_line = (player_bits[player_key] + " ") if player_key and player_key in player_bits else ""
    suit_tag = ""
    if suit_mode == "solo":
        suit_tag = " Suit: forward solo line (context-aware)."
    elif suit_mode == "counter":
        suit_tag = " Suit: counter line — space & later entries vs harmony/rhythm."
    elif suit_mode == "sparse_fill":
        suit_tag = " Suit: sparse fill — short gestures, more rests."
    soul = (session_preset or "").strip().lower() == "rare_groove_soul"
    soul_tag = (
        " Rare groove soul: emotional phrasing through rests and contour, not more notes."
        if soul and style == "sparse_emotional"
        else ""
    )
    role_tag = f" Role vs anchor: {lead_role_name}." if lead_role_name else ""
    preview = (
        f"Lead [{style}, {instr_lbl}{who}]: {mt.normalize_key(key)} {mt.describe_scale(scale)}, "
        f"{bar_count} bar(s), {tempo} BPM — {player_line}{_preview_blurb(style)}{spacious}{suit_tag}{soul_tag}{role_tag}"
    )
    return buf.getvalue(), preview


def _preview_blurb(style: str) -> str:
    if style == "sparse_emotional":
        return (
            "very sparse short phrases, long rests, gentle rise-and-fall contour, soft dynamics — "
            "emotional phrasing without dense runs."
        )
    if style == "sparse":
        return "very few long notes, lots of space."
    if style == "melodic":
        return "balanced phrases, smoother contour (default)."
    if style == "rhythmic":
        return "short syncopated hits, repeated cells, offbeat accents."
    if style == "bluesy":
        return "triad base with b3, tritone color, and m7 from the chord root."
    if style == "fusion":
        return "busier offbeat lines, wider intervals & octave shifts, chromatic tensions leaning back to chord tones."
    return "balanced phrases."
