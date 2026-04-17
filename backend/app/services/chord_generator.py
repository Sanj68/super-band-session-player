"""Rule-based chord lane: ``chord_style`` engine with optional ``chord_player`` bias layer."""

from __future__ import annotations

import io
import random
from typing import Final, TypedDict, cast

import pretty_midi

from app.services.anchor_lane_roles import (
    chord_knobs_for_role,
    chord_role_for_anchor,
    merge_chord_profile,
)
from app.services.session_context import SessionAnchorContext, density_for_bar, slot_pressure
from app.utils import music_theory as mt

_CHORD_STYLES: Final[frozenset[str]] = frozenset(
    {"simple", "jazzy", "wide", "dense", "stabs", "warm_broken"}
)
_CHORD_INSTRUMENTS: Final[frozenset[str]] = frozenset({"piano", "rhodes", "organ", "pad"})
_CHORD_PLAYER_IDS: Final[frozenset[str]] = frozenset({"herbie", "barry_miles", "soul_keys", "funk_stabs"})


class ChordProfile(TypedDict):
    """Bias traits for a named chord personality (inspired-by, not a clone).

    ``base_style`` is a voice hint for preview copy only; ``chord_style`` always selects the engine.
    """

    base_style: str
    voicing_density: float
    color_tone_bias: float
    inversion_activity: float
    rhythmic_comping_bias: float
    sustain_bias: float
    stagger_bias: float
    register_spread_bias: float
    repetition_strength: float
    harmonic_movement_bias: float


chord_profiles: dict[str, ChordProfile] = {
    "herbie": {
        "base_style": "jazzy",
        "voicing_density": 0.74,
        "color_tone_bias": 0.82,
        "inversion_activity": 0.78,
        "rhythmic_comping_bias": 0.72,
        "sustain_bias": 0.5,
        "stagger_bias": 0.64,
        "register_spread_bias": 0.58,
        "repetition_strength": 0.4,
        "harmonic_movement_bias": 0.78,
    },
    "barry_miles": {
        "base_style": "wide",
        "voicing_density": 0.5,
        "color_tone_bias": 0.36,
        "inversion_activity": 0.42,
        "rhythmic_comping_bias": 0.26,
        "sustain_bias": 0.86,
        "stagger_bias": 0.3,
        "register_spread_bias": 0.8,
        "repetition_strength": 0.7,
        "harmonic_movement_bias": 0.32,
    },
    "soul_keys": {
        "base_style": "warm_broken",
        "voicing_density": 0.56,
        "color_tone_bias": 0.76,
        "inversion_activity": 0.34,
        "rhythmic_comping_bias": 0.38,
        "sustain_bias": 0.8,
        "stagger_bias": 0.48,
        "register_spread_bias": 0.4,
        "repetition_strength": 0.56,
        "harmonic_movement_bias": 0.44,
    },
    "funk_stabs": {
        "base_style": "stabs",
        "voicing_density": 0.42,
        "color_tone_bias": 0.46,
        "inversion_activity": 0.54,
        "rhythmic_comping_bias": 0.9,
        "sustain_bias": 0.2,
        "stagger_bias": 0.26,
        "register_spread_bias": 0.36,
        "repetition_strength": 0.64,
        "harmonic_movement_bias": 0.6,
    },
}


def normalize_chord_style(chord_style: str | None) -> str:
    if chord_style is None:
        return "simple"
    s = str(chord_style).strip().lower()
    return s if s in _CHORD_STYLES else "simple"


def normalize_chord_instrument(chord_instrument: str | None) -> str:
    if chord_instrument is None:
        return "piano"
    s = str(chord_instrument).strip().lower()
    return s if s in _CHORD_INSTRUMENTS else "piano"


def normalize_chord_player(chord_player: str | None) -> str | None:
    if chord_player is None:
        return None
    s = str(chord_player).strip().lower()
    if not s or s in ("none", "off", "null"):
        return None
    return s if s in _CHORD_PLAYER_IDS else None


def chord_midi_program(chord_instrument: str) -> int:
    return {"piano": 0, "rhodes": 4, "organ": 16, "pad": 89}.get(chord_instrument, 0)


