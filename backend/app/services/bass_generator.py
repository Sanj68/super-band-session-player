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
from app.services.bass_articulation import ghost_eligibility, shape_note
from app.services.bass_performance import BassPerformanceNote, infer_bass_articulations
from app.services.bass_phrase_engine_v2 import generate_bass_phrase_v2
from app.services.bass_phrase_plan import build_phrase_plan
from app.services.conditioning import (
    UnifiedConditioning,
    has_source_groove,
    source_kick_weight,
    source_snare_weight,
    source_slot_pressure,
)
from app.services.reference_guidance import ReferenceGrooveGuidance, build_reference_guidance
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
_BASS_ENGINES: Final[frozenset[str]] = frozenset({"baseline", "phrase_v2"})


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


class ChordSegment(TypedDict):
    start_bar: int
    end_bar: int
    root_pc: int
    quality: str
    target_pcs: tuple[int, ...]
    passing_pcs: tuple[int, ...]
    avoid_pcs: tuple[int, ...]
    confidence: float


class PhraseIntentBar(TypedDict):
    role: str
    density_mult: float
    rest_bias: float
    offbeat_push: float
    cadence_strength: float
    sustain_mult: float
    allow_fill: bool


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


def normalize_bass_engine(bass_engine: str | None) -> str:
    if bass_engine is None:
        return "baseline"
    s = str(bass_engine).strip().lower()
    return s if s in _BASS_ENGINES else "baseline"


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


def _source_groove_rhythm(
    conditioning: UnifiedConditioning | None,
    context: SessionAnchorContext | None,
) -> bool:
    return conditioning is not None and has_source_groove(conditioning) and not _drum_anchor_ctx(context)


def _source_bar_mean_pressure(conditioning: UnifiedConditioning, bar: int) -> float:
    return sum(source_slot_pressure(conditioning, bar, s) for s in range(16)) / 16.0


def _source_bar_groove_conf(conditioning: UnifiedConditioning, bar: int) -> float:
    if not conditioning.source_groove_confidence:
        return 0.0
    b = max(0, min(bar, len(conditioning.source_groove_confidence) - 1))
    return float(conditioning.source_groove_confidence[b])


def _apply_source_groove_intent_nudge(
    intent: PhraseIntentBar,
    *,
    conditioning: UnifiedConditioning,
    context: SessionAnchorContext | None,
    bar: int,
) -> PhraseIntentBar:
    if not _source_groove_rhythm(conditioning, context):
        return intent
    mp = _source_bar_mean_pressure(conditioning, bar)
    gc = _source_bar_groove_conf(conditioning, bar)
    w = 0.45 * max(0.15, min(1.0, gc))
    dm = float(intent["density_mult"])
    rb = float(intent["rest_bias"])
    delta = (mp - 0.38) * 0.11 * w
    dm = max(0.55, min(1.35, dm + delta))
    rb = max(0.0, min(0.65, rb - delta * 0.35))
    out = cast(PhraseIntentBar, dict(intent))
    out["density_mult"] = dm
    out["rest_bias"] = rb
    return out


def _source_kick_window_max(conditioning: UnifiedConditioning, bar: int, slot: int, radius: int = 2) -> float:
    lo, hi = max(0, slot - radius), min(15, slot + radius)
    return max(source_kick_weight(conditioning, bar, j) for j in range(lo, hi + 1))


def _repeated_minor_chord_root_pc(chord_progression: list[str] | None, bar_count: int) -> int | None:
    """Return the repeated manual minor root pc, or None when not a one-chord minor vamp."""
    try:
        chords = mt.progression_chords_for_bars(chord_progression, max(1, int(bar_count)))
    except ValueError:
        return None
    if not chords:
        return None
    root = int(chords[0].root_pc) % 12
    quality = str(chords[0].quality)
    if not quality.startswith("minor"):
        return None
    for chord in chords[1:]:
        if int(chord.root_pc) % 12 != root or str(chord.quality) != quality:
            return None
    return root


def _supportive_source_minor_riff_active(
    *,
    style: str,
    context: SessionAnchorContext | None,
    conditioning: UnifiedConditioning | None,
    chord_progression: list[str] | None,
    bar_count: int,
) -> int | None:
    if style != "supportive":
        return None
    if not _source_groove_rhythm(conditioning, context):
        return None
    return _repeated_minor_chord_root_pc(chord_progression, bar_count)


def _source_riff_slot_score(conditioning: UnifiedConditioning, bar: int, slot: int) -> float:
    sk = source_kick_weight(conditioning, bar, slot)
    pr = source_slot_pressure(conditioning, bar, slot)
    sn = source_snare_weight(conditioning, bar, slot)
    neighbor = _source_kick_window_max(conditioning, bar, slot, radius=1)
    score = (0.58 * sk) + (0.28 * pr) + (0.16 * neighbor)
    if sn >= 0.5 and sk < 0.32:
        score -= 0.55 * sn
    if slot in (0, 8):
        score -= 0.45
    if slot % 4 == 0:
        score -= 0.08
    return score


def _source_riff_pick_slots(
    conditioning: UnifiedConditioning,
    *,
    bar: int,
    salt: int,
    prefer_from: tuple[int, ...] = (),
) -> tuple[int, ...]:
    candidates = tuple(s for s in range(1, 16) if s != 8)
    ranked = sorted(
        candidates,
        key=lambda s: (
            _source_riff_slot_score(conditioning, bar, s),
            -abs(s - 8),
            -((salt + bar * 17 + s * 31) % 11),
        ),
        reverse=True,
    )
    picked: list[int] = []
    for s in prefer_from:
        if s in candidates and _source_riff_slot_score(conditioning, bar, s) >= 0.12:
            picked.append(int(s))
        if len(picked) >= 2:
            break
    for s in ranked:
        if s in picked:
            continue
        if _source_riff_slot_score(conditioning, bar, s) < 0.16 and picked:
            continue
        sk = source_kick_weight(conditioning, bar, s)
        sn = source_snare_weight(conditioning, bar, s)
        if sn >= 0.58 and sk < 0.34:
            continue
        picked.append(int(s))
        if len(picked) >= 2:
            break
    if not picked:
        fallback = (6, 10, 14, 15, 5, 11)
        picked = [fallback[(salt + bar) % len(fallback)]]
    return tuple(sorted(set(picked[:2])))


def _build_supportive_source_minor_riff_slots(
    conditioning: UnifiedConditioning,
    *,
    bar_count: int,
    salt: int,
) -> tuple[tuple[int, ...], ...]:
    bars = max(1, int(bar_count))
    cell_len = 2 if bars >= 2 else 1
    cell_extras = tuple(
        _source_riff_pick_slots(conditioning, bar=bar, salt=salt)
        for bar in range(cell_len)
    )
    out: list[tuple[int, ...]] = []
    for bar in range(bars):
        extras = set(cell_extras[bar % cell_len])
        hot = _source_riff_pick_slots(conditioning, bar=bar, salt=salt, prefer_from=tuple(sorted(extras)))
        for s in hot:
            if _source_riff_slot_score(conditioning, bar, s) >= 0.34:
                extras.add(s)
        if (bar + 1) % 4 == 0:
            release_pick = _source_riff_pick_slots(conditioning, bar=bar, salt=salt + 19, prefer_from=(14, 15, 12))
            extras.add(release_pick[-1])
        slots_set = sorted({0, 8, *extras})
        slots = slots_set
        if bar == bars - 1 and not any(s >= 12 for s in slots):
            tail_slot = 14
            for s in (14, 12, 15, 13):
                sk = source_kick_weight(conditioning, bar, s)
                sn = source_snare_weight(conditioning, bar, s)
                if sn >= 0.62 and sk < 0.28:
                    continue
                tail_slot = s
                break
            slots = sorted({*set(slots), tail_slot})
        if len(slots) > 4:
            protected = {0, 8}
            if bar == bars - 1:
                late_candidates = [s for s in slots if s >= 12]
                if late_candidates:
                    preferred_tail = min(late_candidates, key=lambda s: abs(s - 14))
                    protected.add(preferred_tail)
            extras_ranked = sorted(
                (s for s in slots if s not in protected),
                key=lambda s: (_source_riff_slot_score(conditioning, bar, s), s >= 12),
                reverse=True,
            )
            take = max(0, 4 - len(protected))
            slots = sorted({*protected, *extras_ranked[:take]})
        out.append(tuple(slots))
    return tuple(out)


def _supportive_source_minor_pitch(
    *,
    slot: int,
    note_index: int,
    role: str,
    root_pitch: int,
    fifth_pitch: int,
    third_pitch: int,
    avoid_pcs: tuple[int, ...],
    scale: str,
    rng: random.Random | object,
) -> int:
    if slot == 0:
        return root_pitch
    root_pc = root_pitch % 12
    scale_pcs = {(root_pc + interval) % 12 for interval in mt.scale_intervals(scale)}
    flat7_pc = (root_pc + 10) % 12
    octave = root_pitch + 12 if root_pitch + 12 <= 58 else root_pitch
    choices: list[int] = [root_pitch, fifth_pitch, octave, third_pitch]
    if flat7_pc in scale_pcs and flat7_pc not in set(avoid_pcs):
        choices.append(_nearest_pitch_for_pc(flat7_pc, root_pitch, lo=30, hi=62))
    if slot == 8:
        weights = (0.34, 0.28, 0.26, 0.12) + ((0.10,) if len(choices) > 4 else ())
    elif role == "release" and slot >= 12:
        weights = (0.38, 0.24, 0.14, 0.10) + ((0.22,) if len(choices) > 4 else ())
    elif note_index % 2 == 0:
        weights = (0.18, 0.28, 0.36, 0.16) + ((0.14,) if len(choices) > 4 else ())
    else:
        weights = (0.22, 0.30, 0.20, 0.24) + ((0.14,) if len(choices) > 4 else ())
    if len(weights) != len(choices):
        weights = tuple(1.0 for _ in choices)
    pitch = rng.choices(tuple(choices), weights=weights, k=1)[0]
    if avoid_pcs and (pitch % 12) in set(avoid_pcs):
        return root_pitch
    return pitch


