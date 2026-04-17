"""Rule-based drum lane: ``drum_style`` engine with optional ``drum_player`` bias layer."""

from __future__ import annotations

import io
import random
from typing import Final, TypedDict

import pretty_midi

from app.services.session_context import SessionAnchorContext, slot_pressure

_KICK: Final[int] = 36
_SNARE: Final[int] = 38
_HIHAT_CLOSED: Final[int] = 42
_HIHAT_OPEN: Final[int] = 46
_RIM: Final[int] = 37
_LOW_TOM: Final[int] = 43

_DRUM_STYLES: Final[frozenset[str]] = frozenset(
    {"straight", "broken", "shuffle", "funk", "latin", "laid_back_soul"}
)
_DRUM_KITS: Final[frozenset[str]] = frozenset({"standard", "dry", "percussion"})
_DRUM_PLAYER_IDS: Final[frozenset[str]] = frozenset({"stubblefield", "questlove", "dilla"})


class DrumProfile(TypedDict):
    """Bias traits for a named drum personality (inspired-by, not a clone).

    ``base_style`` is a voice hint for preview copy only; ``drum_style`` always selects the engine.
    """

    base_style: str
    kick_activity: float
    snare_backbeat_strength: float
    ghost_note_bias: float
    hat_density_bias: float
    swing_bias: float
    timing_looseness: float
    behind_beat_bias: float
    repetition_strength: float
    fill_activity: float
    dynamic_range: float


drum_profiles: dict[str, DrumProfile] = {
    "stubblefield": {
        "base_style": "funk",
        "kick_activity": 0.74,
        "snare_backbeat_strength": 0.9,
        "ghost_note_bias": 0.56,
        "hat_density_bias": 0.58,
        "swing_bias": 0.38,
        "timing_looseness": 0.52,
        "behind_beat_bias": 0.36,
        "repetition_strength": 0.8,
        "fill_activity": 0.38,
        "dynamic_range": 0.74,
    },
    "questlove": {
        "base_style": "laid_back_soul",
        "kick_activity": 0.42,
        "snare_backbeat_strength": 0.78,
        "ghost_note_bias": 0.22,
        "hat_density_bias": 0.4,
        "swing_bias": 0.48,
        "timing_looseness": 0.42,
        "behind_beat_bias": 0.78,
        "repetition_strength": 0.7,
        "fill_activity": 0.14,
        "dynamic_range": 0.52,
    },
    "dilla": {
        "base_style": "shuffle",
        "kick_activity": 0.56,
        "snare_backbeat_strength": 0.64,
        "ghost_note_bias": 0.34,
        "hat_density_bias": 0.66,
        "swing_bias": 0.88,
        "timing_looseness": 0.62,
        "behind_beat_bias": 0.58,
        "repetition_strength": 0.84,
        "fill_activity": 0.24,
        "dynamic_range": 0.76,
    },
}


def normalize_drum_style(drum_style: str | None) -> str:
    if drum_style is None:
        return "straight"
    s = str(drum_style).strip().lower()
    return s if s in _DRUM_STYLES else "straight"


def normalize_drum_kit(drum_kit: str | None) -> str:
    if drum_kit is None:
        return "standard"
    s = str(drum_kit).strip().lower()
    return s if s in _DRUM_KITS else "standard"


def normalize_drum_player(drum_player: str | None) -> str | None:
    if drum_player is None:
        return None
    s = str(drum_player).strip().lower()
    if not s or s in ("none", "off", "null"):
        return None
    return s if s in _DRUM_PLAYER_IDS else None


def _behind_nudge(traits: DrumProfile | None, sixteenth: float) -> float:
    if not traits:
        return 0.0
    return sixteenth * traits["behind_beat_bias"] * 0.15


def _loosen_mul(traits: DrumProfile | None) -> float:
    if not traits:
        return 1.0
    return 1.0 + traits["timing_looseness"] * 0.11


def _rep_bar(traits: DrumProfile | None, bar: int) -> int:
    if traits and traits["repetition_strength"] > 0.56:
        return 0
    return bar