def chord_instrument_label(chord_instrument: str) -> str:
    return {
        "piano": "piano",
        "rhodes": "Rhodes EP",
        "organ": "organ",
        "pad": "pad",
    }.get(chord_instrument, chord_instrument)


def _phrase_bar(traits: ChordProfile | None, bar: int) -> int:
    if traits and traits["repetition_strength"] > 0.6:
        return bar // 2
    return bar


def _phrase_seed(traits: ChordProfile | None, bar: int, salt: int, deg: int) -> int:
    b = _phrase_bar(traits, bar)
    return salt + b * 3 + deg * 5


def _sustain_leg_range(traits: ChordProfile | None) -> tuple[float, float]:
    if not traits:
        return (0.86, 0.995)
    sb = traits["sustain_bias"]
    return (0.7 + 0.22 * sb, 0.86 + 0.13 * sb)


def _jazzy_t1_range(traits: ChordProfile | None) -> tuple[float, float]:
    if not traits:
        return (0.88, 0.995)
    sb = traits["sustain_bias"]
    return (0.76 + 0.16 * sb, 0.9 + 0.09 * sb)


def _stagger_pair(traits: ChordProfile | None) -> tuple[float, float]:
    if not traits:
        return (0.12, 0.55)
    sg = traits["stagger_bias"]
    return (0.07 + 0.08 * (1.0 - sg), 0.32 + 0.48 * sg)


def _broken_prob(traits: ChordProfile | None) -> float:
    if not traits:
        return 0.4
    return min(0.92, 0.18 + 0.68 * traits["rhythmic_comping_bias"])


def _inv_extra(traits: ChordProfile | None) -> int:
    if not traits:
        return 0
    return int(3.5 * traits["inversion_activity"])


def _hm_shift(traits: ChordProfile | None) -> int:
    if not traits:
        return 0
    return int(4 * traits["harmonic_movement_bias"])


def _base_oct(traits: ChordProfile | None) -> int:
    if not traits:
        return random.choice((3, 4, 4, 4, 5))
    rs = traits["register_spread_bias"]
    w = (1.0 + rs * 1.4, 3.2, 3.2, 3.2, 1.0 + rs * 1.4)
    return random.choices((3, 4, 4, 4, 5), weights=w, k=1)[0]


def _seventh_simple(traits: ChordProfile | None, bar: int, salt: int, deg: int) -> bool:
    base = bar % 2 == 1
    if not traits:
        return base
    cb = traits["color_tone_bias"]
    ph = _phrase_seed(traits, bar, salt, deg)
    extra = (ph % 11) / 11.0 < cb * 0.5
    return base or extra


def _maybe_add_ninth(tones: list[int], traits: ChordProfile | None) -> None:
    if not traits or not tones:
        return
    cb = traits["color_tone_bias"]
    if cb < 0.5:
        return
    r = tones[0]
    nine = r + 14
    if nine > tones[-1] and nine <= 100 and random.random() < 0.22 + 0.38 * cb:
        tones.append(nine)


def _maybe_color_extension(tones: list[int], traits: ChordProfile | None) -> None:
    if not traits or len(tones) < 2:
        return
    cb = traits["color_tone_bias"]
    r = tones[0]
    if cb > 0.68 and random.random() < 0.18 + 0.22 * cb:
        el = r + 17
        if el > tones[-1] and el <= 101:
            tones.append(el)
    if cb > 0.75 and random.random() < 0.14:
        sh = r + 18
        if sh not in tones and sh <= 103:
            tones.append(sh)


def _dense_cap(traits: ChordProfile | None, n: int) -> int:
    if not traits:
        return n
    cap = 3 + int(2.2 + traits["voicing_density"] * 3.8)
    return min(n, max(3, cap))


def _stab_len_scale(traits: ChordProfile | None) -> float:
    if not traits:
        return 1.0
    sb = traits["sustain_bias"]
    return 0.48 + 0.62 * sb


def _groove_chord_anchor(ctx: SessionAnchorContext | None) -> bool:
    return ctx is not None and ctx.anchor_lane in ("drums", "bass")