def _drum_profile_groove(player_key: str | None) -> tuple[float, float, float]:
    """(kick_lock, bounce, restraint) multipliers for drum-anchor pocket shaping."""
    if player_key == "bootsy":
        return 1.12, 1.18, 0.92
    if player_key == "marcus":
        return 1.06, 1.08, 0.95
    if player_key == "pino":
        return 1.08, 0.92, 1.22
    return 1.0, 1.0, 1.0


def _apply_reference_groove_nudge(
    intent: PhraseIntentBar,
    guidance: ReferenceGrooveGuidance,
    conditioning: UnifiedConditioning,
    bar: int,
) -> PhraseIntentBar:
    if not guidance.should_apply_bar(bar):
        return intent

    density_mult = float(intent["density_mult"])
    rest_bias = float(intent["rest_bias"])
    offbeat_push = float(intent["offbeat_push"])
    pocket_feel = str(conditioning.groove_profile.pocket_feel)

    if pocket_feel == "laid_back":
        density_mult *= 0.94
        rest_bias += 0.05
    elif pocket_feel == "driving":
        density_mult *= 1.06
        offbeat_push += 0.04
    elif pocket_feel == "syncopated":
        offbeat_push += 0.06

    if bar < len(guidance.bar_energy):
        energy = float(guidance.bar_energy[bar])
        if energy < 0.30:
            rest_bias += 0.04
        elif energy > 0.75:
            density_mult *= 1.04

    global_syncopation = float(conditioning.groove_profile.syncopation_score)
    offbeat_push += 0.05 * (global_syncopation - 0.5)

    out = cast(PhraseIntentBar, dict(intent))
    out["density_mult"] = max(0.55, min(1.35, density_mult))
    out["rest_bias"] = max(0.0, min(0.65, rest_bias))
    out["offbeat_push"] = max(0.0, min(1.0, offbeat_push))
    return out


def _pc_to_bass_register(pc: int, *, octave: int = 2, lo: int = 30, hi: int = 62) -> int:
    note = mt.pc_to_midi_note(pc % 12, octave)
    while note < lo:
        note += 12
    while note > hi:
        note -= 12
    return max(lo, min(hi, note))


def _nearest_pitch_for_pc(pc: int, reference: int, *, lo: int = 30, hi: int = 62) -> int:
    base = _pc_to_bass_register(pc, octave=2, lo=lo, hi=hi)
    options = [base - 12, base, base + 12]
    valid = [p for p in options if lo <= p <= hi]
    if not valid:
        return base
    return min(valid, key=lambda p: (abs(p - reference), p))


def _infer_chord_quality(root_pc: int, target_pcs: tuple[int, ...]) -> str:
    if not target_pcs:
        return "unknown"
    ints = {(pc - root_pc) % 12 for pc in target_pcs}
    has_m3 = 3 in ints
    has_M3 = 4 in ints
    has_m7 = 10 in ints
    if has_M3 and has_m7:
        return "dominant"
    if has_M3:
        return "major"
    if has_m3:
        return "minor"
    return "unknown"


def _segment_signature(seg: ChordSegment) -> tuple[int, str, tuple[int, ...]]:
    return (int(seg["root_pc"]) % 12, str(seg["quality"]), tuple(sorted(int(x) % 12 for x in seg["target_pcs"])))


def _phrase_role_for_bar(bar: int) -> str:
    cycle = ("anchor", "answer", "push", "release")
    return cycle[bar % len(cycle)]


def _build_phrase_intent_plan(
    *,
    bar_count: int,
    context: SessionAnchorContext | None,
    style: str,
) -> list[PhraseIntentBar]:
    plan: list[PhraseIntentBar] = []
    for bar in range(max(1, bar_count)):
        role = _phrase_role_for_bar(bar)
        energy = 0.5
        accent = 0.5
        if context is not None and bar < len(context.density_per_bar):
            mean_den = max(1e-6, float(context.mean_density))
            den = float(context.density_per_bar[bar])
            energy = max(0.0, min(1.0, den / max(2.0, mean_den * 1.4)))
        if context is not None and bar < len(context.onsets_norm_per_bar):
            onsets = context.onsets_norm_per_bar[bar]
            accent = 0.7 if any(x <= 0.08 for x in onsets) else 0.45

        if role == "anchor":
            density_mult = 0.9
            rest_bias = 0.15
            offbeat_push = 0.15
            cadence_strength = 0.2
            sustain_mult = 1.03
            allow_fill = False
        elif role == "answer":
            density_mult = 1.0
            rest_bias = 0.22
            offbeat_push = 0.28
            cadence_strength = 0.25
            sustain_mult = 0.98
            allow_fill = False
        elif role == "push":
            density_mult = 1.15
            rest_bias = 0.12
            offbeat_push = 0.45
            cadence_strength = 0.35
            sustain_mult = 0.92
            allow_fill = True
        else:  # release
            density_mult = 0.75
            rest_bias = 0.34
            offbeat_push = 0.08
            cadence_strength = 0.78 if ((bar + 1) % 4 == 0) else 0.48
            sustain_mult = 1.08
            allow_fill = False

        # Nudge intent by observed source density/accent without changing role semantics.
        density_mult *= 0.9 + (0.2 * energy)
        if accent < 0.5:
            rest_bias = min(0.55, rest_bias + 0.05)
        if style in ("rhythmic", "fusion"):
            density_mult *= 1.06
            offbeat_push = min(0.75, offbeat_push + 0.07)
        if style == "supportive":
            density_mult *= 0.92
            rest_bias = min(0.58, rest_bias + 0.04)
        if style == "melodic":
            offbeat_push *= 0.85
            sustain_mult *= 1.04

        plan.append(
            {
                "role": role,
                "density_mult": max(0.55, min(1.35, density_mult)),
                "rest_bias": max(0.0, min(0.65, rest_bias)),
                "offbeat_push": max(0.0, min(1.0, offbeat_push)),
                "cadence_strength": max(0.0, min(1.0, cadence_strength)),
                "sustain_mult": max(0.75, min(1.2, sustain_mult)),
                "allow_fill": allow_fill,
            }
        )
    return plan


def _apply_intent_density(
    slots: tuple[int, ...],
    intent: PhraseIntentBar,
    *,
    keep_root_slot: int = 0,
) -> tuple[int, ...]:
    base = sorted(set(slots))
    if not base:
        return (keep_root_slot,)
    target = max(2, int(round(len(base) * float(intent["density_mult"]))))
    if target >= len(base):
        out = list(base)
    else:
        # Keep structural downbeats first, then early phrase markers.
        priority = [keep_root_slot, 8, 4, 12, 6, 10, 14, 2]
        out: list[int] = []
        for p in priority:
            if p in base and p not in out:
                out.append(p)
            if len(out) >= target:
                break
        for s in base:
            if s not in out:
                out.append(s)
            if len(out) >= target:
                break
    if keep_root_slot not in out:
        out.insert(0, keep_root_slot)
    return tuple(sorted(set(out)))


