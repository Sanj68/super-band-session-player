"""Bass Phrase Engine v2: kick-aware phrase planning path."""

from __future__ import annotations

import io
import random
from typing import Any

import pretty_midi

from app.services.bass_performance import BassPerformanceNote, infer_bass_articulations
from app.services.conditioning import (
    UnifiedConditioning,
    has_source_groove,
    source_kick_weight,
    source_slot_pressure,
    source_snare_weight,
)
from app.services.bass_vocabulary.paul_chambers import (
    get_chromatic_approaches,
    get_walking_cell,
    normalize_chord_quality,
)
from app.services.session_context import (
    SessionAnchorContext,
    drum_kick_weight,
    drum_snare_weight,
    slot_pressure,
)
from app.services.style_adapter import BASS_STYLE_ADAPTER
from app.utils import music_theory as mt

_BASS_STYLES = frozenset({"supportive", "melodic", "rhythmic", "slap", "fusion"})
_BASS_INSTRUMENTS = frozenset({"finger_bass", "slap_bass", "synth_bass"})
_BASS_PLAYERS = frozenset({"bootsy", "marcus", "pino"}) | BASS_STYLE_ADAPTER.bass_player_ids()


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
    return s if s in _BASS_PLAYERS else None


def normalize_bass_instrument(bass_instrument: str | None) -> str:
    if bass_instrument is None:
        return "finger_bass"
    s = str(bass_instrument).strip().lower()
    return s if s in _BASS_INSTRUMENTS else "finger_bass"


def bass_midi_program(bass_instrument: str, bass_style: str) -> int:
    bi = normalize_bass_instrument(bass_instrument)
    if bi == "slap_bass":
        return 36
    if bi == "synth_bass":
        return 38
    if bass_style == "slap":
        return 36
    return 33


def _pc_to_bass_register(pc: int, *, octave: int = 2, lo: int = 30, hi: int = 62) -> int:
    note = mt.pc_to_midi_note(pc % 12, octave)
    while note < lo:
        note += 12
    while note > hi:
        note -= 12
    return max(lo, min(hi, note))


def _bar_role(bar: int, role_span: int) -> str:
    if role_span <= 2:
        return "anchor" if bar % 2 == 0 else "answer"
    cycle = ("anchor", "push", "anchor", "release")
    return cycle[bar % len(cycle)]


def _kick_guided_slots(ctx: SessionAnchorContext, bar: int) -> list[int]:
    out: list[int] = []
    for s in range(16):
        k = drum_kick_weight(ctx, bar, s)
        if k >= 0.34:
            out.append(s)
        elif s % 4 == 0 and k >= 0.2:
            out.append(s)
    return sorted(set(out))


def _source_guided_slots(conditioning: UnifiedConditioning | None, bar: int) -> list[int]:
    if not has_source_groove(conditioning):
        return []
    out: list[int] = []
    for s in range(16):
        k = source_kick_weight(conditioning, bar, s)
        p = source_slot_pressure(conditioning, bar, s)
        if k >= 0.34 or (s % 4 == 0 and p >= 0.42):
            out.append(s)
        elif s % 4 != 0 and k >= 0.24 and p >= 0.32:
            out.append(s)
    return sorted(set(out))


def _phrase_slots(role: str, kick_slots: list[int], *, kick_take: int = 3, extra_hits: int = 0) -> list[int]:
    if role == "anchor":
        base = [0, 8]
    elif role == "push":
        base = [0, 6, 10, 14]
    elif role == "release":
        base = [0, 8, 12]
    else:  # answer
        base = [0, 7, 12]
    if kick_slots:
        base.extend(kick_slots[: max(0, int(kick_take))])
    out = sorted(set(x for x in base if 0 <= x <= 15))
    if 0 not in out:
        out.insert(0, 0)
    max_hits = (4 if role in ("anchor", "release") else 5) + max(0, int(extra_hits))
    return out[:max_hits]


# ---- v0.3b reference-aware groove (BUILD_NOTES §6) -------------------------