def _mean_gap_bar(ctx: SessionAnchorContext, bar: int) -> float:
    b = bar % ctx.bar_count
    g = ctx.mean_gap_sec_per_bar
    if b < len(g) and g[b] > 1e-6:
        return float(g[b])
    return float(ctx.sixteenth_len_sec) * 2.5


def _nudge_time_away_from_crowd(
    ctx: SessionAnchorContext,
    bar: int,
    t_offset_sec: float,
    *,
    sixteenth: float,
    spb: float,
    bar_len: float,
) -> float:
    """Slide an onset slightly toward a lower slot_pressure neighborhood (bounded)."""
    slot = int(t_offset_sec / max(1e-9, sixteenth))
    slot = max(0, min(15, slot))
    p_here = slot_pressure(ctx, bar, slot)
    if p_here < 0.42:
        return t_offset_sec
    best_off, best_p = t_offset_sec, p_here
    for d in (-2, -1, 1, 2, 3):
        ns = max(0, min(15, slot + d))
        ph = slot_pressure(ctx, bar, ns)
        if ph < best_p - 0.05:
            best_p = ph
            best_off = ns * sixteenth + random.uniform(0, 0.01) * spb
    return min(max(0.0, best_off), bar_len - sixteenth * 0.5)


def _broken_prob_groove(
    ctx: SessionAnchorContext | None,
    traits: ChordProfile | None,
    style: str,
    player_key: str | None,
) -> float:
    bp = _broken_prob(traits)
    if not _groove_chord_anchor(ctx):
        return bp
    boost = 0.2 if style == "simple" else 0.14 if style in ("wide", "dense") else 0.1 if style == "jazzy" else 0.06
    if style == "warm_broken":
        boost = 0.08
    if player_key == "herbie":
        boost += 0.12
    elif player_key == "soul_keys":
        boost *= 0.88
    elif player_key == "funk_stabs":
        boost *= 0.65
    elif player_key == "barry_miles":
        boost *= 0.55
    return min(0.93, bp + boost)


def _sustain_leg_range_groove(
    ctx: SessionAnchorContext | None,
    traits: ChordProfile | None,
    bar: int,
    style: str,
    player_key: str | None,
) -> tuple[float, float]:
    lo, hi = _sustain_leg_range(traits)
    if not _groove_chord_anchor(ctx):
        return lo, hi
    dd = density_for_bar(ctx, bar)
    mg = _mean_gap_bar(ctx, bar)
    shrink = 0.07 + 0.11 * min(1.0, dd / 12.0)
    shrink -= 0.05 * min(1.0, mg / (ctx.bar_len_sec * 0.5 + 1e-6))
    if style in ("simple", "dense", "wide", "warm_broken"):
        shrink += 0.05
    if style == "stabs":
        shrink += 0.03
    shrink = max(0.0, min(0.24, shrink))
    if player_key == "barry_miles" and mg > ctx.bar_len_sec * 0.22:
        shrink *= 0.58
    return (max(0.42, lo - shrink), max(lo + 0.03, hi - shrink * 1.1))


def _jazzy_t1_range_groove(
    ctx: SessionAnchorContext | None,
    traits: ChordProfile | None,
    bar: int,
    player_key: str | None,
) -> tuple[float, float]:
    j_lo, j_hi = _jazzy_t1_range(traits)
    if not _groove_chord_anchor(ctx):
        return j_lo, j_hi
    dd = density_for_bar(ctx, bar)
    cut = 0.04 + 0.1 * min(1.0, dd / 13.0)
    if player_key == "herbie":
        cut *= 0.82
    return (max(0.68, j_lo - cut * 0.6), max(j_lo + 0.02, j_hi - cut))


def _wide_upper_oct(traits: ChordProfile | None) -> int:
    if not traits:
        return 5
    return 6 if traits["register_spread_bias"] > 0.62 and random.random() < 0.35 else 5