def _build_chord_segments(
    *,
    context: SessionAnchorContext | None,
    bar_count: int,
    key: str,
    scale: str,
    chord_progression: list[str] | None = None,
) -> tuple[list[ChordSegment], list[ChordSegment]]:
    custom_chords = mt.progression_chords_for_bars(chord_progression, bar_count)
    if custom_chords:
        key_pc = mt.key_root_pc(key)
        scale_pcs = tuple((key_pc + x) % 12 for x in mt.scale_intervals(scale))
        segments: list[ChordSegment] = []
        per_bar: list[ChordSegment] = []
        for bar, chord in enumerate(custom_chords):
            target = tuple(sorted({int(pc) % 12 for pc in chord.tone_pcs}))
            passing = tuple(pc for pc in scale_pcs if pc not in target)
            avoid = tuple(pc for pc in range(12) if pc not in scale_pcs and pc not in target)
            seg: ChordSegment = {
                "start_bar": bar,
                "end_bar": bar,
                "root_pc": int(chord.root_pc) % 12,
                "quality": _infer_chord_quality(chord.root_pc, target),
                "target_pcs": target,
                "passing_pcs": passing,
                "avoid_pcs": avoid,
                "confidence": 1.0,
            }
            segments.append(seg)
            per_bar.append(seg)
        return segments, per_bar

    if context is None:
        fallback_segments: list[ChordSegment] = []
        per_bar: list[ChordSegment] = []
        degrees = mt.progression_degrees_for_bars(bar_count, scale)
        for bar, deg in enumerate(degrees):
            tones = mt.chord_tones_midi(key, scale, deg, octave=2, seventh=False)
            root_pc = int(tones[0]) % 12
            target = tuple(sorted({int(p) % 12 for p in tones}))
            seg: ChordSegment = {
                "start_bar": bar,
                "end_bar": bar,
                "root_pc": root_pc,
                "quality": _infer_chord_quality(root_pc, target),
                "target_pcs": target,
                "passing_pcs": (),
                "avoid_pcs": (),
                "confidence": 0.0,
            }
            fallback_segments.append(seg)
            per_bar.append(seg)
        return fallback_segments, per_bar

    max_bars = min(
        bar_count,
        len(context.harmonic_root_pc_per_bar),
        len(context.harmonic_target_pcs_per_bar),
    )
    if max_bars <= 0:
        return _build_chord_segments(context=None, bar_count=bar_count, key=key, scale=scale)

    bars: list[ChordSegment] = []
    for bar in range(max_bars):
        root_pc = int(context.harmonic_root_pc_per_bar[bar]) % 12
        target = tuple(sorted({int(pc) % 12 for pc in context.harmonic_target_pcs_per_bar[bar]}))
        passing = (
            tuple(sorted({int(pc) % 12 for pc in context.harmonic_passing_pcs_per_bar[bar]}))
            if bar < len(context.harmonic_passing_pcs_per_bar)
            else ()
        )
        avoid = (
            tuple(sorted({int(pc) % 12 for pc in context.harmonic_avoid_pcs_per_bar[bar]}))
            if bar < len(context.harmonic_avoid_pcs_per_bar)
            else ()
        )
        conf = float(context.harmonic_confidence_per_bar[bar]) if bar < len(context.harmonic_confidence_per_bar) else 0.0
        bars.append(
            {
                "start_bar": bar,
                "end_bar": bar,
                "root_pc": root_pc,
                "quality": _infer_chord_quality(root_pc, target),
                "target_pcs": target,
                "passing_pcs": passing,
                "avoid_pcs": avoid,
                "confidence": conf,
            }
        )

    segments: list[ChordSegment] = []
    per_bar: list[ChordSegment] = [bars[0]]
    current = dict(bars[0])
    for bar in range(1, max_bars):
        nxt = bars[bar]
        if _segment_signature(current) == _segment_signature(nxt):
            current["end_bar"] = bar
            per_bar.append(current)  # type: ignore[arg-type]
            continue
        segments.append(cast(ChordSegment, current))
        current = dict(nxt)
        per_bar.append(cast(ChordSegment, current))
    segments.append(cast(ChordSegment, current))

    if max_bars < bar_count:
        last = per_bar[-1]
        for bar in range(max_bars, bar_count):
            seg = cast(ChordSegment, {**last, "start_bar": bar, "end_bar": bar})
            per_bar.append(seg)
            segments.append(seg)
    return segments, per_bar


def _bar_harmonic_priority_pitches(
    seg: ChordSegment,
    *,
    prev_pitch: int | None = None,
    lo: int = 30,
    hi: int = 62,
) -> tuple[int, int, int]:
    root_pc = int(seg["root_pc"]) % 12
    quality = str(seg["quality"])
    if quality == "major" or quality == "dominant":
        third_pc = (root_pc + 4) % 12
    elif quality == "minor":
        third_pc = (root_pc + 3) % 12
    else:
        tpcs = tuple(int(x) % 12 for x in seg["target_pcs"])
        third_pc = next((pc for pc in tpcs if ((pc - root_pc) % 12) in (3, 4)), (root_pc + 3) % 12)
    fifth_pc = (root_pc + 7) % 12
    if prev_pitch is None:
        r = _pc_to_bass_register(root_pc, octave=2, lo=lo, hi=hi)
    else:
        r = _nearest_pitch_for_pc(root_pc, prev_pitch, lo=lo, hi=hi)
    f = _nearest_pitch_for_pc(fifth_pc, r, lo=lo, hi=hi)
    t = _nearest_pitch_for_pc(third_pc, r, lo=lo, hi=hi)
    return r, f, t


def _approach_pitch_to_next_root(next_root_pc: int, *, current_pitch: int, lo: int = 30, hi: int = 62) -> int:
    """Return a subtle chromatic neighbor one semitone from the next chord root."""
    next_root = _nearest_pitch_for_pc(next_root_pc, current_pitch, lo=lo, hi=hi)
    approach = next_root - 1 if current_pitch <= next_root else next_root + 1
    if approach < lo:
        approach = next_root + 1
    if approach > hi:
        approach = next_root - 1
    return max(lo, min(hi, approach))


def _nearest_from_pitch_classes(
    pcs: tuple[int, ...],
    *,
    reference: int,
    lo: int = 30,
    hi: int = 62,
) -> int:
    if not pcs:
        return max(lo, min(hi, reference))
    candidates: list[int] = []
    for pc in pcs:
        base = _pc_to_bass_register(int(pc) % 12, octave=2, lo=lo, hi=hi)
        for p in (base - 12, base, base + 12):
            if lo <= p <= hi:
                candidates.append(p)
    if not candidates:
        return _pc_to_bass_register(int(pcs[0]) % 12, octave=2, lo=lo, hi=hi)
    return min(candidates, key=lambda p: (abs(p - reference), p))


def _pick_harmonic_style_pitch(
    *,
    style: str,
    role: str,
    slot: int,
    root_pitch: int,
    fifth_pitch: int,
    third_pitch: int,
    passing_pcs: tuple[int, ...],
    target_pcs: tuple[int, ...],
    avoid_pcs: tuple[int, ...],
    harm_conf: float,
    prev_pitch: int | None,
    rng: random.Random | object = random,
) -> int:
    if slot % 4 == 0 or role == "anchor":
        return root_pitch
    stable = tuple(sorted({int(pc) % 12 for pc in target_pcs})) if target_pcs else (
        root_pitch % 12,
        fifth_pitch % 12,
        third_pitch % 12,
    )
    color = tuple(pc for pc in stable if pc != (root_pitch % 12))
    ref = prev_pitch if prev_pitch is not None else root_pitch
    pass_prob = min(0.42, 0.08 + (0.48 * max(0.0, min(1.0, harm_conf))))
    if style == "supportive":
        # v0.2.1: tighter pocket — favour root / chord tones; passing tones still possible but rarer.
        pass_prob = min(0.24, pass_prob * 0.37)
    elif style == "melodic":
        pass_prob *= 1.15
    elif style in ("rhythmic", "slap"):
        pass_prob *= 0.85
    if role == "release" and slot >= 12:
        pass_prob *= 0.6
    if passing_pcs and rng.random() < pass_prob:
        p = _nearest_from_pitch_classes(passing_pcs, reference=ref)
    else:
        if style == "supportive":
            pool = color if rng.random() < 0.45 else stable
        elif style == "melodic":
            pool = stable + color
        elif style == "rhythmic":
            pool = (root_pitch % 12,) + color + (root_pitch % 12,)
        elif style == "fusion":
            pool = stable + color + tuple(passing_pcs[:2])
        else:  # slap
            pool = (root_pitch % 12, fifth_pitch % 12, root_pitch % 12, third_pitch % 12)
        p = _nearest_from_pitch_classes(tuple(int(x) % 12 for x in pool), reference=ref)
    if avoid_pcs and (p % 12) in set(avoid_pcs):
        return root_pitch
    return p


def _bar_harmonic_pitches(
    ctx: SessionAnchorContext | None,
    bar: int,
    default_r: int,
    default_t: int,
    default_f: int,
) -> tuple[int, int, int, tuple[int, ...], tuple[int, ...], float]:
    if ctx is None or bar >= len(ctx.harmonic_target_pcs_per_bar):
        return default_r, default_t, default_f, (), (), 0.0
    tpcs = ctx.harmonic_target_pcs_per_bar[bar]
    ppcs = ctx.harmonic_passing_pcs_per_bar[bar] if bar < len(ctx.harmonic_passing_pcs_per_bar) else ()
    apcs = ctx.harmonic_avoid_pcs_per_bar[bar] if bar < len(ctx.harmonic_avoid_pcs_per_bar) else ()
    conf = float(ctx.harmonic_confidence_per_bar[bar]) if bar < len(ctx.harmonic_confidence_per_bar) else 0.0
    if not tpcs:
        return default_r, default_t, default_f, ppcs, apcs, conf
    pitches = [_pc_to_bass_register(pc, octave=2) for pc in tpcs]
    root_guess = _pc_to_bass_register(ctx.harmonic_root_pc_per_bar[bar], octave=2) if bar < len(ctx.harmonic_root_pc_per_bar) else pitches[0]
    if len(pitches) == 1:
        return root_guess, pitches[0], pitches[0], ppcs, apcs, conf
    if len(pitches) == 2:
        return root_guess, pitches[0], pitches[1], ppcs, apcs, conf
    return root_guess, pitches[1], pitches[2], ppcs, apcs, conf


def _rhythmic_drum_slot_keep(
    ctx: SessionAnchorContext,
    bar: int,
    slot: int,
    *,
    d_drum: float,
    kick_lock: float,
    restraint: float,
    rng: random.Random | object = random,
) -> bool:
    """Groove-lock gate for rhythmic 16ths under drum anchor (bounded probabilities)."""
    kw = drum_kick_weight(ctx, bar, slot)
    nk = drum_kick_emphasis_max(ctx, bar, slot, 2)
    pr = slot_pressure(ctx, bar, slot)
    if kw > 0.36 or (slot % 4 == 0 and nk > 0.44):
        return rng.random() < min(0.98, 0.86 + 0.1 * kw * kick_lock)
    if pr > 0.5 and kw < 0.16:
        skip_p = 0.16 + 0.36 * pr - 0.34 * nk
        if d_drum > 12.0:
            skip_p += 0.1
        skip_p = max(0.06, min(0.72, skip_p * restraint))
        return rng.random() > skip_p
    if pr > 0.62 and kw < 0.28:
        return rng.random() > min(0.55, 0.22 + 0.28 * pr)
    return True