def _snare_boost(traits: DrumProfile | None) -> int:
    if not traits:
        return 0
    return int(10 * traits["snare_backbeat_strength"])


def _apply_dynamic_shape(inst: pretty_midi.Instrument, traits: DrumProfile | None) -> None:
    if not traits:
        return
    dr = traits["dynamic_range"]
    mid = 86.0
    for n in inst.notes:
        n.velocity = max(1, min(127, int(mid + (n.velocity - mid) * (0.36 + 0.64 * dr))))


def _emit_profile_ghost_snares(
    inst: pretty_midi.Instrument,
    bar_off: float,
    sixteenth: float,
    *,
    salt: int,
    bar: int,
    traits: DrumProfile | None,
    anchor_ctx: SessionAnchorContext | None = None,
) -> None:
    if not traits or traits["ghost_note_bias"] < 0.18:
        return
    gh = traits["ghost_note_bias"]
    bb = _behind_nudge(traits, sixteenth)
    for gs in (5, 7, 13, 15):
        if anchor_ctx and slot_pressure(anchor_ctx, bar, gs) > 0.62 and random.random() < 0.5:
            continue
        if (salt * 5 + bar * 7 + gs * 2) % 10 < int(2 + gh * 6):
            t0 = bar_off + gs * sixteenth + bb * 0.82
            v = max(18, min(54, int(24 + gh * 32)))
            inst.notes.append(
                pretty_midi.Note(velocity=v, pitch=_SNARE, start=t0, end=t0 + sixteenth * (0.16 + (salt % 5) / 120.0))
            )


def _preview(
    style: str,
    bar_count: int,
    tempo: int,
    kit: str,
    *,
    session_preset: str | None,
    drum_player: str | None,
) -> str:
    blurbs = {
        "straight": "clean 4/4 kick/snare backbone with steady 8th hi-hats.",
        "broken": "offbeat kick placements and varied backbeat spacing for a less predictable pocket.",
        "shuffle": "swung 8th-note hi-hat feel with pushed offbeats; kicks/snares follow a bluesy grid.",
        "funk": "syncopated kick cells with tight repeating 16th hats and snare on 2 & 4.",
        "latin": "lighter kick, syncopated hats and rims/toms for a more clave-like percussive phrase.",
        "laid_back_soul": (
            "soft backbeat, quarter-note hats nudged behind the beat, low velocities, no fill runs — "
            "space, warmth, and groove over density; variation stays in timing/velocity, not busier fills."
        ),
    }
    kit_bits = {
        "standard": "GM standard kit.",
        "dry": "Lower velocities for a dryer, tighter room sound.",
        "percussion": "Auxiliary conga/bongo/cowbell layers for a more percussion-forward balance on the kit.",
    }
    soul = (session_preset or "").strip().lower() == "rare_groove_soul"
    tag = (
        " Rare groove soul session: emotional phrasing through pocket, not more notes."
        if soul and style == "laid_back_soul"
        else ""
    )
    player_bits = {
        "stubblefield": "Drum player «stubblefield» (greasy funk-pocket voice on your style): ghosts, backbeat weight, human looseness.",
        "questlove": "Drum player «questlove» (laid-back soul voice on your style): fat, behind, restrained fills.",
        "dilla": "Drum player «dilla» (swung loop voice on your style): off-grid feel, intentional pocket, sample-era tilt.",
    }
    tail = blurbs.get(style, blurbs["straight"])
    if drum_player and drum_player in player_bits:
        tail = f"{player_bits[drum_player]} {tail}"
    who = f", {drum_player}" if drum_player else ""
    return (
        f"Drums [{style}, kit={kit}{who}]: 4/4, {bar_count} bar(s), {tempo} BPM — "
        f"{tail} {kit_bits.get(kit, kit_bits['standard'])}{tag}"
    )


def _note(
    inst: pretty_midi.Instrument,
    *,
    pitch: int,
    vel: int,
    t0: float,
    t1: float,
) -> None:
    inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))