def _preview_blurb(style: str) -> str:
    return {
        "simple": "plain triads with light sevenths on alternating bars, sustained pads.",
        "jazzy": "seventh chords, occasional 9th color, and mild inversion shifts for smoother harmony.",
        "wide": "spread voicings: low root with upper-structure thirds, fifths, and sevenths lifted.",
        "dense": "thicker sustained stacks: sevenths plus doubled upper chord tones for harmonic fill.",
        "stabs": "short rhythmic hits on downbeats and backbeats; still one harmony per bar.",
        "warm_broken": (
            "broken voicings with 7ths and gentle 9th color, slow arpeggiated motion, warm low velocities, "
            "occasional soft comp taps — space and warmth, not block chords."
        ),
    }.get(style, "sustained harmony.")


def _player_blurb(player_key: str | None) -> str:
    if not player_key:
        return ""
    bits = {
        "herbie": "Chord player «herbie» (color-forward comp voice): clustered voicings, active harmony, rhythmic punch.",
        "barry_miles": "Chord player «barry_miles» (modal bed voice): wide dark stacks, long sustain, less chatter.",
        "soul_keys": "Chord player «soul_keys» (warm support voice): gentle 7ths/9ths, broken soul motion, restraint.",
        "funk_stabs": "Chord player «funk_stabs» (groove punctuation voice): short syncopated hits, tight pocket.",
    }
    return bits.get(player_key, "")


def _shape_velocities(inst: pretty_midi.Instrument, player_key: str | None) -> None:
    if not player_key:
        return
    if player_key == "barry_miles":
        for n in inst.notes:
            n.velocity = max(1, min(127, int(n.velocity * 0.9)))
    elif player_key == "soul_keys":
        for n in inst.notes:
            n.velocity = max(24, min(98, int(40 + (n.velocity - 58) * 0.78)))
    elif player_key == "funk_stabs":
        for n in inst.notes:
            n.velocity = max(44, min(124, int(n.velocity + 4)))
    elif player_key == "herbie":
        for n in inst.notes:
            n.velocity = max(38, min(122, int(n.velocity + (n.pitch % 4))))