def _rhythmic_source_slot_keep(
    conditioning: UnifiedConditioning,
    bar: int,
    slot: int,
    *,
    rng: random.Random | object,
) -> bool:
    """Soft pocket gate from upload groove maps (lighter than drum-anchor gate)."""
    if slot == 0:
        return True
    kw = source_kick_weight(conditioning, bar, slot)
    nk = _source_kick_window_max(conditioning, bar, slot, radius=2)
    pr = source_slot_pressure(conditioning, bar, slot)
    if kw > 0.34 or (slot % 4 == 0 and nk > 0.4):
        return rng.random() < min(0.96, 0.82 + 0.1 * kw)
    if pr > 0.54 and kw < 0.17:
        skip_p = 0.1 + 0.26 * pr
        return rng.random() > min(0.52, skip_p)
    if pr > 0.66 and kw < 0.24:
        return rng.random() > 0.45
    return True


def _supportive_slot_may_apply_rest_drop(
    *,
    role: str,
    s: int,
    rest_bias: float,
    rng: random.Random | object = random,
) -> bool:
    """Return True to skip this hit (phrase-plan rest_bias). Never thin core pocket/cadence shells."""
    if s in (0, 8):
        return False
    if role == "release" and s >= 12:
        return False
    w = 0.42 * rest_bias
    if role == "anchor":
        w *= 0.38
    elif role == "answer":
        w *= 0.9
    elif role in ("push", "release"):
        w *= 0.88
    return rng.random() < min(0.48, w)


def _supportive_thin_push_slots(pat: tuple[int, ...], salt: int, bar: int) -> tuple[int, ...]:
    """Cap supportive push density: energize with fewer 16th hits (avoid pseudo-rhythmic clutter)."""
    s = sorted({int(x) for x in pat if 0 <= int(x) <= 15})
    if len(s) <= 4:
        return tuple(s)
    protect = {0, 8}
    if 14 in s:
        protect.add(14)
    sl = list(s)
    rng = random.Random(salt * 199 + bar * 17 + 53 * len(s))
    while len(sl) > 4:
        drop_candidates = [x for x in sl if x not in protect]
        if not drop_candidates:
            if 14 in protect and 14 in sl:
                protect.discard(14)
                continue
            break
        drop = rng.choice(sorted(drop_candidates, key=lambda x: (x % 4 != 0, -x)))
        sl.remove(drop)
    return tuple(sl)


