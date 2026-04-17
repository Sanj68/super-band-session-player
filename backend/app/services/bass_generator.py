"""Rule-based bass lane: groove cells, anchors, optional ``bass_player`` profiles."""

from __future__ import annotations

import io
import random
from typing import Final, TypedDict, cast

import pretty_midi

from app.services.anchor_lane_roles import (
    bass_knobs_for_role,
    bass_role_for_anchor,
    merge_bass_profile,
)
from app.services.session_context import (
    SessionAnchorContext,
    density_for_bar,
    drum_kick_emphasis_max,
    drum_kick_weight,
    drum_snare_weight,
    slot_pressure,
)
from app.utils import music_theory as mt

_BASS_STYLES: Final[frozenset[str]] = frozenset(
    {"supportive", "melodic", "rhythmic", "slap", "fusion"}
)
_BASS_INSTRUMENTS: Final[frozenset[str]] = frozenset({"finger_bass", "slap_bass", "synth_bass"})
_BASS_PLAYER_IDS: Final[frozenset[str]] = frozenset({"bootsy", "marcus", "pino"})


class BassProfile(TypedDict):
    """Behavior traits for a named bass personality (inspired-by, not a clone).

    ``base_style`` names the *voice* the profile leans toward for copy/preview only;
    the actual engine is always the session ``bass_style``.
    """

    base_style: str
    density_ceiling: int
    groove_repetition_strength: float
    octave_jump_bias: float
    root_anchor_strength: float
    syncopation_bias: float
    fill_activity: float
    rest_preference: float
    register_min: int
    register_max: int
    articulation_length_bias: float
    offbeat_bias: float
    ghost_note_bias: float
    contour_preference: float


bass_profiles: dict[str, BassProfile] = {
    "bootsy": {
        "base_style": "rhythmic",
        "density_ceiling": 4,
        "groove_repetition_strength": 0.92,
        "octave_jump_bias": 0.72,
        "root_anchor_strength": 0.9,
        "syncopation_bias": 0.58,
        "fill_activity": 0.22,
        "rest_preference": 0.34,
        "register_min": 34,
        "register_max": 58,
        "articulation_length_bias": 0.86,
        "offbeat_bias": 0.62,
        "ghost_note_bias": 0.12,
        "contour_preference": 0.42,
    },
    "marcus": {
        "base_style": "slap",
        "density_ceiling": 5,
        "groove_repetition_strength": 0.44,
        "octave_jump_bias": 0.58,
        "root_anchor_strength": 0.52,
        "syncopation_bias": 0.84,
        "fill_activity": 0.74,
        "rest_preference": 0.15,
        "register_min": 33,
        "register_max": 62,
        "articulation_length_bias": 0.64,
        "offbeat_bias": 0.78,
        "ghost_note_bias": 0.14,
        "contour_preference": 0.52,
    },
    "pino": {
        "base_style": "melodic",
        "density_ceiling": 4,
        "groove_repetition_strength": 0.86,
        "octave_jump_bias": 0.18,
        "root_anchor_strength": 0.96,
        "syncopation_bias": 0.26,
        "fill_activity": 0.07,
        "rest_preference": 0.55,
        "register_min": 36,
        "register_max": 52,
        "articulation_length_bias": 1.15,
        "offbeat_bias": 0.28,
        "ghost_note_bias": 0.06,
        "contour_preference": 0.82,
    },
}

# Sixteenth slots (always include 0 = beat 1 root anchor). Max ~5 hits/bar for pocket feel.
_RHYTHMIC_GROOVES: Final[tuple[tuple[int, ...], ...]] = (
    (0, 4, 8, 12),
    (0, 6, 10, 14),
    (0, 3, 8, 12),
    (0, 4, 10, 14),
    (0, 5, 8, 13),
    (0, 4, 7, 11),
)

_FUSION_GROOVES: Final[tuple[tuple[int, ...], ...]] = (
    (0, 3, 7, 10, 14),
    (0, 2, 6, 10, 12),
    (0, 4, 7, 11, 14),
    (0, 3, 8, 11, 15),
    (0, 2, 5, 9, 12),
)