# Evidence threshold below which we refuse to claim a reference lock —
# the v0.3a validation pack's low-confidence gate (0.45), validated there:
# wrong analyser answers self-report below it.
_REFERENCE_EVIDENCE_THRESHOLD = 0.45


def _resolve_reference_lock(
    lock_to_groove: float | None,
    conditioning: UnifiedConditioning | None,
) -> tuple[float, str]:
    """Resolve the lock-to-groove knob against available evidence.

    Returns (lock, state) where state is one of:
      "locked" — source groove present, evidence strong, lock > 0
      "off"    — evidence fine but the user dialled lock to 0
      "thin"   — source present but analysis confidence under the v0.3a
                 gate: fall back to the standard pocket, claim nothing
      "none"   — no reference source at all
    """
    if not has_source_groove(conditioning):
        return 0.0, "none"
    assert conditioning is not None
    evidence = 0.5 * (
        float(conditioning.tempo_confidence) + float(conditioning.beat_phase_confidence)
    )
    # Live bridge frames carry exact MIDI-derived groove with per-bar
    # confidence but no audio-analysis tempo/phase confidence — accept
    # whichever evidence channel is stronger.
    rows = [float(x) for x in (conditioning.source_groove_confidence or ()) if float(x) > 0.0]
    if rows:
        evidence = max(evidence, sum(rows) / len(rows))
    if evidence < _REFERENCE_EVIDENCE_THRESHOLD:
        return 0.0, "thin"
    lock = 0.5 if lock_to_groove is None else max(0.0, min(1.0, float(lock_to_groove)))
    return lock, ("locked" if lock > 0.0 else "off")


def _space_score(
    context: SessionAnchorContext | None,
    conditioning: UnifiedConditioning | None,
    bar: int,
    slot: int,
) -> float | None:
    """Per-slot space score: ``1 - snare - 0.5*pressure + kick`` (spec §6).

    Pocket GATING, not mimicking: high score = room for the bass (kick
    adjacency, no snare, low pressure). None when there is no rhythm
    evidence at all.
    """
    if has_source_groove(conditioning):
        return (
            1.0
            - source_snare_weight(conditioning, bar, slot)
            - (0.5 * source_slot_pressure(conditioning, bar, slot))
            + source_kick_weight(conditioning, bar, slot)
        )
    if context is not None and context.anchor_lane == "drums":
        return (
            1.0
            - drum_snare_weight(context, bar, slot)
            - (0.5 * slot_pressure(context, bar, slot))
            + drum_kick_weight(context, bar, slot)
        )
    return None


def _profile_float(profile: dict[str, Any], key: str, fallback: float) -> float:
    raw = profile.get(key)
    if isinstance(raw, int | float):
        return float(raw)
    return fallback


def _profile_int(profile: dict[str, Any], key: str, fallback: int) -> int:
    raw = profile.get(key)
    if isinstance(raw, int):
        return int(raw)
    return fallback


def _pino_phrase_slots(
    role: str,
    kick_slots: list[int],
    *,
    rng: random.Random | object,
    profile: dict[str, Any],
) -> list[int]:
    if role == "push":
        base = [0, 7, 12]
    elif role == "answer":
        base = [0, 10]
    elif role == "release":
        base = [0, 12] if rng.random() > _profile_float(profile, "rest_preference", 0.5) else [0]
    else:
        base = [0, 8]
    for slot in kick_slots:
        if slot != 0 and slot % 4 != 0 and rng.random() < _profile_float(profile, "offbeat_bias", 0.32):
            base.append(slot)
    out = sorted(set(x for x in base if 0 <= x <= 15))
    if 0 not in out:
        out.insert(0, 0)
    return out[: max(1, _profile_int(profile, "density_ceiling", 3))]