def _clamp_f(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _supportive_unified_slot8_pitch(
    *,
    planned_tidx: int,
    pr_role: str,
    r: int,
    f: int,
    t: int,
    cadence_strength: float,
    rng: random.Random | object = random,
) -> int:
    """One decision path for slot 8: role + phrase (tone index) in a single mix, no silent override."""
    base = _deg_pitch_map(r, f, t, planned_tidx)
    if pr_role == "anchor":
        # Phrase dials root vs fifth; small third/fifth nudge keeps bar from feeling frozen.
        if planned_tidx == 0:
            w = (0.78, 0.16, 0.06)  # r, f, t
        elif planned_tidx == 1:
            w = (0.22, 0.64, 0.14)
        else:
            w = (0.2, 0.25, 0.55)
        pick = rng.choices((r, f, t), weights=w, k=1)[0]
        return pick
    if pr_role == "answer":
        if planned_tidx == 0:
            w = (0.3, 0.48, 0.22)
        elif planned_tidx == 1:
            w = (0.18, 0.55, 0.27)
        else:
            w = (0.2, 0.22, 0.58)
        return rng.choices((r, f, t), weights=w, k=1)[0]
    if pr_role == "push":
        return base
    if pr_role == "release":
        a = _clamp_f(cadence_strength, 0.0, 1.0)
        wroot = 0.34 + 0.5 * a
        rem = 1.0 - wroot
        if planned_tidx == 0:
            w = (wroot + rem * 0.22, rem * 0.5, rem * 0.28)
        elif planned_tidx == 1:
            w = (wroot * 0.5 + 0.15, wroot * 0.1 + rem * 0.45, rem * 0.25)
        else:
            w = (wroot * 0.55, rem * 0.32, rem * 0.38)
        tsum = w[0] + w[1] + w[2]
        w = (w[0] / tsum, w[1] / tsum, w[2] / tsum)
        return rng.choices((r, f, t), weights=w, k=1)[0]
    return base


def generate_bass(
    *,
    tempo: int,
    bar_count: int,
    key: str,
    scale: str,
    bass_style: str | None = None,
    bass_instrument: str | None = None,
    bass_player: str | None = None,
    bass_engine: str | None = None,
    chord_progression: list[str] | None = None,
    session_preset: str | None = None,
    context: SessionAnchorContext | None = None,
    conditioning: UnifiedConditioning | None = None,
    seed: int | None = None,
    return_performance_notes: bool = False,
) -> tuple[bytes, str] | tuple[bytes, str, tuple[BassPerformanceNote, ...]]:
    rng = random.Random(seed) if seed is not None else random
    engine_mode = normalize_bass_engine(bass_engine)
    if engine_mode == "phrase_v2":
        return generate_bass_phrase_v2(
            tempo=tempo,
            bar_count=bar_count,
            key=key,
            scale=scale,
            bass_style=bass_style,
            bass_instrument=bass_instrument,
            bass_player=bass_player,
            session_preset=session_preset,
            context=context,
            seed=seed,
            return_performance_notes=return_performance_notes,
        )

    soul_preset = (session_preset or "").strip().lower() == "rare_groove_soul"
    player_key = normalize_bass_player(bass_player)
    use_profile = player_key is not None
    traits: BassProfile | None = bass_profiles[player_key] if use_profile and player_key else None

    style = normalize_bass_style(bass_style)
    reference_guidance = build_reference_guidance(
        conditioning,
        has_midi_anchor=context is not None,
    )
    reference_guidance_applied = False
    bi = normalize_bass_instrument(bass_instrument)
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    program = bass_midi_program(bi, style)
    inst = pretty_midi.Instrument(program=program, name="Bass")
    spb = 60.0 / float(tempo)
    eighth = spb / 2.0
    sixteenth = spb / 4.0
    degrees = mt.progression_degrees_for_bars(bar_count, scale)
    has_custom_progression = bool([c for c in (chord_progression or []) if str(c).strip()])
    salt = rng.randint(0, 127)

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
        return base * rng.uniform(0.88, 1.06) * art

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

    segments, segment_per_bar = _build_chord_segments(
        context=context,
        bar_count=bar_count,
        key=key,
        scale=scale,
        chord_progression=chord_progression,
    )
    _ = segments  # keep named list for diagnostics/debug readability
    phrase_plan = build_phrase_plan(
        bar_count=bar_count, style=style, salt=salt, context=context, conditioning=conditioning
    )
    source_minor_riff_root_pc = _supportive_source_minor_riff_active(
        style=style,
        context=context,
        conditioning=conditioning,
        chord_progression=chord_progression,
        bar_count=bar_count,
    )
    source_minor_riff_slots = (
        _build_supportive_source_minor_riff_slots(
            conditioning,
            bar_count=bar_count,
            salt=salt,
        )
        if source_minor_riff_root_pc is not None and conditioning is not None
        else ()
    )
    prev_structural_pitch: int | None = None

    for bar, deg in enumerate(degrees):
        phrase_bar = phrase_plan[bar] if bar < len(phrase_plan) else phrase_plan[-1]
        target_density = max(2, len(tuple(phrase_bar.slots)))
        base_density = 4 if style in ("rhythmic", "fusion", "melodic") else 2
        intent: PhraseIntentBar = {
            "role": phrase_bar.role,
            "density_mult": max(0.55, min(1.35, float(target_density) / float(base_density))),
            "rest_bias": float(phrase_bar.rest_bias),
            "offbeat_push": float(phrase_bar.accent_push),
            "cadence_strength": float(phrase_bar.cadence_bias),
            "sustain_mult": float(phrase_bar.sustain_mult),
            "allow_fill": bool(phrase_bar.allow_fill),
        }
        if style == "supportive" and reference_guidance.available and conditioning is not None:
            intent = _apply_reference_groove_nudge(intent, reference_guidance, conditioning, bar)
            reference_guidance_applied = reference_guidance_applied or reference_guidance.should_apply_bar(bar)
        if style == "supportive" and conditioning is not None:
            intent = _apply_source_groove_intent_nudge(intent, conditioning=conditioning, context=context, bar=bar)
        root = mt.bass_root_midi(key, scale, deg, octave=2)
        ojp = oct_jump_p()
        if style in ("rhythmic", "fusion") and rng.random() < ojp:
            root = min(52, root + 12)
        elif style == "melodic" and rng.random() < min(0.22, ojp * 0.55):
            root = min(52, root + 12)

        chord2 = mt.chord_tones_midi(key, scale, deg, octave=2, seventh=False)
        r_default, t_default, f_default = chord2[0], chord2[1], chord2[2]
        r, t, f, passing_pcs, avoid_pcs, harm_conf = _bar_harmonic_pitches(context, bar, r_default, t_default, f_default)
        if conditioning is not None:
            cbar = conditioning.harmonic_bar(bar)
            if cbar is not None and cbar.target_pcs:
                cpitches = [_pc_to_bass_register(int(pc) % 12, octave=2) for pc in cbar.target_pcs]
                root_guess = _pc_to_bass_register(int(cbar.root_pc) % 12, octave=2)
                if len(cpitches) == 1:
                    r, t, f = root_guess, cpitches[0], cpitches[0]
                elif len(cpitches) == 2:
                    r, t, f = root_guess, cpitches[0], cpitches[1]
                else:
                    r, t, f = root_guess, cpitches[1], cpitches[2]
                passing_pcs = tuple(int(x) % 12 for x in cbar.passing_pcs)
                avoid_pcs = tuple(int(x) % 12 for x in cbar.avoid_pcs)
                harm_conf = max(harm_conf, float(cbar.confidence))
        seg = segment_per_bar[bar] if bar < len(segment_per_bar) else segment_per_bar[-1]
        # Chord-targeted structural priority: root -> fifth -> third.
        r, f, t = _bar_harmonic_priority_pitches(seg, prev_pitch=prev_structural_pitch, lo=30, hi=62)
        passing_pcs = tuple(seg["passing_pcs"]) if seg["passing_pcs"] else passing_pcs
        avoid_pcs = tuple(seg["avoid_pcs"]) if seg["avoid_pcs"] else avoid_pcs
        harm_conf = max(harm_conf, float(seg["confidence"]))
        if conditioning is not None:
            bar_anchor = float(conditioning.bar_start_anchor_sec)
        else:
            bar_anchor = float(context.bar_start_anchor_sec) if context is not None else 0.0
        bar_t0 = bar_anchor + (bar * 4 * spb)
        bar_t1 = (bar + 1) * 4 * spb
        bar_t1 = bar_anchor + ((bar + 1) * 4 * spb)
        bar_density = density_for_bar(context, bar) if context else 0.0

        def emit_note(
            *,
            pitch: int,
            start: float,
            end: float,
            vel: int,
            slot: int,
            is_structural: bool,
            traits_fallback: BassProfile,
        ) -> None:
            e2, v2 = shape_note(
                start=start,
                end=end,
                velocity=vel,
                slot=slot,
                role=str(phrase_bar.role),
                style=style,
                is_structural=is_structural,
                cadence_bias=float(phrase_bar.cadence_bias),
                sustain_mult=float(phrase_bar.sustain_mult),
                bar_end=bar_t1,
            )
            p2 = _clamp_pitch(pitch, 0, 127, use_profile=use_profile, traits=traits or traits_fallback)
            inst.notes.append(pretty_midi.Note(velocity=v2, pitch=p2, start=start, end=e2))

        if style == "supportive":
            release_main_hits = 0
            if soul_preset:
                leg0 = (0.88 + (salt % 9) / 100.0) * art
                leg1 = (0.84 + ((salt >> 3) % 9) / 100.0) * art
                ghost_p = 0.03
                for beat, leg in ((0.0, leg0), (2.0, leg1)):
                    leg_use = leg * float(intent["sustain_mult"])
                    slot_b = int(beat * 4)  # beat 1→0, beat 3→8 in sixteenth grid
                    late = spb * (0.011 + (salt % 3) * 0.0025)
                    late += spb * 0.006 if beat == 2.0 else 0.0
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
                    elif _source_groove_rhythm(conditioning, context):
                        sk = source_kick_weight(conditioning, bar, slot_b)
                        late += sixteenth * 0.09 * sk
                    t_j = rng.uniform(0, 0.006) * spb
                    t0 = bar_t0 + beat * spb + late + t_j
                    t1 = t0 + spb * leg_use
                    p = root
                    if beat == 2.0:
                        m = (salt + bar * 3) % 7
                        if _drum_anchor_ctx(context):
                            kw8 = drum_kick_weight(context, bar, 8)
                        elif _source_groove_rhythm(conditioning, context):
                            kw8 = source_kick_weight(conditioning, bar, 8)
                        else:
                            kw8 = 0.0
                        grid_ref = _drum_anchor_ctx(context) or _source_groove_rhythm(conditioning, context)
                        if m == 1 and not (grid_ref and kw8 > 0.42):
                            p = t
                        elif m == 5 and not (grid_ref and kw8 > 0.5):
                            p = f
                        elif grid_ref and kw8 > 0.38 and rng.random() < 0.55 * (kick_lock_m if _drum_anchor_ctx(context) else 0.88):
                            p = f if rng.random() < 0.45 else root
                    vb = (88, 80)
                    vel = max(72, min(100, (vb[0] if beat == 0 else vb[1]) + rng.randint(-5, 5)))
                    emit_note(
                        pitch=p,
                        start=t0,
                        end=t1,
                        vel=vel,
                        slot=slot_b,
                        is_structural=(beat == 0.0),
                        traits_fallback=bass_profiles["pino"],
                    )
                    if beat == 0.0:
                        prev_structural_pitch = p
            else:
                if use_profile and traits:
                    ghost_p = min(0.2, 0.035 + traits["ghost_note_bias"] * 0.42)
                elif bias_traits:
                    ghost_p = min(0.2, 0.035 + bias_traits["ghost_note_bias"] * 0.42)
                else:
                    ghost_p = 0.14
                anchor_slot8_emitted = False
                release_main_late_emitted = False
                pat = tuple(
                    sorted({int(x) for x in phrase_bar.slots if 0 <= int(x) <= 15}),
                )
                if source_minor_riff_slots:
                    pat = source_minor_riff_slots[bar] if bar < len(source_minor_riff_slots) else source_minor_riff_slots[-1]
                if not pat:
                    pat = (0, 8)
                pr_role = str(phrase_bar.role)
                if pr_role == "push":
                    pat = _supportive_thin_push_slots(pat, salt, bar)
                tone_path = tuple(phrase_bar.tone_path) if phrase_bar.tone_path else (0, 0)
                reg_sh = int(phrase_bar.register_shift)
                rest_b = float(intent["rest_bias"])
                d_drum = density_for_bar(context, bar) if context else 0.0
                for j, s in enumerate(pat):
                    if _supportive_slot_may_apply_rest_drop(role=pr_role, s=s, rest_bias=rest_b, rng=rng):
                        continue
                    if _drum_anchor_ctx(context):
                        if not _rhythmic_drum_slot_keep(
                            context, bar, s, d_drum=d_drum, kick_lock=kick_lock_m, restraint=restraint_m, rng=rng
                        ):
                            continue
                    elif _source_groove_rhythm(conditioning, context):
                        if not _rhythmic_source_slot_keep(conditioning, bar, s, rng=rng):
                            continue
                        sk_av = source_kick_weight(conditioning, bar, s)
                        sn_av = source_snare_weight(conditioning, bar, s)
                        if (
                            s % 4 != 0
                            and sn_av > 0.52
                            and sk_av < 0.30
                            and rng.random() < 0.16 + sn_av * 0.28
                        ):
                            continue
                    elif context and s % 4 != 0:
                        prs = slot_pressure(context, bar, s)
                        if prs > 0.6 and rng.random() < 0.22 + prs * 0.3:
                            continue
                    tidx = int(tone_path[j % max(1, len(tone_path))])
                    if s == 8:
                        p = _supportive_unified_slot8_pitch(
                            planned_tidx=tidx,
                            pr_role=pr_role,
                            r=r,
                            f=f,
                            t=t,
                            cadence_strength=float(intent["cadence_strength"]),
                            rng=rng,
                        )
                    else:
                        p = _deg_pitch_map(r, f, t, tidx)
                    if s not in (0, 8):
                        p = _pick_harmonic_style_pitch(
                            style="supportive",
                            role=pr_role,
                            slot=s,
                            root_pitch=r,
                            fifth_pitch=f,
                            third_pitch=t,
                            passing_pcs=passing_pcs,
                            target_pcs=tuple(seg["target_pcs"]) if seg["target_pcs"] else (),
                            avoid_pcs=avoid_pcs,
                            harm_conf=float(harm_conf),
                            prev_pitch=prev_structural_pitch,
                            rng=rng,
                        )
                    if source_minor_riff_slots:
                        p = _supportive_source_minor_pitch(
                            slot=s,
                            note_index=j,
                            role=pr_role,
                            root_pitch=r,
                            fifth_pitch=f,
                            third_pitch=t,
                            avoid_pcs=avoid_pcs,
                            scale=scale,
                            rng=rng,
                        )
                    if reg_sh == 12 and pr_role == "push" and s not in (0, 8) and p == r and rng.random() < 0.35:
                        p = min(58, r + 12)
                    pass_p = 0.0
                    if (
                        passing_pcs
                        and float(harm_conf) >= 0.12
                        and s not in (0, 8)
                        and not (pr_role == "release" and s >= 12)
                    ):
                        pass_p = min(0.3, 0.06 + 0.5 * float(harm_conf))
                    if pr_role == "push" and pass_p > 0.0:
                        pass_p *= 0.72
                    if pass_p > 0.0 and rng.random() < pass_p:
                        p = _pc_to_bass_register(rng.choice(passing_pcs), octave=2)
                    if avoid_pcs and (p % 12) in set(avoid_pcs):
                        p = r
                    obp = float(intent["offbeat_push"])
                    late = sixteenth * 0.055 * obp * (0.45 if s % 4 == 0 else 1.0)
                    if context:
                        dbar = density_for_bar(context, bar)
                        late += spb * 0.0075 * min(1.0, dbar / 8.0)
                    if _drum_anchor_ctx(context):
                        kw = drum_kick_weight(context, bar, s)
                        late += sixteenth * (0.14 * kw - 0.05 * drum_kick_weight(context, bar, (s + 3) % 16)) * kick_lock_m
                        late += sixteenth * 0.06 * bounce_m * (0.55 if player_key == "bootsy" else 0.35)
                    elif _source_groove_rhythm(conditioning, context):
                        sk = source_kick_weight(conditioning, bar, s)
                        late += sixteenth * 0.1 * sk
                    t_j = rng.uniform(0, 0.018) * spb
                    t0 = bar_t0 + s * sixteenth + late + t_j
                    if pr_role == "anchor" and s in (0, 8):
                        t0 += spb * rng.uniform(-0.0018, 0.0038) * (1.0 + 0.4 * (1.0 if s == 8 else 0.0))
                    if j + 1 < len(pat):
                        span_16 = max(0.45, float(pat[j + 1] - s))
                    else:
                        span_16 = max(0.5, 16.0 - float(s))
                    leg_use = rng.uniform(0.86, 0.95) * art * float(intent["sustain_mult"])
                    if _drum_anchor_ctx(context) and d_drum > 11.0:
                        leg_use *= 1.0 + 0.028 * restraint_m * (1.1 if player_key == "pino" else 1.0)
                    elif _source_groove_rhythm(conditioning, context):
                        mp = _source_bar_mean_pressure(conditioning, bar)
                        gc = _source_bar_groove_conf(conditioning, bar)
                        if mp > 0.44:
                            leg_use *= 1.0 + 0.018 * max(0.12, min(1.0, gc)) * (mp - 0.44)
                        elif mp < 0.22:
                            leg_use *= 1.0 - 0.012 * max(0.12, min(1.0, gc)) * (0.22 - mp)
                    if d_drum > 14.0:
                        leg_use = min(0.97, leg_use * 1.02)
                    if pr_role == "release" and s == 14:
                        span_16 = max(span_16, rng.uniform(1.05, 1.3))
                    t1 = min(bar_t1 - 1e-4, t0 + sixteenth * span_16 * 0.9 * leg_use)
                    if t1 <= t0:
                        t1 = min(bar_t1 - 1e-4, t0 + sixteenth * 0.55)
                    vel = max(72, min(100, (92 if s == 0 else 80) + rng.randint(-5, 5)))
                    if s not in (0, 4, 8, 12) and s % 4 != 0:
                        vel = max(70, min(98, vel - 4))
                    if pr_role == "anchor" and s in (0, 8):
                        vel = max(72, min(100, vel + (1 if (salt + bar + s) % 3 == 0 else 0) + (1 if s == 8 else 0)))
                    emit_note(
                        pitch=p,
                        start=t0,
                        end=t1,
                        vel=vel,
                        slot=s,
                        is_structural=(s == 0),
                        traits_fallback=bass_profiles["pino"],
                    )
                    if pr_role == "anchor" and s == 8:
                        anchor_slot8_emitted = True
                    if pr_role == "release":
                        release_main_hits += 1
                        if s >= 12:
                            release_main_late_emitted = True
                    if s == 0:
                        prev_structural_pitch = p
                if pr_role == "anchor" and 8 in pat and not anchor_slot8_emitted:
                    tidx_a8 = int(tone_path[min(1, max(0, len(tone_path) - 1))])
                    p_a8 = _supportive_unified_slot8_pitch(
                        planned_tidx=tidx_a8,
                        pr_role=pr_role,
                        r=r,
                        f=f,
                        t=t,
                        cadence_strength=float(intent["cadence_strength"]),
                        rng=rng,
                    )
                    if avoid_pcs and (p_a8 % 12) in set(avoid_pcs):
                        p_a8 = r
                    a8_late = sixteenth * 0.03 * float(intent["offbeat_push"])
                    a8_t0 = bar_t0 + 8 * sixteenth + a8_late + rng.uniform(0.0, 0.004) * spb
                    a8_t1 = min(bar_t1 - 1e-4, a8_t0 + sixteenth * rng.uniform(2.1, 2.9) * float(intent["sustain_mult"]))
                    if a8_t1 > a8_t0:
                        emit_note(
                            pitch=p_a8,
                            start=a8_t0,
                            end=a8_t1,
                            vel=rng.randint(78, 90),
                            slot=8,
                            is_structural=False,
                            traits_fallback=bass_profiles["pino"],
                        )
            # Optional pickup / extra tail: phrase plan already had slot 14 on release — avoid duplicate 14;
            # add pickup on 12 when the grid omits it; add tail if the plan thinned 14.
            if (not soul_preset) and str(phrase_bar.role) == "release":
                is_cadence_bar = ((bar + 1) % 4) == 0
                cadence_bias = float(phrase_bar.cadence_bias)
                pat_rel = tuple(
                    sorted({int(x) for x in phrase_bar.slots if 0 <= int(x) <= 15}),
                ) or (0, 8, 14)
                # Cadence: thin stacked fills when the main line + phrase already carries weight.
                stack_pen = min(0.42, 0.1 * max(0, int(release_main_hits) - 1) + (0.12 if 14 in pat_rel else 0.0))
                sw_cad = 0.0
                if _drum_anchor_ctx(context):
                    sw_cad = (drum_snare_weight(context, bar, 4) + drum_snare_weight(context, bar, 12)) * 0.5
                elif _source_groove_rhythm(conditioning, context):
                    sw_cad = (
                        source_snare_weight(conditioning, bar, 4) + source_snare_weight(conditioning, bar, 12)
                    ) * 0.5
                stack_pen = min(0.52, stack_pen + 0.1 * min(1.0, sw_cad))
                had_pick = False
                had_tail = False
                pickup_raw = 0.42 + 0.42 * cadence_bias + (0.20 if is_cadence_bar else 0.0)
                pickup_prob = min(0.97 if is_cadence_bar else 0.9, pickup_raw)
                if is_cadence_bar:
                    pickup_prob = max(0.92, pickup_prob)
                pickup_prob *= 1.0 - stack_pen
                if 12 not in pat_rel and rng.random() < pickup_prob:
                    pickup_slot = 12
                    pickup_push = 0.0
                    if _drum_anchor_ctx(context):
                        pickup_push += sixteenth * 0.07 * drum_kick_weight(context, bar, pickup_slot) * kick_lock_m
                    elif _source_groove_rhythm(conditioning, context):
                        pickup_push += sixteenth * 0.055 * source_kick_weight(conditioning, bar, pickup_slot)
                    pt0 = bar_t0 + pickup_slot * sixteenth + pickup_push + rng.uniform(0.0, 0.006) * spb
                    pt1 = min(bar_t1 - 1e-4, pt0 + sixteenth * rng.uniform(0.7, 0.98))
                    if pt1 > pt0:
                        had_pick = True
                        pp = f if rng.random() < (0.68 if is_cadence_bar else 0.56) else r
                        if avoid_pcs and (pp % 12) in set(avoid_pcs):
                            pp = r
                        emit_note(
                            pitch=pp,
                            start=pt0,
                            end=pt1,
                            vel=rng.randint(72, 86),
                            slot=pickup_slot,
                            is_structural=False,
                            traits_fallback=bass_profiles["pino"],
                        )

                tail_base = (0.72 * cadence_bias) + (0.2 if is_cadence_bar else 0.0)
                if is_cadence_bar:
                    tail_base += 0.16
                tail_prob = min(0.99 if is_cadence_bar else 0.96, tail_base)
                if is_cadence_bar:
                    tail_prob = max(0.95, tail_prob)
                tail_prob *= 1.0 - (0.25 * float(had_pick)) - (0.15 * min(0.5, sw_cad))
                tail_prob *= 1.0 - (0.12 * int(release_main_hits) / 5.0)
                tail_prob = max(0.0, min(0.99, tail_prob))
                if 14 not in pat_rel and rng.random() < tail_prob:
                    cad_slot = 14 if is_cadence_bar else (14 if rng.random() < 0.75 else 13)
                    tail_push = 0.0
                    if _drum_anchor_ctx(context):
                        tail_push += sixteenth * 0.09 * drum_kick_weight(context, bar, cad_slot) * kick_lock_m
                    elif _source_groove_rhythm(conditioning, context):
                        tail_push += sixteenth * 0.06 * source_kick_weight(conditioning, bar, cad_slot)
                    ct0 = bar_t0 + cad_slot * sixteenth + tail_push + rng.uniform(0.0, 0.006) * spb
                    ct1 = min(
                        bar_t1 - 1e-4,
                        ct0 + sixteenth * rng.uniform(1.0 if is_cadence_bar else 0.92, 1.34 if is_cadence_bar else 1.22),
                    )
                    if ct1 > ct0:
                        had_tail = True
                        cp = r if rng.random() < (0.84 if is_cadence_bar else 0.66) else f
                        if avoid_pcs and (cp % 12) in set(avoid_pcs):
                            cp = r
                        emit_note(
                            pitch=cp,
                            start=ct0,
                            end=ct1,
                            vel=rng.randint(80, 94),
                            slot=cad_slot,
                            is_structural=True,
                            traits_fallback=bass_profiles["pino"],
                        )
                if float(tempo) <= 92.0 and (not release_main_late_emitted) and (not had_tail):
                    floor_slot = 14
                    floor_push = 0.0
                    if _drum_anchor_ctx(context):
                        floor_push += sixteenth * 0.06 * drum_kick_weight(context, bar, floor_slot) * kick_lock_m
                    elif _source_groove_rhythm(conditioning, context):
                        floor_push += sixteenth * 0.05 * source_kick_weight(conditioning, bar, floor_slot)
                    ft0 = bar_t0 + floor_slot * sixteenth + floor_push + rng.uniform(0.0, 0.004) * spb
                    ft1 = min(bar_t1 - 1e-4, ft0 + sixteenth * rng.uniform(1.06, 1.28))
                    if ft1 > ft0:
                        fp = r if rng.random() < 0.82 else f
                        if avoid_pcs and (fp % 12) in set(avoid_pcs):
                            fp = r
                        emit_note(
                            pitch=fp,
                            start=ft0,
                            end=ft1,
                            vel=rng.randint(78, 90),
                            slot=floor_slot,
                            is_structural=True,
                            traits_fallback=bass_profiles["pino"],
                        )
                if has_custom_progression and bar + 1 < len(segment_per_bar):
                    next_seg = segment_per_bar[bar + 1]
                    next_root_pc = int(next_seg["root_pc"]) % 12
                    if next_root_pc != int(seg["root_pc"]) % 12:
                        ap = _approach_pitch_to_next_root(next_root_pc, current_pitch=r)
                        at0 = bar_t1 - (sixteenth * 0.72) + rng.uniform(0.0, 0.004) * spb
                        at1 = min(bar_t1 - 1e-4, at0 + sixteenth * rng.uniform(0.45, 0.62))
                        if at1 > at0:
                            emit_note(
                                pitch=ap,
                                start=at0,
                                end=at1,
                                vel=rng.randint(62, 74),
                                slot=15,
                                is_structural=False,
                                traits_fallback=bass_profiles["pino"],
                            )
            ghost_use = ghost_p * ghost_eligibility(
                style=style,
                role=str(phrase_bar.role),
                cadence_bias=float(phrase_bar.cadence_bias),
                bar_density=float(bar_density),
            )
            if str(phrase_bar.role) == "release":
                ghost_use *= 0.38
            if _drum_anchor_ctx(context):
                dd = density_for_bar(context, bar)
                ghost_use *= 1.0 - 0.5 * min(1.0, dd / 16.0)
                sw_back = (drum_snare_weight(context, bar, 4) + drum_snare_weight(context, bar, 12)) * 0.5
                if str(phrase_bar.role) == "release":
                    ghost_use *= 1.0 - 0.22 * min(1.0, sw_back)
                ghost_use += (0.055 if player_key == "bootsy" else 0.035 if player_key == "marcus" else 0.02) * min(
                    1.0, sw_back
                )
                ghost_use = max(0.02, min(0.22, ghost_use * restraint_m))
            if rng.random() < ghost_use:
                ghost_b = rng.choice((1.0, 3.0))
                gt0 = bar_t0 + ghost_b * spb
                gt1 = gt0 + spb * rng.uniform(0.12, 0.22)
                if gt1 <= bar_t1:
                    gp = rng.choice((root, f))
                    if avoid_pcs and (gp % 12) in set(avoid_pcs):
                        gp = root
                    emit_note(
                        pitch=gp,
                        start=gt0,
                        end=gt1,
                        vel=rng.randint(44, 62),
                        slot=int(ghost_b * 4),
                        is_structural=False,
                        traits_fallback=bass_profiles["pino"],
                    )
            if _drum_anchor_ctx(context):
                sw = (drum_snare_weight(context, bar, 4) + drum_snare_weight(context, bar, 12)) * 0.5
                ans_p = min(0.42, (0.1 + 0.28 * sw) * (0.55 if player_key == "pino" else 1.0) / max(0.85, restraint_m))
                if str(phrase_bar.role) == "release":
                    ans_p *= 0.12
                if sw > 0.32 and rng.random() < ans_p:
                    ah = 6 if drum_snare_weight(context, bar, 4) >= drum_snare_weight(context, bar, 12) else 14
                    if drum_kick_weight(context, bar, ah) < 0.38:
                        t0a = bar_t0 + ah * sixteenth + rng.uniform(0, 0.008) * spb
                        t1a = t0a + sixteenth * rng.uniform(0.55, 0.95)
                        if t1a <= bar_t1:
                            ap = f if rng.random() < 0.55 else root
                            if avoid_pcs and (ap % 12) in set(avoid_pcs):
                                ap = root
                            emit_note(
                                pitch=ap,
                                start=t0a,
                                end=t1a,
                                vel=rng.randint(38, 58),
                                slot=ah,
                                is_structural=False,
                                traits_fallback=bass_profiles["pino"],
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
            rest = (bias_traits["rest_preference"] if bias_traits else 0.0) + (0.45 * float(intent["rest_bias"]))
            for i in range(8):
                if context:
                    slot = min(15, i * 2)
                    if _drum_anchor_ctx(context):
                        kw = drum_kick_weight(context, bar, slot)
                        pr = slot_pressure(context, bar, slot)
                        nk = drum_kick_emphasis_max(context, bar, slot, 2)
                        if i in (0, 4):
                            if kw < 0.18 and pr > 0.72 and rng.random() < 0.22 / kick_lock_m:
                                continue
                        else:
                            skip_p = 0.18 + 0.32 * pr - 0.36 * max(kw, nk) - 0.04 * bounce_m
                            skip_p = max(0.0, min(0.62, skip_p * restraint_m))
                            if rng.random() < skip_p:
                                continue
                    elif (
                        i not in (0, 4)
                        and slot_pressure(context, bar, slot) > 0.64
                        and rng.random() < 0.42
                    ):
                        continue
                if use_profile and rest > 0.32:
                    d = (salt + bar * 2 + i * 3) % 8
                    if i not in (0, 4) and d < int(rest * 5):
                        continue
                idx = shape[i]
                pitch = _deg_pitch_map(r, f, t, idx)
                if i not in (0, 4):
                    pitch = _pick_harmonic_style_pitch(
                        style="melodic",
                        role=str(phrase_bar.role),
                        slot=min(15, i * 2),
                        root_pitch=r,
                        fifth_pitch=f,
                        third_pitch=t,
                        passing_pcs=passing_pcs,
                        target_pcs=tuple(seg["target_pcs"]) if seg["target_pcs"] else (),
                        avoid_pcs=avoid_pcs,
                        harm_conf=float(harm_conf),
                        prev_pitch=prev_structural_pitch,
                        rng=rng,
                    )
                rb_use = rb
                if _drum_anchor_ctx(context) and i in (0, 4):
                    kw_m = drum_kick_weight(context, bar, min(15, i * 2))
                    rb_use = min(0.98, rb + 0.12 * kw_m * kick_lock_m)
                if i in (0, 4) and rng.random() < rb_use:
                    pitch = r
                elif rng.random() < 0.06 * (0.4 if use_profile and traits else 1.0):
                    pitch = min(pitch + 12, 74)
                if intent["role"] == "release" and i >= 6 and rng.random() < (0.55 * float(intent["cadence_strength"])):
                    pitch = r if i == 6 else f
                if avoid_pcs and (pitch % 12) in set(avoid_pcs):
                    pitch = r
                obm = bias_traits["offbeat_bias"] if bias_traits else 0.0
                ph = eighth * 0.035 * obm * (0.45 if i in (0, 4) else 1.0)
                ph += sixteenth * 0.1 * float(intent["offbeat_push"]) * (0.35 if i in (0, 4) else 1.0)
                if context:
                    ph += sixteenth * 0.05 * min(1.0, context.syncopation_score) * (0.4 if i in (0, 4) else 1.0)
                t0 = bar_t0 + i * eighth + ph + rng.uniform(0, 0.012) * spb
                t1 = t0 + eighth * dur_j(0.9)
                vel = max(70, min(100, (88 if i in (0, 4) else 76) + rng.randint(-8, 8)))
                emit_note(
                    pitch=pitch,
                    start=t0,
                    end=t1,
                    vel=vel,
                    slot=min(15, i * 2),
                    is_structural=(i in (0, 4)),
                    traits_fallback=bass_profiles["pino"],
                )
                if i in (0, 4):
                    prev_structural_pitch = pitch

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
            pat = tuple(phrase_bar.slots)
            p_root_off = offbeat_root_p()
            extras: list[int] = []
            if (
                (traits_engine["fill_activity"] > 0.52 if context else (use_profile and traits and traits["fill_activity"] > 0.52))
                and (salt + bar) % 2 == 1
                and 14 not in pat
                and len(pat) < traits_engine["density_ceiling"]
                and bool(intent["allow_fill"])
            ):
                if (not _drum_anchor_ctx(context)) or drum_kick_emphasis_max(context, bar, 12, 3) > 0.32:
                    extras.append(14)
            d_drum = density_for_bar(context, bar) if context else 0.0
            for k_i, s in enumerate(tuple(sorted(set(pat + tuple(extras))))):
                if _drum_anchor_ctx(context):
                    if not _rhythmic_drum_slot_keep(
                        context,
                        bar,
                        s,
                        d_drum=d_drum,
                        kick_lock=kick_lock_m,
                        restraint=restraint_m,
                        rng=rng,
                    ):
                        continue
                elif context and s % 4 != 0:
                    pr = slot_pressure(context, bar, s)
                    if pr > 0.6 and rng.random() < 0.28 + pr * 0.32:
                        continue
                obr = bias_traits["offbeat_bias"] if bias_traits else 0.0
                push = sixteenth * 0.02 * obr * (0.4 if s % 4 == 0 else 1.0)
                push += sixteenth * 0.08 * float(intent["offbeat_push"]) * (0.4 if s % 4 == 0 else 1.0)
                if context:
                    push += sixteenth * 0.045 * min(1.0, context.syncopation_score) * (0.45 if s % 4 == 0 else 1.0)
                if _drum_anchor_ctx(context):
                    push += sixteenth * 0.024 * bounce_m * drum_kick_weight(context, bar, s)
                    if player_key == "marcus":
                        push += sixteenth * 0.012 * drum_snare_weight(context, bar, (s + 15) % 16)
                t0 = bar_t0 + s * sixteenth + push + rng.uniform(0, 0.012) * spb
                t1 = t0 + sixteenth * dur_j(0.74)
                if s % 4 == 0:
                    pitch = r
                    vel = max(88, min(112, 96 + rng.randint(-8, 8)))
                    prev_structural_pitch = pitch
                else:
                    tone_path = tuple(phrase_bar.tone_path) if phrase_bar.tone_path else ()
                    tone_idx = tone_path[k_i % len(tone_path)] if tone_path else (s + salt) % 3
                    pitch = _deg_pitch_map(r, f, t, tone_idx)
                    if rng.random() < p_root_off:
                        pitch = r
                    else:
                        pitch = _pick_harmonic_style_pitch(
                            style="rhythmic",
                            role=str(phrase_bar.role),
                            slot=s,
                            root_pitch=r,
                            fifth_pitch=f,
                            third_pitch=t,
                            passing_pcs=passing_pcs,
                            target_pcs=tuple(seg["target_pcs"]) if seg["target_pcs"] else (),
                            avoid_pcs=avoid_pcs,
                            harm_conf=float(harm_conf),
                            prev_pitch=prev_structural_pitch,
                            rng=rng,
                        )
                    vel = max(74, min(104, 84 + rng.randint(-8, 8)))
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
                    and rng.random() < traits["octave_jump_bias"] * oj_mul
                ):
                    if r + 12 <= traits["register_max"]:
                        pitch = r + 12
                if avoid_pcs and (pitch % 12) in set(avoid_pcs):
                    pitch = r
                emit_note(
                    pitch=pitch,
                    start=t0,
                    end=t1,
                    vel=vel,
                    slot=s,
                    is_structural=(s % 4 == 0),
                    traits_fallback=bass_profiles["bootsy"],
                )

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
                t0 = bar_t0 + s0 * sixteenth + rng.uniform(0, 0.014) * spb
                if _drum_anchor_ctx(context):
                    t0 += sixteenth * 0.05 * drum_kick_weight(context, bar, min(15, s0)) * kick_lock_m
                t1 = t0 + dur_s * sixteenth * dur_j(0.88)
                if t1 > bar_t1:
                    t1 = bar_t1 - 1e-4
                if t1 <= t0:
                    continue
                v = max(44, min(118, vel + rng.randint(-8, 8)))
                emit_note(
                    pitch=int(pitch),
                    start=t0,
                    end=t1,
                    vel=v,
                    slot=min(15, int(s0)),
                    is_structural=(s0 % 4 == 0),
                    traits_fallback=bass_profiles["pino"],
                )

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
            pat_slots = tuple(phrase_bar.slots)
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
                            return rng.random() < min(0.96, 0.82 + 0.12 * kw * kick_lock_m)
                        if pr > 0.58 and nk < 0.2:
                            cut = 0.26 + 0.22 * pr + (0.12 if dd_f > 12 else 0.0)
                            cut = min(0.68, cut * restraint_m)
                            return rng.random() > cut
                        return rng.random() < 0.88

                    pat_slots = tuple(x for x in pat_slots if _keep_fusion_slot(x))
                    if not pat_slots:
                        pat_slots = (0, 8)
                else:
                    pat_slots = tuple(
                        x
                        for x in pat_slots
                        if slot_pressure(context, bar, x) < 0.66 or x in (0, 8) or rng.random() > 0.38
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
                and bool(intent["allow_fill"])
            ):
                if (not _drum_anchor_ctx(context)) or drum_kick_emphasis_max(context, bar, 10, 3) > 0.28:
                    fill_ex.append(11)
            if str(phrase_bar.role) == "release":
                # Release bars should resolve, not keep developing.
                fill_ex = []
                if 14 not in pat_slots:
                    pat_slots = tuple(sorted(set(pat_slots + (14,))))
            for k_i, s in enumerate(tuple(sorted(set(pat_slots + tuple(fill_ex))))):
                tone_path = tuple(phrase_bar.tone_path) if phrase_bar.tone_path else ()
                tone_idx = tone_path[k_i % len(tone_path)] if tone_path else (phase + k_i) % len(pit_cycle)
                pitch = pit_cycle[tone_idx % len(pit_cycle)] + int(phrase_bar.register_shift)
                if pitch > 60:
                    pitch -= 12
                if pitch < 34:
                    pitch += 12
                if s in (0, 8):
                    pitch = r
                    prev_structural_pitch = pitch
                elif str(phrase_bar.role) == "release" and s >= 12:
                    pitch = r if rng.random() < 0.7 else f
                elif True:
                    pitch = _pick_harmonic_style_pitch(
                        style="fusion",
                        role=str(phrase_bar.role),
                        slot=s,
                        root_pitch=r,
                        fifth_pitch=f,
                        third_pitch=t,
                        passing_pcs=passing_pcs,
                        target_pcs=tuple(seg["target_pcs"]) if seg["target_pcs"] else (),
                        avoid_pcs=avoid_pcs,
                        harm_conf=float(harm_conf),
                        prev_pitch=prev_structural_pitch,
                        rng=rng,
                    )
                if rng.random() < 0.07 * (1.4 if use_profile and traits and traits["fill_activity"] > 0.55 else 1.0):
                    pitch = min(62, max(34, pitch + rng.choice((0, 12, -12))))
                if avoid_pcs and (pitch % 12) in set(avoid_pcs):
                    pitch = r
                obf = bias_traits["offbeat_bias"] if bias_traits else 0.0
                push_f = sixteenth * 0.018 * obf * (0.45 if s % 4 == 0 else 1.0)
                push_f += sixteenth * 0.08 * float(intent["offbeat_push"]) * (0.4 if s % 4 == 0 else 1.0)
                if _drum_anchor_ctx(context):
                    push_f += sixteenth * 0.02 * bounce_m * drum_kick_weight(context, bar, s)
                    if player_key == "marcus":
                        push_f += sixteenth * 0.01 * drum_snare_weight(context, bar, (s + 14) % 16)
                t0 = bar_t0 + s * sixteenth + push_f + rng.uniform(0, 0.014) * spb
                t1 = t0 + sixteenth * dur_j(0.72)
                if t1 > bar_t1:
                    t1 = bar_t1 - 1e-4
                if t1 <= t0:
                    continue
                vel = max(76, min(104, (92 if s % 4 == 0 else 80) + rng.randint(-8, 8)))
                emit_note(
                    pitch=pitch,
                    start=t0,
                    end=t1,
                    vel=vel,
                    slot=s,
                    is_structural=(s in (0, 8)),
                    traits_fallback=bass_profiles["marcus"],
                )

    # Keep rendered note ends within an exact timeline cap for deterministic tests.
    total_len = round(float(bar_anchor + (bar_count * 4 * spb)), 6)
    hard_cap = max(0.0, total_len - 1e-4)
    for note in inst.notes:
        if note.end > hard_cap:
            note.end = hard_cap
        if note.start >= note.end:
            note.start = max(0.0, note.end - (sixteenth * 0.35))

    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    midi_bytes = buf.getvalue()
    if return_performance_notes:
        final_pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
        final_notes = sorted(
            (n for final_inst in final_pm.instruments for n in final_inst.notes),
            key=lambda n: (float(n.start), int(n.pitch), float(n.end), int(n.velocity)),
        )
        perf_notes = []
        for note in final_notes:
            rel_start = max(0.0, float(note.start) - float(bar_anchor))
            bar_idx = max(0, min(max(1, bar_count) - 1, int(rel_start // (4.0 * spb))))
            bar_t0 = float(bar_anchor) + (bar_idx * 4.0 * spb)
            slot_idx = max(0, min(15, int(round((float(note.start) - bar_t0) / sixteenth))))
            phrase_bar = phrase_plan[bar_idx] if bar_idx < len(phrase_plan) else phrase_plan[-1]
            perf_notes.append(
                BassPerformanceNote(
                    pitch=int(note.pitch),
                    velocity=int(note.velocity),
                    start=float(note.start),
                    end=float(note.end),
                    articulation="normal",
                    role=str(phrase_bar.role),
                    bar_index=int(bar_idx),
                    slot_index=int(slot_idx),
                    source="baseline",
                    confidence=None,
                )
            )
        perf_notes = list(
            infer_bass_articulations(
                tuple(perf_notes),
                tempo=tempo,
                style=style,
                source="baseline",
            )
        )
    preview = _preview(
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
    if reference_guidance_applied and conditioning is not None:
        preview += f" (ref groove: {conditioning.groove_profile.pocket_feel}, conf {conditioning.groove_profile.confidence:.2f})"
    if return_performance_notes:
        return midi_bytes, preview, tuple(perf_notes)
    return midi_bytes, preview