_MELODIC_SHAPES: Final[tuple[tuple[int, ...], ...]] = (
    (0, 0, 1, 0, 2, 0, 1, 0),
    (0, 1, 1, 0, 2, 2, 0, 1),
    (0, 2, 0, 1, 0, 1, 2, 0),
    (1, 0, 2, 0, 1, 0, 0, 2),
)


def normalize_bass_style(bass_style: str | None) -> str:
    if bass_style is None:
        return "supportive"
    s = str(bass_style).strip().lower()
    return s if s in _BASS_STYLES else "supportive"


def normalize_bass_player(bass_player: str | None) -> str | None:
    if bass_player is None:
        return None
    s = str(bass_player).strip().lower()
    if not s or s in ("none", "off", "null"):
        return None
    return s if s in _BASS_PLAYER_IDS else None


def normalize_bass_instrument(bass_instrument: str | None) -> str:
    if bass_instrument is None:
        return "finger_bass"
    s = str(bass_instrument).strip().lower()
    return s if s in _BASS_INSTRUMENTS else "finger_bass"


def bass_midi_program(bass_instrument: str, bass_style: str) -> int:
    """GM programs: 33 finger, 36 slap, 38 synth. Instrument choice wins for slap_bass."""
    bi = normalize_bass_instrument(bass_instrument)
    if bi == "slap_bass":
        return 36
    if bi == "synth_bass":
        return 38
    if bass_style == "slap":
        return 36
    return 33


def _syncop_score(groove: tuple[int, ...]) -> float:
    return sum(1 for s in groove if s % 4 != 0) / max(len(groove), 1)


def _pick_pool_groove(
    pool: tuple[tuple[int, ...], ...],
    salt: int,
    bar: int,
    *,
    use_profile: bool,
    traits: BassProfile,
) -> tuple[int, ...]:
    if not use_profile:
        return pool[salt % len(pool)]
    ceiling = traits["density_ceiling"]
    candidates = [g for g in pool if len(g) <= ceiling] or list(pool)
    rev = traits["syncopation_bias"] >= 0.5
    candidates = sorted(candidates, key=_syncop_score, reverse=rev)
    rep = traits["groove_repetition_strength"]
    shift = 0 if rep >= 0.55 else bar
    return candidates[(salt + shift) % len(candidates)]