def _anchor_hat_skip(ctx: SessionAnchorContext | None, bar: int, slot: int) -> bool:
    if not ctx or ctx.anchor_lane == "drums":
        return False
    pr = slot_pressure(ctx, bar, slot)
    thr = 0.52 + 0.1 * min(1.0, ctx.mean_density / 10.0)
    if slot % 4 == 0:
        return False
    return pr > thr and random.random() < 0.12 + pr * 0.36


def _anchor_sync_push(ctx: SessionAnchorContext | None, sixteenth: float, slot: int) -> float:
    if not ctx:
        return 0.0
    sc = min(1.0, ctx.syncopation_score)
    w = 0.35 + 0.65 * (1.0 if slot % 2 else 0.55)
    return sixteenth * 0.048 * sc * w


def _emit_straight(
    inst: pretty_midi.Instrument,
    bar_off: float,
    sixteenth: float,
    *,
    salt: int,
    bar: int,
    traits: DrumProfile | None,
    anchor_ctx: SessionAnchorContext | None = None,
) -> None:
    kick_pairs = (
        (0, 8),
        (0, 9),
        (1, 8),
        (2, 7),
        (0, 7),
        (0, 10),
        (1, 9),
        (2, 8),
        (3, 11),
    )
    bw = _rep_bar(traits, bar)
    kicks = kick_pairs[(salt + bw * 3) % len(kick_pairs)]
    snare_pairs = ((4, 12), (3, 11), (5, 13), (4, 13), (5, 12), (6, 14))
    snares = snare_pairs[(salt // 3 + bw * 2) % len(snare_pairs)]
    hat_push = ((salt >> 1) + bar) % 5
    dense_hat = not traits or traits["hat_density_bias"] > 0.42
    hat_stride = 2 if (salt + bar) % 4 != 0 and dense_hat else 1
    bb = _behind_nudge(traits, sixteenth)
    lm = _loosen_mul(traits)
    sb = _snare_boost(traits)
    ka = traits["kick_activity"] if traits else 0.55
    if traits and ka > 0.62 and (salt + bar * 2) % 5 == 1:
        kicks = tuple(sorted(set(kicks + (14,))))
    for s in range(16):
        t0 = bar_off + s * sixteenth + bb + hat_push * sixteenth * 0.045 + _anchor_sync_push(anchor_ctx, sixteenth, s)
        t1 = t0 + sixteenth * random.uniform(0.82 * lm, 0.96 * lm)
        if s % hat_stride == 0:
            if _anchor_hat_skip(anchor_ctx, bar, s):
                pass
            else:
                hat_accent = 72 if s % 4 == 0 else 54
                hat_accent = max(38, min(122, hat_accent + random.randint(-14, 14)))
                _note(inst, pitch=_HIHAT_CLOSED, vel=hat_accent, t0=t0, t1=t1)
        if s in kicks:
            _note(
                inst,
                pitch=_KICK,
                vel=max(84, min(127, 100 + random.randint(-12, 10))),
                t0=t0,
                t1=t0 + sixteenth * random.uniform(0.9 * lm, 1.06 * lm),
            )
        if s in snares:
            _note(
                inst,
                pitch=_SNARE,
                vel=max(86, min(127, 104 + random.randint(-14, 12) + sb)),
                t0=t0,
                t1=t0 + sixteenth * random.uniform(0.9 * lm, 1.06 * lm),
            )


def _emit_broken(
    inst: pretty_midi.Instrument,
    bar_off: float,
    sixteenth: float,
    bar: int,
    *,
    salt: int,
    traits: DrumProfile | None,
    anchor_ctx: SessionAnchorContext | None = None,
) -> None:
    kick_a = (
        (0, 3, 7, 10),
        (0, 4, 8, 11),
        (1, 5, 9, 13),
        (0, 2, 6, 11),
        (1, 4, 7, 12),
    )
    kick_b = (
        (0, 5, 9, 14),
        (0, 6, 10, 14),
        (2, 6, 10, 15),
        (1, 5, 8, 13),
        (0, 7, 10, 15),
    )
    bw = _rep_bar(traits, bar)
    kicks = kick_a[(salt + bw) % len(kick_a)] if bar % 2 == 0 else kick_b[(salt + bw) % len(kick_b)]
    snare_sets = (
        (4, 13),
        (5, 12),
        (4, 12),
        (3, 11),
        (6, 14),
        (5, 11),
        (4, 14),
    )
    snares = snare_sets[(salt // 2 + bw) % len(snare_sets)]
    bb = _behind_nudge(traits, sixteenth)
    lm = _loosen_mul(traits)
    sb = _snare_boost(traits)
    for s in range(16):
        t0 = bar_off + s * sixteenth + bb + _anchor_sync_push(anchor_ctx, sixteenth, s)
        t1 = t0 + sixteenth * random.uniform(0.86 * lm, 0.94 * lm)
        if s % 2 == 0:
            if not _anchor_hat_skip(anchor_ctx, bar, s):
                hv = 68 if s % 4 else 56
                hv = max(44, min(118, hv + random.randint(-8, 8)))
                _note(inst, pitch=_HIHAT_CLOSED, vel=hv, t0=t0, t1=t1)
        if s in kicks:
            _note(
                inst,
                pitch=_KICK,
                vel=max(86, min(124, 98 + random.randint(-10, 10))),
                t0=t0,
                t1=t0 + sixteenth * random.uniform(0.9 * lm, 0.99 * lm),
            )
        if s in snares:
            _note(
                inst,
                pitch=_SNARE,
                vel=max(88, min(124, 100 + random.randint(-8, 8) + sb)),
                t0=t0,
                t1=t0 + sixteenth * random.uniform(0.9 * lm, 0.99 * lm),
            )


def _emit_shuffle(
    inst: pretty_midi.Instrument,
    bar_off: float,
    sixteenth: float,
    spb: float,
    *,
    salt: int,
    bar: int,
    traits: DrumProfile | None,
    anchor_ctx: SessionAnchorContext | None = None,
) -> None:
    eighth = spb / 2.0
    swing_mul = (0.72 + 0.45 * traits["swing_bias"]) if traits else 1.0
    swing = eighth * random.uniform(0.24, 0.42) * swing_mul
    kick_shift = (salt % 5) * sixteenth * 0.18
    kick_beats = ((0, 4), (0, 5), (1, 4), (0, 3))
    sn_beats = ((2, 6), (3, 6), (2, 7), (1, 5))
    bw = _rep_bar(traits, bar)
    kb = kick_beats[(salt + bw * 5) % len(kick_beats)]
    sb = sn_beats[(salt // 2) % len(sn_beats)]
    bb = _behind_nudge(traits, sixteenth)
    lm = _loosen_mul(traits)
    sbst = _snare_boost(traits)
    for i in range(8):
        base = bar_off + i * eighth + bb
        t_hat = base + (swing if i % 2 == 1 else 0.0)
        t_hat += _anchor_sync_push(anchor_ctx, sixteenth, min(15, i * 2 + 1))
        t1 = t_hat + eighth * random.uniform(0.34 * lm, 0.5 * lm)
        slot = min(15, i * 2 + (1 if i % 2 == 1 else 0))
        if not _anchor_hat_skip(anchor_ctx, bar, slot):
            hv = 70 if i % 4 == 0 else 56
            hv = max(40, min(120, hv + random.randint(-10, 10)))
            _note(inst, pitch=_HIHAT_CLOSED, vel=hv, t0=t_hat, t1=t1)
        if i in kb:
            tk = base + (swing * 0.25 if i == kb[1] else 0.0) + kick_shift
            _note(
                inst,
                pitch=_KICK,
                vel=max(86, min(126, 100 + random.randint(-12, 10))),
                t0=tk,
                t1=tk + sixteenth * random.uniform(0.98 * lm, 1.2 * lm),
            )
        if i in sb:
            ts = base + (swing * 0.5 if i == sb[1] else 0.0)
            _note(
                inst,
                pitch=_SNARE,
                vel=max(86, min(126, 102 + random.randint(-12, 10) + sbst)),
                t0=ts,
                t1=ts + sixteenth * random.uniform(0.92 * lm, 1.14 * lm),
            )


def _emit_funk(
    inst: pretty_midi.Instrument,
    bar_off: float,
    sixteenth: float,
    *,
    salt: int,
    bar: int,
    traits: DrumProfile | None,
    anchor_ctx: SessionAnchorContext | None = None,
) -> None:
    bases = (
        (0, 3, 6, 10, 11),
        (0, 2, 6, 10, 13),
        (0, 3, 7, 10, 14),
        (1, 3, 6, 9, 11),
        (0, 4, 7, 11, 14),
        (1, 4, 8, 11, 15),
        (0, 2, 5, 9, 12),
        (2, 5, 8, 12, 15),
    )
    bw = _rep_bar(traits, bar)
    kicks = set(bases[(salt + bw) % len(bases)])
    extra_thresh = 0.55
    if traits:
        extra_thresh = max(0.12, min(0.62, 0.55 - 0.32 * (traits["kick_activity"] - 0.5)))
    if random.random() < extra_thresh:
        kicks.add(random.choice((2, 5, 8, 13, 14, 1, 7)))
    if len(kicks) > 4 and random.random() < 0.28:
        kicks.discard(random.choice(tuple(kicks)))
    sn_opts = ((4, 12), (3, 11), (5, 13), (4, 14))
    sn = sn_opts[(salt // 2 + bw) % len(sn_opts)]
    bb = _behind_nudge(traits, sixteenth)
    lm = _loosen_mul(traits)
    sb = _snare_boost(traits)
    dense_hat = not traits or traits["hat_density_bias"] > 0.48
    for s in range(16):
        t0 = bar_off + s * sixteenth + bb + _anchor_sync_push(anchor_ctx, sixteenth, s)
        t1 = t0 + sixteenth * random.uniform(0.82 * lm, 0.92 * lm)
        if s % 2 == 0 and dense_hat:
            if not _anchor_hat_skip(anchor_ctx, bar, s):
                hv = 78 if s % 4 == 0 else 60
                hv = max(50, min(122, hv + random.randint(-10, 10)))
                _note(inst, pitch=_HIHAT_CLOSED, vel=hv, t0=t0, t1=t1)
        elif s % 2 == 0 and not dense_hat and s % 4 == 0:
            if not _anchor_hat_skip(anchor_ctx, bar, s):
                hv = 72
                _note(inst, pitch=_HIHAT_CLOSED, vel=max(50, min(118, hv + random.randint(-8, 8))), t0=t0, t1=t1)
        if s in kicks:
            _note(
                inst,
                pitch=_KICK,
                vel=max(92, min(126, 104 + random.randint(-10, 8))),
                t0=t0,
                t1=t0 + sixteenth * random.uniform(0.88 * lm, 0.97 * lm),
            )
        if s in sn:
            _note(
                inst,
                pitch=_SNARE,
                vel=max(94, min(126, 106 + random.randint(-10, 8) + sb)),
                t0=t0,
                t1=t0 + sixteenth * random.uniform(0.88 * lm, 0.97 * lm),
            )


def _emit_laid_back_soul(
    inst: pretty_midi.Instrument,
    bar_off: float,
    sixteenth: float,
    *,
    salt: int,
    bar: int,
    traits: DrumProfile | None,
    anchor_ctx: SessionAnchorContext | None = None,
) -> None:
    behind_base = sixteenth * (0.10 + ((salt + bar * 3) % 9) / 90.0)
    behind_mul = 1.0 + (0.35 * traits["behind_beat_bias"]) if traits else 1.0
    behind = behind_base * behind_mul
    hat_pat = ((0, 4, 8, 12), (0, 4, 8, 14), (0, 4, 9, 12))[(salt // 4 + _rep_bar(traits, bar) // 2) % 3]
    kicks = (0,)
    snares = (4, 12)
    lm = _loosen_mul(traits)
    sb = _snare_boost(traits)
    for s in range(16):
        t0 = bar_off + s * sixteenth
        push = behind * (1.05 if s in snares else 0.92 if s in kicks else 0.88)
        if s in hat_pat:
            if not _anchor_hat_skip(anchor_ctx, bar, s):
                t_hit = t0 + push + sixteenth * 0.04 + _anchor_sync_push(anchor_ctx, sixteenth, s)
                vh = 42 + ((salt + s * 2 + bar) % 12)
                vh = max(36, min(62, vh))
                t1 = t_hit + sixteenth * (0.42 + ((salt + bar + s) % 5) / 200.0) * lm
                _note(inst, pitch=_HIHAT_CLOSED, vel=vh, t0=t_hit, t1=t1)
        if s in kicks:
            tk = t0 + push * 1.08
            vk = 58 + ((salt + bar * 5) % 14)
            vk = max(52, min(76, vk))
            _note(
                inst,
                pitch=_KICK,
                vel=vk,
                t0=tk,
                t1=tk + sixteenth * (0.95 + ((salt >> 2) % 4) / 200.0) * lm,
            )
        if s in snares:
            ts = t0 + push * 1.12
            vs = 56 + ((salt // 3 + bar * 2 + s) % 14) + sb // 2
            vs = max(50, min(78, vs))
            _note(
                inst,
                pitch=_SNARE,
                vel=vs,
                t0=ts,
                t1=ts + sixteenth * (0.88 + ((salt >> 1) % 5) / 200.0) * lm,
            )


def _emit_latin(
    inst: pretty_midi.Instrument,
    bar_off: float,
    sixteenth: float,
    *,
    salt: int,
    bar: int,
    traits: DrumProfile | None,
    anchor_ctx: SessionAnchorContext | None = None,
) -> None:
    kick_opts = ((0, 9), (0, 10), (1, 9), (0, 8), (2, 10), (1, 8))
    snare_opts = ((4, 11), (5, 12), (4, 10), (3, 10), (5, 11), (6, 12))
    hat_opts = (
        (2, 5, 8, 11, 14),
        (1, 4, 7, 10, 13),
        (2, 6, 9, 12, 15),
        (0, 3, 6, 9, 12),
        (1, 5, 8, 11, 15),
    )
    bw = _rep_bar(traits, bar)
    kicks = kick_opts[(salt + bw) % len(kick_opts)]
    snares = snare_opts[(salt // 2 + bw) % len(snare_opts)]
    hats = hat_opts[(salt + bw * 2) % len(hat_opts)]
    open_slots = ((7, 15), (6, 14), (8, 15))
    opens = open_slots[(salt + bw) % len(open_slots)]
    fa = traits["fill_activity"] if traits else 0.55
    bb = _behind_nudge(traits, sixteenth)
    lm = _loosen_mul(traits)
    sb = _snare_boost(traits)
    for s in range(16):
        t0 = bar_off + s * sixteenth + bb + _anchor_sync_push(anchor_ctx, sixteenth, s)
        if s in hats:
            if not _anchor_hat_skip(anchor_ctx, bar, s):
                _note(
                    inst,
                    pitch=_HIHAT_CLOSED,
                    vel=max(48, min(118, (62 if s % 3 else 74) + random.randint(-8, 8))),
                    t0=t0,
                    t1=t0 + sixteenth * random.uniform(0.7 * lm, 0.82 * lm),
                )
        if s in opens:
            _note(
                inst,
                pitch=_HIHAT_OPEN,
                vel=max(48, min(110, 58 + random.randint(-8, 10))),
                t0=t0,
                t1=t0 + sixteenth * random.uniform(0.78 * lm, 0.9 * lm),
            )
        if s in kicks:
            _note(
                inst,
                pitch=_KICK,
                vel=max(82, min(118, 92 + random.randint(-10, 10))),
                t0=t0,
                t1=t0 + sixteenth * random.uniform(0.9 * lm, 0.99 * lm),
            )
        if s in snares:
            _note(
                inst,
                pitch=_SNARE,
                vel=max(84, min(120, 96 + random.randint(-10, 10) + sb)),
                t0=t0,
                t1=t0 + sixteenth * random.uniform(0.85 * lm, 0.95 * lm),
            )
        if s in (3, 10) and random.random() < fa + 0.15:
            _note(
                inst,
                pitch=_RIM,
                vel=max(58, min(110, 70 + random.randint(-8, 8))),
                t0=t0,
                t1=t0 + sixteenth * random.uniform(0.75 * lm, 0.86 * lm),
            )
        if s in (6, 13) and random.random() < fa:
            _note(
                inst,
                pitch=_LOW_TOM,
                vel=max(54, min(108, 66 + random.randint(-8, 8))),
                t0=t0,
                t1=t0 + sixteenth * random.uniform(0.8 * lm, 0.9 * lm),
            )


def generate_drums(
    *,
    tempo: int,
    bar_count: int,
    drum_style: str | None = None,
    drum_kit: str | None = None,
    session_preset: str | None = None,
    drum_player: str | None = None,
    context: SessionAnchorContext | None = None,
) -> tuple[bytes, str]:
    style = normalize_drum_style(drum_style)
    kit = normalize_drum_kit(drum_kit)
    player_key = normalize_drum_player(drum_player)
    traits: DrumProfile | None = drum_profiles[player_key] if player_key else None
    salt = random.randint(0, 255)
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    inst = pretty_midi.Instrument(program=0, is_drum=True, name="Drums")
    spb = 60.0 / float(tempo)
    sixteenth = spb / 4.0

    for bar in range(bar_count):
        bar_off = bar * 4 * spb
        if style == "straight":
            _emit_straight(inst, bar_off, sixteenth, salt=salt, bar=bar, traits=traits, anchor_ctx=context)
        elif style == "broken":
            _emit_broken(inst, bar_off, sixteenth, bar, salt=salt, traits=traits, anchor_ctx=context)
        elif style == "shuffle":
            _emit_shuffle(inst, bar_off, sixteenth, spb, salt=salt, bar=bar, traits=traits, anchor_ctx=context)
        elif style == "funk":
            _emit_funk(inst, bar_off, sixteenth, salt=salt, bar=bar, traits=traits, anchor_ctx=context)
        elif style == "laid_back_soul":
            _emit_laid_back_soul(inst, bar_off, sixteenth, salt=salt, bar=bar, traits=traits, anchor_ctx=context)
        else:
            _emit_latin(inst, bar_off, sixteenth, salt=salt, bar=bar, traits=traits, anchor_ctx=context)
        _emit_profile_ghost_snares(inst, bar_off, sixteenth, salt=salt, bar=bar, traits=traits, anchor_ctx=context)

    if kit == "percussion":
        fa = traits["fill_activity"] if traits else 0.5
        bongo_hi, bongo_lo, conga_hi, cowbell = 60, 61, 62, 56
        for bar in range(bar_count):
            bar_off = bar * 4 * spb
            for slot, pitch, vel in (
                (3, conga_hi, 62),
                (7, bongo_lo, 58),
                (11, cowbell, 54),
                (14, bongo_hi, 56),
            ):
                if random.random() > fa + 0.2:
                    continue
                t0 = bar_off + slot * sixteenth + _behind_nudge(traits, sixteenth)
                t1 = t0 + sixteenth * 0.75
                _note(inst, pitch=pitch, vel=vel, t0=t0, t1=t1)

    _apply_dynamic_shape(inst, traits)

    if kit == "dry":
        for n in inst.notes:
            n.velocity = max(1, min(127, int(n.velocity * 0.82)))

    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    return buf.getvalue(), _preview(style, bar_count, tempo, kit, session_preset=session_preset, drum_player=player_key)