def _pick_pino_pitch(
    slot: int,
    role: str,
    *,
    root_pc: int,
    stable_pcs: list[int],
    passing_pcs: list[int],
    avoid_pcs: list[int],
    previous_pitch: int | None,
    profile: dict[str, Any],
    rng: random.Random | object,
) -> int:
    lo = _profile_int(profile, "register_min", 34)
    hi = _profile_int(profile, "register_max", 55)
    root_pitch = _pc_to_bass_register(root_pc, octave=2, lo=lo, hi=hi)
    if slot == 0 or role == "anchor":
        return root_pitch

    clean_stable = [pc for pc in stable_pcs if pc not in set(avoid_pcs)]
    if passing_pcs and slot % 4 != 0 and rng.random() < 0.28:
        target_pc = rng.choice(passing_pcs)
    else:
        target_pool = clean_stable or [root_pc]
        thirds_or_sevenths = [pc for pc in target_pool if (pc - root_pc) % 12 in (3, 4, 10, 11)]
        if thirds_or_sevenths and rng.random() < 0.62:
            target_pc = rng.choice(thirds_or_sevenths)
        else:
            target_pc = rng.choice(target_pool)

    center = previous_pitch if previous_pitch is not None else root_pitch
    pitch = _pc_to_bass_register(target_pc, octave=2, lo=lo, hi=hi)
    while abs(pitch - center) > 6 and pitch - 12 >= lo:
        pitch -= 12
    while abs(pitch - center) > 6 and pitch + 12 <= hi:
        pitch += 12
    if pitch % 12 in set(avoid_pcs):
        return root_pitch
    return pitch


def _harmonic_bar_plan(
    bar: int,
    *,
    key: str,
    scale: str,
    context: SessionAnchorContext | None,
    conditioning: UnifiedConditioning | None = None,
) -> tuple[int, list[int], list[int], list[int], float]:
    if context is not None and bar < len(context.harmonic_target_pcs_per_bar):
        root = int(context.harmonic_root_pc_per_bar[bar])
        stable = [int(x) for x in context.harmonic_target_pcs_per_bar[bar]]
        passing = [int(x) for x in context.harmonic_passing_pcs_per_bar[bar]]
        avoid = [int(x) for x in context.harmonic_avoid_pcs_per_bar[bar]]
        conf = float(context.harmonic_confidence_per_bar[bar]) if bar < len(context.harmonic_confidence_per_bar) else 0.2
        return root, stable, passing, avoid, conf
    harm = conditioning.harmonic_bar(bar) if conditioning is not None else None
    if harm is not None:
        return (
            int(harm.root_pc),
            [int(x) for x in harm.target_pcs],
            [int(x) for x in harm.passing_pcs],
            [int(x) for x in harm.avoid_pcs],
            float(harm.confidence),
        )

    key_pc = mt.key_root_pc(key)
    intervals = mt.scale_intervals(scale)
    root = key_pc
    stable = [key_pc, (key_pc + intervals[2 % len(intervals)]) % 12, (key_pc + intervals[4 % len(intervals)]) % 12]
    passing = [(key_pc + x) % 12 for x in intervals if ((key_pc + x) % 12) not in stable]
    avoid = [pc for pc in range(12) if pc not in [(key_pc + x) % 12 for x in intervals]]
    return root, stable, passing, avoid, 0.2


def _quality_from_targets(root_pc: int, stable_pcs: list[int]) -> str:
    intervals = {(int(pc) - int(root_pc)) % 12 for pc in stable_pcs}
    if 4 in intervals and 10 in intervals:
        return "dominant"
    if 4 in intervals:
        return "major"
    if 3 in intervals:
        return "minor"
    return "dominant"


def _pick_pitch(
    slot: int,
    role: str,
    *,
    root_pc: int,
    stable_pcs: list[int],
    passing_pcs: list[int],
    avoid_pcs: list[int],
    conf: float,
    rng: random.Random | object = random,
) -> int:
    strong = slot % 4 == 0
    if strong or role == "anchor":
        return _pc_to_bass_register(root_pc, octave=2)
    if passing_pcs and rng.random() < min(0.5, 0.15 + 0.5 * conf):
        return _pc_to_bass_register(rng.choice(passing_pcs), octave=2)
    pick_pc = rng.choice(stable_pcs or [root_pc])
    if pick_pc in avoid_pcs:
        pick_pc = root_pc
    return _pc_to_bass_register(pick_pc, octave=2)