def _pick_fusion_pair(
    salt: int,
    bar: int,
    *,
    use_profile: bool,
    traits: BassProfile,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    if not use_profile:
        n = len(_FUSION_GROOVES)
        return _FUSION_GROOVES[salt % n], _FUSION_GROOVES[(salt + 3) % n]
    ceiling = traits["density_ceiling"]
    candidates = [g for g in _FUSION_GROOVES if len(g) <= ceiling] or list(_FUSION_GROOVES)
    rev = traits["syncopation_bias"] >= 0.5
    candidates = sorted(candidates, key=_syncop_score, reverse=rev)
    rep = traits["groove_repetition_strength"]
    sh = 0 if rep >= 0.55 else bar
    ia = (salt + sh) % len(candidates)
    ib = (salt + 3 + sh * 2) % len(candidates)
    return candidates[ia], candidates[ib]


def _thin_slots(
    slots: tuple[int, ...],
    *,
    use_profile: bool,
    traits: BassProfile,
    salt: int,
    bar: int,
) -> tuple[int, ...]:
    base = sorted(set(slots))
    if not use_profile:
        return tuple(base)
    ceiling = max(2, traits["density_ceiling"])
    protected = {0}
    out = list(base)
    while len(out) > ceiling:
        removable = [x for x in out if x not in protected]
        if not removable:
            break
        out.remove(max(removable))
    rest = traits["rest_preference"]
    thinned: list[int] = []
    for s in out:
        if s == 0:
            thinned.append(s)
            continue
        d = ((salt * 31 + bar * 7 + s * 5) % 1000) / 1000.0
        if s == 8 and d < rest * 0.22:
            continue
        if s not in (0, 8) and d < rest * 0.42:
            continue
        thinned.append(s)
    if 0 not in thinned:
        thinned.insert(0, 0)
    return tuple(sorted(set(thinned)))


def _clamp_pitch(pitch: int, lo: int, hi: int, *, use_profile: bool, traits: BassProfile) -> int:
    if not use_profile:
        return pitch
    lo_r, hi_r = traits["register_min"], traits["register_max"]
    return max(lo_r, min(hi_r, pitch))


def _preview(
    style: str,
    key: str,
    scale: str,
    bar_count: int,
    tempo: int,
    bass_instrument: str,
    bass_player: str | None,
    *,
    rare_groove_soul: bool = False,
    anchor_role: str | None = None,
) -> str:
    k = mt.normalize_key(key)
    sc = mt.describe_scale(scale)
    blurbs = {
        "supportive": "simple pocket on roots (beats 1 & 3), fewer attacks, stable harmony.",
        "melodic": "8th-line with chord tones (root / 3rd / 5th) for smoother shape and voice-leading.",
        "rhythmic": "tight syncopated 16ths on the root with repeating cells in each bar.",
        "slap": "accented downbeats, short staccato hits, octave pops and ghosted 16ths for a slap-like feel.",
        "fusion": "busy 16ths across chord roots, fifths, and octaves with wider motion while staying in the changes.",
    }
    inst_lbl = {
        "finger_bass": "finger bass",
        "slap_bass": "slap bass",
        "synth_bass": "synth bass",
    }.get(bass_instrument, bass_instrument)
    player_bits = {
        "bootsy": "Player «bootsy» (funk-pocket voice on your style): spacious, bouncy, root-heavy, swagger.",
        "marcus": "Player «marcus» (slap-forward voice on your style): sharp, syncopated, fill-ready, upper accents.",
        "pino": "Player «pino» (soul-line voice on your style): smooth, selective, elegant contour, high space.",
    }
    tail = blurbs.get(style, blurbs["supportive"])
    if rare_groove_soul and style == "supportive":
        tail = (
            "Rare groove soul — space, warmth, and a repeating pocket: roots on 1 & 3, "
            "micro-late feel, minimal ghosts, only rare 3rd/5th color. "
        ) + tail
    if bass_player and bass_player in player_bits:
        tail = f"{player_bits[bass_player]} {tail}"
    who = f", {bass_player}" if bass_player else ""
    role_tag = f" Role vs anchor: {anchor_role}." if anchor_role else ""
    return (
        f"Bass [{style}, {inst_lbl}{who}]: {k} {sc}, {bar_count} bar(s), {tempo} BPM — {tail}{role_tag}"
    )


def _deg_pitch_map(r: int, t: int, f: int, idx: int) -> int:
    return (r, t, f)[idx % 3]


def _drum_anchor_ctx(ctx: SessionAnchorContext | None) -> bool:
    return ctx is not None and ctx.anchor_lane == "drums"


def _drum_profile_groove(player_key: str | None) -> tuple[float, float, float]:
    """(kick_lock, bounce, restraint) multipliers for drum-anchor pocket shaping."""
    if player_key == "bootsy":
        return 1.12, 1.18, 0.92
    if player_key == "marcus":
        return 1.06, 1.08, 0.95
    if player_key == "pino":
        return 1.08, 0.92, 1.22
    return 1.0, 1.0, 1.0


def _rhythmic_drum_slot_keep(
    ctx: SessionAnchorContext,
    bar: int,
    slot: int,
    *,
    d_drum: float,
    kick_lock: float,
    restraint: float,
) -> bool:
    """Groove-lock gate for rhythmic 16ths under drum anchor (bounded probabilities)."""
    kw = drum_kick_weight(ctx, bar, slot)
    nk = drum_kick_emphasis_max(ctx, bar, slot, 2)
    pr = slot_pressure(ctx, bar, slot)
    if kw > 0.36 or (slot % 4 == 0 and nk > 0.44):
        return random.random() < min(0.98, 0.86 + 0.1 * kw * kick_lock)
    if pr > 0.5 and kw < 0.16:
        skip_p = 0.16 + 0.36 * pr - 0.34 * nk
        if d_drum > 12.0:
            skip_p += 0.1
        skip_p = max(0.06, min(0.72, skip_p * restraint))
        return random.random() > skip_p
    if pr > 0.62 and kw < 0.28:
        return random.random() > min(0.55, 0.22 + 0.28 * pr)
    return True


def generate_bass(
    *,
    tempo: int,
    bar_count: int,
    key: str,
    scale: str,
    bass_style: str | None = None,
    bass_instrument: str | None = None,
    bass_player: str | None = None,
    session_preset: str | None = None,
    context: SessionAnchorContext | None = None,
) -> tuple[bytes, str]:
    soul_preset = (session_preset or "").strip().lower() == "rare_groove_soul"
    player_key = normalize_bass_player(bass_player)
    use_profile = player_key is not None
    traits: BassProfile | None = bass_profiles[player_key] if use_profile and player_key else None

    style = normalize_bass_style(bass_style)

    bi = normalize_bass_instrument(bass_instrument)
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    program = bass_midi_program(bi, style)
    inst = pretty_midi.Instrument(program=program, name="Bass")
    spb = 60.0 / float(tempo)
    eighth = spb / 2.0
    sixteenth = spb / 4.0
    degrees = mt.progression_degrees_for_bars(bar_count, scale)
    salt = random.randint(0, 127)

    melodic_shape = _MELODIC_SHAPES[salt % len(_MELODIC_SHAPES)]
    slap_template_idx = salt % 5

    bass_role_name = bass_role_for_anchor(context.anchor_lane) if context else None
    bass_knobs = bass_knobs_for_role(bass_role_name) if context and bass_role_name else None
    if context and bass_knobs:
        traits_engine: BassProfile = cast(
            BassProfile,
            merge_bass_profile(dict(traits or bass_profiles["bootsy"]), bass_knobs),
        )
    elif use_profile and traits:
        traits_engine = traits
    else:
        traits_engine = bass_profiles["bootsy"]

    if use_profile and traits:
        bias_traits: BassProfile | None = traits
    elif context:
        bias_traits = traits_engine
    else:
        bias_traits = None

    art = traits_engine["articulation_length_bias"] if context else (traits["articulation_length_bias"] if use_profile and traits else 1.0)
    kick_lock_m, bounce_m, restraint_m = _drum_profile_groove(player_key)
    if bass_knobs:
        kick_lock_m *= bass_knobs.kick_lock_mult
        bounce_m *= bass_knobs.bounce_mult
        restraint_m *= bass_knobs.restraint_mult

    def dur_j(base: float) -> float:
        return base * random.uniform(0.88, 1.06) * art

    def oct_jump_p() -> float:
        if not bias_traits:
            return 0.12
        return min(0.42, 0.08 + bias_traits["octave_jump_bias"] * 0.38)

    def offbeat_root_p() -> float:
        if not bias_traits:
            return 0.55
        ra = bias_traits["root_anchor_strength"]
        ob = bias_traits["offbeat_bias"]
        return min(0.97, 0.18 + 0.62 * ra + 0.12 * ob)

    def melodic_root_bias() -> float:
        if not bias_traits:
            return 0.85
        return min(0.98, 0.7 + 0.28 * bias_traits["root_anchor_strength"])

    for bar, deg in enumerate(degrees):
        root = mt.bass_root_midi(key, scale, deg, octave=2)
        ojp = oct_jump_p()
        if style in ("rhythmic", "fusion") and random.random() < ojp:
            root = min(52, root + 12)
        elif style == "melodic" and random.random() < min(0.22, ojp * 0.55):
            root = min(52, root + 12)

        chord2 = mt.chord_tones_midi(key, scale, deg, octave=2, seventh=False)
        r, t, f = chord2[0], chord2[1], chord2[2]
        bar_t0 = bar * 4 * spb
        bar_t1 = (bar + 1) * 4 * spb

        if style == "supportive":
            if soul_preset:
                leg0 = (0.88 + (salt % 9) / 100.0) * art
                leg1 = (0.84 + ((salt >> 3) % 9) / 100.0) * art
                ghost_p = 0.03
            else:
                leg0, leg1 = random.uniform(0.86, 0.96), random.uniform(0.82, 0.93)
                leg0 *= art
                leg1 *= art
                if use_profile and traits:
                    ghost_p = min(0.2, 0.035 + traits["ghost_note_bias"] * 0.42)
                elif bias_traits:
                    ghost_p = min(0.2, 0.035 + bias_traits["ghost_note_bias"] * 0.42)
                else:
                    ghost_p = 0.14
            for beat, leg in ((0.0, leg0), (2.0, leg1)):
                leg_use = leg
                slot_b = int(beat * 4)  # beat 1→0, beat 3→8 in sixteenth grid
                late = spb * (0.011 + (salt % 3) * 0.0025) if soul_preset else 0.0
                late += spb * 0.006 if soul_preset and beat == 2.0 else 0.0
                if context:
                    dbar = density_for_bar(context, bar)
                    late += spb * 0.0075 * min(1.0, dbar / 8.0)
                if _drum_anchor_ctx(context):
                    kw = drum_kick_weight(context, bar, slot_b)
                    late += sixteenth * (0.14 * kw - 0.05 * drum_kick_weight(context, bar, (slot_b + 3) % 16)) * kick_lock_m
                    late += sixteenth * 0.06 * bounce_m * (0.55 if player_key == "bootsy" else 0.35)
                    dd = density_for_bar(context, bar)
                    if dd > 11.0:
                        leg_use *= 1.0 + 0.035 * restraint_m * (1.15 if player_key == "pino" else 1.0)
                    if dd > 14.0:
                        leg_use = min(0.97, leg_use * 1.025)
                t_j = random.uniform(0, 0.018) * spb if not soul_preset else random.uniform(0, 0.006) * spb
                t0 = bar_t0 + beat * spb + late + t_j
                t1 = t0 + spb * leg_use
                p = root
                if soul_preset and beat == 2.0:
                    m = (salt + bar * 3) % 7
                    kw8 = drum_kick_weight(context, bar, 8) if _drum_anchor_ctx(context) else 0.0
                    if m == 1 and not (_drum_anchor_ctx(context) and kw8 > 0.42):
                        p = t
                    elif m == 5 and not (_drum_anchor_ctx(context) and kw8 > 0.5):
                        p = f
                    elif _drum_anchor_ctx(context) and kw8 > 0.38 and random.random() < 0.55 * kick_lock_m:
                        p = f if random.random() < 0.45 else root
                vb = (88, 80) if soul_preset else (92, 84)
                vel = max(72, min(100, (vb[0] if beat == 0 else vb[1]) + random.randint(-5, 5)))
                p = _clamp_pitch(p, 0, 127, use_profile=use_profile, traits=traits or bass_profiles["pino"])
                inst.notes.append(pretty_midi.Note(velocity=vel, pitch=p, start=t0, end=t1))
            ghost_use = ghost_p
            if _drum_anchor_ctx(context):
                dd = density_for_bar(context, bar)
                ghost_use *= 1.0 - 0.5 * min(1.0, dd / 16.0)
                sw_back = (drum_snare_weight(context, bar, 4) + drum_snare_weight(context, bar, 12)) * 0.5
                ghost_use += (0.055 if player_key == "bootsy" else 0.035 if player_key == "marcus" else 0.02) * min(
                    1.0, sw_back
                )
                ghost_use = max(0.02, min(0.22, ghost_use * restraint_m))
            if random.random() < ghost_use:
                ghost_b = random.choice((1.0, 3.0))
                gt0 = bar_t0 + ghost_b * spb
                gt1 = gt0 + spb * random.uniform(0.12, 0.22)
                if gt1 <= bar_t1:
                    gp = random.choice((root, f))
                    gp = _clamp_pitch(gp, 0, 127, use_profile=use_profile, traits=traits or bass_profiles["pino"])
                    inst.notes.append(
                        pretty_midi.Note(
                            velocity=random.randint(44, 62),
                            pitch=gp,
                            start=gt0,
                            end=gt1,
                        )
                    )
            if _drum_anchor_ctx(context):
                sw = (drum_snare_weight(context, bar, 4) + drum_snare_weight(context, bar, 12)) * 0.5
                ans_p = min(0.42, (0.1 + 0.28 * sw) * (0.55 if player_key == "pino" else 1.0) / max(0.85, restraint_m))
                if sw > 0.32 and random.random() < ans_p:
                    ah = 6 if drum_snare_weight(context, bar, 4) >= drum_snare_weight(context, bar, 12) else 14
                    if drum_kick_weight(context, bar, ah) < 0.38:
                        t0a = bar_t0 + ah * sixteenth + random.uniform(0, 0.008) * spb
                        t1a = t0a + sixteenth * random.uniform(0.55, 0.95)
                        if t1a <= bar_t1:
                            ap = f if random.random() < 0.55 else root
                            ap = _clamp_pitch(ap, 0, 127, use_profile=use_profile, traits=traits or bass_profiles["pino"])
                            inst.notes.append(
                                pretty_midi.Note(
                                    velocity=random.randint(38, 58),
                                    pitch=ap,
                                    start=t0a,
                                    end=t1a,
                                )
                            )

        elif style == "melodic":
            if use_profile and traits:
                bd = 1 + int((1.0 - min(0.98, traits["contour_preference"])) * 4)
                rot = (salt + bar // bd) % 8
            elif bias_traits:
                bd = 1 + int((1.0 - min(0.98, bias_traits["contour_preference"])) * 4)
                rot = (salt + bar // bd) % 8
            else:
                rot = (salt + bar // 2) % 8
            shape = melodic_shape[rot:] + melodic_shape[:rot]
            rb = melodic_root_bias()
            rest = bias_traits["rest_preference"] if bias_traits else 0.0
            for i in range(8):
                if context:
                    slot = min(15, i * 2)
                    if _drum_anchor_ctx(context):
                        kw = drum_kick_weight(context, bar, slot)
                        pr = slot_pressure(context, bar, slot)
                        nk = drum_kick_emphasis_max(context, bar, slot, 2)
                        if i in (0, 4):
                            if kw < 0.18 and pr > 0.72 and random.random() < 0.22 / kick_lock_m:
                                continue
                        else:
                            skip_p = 0.18 + 0.32 * pr - 0.36 * max(kw, nk) - 0.04 * bounce_m
                            skip_p = max(0.0, min(0.62, skip_p * restraint_m))
                            if random.random() < skip_p:
                                continue
                    elif (
                        i not in (0, 4)
                        and slot_pressure(context, bar, slot) > 0.64
                        and random.random() < 0.42
                    ):
                        continue
                if use_profile and rest > 0.32:
                    d = (salt + bar * 2 + i * 3) % 8
                    if i not in (0, 4) and d < int(rest * 5):
                        continue
                idx = shape[i]
                pitch = _deg_pitch_map(r, t, f, idx)
                rb_use = rb
                if _drum_anchor_ctx(context) and i in (0, 4):
                    kw_m = drum_kick_weight(context, bar, min(15, i * 2))
                    rb_use = min(0.98, rb + 0.12 * kw_m * kick_lock_m)
                if i in (0, 4) and random.random() < rb_use:
                    pitch = r
                elif random.random() < 0.06 * (0.4 if use_profile and traits else 1.0):
                    pitch = min(pitch + 12, 74)
                pitch = _clamp_pitch(pitch, 0, 127, use_profile=use_profile, traits=traits or bass_profiles["pino"])
                obm = bias_traits["offbeat_bias"] if bias_traits else 0.0
                ph = eighth * 0.035 * obm * (0.45 if i in (0, 4) else 1.0)
                if context:
                    ph += sixteenth * 0.05 * min(1.0, context.syncopation_score) * (0.4 if i in (0, 4) else 1.0)
                t0 = bar_t0 + i * eighth + ph + random.uniform(0, 0.012) * spb
                t1 = t0 + eighth * dur_j(0.9)
                vel = max(70, min(100, (88 if i in (0, 4) else 76) + random.randint(-8, 8)))
                inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))

        elif style == "rhythmic":
            raw = _pick_pool_groove(
                _RHYTHMIC_GROOVES,
                salt,
                bar,
                use_profile=use_profile,
                traits=traits_engine,
            )
            pat = _thin_slots(
                raw,
                use_profile=use_profile,
                traits=traits_engine,
                salt=salt,
                bar=bar,
            )
            p_root_off = offbeat_root_p()
            extras: list[int] = []
            if (
                (traits_engine["fill_activity"] > 0.52 if context else (use_profile and traits and traits["fill_activity"] > 0.52))
                and (salt + bar) % 2 == 1
                and 14 not in pat
                and len(pat) < traits_engine["density_ceiling"]
            ):
                if (not _drum_anchor_ctx(context)) or drum_kick_emphasis_max(context, bar, 12, 3) > 0.32:
                    extras.append(14)
            d_drum = density_for_bar(context, bar) if context else 0.0
            for s in tuple(sorted(set(pat + tuple(extras)))):
                if _drum_anchor_ctx(context):
                    if not _rhythmic_drum_slot_keep(
                        context,
                        bar,
                        s,
                        d_drum=d_drum,
                        kick_lock=kick_lock_m,
                        restraint=restraint_m,
                    ):
                        continue
                elif context and s % 4 != 0:
                    pr = slot_pressure(context, bar, s)
                    if pr > 0.6 and random.random() < 0.28 + pr * 0.32:
                        continue
                obr = bias_traits["offbeat_bias"] if bias_traits else 0.0
                push = sixteenth * 0.02 * obr * (0.4 if s % 4 == 0 else 1.0)
                if context:
                    push += sixteenth * 0.045 * min(1.0, context.syncopation_score) * (0.45 if s % 4 == 0 else 1.0)
                if _drum_anchor_ctx(context):
                    push += sixteenth * 0.024 * bounce_m * drum_kick_weight(context, bar, s)
                    if player_key == "marcus":
                        push += sixteenth * 0.012 * drum_snare_weight(context, bar, (s + 15) % 16)
                t0 = bar_t0 + s * sixteenth + push + random.uniform(0, 0.012) * spb
                t1 = t0 + sixteenth * dur_j(0.74)
                if s % 4 == 0:
                    pitch = r
                    vel = max(88, min(112, 96 + random.randint(-8, 8)))
                else:
                    pitch = _deg_pitch_map(r, t, f, (s + salt) % 3)
                    if random.random() < p_root_off:
                        pitch = r
                    vel = max(74, min(104, 84 + random.randint(-8, 8)))
                oj_mul = 0.38 * (
                    1.14
                    if _drum_anchor_ctx(context) and player_key == "bootsy" and bias_traits and bias_traits["octave_jump_bias"] > 0.55
                    else 1.0
                )
                if (
                    use_profile
                    and traits
                    and traits["octave_jump_bias"] > 0.55
                    and s in (8, 12)
                    and random.random() < traits["octave_jump_bias"] * oj_mul
                ):
                    if r + 12 <= traits["register_max"]:
                        pitch = r + 12
                pitch = _clamp_pitch(pitch, 0, 127, use_profile=use_profile, traits=traits or bass_profiles["bootsy"])
                inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))

        elif style == "slap":
            oct_pop = min(r + 12, 72)
            fifth_low = f if f < 56 else f - 12
            event_templates = (
                ((0, 2, r, 108), (2, 1, r, 52), (6, 2, oct_pop, 100), (10, 1, r, 50), (12, 2, fifth_low, 88)),
                ((0, 2, r, 106), (3, 1, r, 48), (7, 2, oct_pop, 98), (11, 1, r, 52), (13, 2, fifth_low, 90)),
                ((1, 2, r, 110), (4, 1, r, 54), (8, 2, oct_pop, 102), (12, 1, r, 48), (14, 1, fifth_low, 86)),
                ((0, 2, t, 102), (4, 1, r, 56), (8, 2, oct_pop, 104), (11, 1, f, 52), (14, 2, r, 84)),
                ((1, 2, f, 96), (5, 1, r, 58), (9, 2, oct_pop, 100), (13, 1, t, 54)),
            )
            tpl = (slap_template_idx + bar // 2) % len(event_templates)
            events = event_templates[tpl]
            for s0, dur_s, pitch, vel in events:
                t0 = bar_t0 + s0 * sixteenth + random.uniform(0, 0.014) * spb
                if _drum_anchor_ctx(context):
                    t0 += sixteenth * 0.05 * drum_kick_weight(context, bar, min(15, s0)) * kick_lock_m
                t1 = t0 + dur_s * sixteenth * dur_j(0.88)
                if t1 > bar_t1:
                    t1 = bar_t1 - 1e-4
                if t1 <= t0:
                    continue
                pitch = _clamp_pitch(int(pitch), 0, 127, use_profile=use_profile, traits=traits or bass_profiles["pino"])
                v = max(44, min(118, vel + random.randint(-8, 8)))
                inst.notes.append(pretty_midi.Note(velocity=v, pitch=pitch, start=t0, end=t1))

        else:  # fusion
            fusion_slots_a, fusion_slots_b = _pick_fusion_pair(
                salt, bar, use_profile=use_profile, traits=traits_engine
            )
            pat_slots = fusion_slots_a if bar % 2 == 0 else fusion_slots_b
            pat_slots = _thin_slots(
                tuple(sorted(set(pat_slots))),
                use_profile=use_profile,
                traits=traits_engine,
                salt=salt,
                bar=bar,
            )
            if context:
                if _drum_anchor_ctx(context):
                    dd_f = density_for_bar(context, bar)

                    def _keep_fusion_slot(x: int) -> bool:
                        kw = drum_kick_weight(context, bar, x)
                        pr = slot_pressure(context, bar, x)
                        nk = drum_kick_emphasis_max(context, bar, x, 2)
                        if x in (0, 8):
                            return True
                        if kw > 0.22 or nk > 0.4:
                            return random.random() < min(0.96, 0.82 + 0.12 * kw * kick_lock_m)
                        if pr > 0.58 and nk < 0.2:
                            cut = 0.26 + 0.22 * pr + (0.12 if dd_f > 12 else 0.0)
                            cut = min(0.68, cut * restraint_m)
                            return random.random() > cut
                        return random.random() < 0.88

                    pat_slots = tuple(x for x in pat_slots if _keep_fusion_slot(x))
                    if not pat_slots:
                        pat_slots = (0, 8)
                else:
                    pat_slots = tuple(
                        x
                        for x in pat_slots
                        if slot_pressure(context, bar, x) < 0.66 or x in (0, 8) or random.random() > 0.38
                    )
                    if not pat_slots:
                        pat_slots = (0, 8)
            pit_cycle = (r, f, r, t, f, r)
            phase = (salt // 2 + bar // 2) % len(pit_cycle)
            fill_ex: list[int] = []
            if (
                (traits_engine["fill_activity"] > 0.58 if context else (use_profile and traits and traits["fill_activity"] > 0.58))
                and bar % 2 == 1
                and 11 not in pat_slots
                and len(pat_slots) < traits_engine["density_ceiling"]
            ):
                if (not _drum_anchor_ctx(context)) or drum_kick_emphasis_max(context, bar, 10, 3) > 0.28:
                    fill_ex.append(11)
            for k_i, s in enumerate(tuple(sorted(set(pat_slots + tuple(fill_ex))))):
                pitch = pit_cycle[(phase + k_i) % len(pit_cycle)]
                if pitch > 60:
                    pitch -= 12
                if pitch < 34:
                    pitch += 12
                if s in (0, 8):
                    pitch = r
                elif random.random() < 0.07 * (1.4 if use_profile and traits and traits["fill_activity"] > 0.55 else 1.0):
                    pitch = min(62, max(34, pitch + random.choice((0, 12, -12))))
                obf = bias_traits["offbeat_bias"] if bias_traits else 0.0
                push_f = sixteenth * 0.018 * obf * (0.45 if s % 4 == 0 else 1.0)
                if _drum_anchor_ctx(context):
                    push_f += sixteenth * 0.02 * bounce_m * drum_kick_weight(context, bar, s)
                    if player_key == "marcus":
                        push_f += sixteenth * 0.01 * drum_snare_weight(context, bar, (s + 14) % 16)
                t0 = bar_t0 + s * sixteenth + push_f + random.uniform(0, 0.014) * spb
                t1 = t0 + sixteenth * dur_j(0.72)
                if t1 > bar_t1:
                    t1 = bar_t1 - 1e-4
                if t1 <= t0:
                    continue
                vel = max(76, min(104, (92 if s % 4 == 0 else 80) + random.randint(-8, 8)))
                pitch = _clamp_pitch(pitch, 0, 127, use_profile=use_profile, traits=traits or bass_profiles["marcus"])
                inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))

    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    return buf.getvalue(), _preview(
        style,
        key,
        scale,
        bar_count,
        tempo,
        bi,
        player_key,
        rare_groove_soul=soul_preset,
        anchor_role=bass_role_name,
    )