def generate_chords(
    *,
    tempo: int,
    bar_count: int,
    key: str,
    scale: str,
    chord_style: str | None = None,
    chord_instrument: str | None = None,
    session_preset: str | None = None,
    chord_player: str | None = None,
    context: SessionAnchorContext | None = None,
) -> tuple[bytes, str]:
    style = normalize_chord_style(chord_style)
    ci = normalize_chord_instrument(chord_instrument)
    player_key = normalize_chord_player(chord_player)
    traits: ChordProfile | None = chord_profiles[player_key] if player_key else None
    chord_role_name = chord_role_for_anchor(context.anchor_lane) if context else None
    chord_role_knobs = chord_knobs_for_role(chord_role_name or "primary") if context else None
    if context and chord_role_knobs:
        traits = cast(
            ChordProfile,
            merge_chord_profile(dict(traits or chord_profiles["barry_miles"]), chord_role_knobs),
        )

    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    inst = pretty_midi.Instrument(program=chord_midi_program(ci), name="Chords")
    spb = 60.0 / float(tempo)
    bar_len = 4 * spb
    sixteenth = spb / 4.0
    degrees = mt.progression_degrees_for_bars(bar_count, scale)
    salt = random.randint(0, 127)

    for bar, deg in enumerate(degrees):
        bar_t0 = bar * bar_len
        bar_end = bar_t0 + bar_len
        ps = _phrase_seed(traits, bar, salt, deg)
        gap_pull = 0.0
        sync_push = 0.0
        if context:
            bi = bar % context.bar_count
            gaps = context.mean_gap_sec_per_bar
            base_gap = gaps[bi] if bi < len(gaps) else context.sixteenth_len_sec * 2.5
            gap_pull = base_gap * (0.08 + 0.025 * min(1.0, density_for_bar(context, bar) / 9.0))
            sync_push = sixteenth * 0.05 * min(1.0, context.syncopation_score)
            if chord_role_knobs:
                gap_pull *= chord_role_knobs.gap_pull_mult
                sync_push *= chord_role_knobs.sync_push_mult

        if style == "simple":
            seventh = _seventh_simple(traits, bar, salt, deg)
            base_oct = _base_oct(traits)
            tones = list(mt.chord_tones_midi(key, scale, deg, octave=base_oct, seventh=seventh))
            _maybe_add_ninth(tones, traits)
            inv = (ps + _inv_extra(traits)) % min(3, len(tones))
            tones = tones[inv:] + tones[:inv]
            if random.random() < 0.28 + (0.22 * traits["voicing_density"] if traits else 0.0):
                tones = [p + random.choice((0, 12, -12)) for p in tones]
                tones = [max(40, min(96, p)) for p in tones]
            lo, hi = (
                _sustain_leg_range_groove(context, traits, bar, style, player_key)
                if context
                else _sustain_leg_range(traits)
            )
            leg = bar_len * random.uniform(lo, hi)
            st_lo, st_hi = _stagger_pair(traits)
            br = _broken_prob_groove(context, traits, style, player_key) if context else _broken_prob(traits)
            dens_b = density_for_bar(context, bar) if context else 0.0
            force_broken = (
                _groove_chord_anchor(context) and random.random() < 0.18 + 0.14 * min(1.0, dens_b / 14.0)
            )
            if random.random() < br or force_broken:
                for n, pitch in enumerate(tones):
                    off = n * sixteenth * random.uniform(st_lo, st_hi) + gap_pull * min(n, 4) * 0.45
                    if _groove_chord_anchor(context):
                        off = _nudge_time_away_from_crowd(
                            context, bar, off, sixteenth=sixteenth, spb=spb, bar_len=bar_len
                        )
                    t0 = bar_t0 + off
                    t1 = min(bar_t0 + leg, bar_end - 1e-4)
                    if t1 <= t0:
                        continue
                    vel = max(40, min(112, (68 - n * 4) + random.randint(-12, 12)))
                    inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))
            else:
                t0 = bar_t0 + random.uniform(0, 0.035) * spb
                leg_pad = leg
                if _groove_chord_anchor(context):
                    mg = _mean_gap_bar(context, bar)
                    leg_pad *= 0.68 + 0.22 * min(1.0, mg / (bar_len * 0.28 + 1e-6))
                    leg_pad = max(bar_len * 0.34, min(leg, leg_pad))
                t1 = bar_t0 + leg_pad
                for n, pitch in enumerate(tones):
                    vel = max(40, min(112, (68 - n * 4) + random.randint(-12, 12)))
                    inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))

        elif style == "jazzy":
            tones = list(mt.chord_tones_midi(key, scale, deg, octave=4, seventh=True))
            root = tones[0]
            nine = root + 14
            if nine > tones[-1] and (traits is None or random.random() < 0.55 + 0.35 * traits["color_tone_bias"]):
                tones.append(nine)
            _maybe_color_extension(tones, traits)
            inv_bias = 1 + int(2.2 * traits["inversion_activity"]) if traits else 1
            if bar % (3 if inv_bias > 2 else 2) == 1 and len(tones) >= 4 and (traits is None or traits["inversion_activity"] > 0.45):
                tones = [tones[1], tones[2], tones[0] + 12] + list(tones[3:])
            rot_thresh = 0.62 - (0.18 * traits["inversion_activity"] if traits else 0.0)
            if random.random() < rot_thresh:
                k = random.randint(0, max(0, len(tones) - 1))
                tones = tones[k:] + tones[:k]
            elif (ps + _hm_shift(traits)) % 3 == 0 and len(tones) >= 3:
                tones = [tones[-1]] + tones[:-1]
            if random.random() < 0.22 + (0.28 * traits["inversion_activity"] if traits else 0.0) and len(tones) >= 2:
                tones = [tones[0] + 12] + tones[1:]
            if _groove_chord_anchor(context) and player_key == "herbie" and traits and random.random() < 0.24:
                _maybe_color_extension(tones, traits)
            t0_base = bar_t0 + random.uniform(0, 0.05) * bar_len * (
                0.65 + 0.35 * (traits["rhythmic_comping_bias"] if traits else 1.0)
            )
            t0_base += sync_push + gap_pull * 0.35
            j_lo, j_hi = (
                _jazzy_t1_range_groove(context, traits, bar, player_key)
                if context
                else _jazzy_t1_range(traits)
            )
            t1_end = bar_t0 + bar_len * random.uniform(j_lo, j_hi)
            sg_lo, sg_hi = 0.1, 0.75
            if traits:
                sg_lo = 0.06 + 0.08 * traits["stagger_bias"]
                sg_hi = 0.42 + 0.48 * traits["stagger_bias"]
            if _groove_chord_anchor(context):
                sg_lo = max(0.04, sg_lo - 0.04)
                sg_hi = min(0.92, sg_hi + 0.08)
                if player_key == "herbie":
                    sg_hi = min(0.95, sg_hi + 0.05)
            for n, pitch in enumerate(tones):
                rel = n * sixteenth * random.uniform(sg_lo, sg_hi)
                if _groove_chord_anchor(context):
                    rel = _nudge_time_away_from_crowd(
                        context, bar, rel, sixteenth=sixteenth, spb=spb, bar_len=bar_len
                    )
                t0 = min(t0_base + rel, bar_end - sixteenth)
                t1 = min(t1_end, bar_end - 1e-4)
                if t1 <= t0:
                    continue
                vel = max(40, min(118, (72 - n * 3) + random.randint(-12, 12)))
                inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))

        elif style == "wide":
            low = mt.chord_tones_midi(key, scale, deg, octave=3, seventh=False)
            r, t, f = low[0], low[1], low[2]
            up_oct = _wide_upper_oct(traits)
            upper = mt.chord_tones_midi(key, scale, deg, octave=up_oct, seventh=True)
            pitches = [r, t + 12, f + 12]
            if len(upper) > 3:
                sev = upper[-1]
                if sev <= pitches[-1]:
                    sev += 12
                pitches.append(sev)
            if random.random() < 0.58 - (0.12 * traits["inversion_activity"] if traits else 0.0):
                pitches = pitches[1:] + pitches[:1]
            t0_base = bar_t0 + random.uniform(0, 0.06) * bar_len * (0.75 if traits and traits["rhythmic_comping_bias"] < 0.35 else 1.0)
            w_lo, w_hi = (
                _sustain_leg_range_groove(context, traits, bar, style, player_key)
                if context
                else _sustain_leg_range(traits)
            )
            t1_end = bar_t0 + bar_len * random.uniform(max(0.82, w_lo), min(0.998, w_hi + 0.04))
            if _groove_chord_anchor(context):
                dd = density_for_bar(context, bar)
                t1_end = bar_t0 + (t1_end - bar_t0) * (1.0 - 0.11 * min(1.0, dd / 12.0))
            st_lo, st_hi = _stagger_pair(traits)
            st_m = 0.15 + 0.55 * (traits["stagger_bias"] if traits else 0.5)
            for n, pitch in enumerate(pitches):
                rel = n * sixteenth * random.uniform(st_lo * 0.9 + 0.05, st_hi * 0.85 + st_m * 0.25)
                if _groove_chord_anchor(context):
                    rel = _nudge_time_away_from_crowd(
                        context, bar, rel, sixteenth=sixteenth, spb=spb, bar_len=bar_len
                    )
                t0 = t0_base + rel
                t1 = min(t1_end, bar_end - 1e-4)
                if t1 <= t0:
                    continue
                vel = max(38, min(118, (66 - n * 3) + random.randint(-12, 12)))
                inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))

        elif style == "warm_broken":
            tones = list(mt.chord_tones_midi(key, scale, deg, octave=4, seventh=True))
            root = tones[0]
            nine = root + 14
            while nine <= tones[-1]:
                nine += 12
            if nine <= 96 and (traits is None or random.random() < 0.55 + 0.35 * traits["color_tone_bias"]):
                tones.append(nine)
            _maybe_color_extension(tones, traits)
            k_rot = (ps + _inv_extra(traits)) % min(3, len(tones))
            tones = tones[k_rot:] + tones[:k_rot]
            arp_families = (
                (0, 5, 10, 14),
                (0, 4, 9, 13),
                (1, 6, 11, 14),
                (0, 3, 8, 12),
            )
            arp_idx = (ps + _hm_shift(traits)) % len(arp_families)
            arp_slots = arp_families[arp_idx]
            sb = traits["sustain_bias"] if traits else 0.72
            sustain_end = bar_t0 + bar_len * (0.88 + 0.1 * sb + ((salt >> 2) + _phrase_bar(traits, bar)) % 4 / 120.0)
            if _groove_chord_anchor(context):
                dd = density_for_bar(context, bar)
                sustain_end -= bar_len * (0.045 + 0.09 * min(1.0, dd / 12.0))
                if player_key == "soul_keys":
                    sustain_end -= bar_len * 0.025
            st_off = sixteenth * (0.08 + 0.12 * (traits["stagger_bias"] if traits else 0.5))
            if _groove_chord_anchor(context) and player_key == "soul_keys":
                st_off *= 1.12
            for n, slot in enumerate(arp_slots):
                if n >= len(tones):
                    break
                pitch = tones[n]
                off = slot * sixteenth + random.uniform(0, 0.02) * spb + n * st_off * 0.15
                if _groove_chord_anchor(context):
                    off = _nudge_time_away_from_crowd(
                        context, bar, off, sixteenth=sixteenth, spb=spb, bar_len=bar_len
                    )
                t0 = bar_t0 + off
                t1 = min(sustain_end - n * sixteenth * (0.1 + 0.08 * (1 - sb)), bar_end - 1e-4)
                if t1 <= t0:
                    t1 = min(t0 + sixteenth * (2.2 + 0.8 * sb), bar_end - 1e-4)
                vel = max(34, min(78, 52 - n * 3 + ((salt + bar + slot) % 9) - 4))
                inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))
            tap_roll = 5 if traits and traits["rhythmic_comping_bias"] < 0.42 else 4
            if (ps * 3) % tap_roll == 2:
                tap_slot = 11
                tq = bar_t0 + tap_slot * sixteenth + random.uniform(0, 0.015) * spb
                tq1 = min(tq + sixteenth * (1.05 + 0.35 * (traits["sustain_bias"] if traits else 0.7)), bar_end - 1e-4)
                if tq1 > tq:
                    inst.notes.append(
                        pretty_midi.Note(
                            velocity=max(28, min(58, 40 + (salt % 6))),
                            pitch=tones[0],
                            start=tq,
                            end=tq1,
                        )
                    )

        elif style == "dense":
            tones = mt.chord_tones_midi(key, scale, deg, octave=4, seventh=True)
            fill = list(tones)
            vd = traits["voicing_density"] if traits else 0.65
            if vd > 0.48:
                fill.append(tones[0] + 12)
            if len(tones) >= 3 and vd > 0.55:
                fill.append(tones[2] + 12)
            uniq: list[int] = []
            for p in fill:
                if p not in uniq:
                    uniq.append(p)
            uniq = uniq[: _dense_cap(traits, len(uniq))]
            if random.random() < 0.5 + (0.12 * traits["inversion_activity"] if traits else 0.0):
                r0 = random.randint(0, max(0, len(uniq) - 1))
                uniq = uniq[r0:] + uniq[:r0]
            t0_base = bar_t0 + random.uniform(0, 0.045) * bar_len
            d_lo, d_hi = (
                _sustain_leg_range_groove(context, traits, bar, style, player_key)
                if context
                else _sustain_leg_range(traits)
            )
            t1_end = bar_t0 + bar_len * random.uniform(max(0.88, d_lo), min(0.998, d_hi + 0.02))
            if _groove_chord_anchor(context):
                mg = _mean_gap_bar(context, bar)
                t1_end = bar_t0 + (t1_end - bar_t0) * (
                    0.84 + 0.12 * min(1.0, mg / (bar_len * 0.22 + 1e-6))
                )
            st_lo, st_hi = _stagger_pair(traits)
            for n, pitch in enumerate(uniq):
                rel = n * sixteenth * random.uniform(st_lo * 0.5 + 0.04, st_hi * 0.65 + 0.1)
                if _groove_chord_anchor(context):
                    rel = _nudge_time_away_from_crowd(
                        context, bar, rel, sixteenth=sixteenth, spb=spb, bar_len=bar_len
                    )
                    sl = max(0, min(15, int(rel / max(1e-9, sixteenth))))
                    if slot_pressure(context, bar, sl) > 0.84 and random.random() < 0.42:
                        continue
                t0 = min(t0_base + rel, bar_end - sixteenth)
                t1 = min(t1_end, bar_end - 1e-4)
                if t1 <= t0:
                    continue
                vel = max(36, min(118, (64 - n * 2) + random.randint(-12, 12)))
                inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))

        else:  # stabs
            seventh = bar % 2 == 0 or (
                traits is not None and random.random() < 0.25 + 0.35 * traits["color_tone_bias"]
            )
            tones = list(mt.chord_tones_midi(key, scale, deg, octave=4, seventh=seventh))
            _maybe_add_ninth(tones, traits)
            slot_sets = (
                (0, 4, 8, 12),
                (0, 3, 7, 11),
                (1, 4, 9, 13),
                (2, 5, 8, 14),
                (0, 2, 6, 10),
                (1, 5, 9, 14),
                (0, 4, 6, 11),
                (2, 6, 10, 14),
            )
            si = (ps + _hm_shift(traits) + bar) % len(slot_sets)
            slots = slot_sets[si]
            if traits and traits["rhythmic_comping_bias"] > 0.75 and len(slots) > 3:
                slots = slots[: min(len(slots), 3 + int(traits["voicing_density"] * 2))]
            stab_scale = _stab_len_scale(traits)
            if _groove_chord_anchor(context):
                stab_scale *= 0.84 + 0.1 * min(1.0, _mean_gap_bar(context, bar) / (bar_len * 0.2 + 1e-6))
                if player_key == "funk_stabs":
                    stab_scale *= 0.87
            for slot in slots:
                s_use = slot
                if _groove_chord_anchor(context):
                    prs = slot_pressure(context, bar, slot)
                    if prs > 0.72 and random.random() < 0.52:
                        alt = min(15, slot + random.choice((1, 2, -1, -2)))
                        if slot_pressure(context, bar, alt) < prs - 0.08:
                            s_use = alt
                    if slot_pressure(context, bar, s_use) > 0.88 and random.random() < 0.38:
                        continue
                t0 = bar_t0 + s_use * sixteenth + random.uniform(0, 0.04) * spb
                if _groove_chord_anchor(context):
                    t0 = bar_t0 + _nudge_time_away_from_crowd(
                        context,
                        bar,
                        t0 - bar_t0,
                        sixteenth=sixteenth,
                        spb=spb,
                        bar_len=bar_len,
                    )
                stab_len = sixteenth * random.uniform(1.15, 2.55) * stab_scale
                t1 = min(t0 + stab_len, bar_end - 1e-4)
                if t1 <= t0:
                    continue
                accent = slot == slots[0]  # pattern accent from original slot choice
                for idx, pitch in enumerate(tones):
                    vel = max(48, min(118, (82 if accent else 70) - idx * 4 + random.randint(-8, 10)))
                    inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=t0, end=t1))

    _shape_velocities(inst, player_key)

    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    ci_lbl = chord_instrument_label(ci)
    who = f", {player_key}" if player_key else ""
    head = (
        f"Chords [{style}, {ci_lbl}{who}]: {mt.normalize_key(key)} {mt.describe_scale(scale)}, "
        f"{bar_count} bar(s), {tempo} BPM"
    )
    inst_note = {
        "piano": "Acoustic piano tone.",
        "rhodes": "Electric piano / Rhodes character.",
        "organ": "Drawbar organ color.",
        "pad": "Sustained pad timbre.",
    }.get(ci, "")
    soul = (session_preset or "").strip().lower() == "rare_groove_soul"
    soul_tag = (
        " Rare groove soul: broken warmth and gentle comp — space over stacks."
        if soul and style == "warm_broken"
        else ""
    )
    pb = _player_blurb(player_key)
    mid = f"{pb} {_preview_blurb(style)}".strip() if pb else _preview_blurb(style)
    role_tag = f" Role vs anchor: {chord_role_name}." if chord_role_name else ""
    preview = f"{head} — {mid} {inst_note}{soul_tag}{role_tag}"
    return buf.getvalue(), preview