def generate_bass_phrase_v2(
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
    conditioning: UnifiedConditioning | None = None,
    seed: int | None = None,
    return_performance_notes: bool = False,
    lock_to_groove: float | None = None,
) -> tuple[bytes, str] | tuple[bytes, str, tuple[BassPerformanceNote, ...]]:
    rng = random.Random(seed) if seed is not None else random
    style = normalize_bass_style(bass_style)
    player = normalize_bass_player(bass_player)
    bi = normalize_bass_instrument(bass_instrument)
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    inst = pretty_midi.Instrument(program=bass_midi_program(bi, style), name="Bass")
    spb = 60.0 / float(tempo)
    sixteenth = spb / 4.0
    bar_anchor = float(context.bar_start_anchor_sec) if context is not None else 0.0
    role_span = 4 if bar_count >= 4 else 2
    perf_notes: list[BassPerformanceNote] = []
    player_persona = BASS_STYLE_ADAPTER.bass_persona(player) if player is not None else None
    player_profile_raw = player_persona.get("profile") if player_persona is not None else None
    player_profile: dict[str, Any] = dict(player_profile_raw) if isinstance(player_profile_raw, dict) else {}

    if player == "paul_chambers":
        harmonic = [
            _harmonic_bar_plan(bar, key=key, scale=scale, context=context, conditioning=conditioning)
            for bar in range(max(1, bar_count))
        ]
        for bar, (root_pc, stable_pcs, _passing_pcs, _avoid_pcs, _conf) in enumerate(harmonic):
            role = _bar_role(bar, role_span)
            root_pitch = _pc_to_bass_register(root_pc, octave=2, lo=31, hi=50)
            quality = normalize_chord_quality(_quality_from_targets(root_pc, stable_pcs))
            cell = get_walking_cell(root_pitch, quality, bar)
            next_root_pc = harmonic[(bar + 1) % len(harmonic)][0]
            next_root = _pc_to_bass_register(next_root_pc, octave=2, lo=31, hi=57)
            while abs(next_root - cell[2]) > 7 and next_root + 12 <= 57:
                next_root += 12
            while abs(next_root - cell[2]) > 7 and next_root - 12 >= 31:
                next_root -= 12
            approaches = get_chromatic_approaches(cell[2], next_root)
            if approaches:
                cell[3] = approaches[-1]
            bar_t0 = bar_anchor + bar * 4.0 * spb
            bar_t1 = bar_anchor + (bar + 1) * 4.0 * spb
            for beat_idx, pitch in enumerate(cell):
                slot = beat_idx * 4
                start = max(
                    bar_t0,
                    bar_t0 + beat_idx * spb + spb * 0.02 + (spb * 0.006 if beat_idx in (1, 3) else 0.0)
                    + rng.uniform(-0.002, 0.004) * spb,
                )
                end = min(bar_t1 - 1e-4, start + spb * rng.uniform(0.82, 0.9))
                if end <= start:
                    continue
                vel = max(64, min(106, (92, 82, 88, 78)[beat_idx] + rng.randint(-4, 4)))
                inst.notes.append(pretty_midi.Note(velocity=vel, pitch=pitch, start=start, end=end))
                if return_performance_notes:
                    perf_notes.append(
                        BassPerformanceNote(
                            pitch=int(pitch),
                            velocity=int(vel),
                            start=float(start),
                            end=float(end),
                            articulation="normal",
                            role=str(role),
                            bar_index=int(bar),
                            slot_index=int(slot),
                            source="phrase_v2",
                            confidence=None,
                        )
                    )

        pm.instruments.append(inst)
        buf = io.BytesIO()
        pm.write(buf)
        preview = (
            f"Bass [phrase_v2, {bi}, {style}, paul_chambers]: "
            f"{mt.normalize_key(key)} {mt.describe_scale(scale)}, {bar_count} bar(s), {tempo} BPM — "
            "quarter-note walking, strong-beat chord targets, and chromatic beat-4 approaches"
            + (", constrained by live harmonic context." if conditioning is not None and conditioning.harmonic_bars else ".")
        )
        if return_performance_notes:
            perf_notes = list(
                infer_bass_articulations(
                    tuple(perf_notes),
                    tempo=tempo,
                    style=style,
                    source="phrase_v2",
                )
            )
            return buf.getvalue(), preview, tuple(perf_notes)
        return buf.getvalue(), preview

    if player == "pino":
        previous_pitch: int | None = None
        for bar in range(max(1, bar_count)):
            role = _bar_role(bar, role_span)
            kick_slots = _kick_guided_slots(context, bar) if context is not None and context.anchor_lane == "drums" else []
            live_slots = _source_guided_slots(conditioning, bar)
            if live_slots and not kick_slots:
                kick_slots = live_slots
            elif live_slots:
                kick_slots = sorted(set(kick_slots).union(live_slots[:2]))
            slots = _pino_phrase_slots(role, kick_slots, rng=rng, profile=player_profile)
            root_pc, stable_pcs, passing_pcs, avoid_pcs, _conf = _harmonic_bar_plan(
                bar,
                key=key,
                scale=scale,
                context=context,
                conditioning=conditioning,
            )
            bar_t0 = bar_anchor + bar * 4.0 * spb
            bar_t1 = bar_anchor + (bar + 1) * 4.0 * spb

            for slot_index, slot in enumerate(slots):
                live_pressure = source_slot_pressure(conditioning, bar, slot) if has_source_groove(conditioning) else 0.0
                live_kick = source_kick_weight(conditioning, bar, slot) if has_source_groove(conditioning) else 0.0
                if live_pressure > 0.72 and live_kick < 0.18 and slot % 4 != 0:
                    continue
                pitch = _pick_pino_pitch(
                    slot,
                    role,
                    root_pc=root_pc,
                    stable_pcs=stable_pcs,
                    passing_pcs=passing_pcs,
                    avoid_pcs=avoid_pcs,
                    previous_pitch=previous_pitch,
                    profile=player_profile,
                    rng=rng,
                )
                behind = spb * 0.028
                if live_kick > 0.0:
                    behind += sixteenth * 0.025 * min(1.0, live_kick)
                start = bar_t0 + slot * sixteenth + behind + rng.uniform(-0.002, 0.006) * spb
                if slot == 0 and len(slots) == 1:
                    dur = spb * 2.75
                elif slot % 8 == 0:
                    dur = spb * 1.72
                elif slot % 4 == 0:
                    dur = spb * 1.18
                else:
                    dur = spb * 0.78
                dur *= _profile_float(player_profile, "articulation_length_bias", 1.24)
                next_slot = slots[slot_index + 1] if slot_index + 1 < len(slots) else None
                next_slot_start = bar_t0 + next_slot * sixteenth if next_slot is not None else bar_t1
                end = min(bar_t1 - 1e-4, next_slot_start - 1e-4, start + dur)
                if end <= start:
                    continue
                vel_base = 84 if slot == 0 else 72
                if role == "push":
                    vel_base += 3
                elif role == "release":
                    vel_base -= 4
                if live_kick > 0.0:
                    vel_base += int(round(5.0 * min(1.0, live_kick)))
                final_vel = max(54, min(100, vel_base + rng.randint(-4, 4)))
                inst.notes.append(
                    pretty_midi.Note(
                        velocity=final_vel,
                        pitch=pitch,
                        start=start,
                        end=end,
                    )
                )
                previous_pitch = pitch
                if return_performance_notes:
                    perf_notes.append(
                        BassPerformanceNote(
                            pitch=int(pitch),
                            velocity=int(final_vel),
                            start=float(start),
                            end=float(end),
                            articulation="normal",
                            role=str(role),
                            bar_index=int(bar),
                            slot_index=int(slot),
                            source="phrase_v2",
                            confidence=None,
                        )
                    )

        pm.instruments.append(inst)
        buf = io.BytesIO()
        pm.write(buf)
        preview = (
            f"Bass [phrase_v2, {bi}, {style}, pino]: "
            f"{mt.normalize_key(key)} {mt.describe_scale(scale)}, {bar_count} bar(s), {tempo} BPM — "
            "laid-back neo-soul pocket, warm sustained chord-tone targets, and rare-groove space"
            + (", conditioned by live source groove." if has_source_groove(conditioning) else ".")
        )
        if return_performance_notes:
            perf_notes = list(
                infer_bass_articulations(
                    tuple(perf_notes),
                    tempo=tempo,
                    style=style,
                    source="phrase_v2",
                )
            )
            return buf.getvalue(), preview, tuple(perf_notes)
        return buf.getvalue(), preview

    # v0.3b: resolve the reference lock once for the whole part. lock drives
    # kick gravitation, restraint, and pressure-aversion from ONE knob.
    lock, lock_state = _resolve_reference_lock(lock_to_groove, conditioning)
    # Precompute the harmonic plan so every bar can resolve INTO the next
    # chord (spec: "resolve into the next chord, every bar").
    harmonic_plan = [
        _harmonic_bar_plan(b, key=key, scale=scale, context=context, conditioning=conditioning)
        for b in range(max(1, bar_count))
    ]

    for bar in range(max(1, bar_count)):
        role = _bar_role(bar, role_span)
        kick_slots = _kick_guided_slots(context, bar) if context is not None and context.anchor_lane == "drums" else []
        live_slots = _source_guided_slots(conditioning, bar)
        if live_slots and not kick_slots:
            kick_slots = live_slots
        elif live_slots:
            kick_slots = sorted(set(kick_slots).union(live_slots[:2]))
        if lock_state == "locked":
            # kick_lock_mult: higher lock pulls more kick-adjacent slots into
            # the phrase (and allows one extra hit at full glue).
            slots = _phrase_slots(
                role,
                kick_slots,
                kick_take=3 + (1 if lock >= 0.7 else 0),
                extra_hits=1 if lock >= 0.75 else 0,
            )
        else:
            slots = _phrase_slots(role, kick_slots)
        root_pc, stable_pcs, passing_pcs, avoid_pcs, conf = harmonic_plan[bar]
        next_root_pc = harmonic_plan[(bar + 1) % len(harmonic_plan)][0]
        bar_t0 = bar_anchor + bar * 4.0 * spb
        bar_t1 = bar_anchor + (bar + 1) * 4.0 * spb

        for slot in slots:
            live_pressure = source_slot_pressure(conditioning, bar, slot) if has_source_groove(conditioning) else 0.0
            live_kick = source_kick_weight(conditioning, bar, slot) if has_source_groove(conditioning) else 0.0
            if lock_state == "locked" and slot != 0:
                # Pocket gating via the one space score (spec §6): higher lock
                # = stricter threshold (pressure-aversion) and firmer
                # restraint. Only the ONE is never gated — a bassist keeps
                # the one even in a crowded pocket; beats 2 and 4 are exactly
                # where the snare needs breathing room.
                space = _space_score(context, conditioning, bar, slot)
                if space is not None:
                    space_threshold = 0.42 + (0.28 * lock)
                    restraint = 0.50 + (0.50 * lock)
                    if space < space_threshold and rng.random() < restraint:
                        continue
            else:
                if context is not None:
                    pressure = slot_pressure(context, bar, slot)
                    kick = drum_kick_weight(context, bar, slot) if context.anchor_lane == "drums" else 0.0
                    # Rest-space rule: avoid busy non-kick slots.
                    if pressure > 0.72 and kick < 0.18 and slot % 4 != 0 and rng.random() < 0.45:
                        continue
                if live_pressure > 0.78 and live_kick < 0.18 and slot % 4 != 0 and rng.random() < 0.35:
                    continue
            pitch = _pick_pitch(
                slot,
                role,
                root_pc=root_pc,
                stable_pcs=stable_pcs,
                passing_pcs=passing_pcs,
                avoid_pcs=avoid_pcs,
                conf=conf,
                rng=rng,
            )
            # Resolve into the next chord: in release/answer bars the final
            # late-bar hit becomes a chromatic approach to the next bar's
            # root (the paul_chambers vocabulary, reused — spec §6).
            if (
                role in ("release", "answer")
                and slot == slots[-1]
                and slot >= 10
                and conf >= 0.3
                and next_root_pc != root_pc
            ):
                next_root = _pc_to_bass_register(next_root_pc, octave=2, lo=30, hi=62)
                while abs(next_root - pitch) > 7 and next_root + 12 <= 62:
                    next_root += 12
                while abs(next_root - pitch) > 7 and next_root - 12 >= 30:
                    next_root -= 12
                approaches = get_chromatic_approaches(pitch, next_root)
                if approaches:
                    pitch = int(approaches[-1])
            start = bar_t0 + slot * sixteenth
            if context is not None and context.anchor_lane == "drums":
                start += sixteenth * 0.05 * drum_kick_weight(context, bar, slot)
            if live_kick > 0.0:
                # timing glue scales with the lock (0.5 reproduces the
                # previous fixed 0.035 nudge)
                glue = (0.02 + (0.03 * lock)) if lock_state == "locked" else 0.035
                start += sixteenth * glue * min(1.0, live_kick)
            start += rng.uniform(0.0, 0.008) * spb
            dur = sixteenth * (1.2 if slot % 4 == 0 else 0.85)
            if role == "release":
                dur *= 0.9
            end = min(bar_t1 - 1e-4, start + dur)
            if end <= start:
                continue
            vel = 92 if slot % 4 == 0 else 78
            if live_kick > 0.0:
                # kick accenting scales with the lock (0.5 reproduces the
                # previous fixed +10)
                accent = (6.0 + (8.0 * lock)) if lock_state == "locked" else 10.0
                vel += int(round(accent * min(1.0, live_kick)))
            if role == "push":
                vel += 4
            elif role == "release":
                vel -= 5
            final_vel = max(54, min(112, vel + rng.randint(-6, 6)))
            inst.notes.append(
                pretty_midi.Note(
                    velocity=final_vel,
                    pitch=pitch,
                    start=start,
                    end=end,
                )
            )
            if return_performance_notes:
                perf_notes.append(
                    BassPerformanceNote(
                        pitch=int(pitch),
                        velocity=int(final_vel),
                        start=float(start),
                        end=float(end),
                        articulation="normal",
                        role=str(role),
                        bar_index=int(bar),
                        slot_index=int(slot),
                        source="phrase_v2",
                        confidence=None,
                    )
                )

    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    if lock_state == "locked":
        groove_clause = f", locked to the reference groove (lock {lock:.2f})."
    elif lock_state == "off":
        groove_clause = ", reference groove available but lock dialled to 0."
    elif lock_state == "thin":
        # Confidence-gated honesty (spec §6): never claim a lock the
        # analysis can't support.
        groove_clause = ", standard pocket — reference evidence too thin to claim a groove lock."
    else:
        groove_clause = "."
    preview = (
        f"Bass [phrase_v2, {bi}, {style}{', ' + player if player else ''}]: "
        f"{mt.normalize_key(key)} {mt.describe_scale(scale)}, {bar_count} bar(s), {tempo} BPM — "
        "kick-aware phrase roles, rest-space gating, bar-level harmonic targets"
        + groove_clause
    )
    if return_performance_notes:
        perf_notes = list(
            infer_bass_articulations(
                tuple(perf_notes),
                tempo=tempo,
                style=style,
                source="phrase_v2",
            )
        )
        return buf.getvalue(), preview, tuple(perf_notes)
    return buf.getvalue(), preview
