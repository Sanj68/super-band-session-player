"""Bass Phrase Engine v2: kick-aware phrase planning path."""

from __future__ import annotations

import io
import random

import pretty_midi

from app.services.session_context import (
    SessionAnchorContext,
    drum_kick_weight,
    slot_pressure,
)
from app.utils import music_theory as mt

_BASS_STYLES = frozenset({"supportive", "melodic", "rhythmic", "slap", "fusion"})
_BASS_INSTRUMENTS = frozenset({"finger_bass", "slap_bass", "synth_bass"})
_BASS_PLAYERS = frozenset({"bootsy", "marcus", "pino"})


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


def _phrase_slots(role: str, kick_slots: list[int]) -> list[int]:
    if role == "anchor":
        base = [0, 8]
    elif role == "push":
        base = [0, 6, 10, 14]
    elif role == "release":
        base = [0, 8, 12]
    else:  # answer
        base = [0, 7, 12]
    if kick_slots:
        base.extend(kick_slots[:3])
    out = sorted(set(x for x in base if 0 <= x <= 15))
    if 0 not in out:
        out.insert(0, 0)
    max_hits = 4 if role in ("anchor", "release") else 5
    return out[:max_hits]


def _harmonic_bar_plan(
    bar: int,
    *,
    key: str,
    scale: str,
    context: SessionAnchorContext | None,
) -> tuple[int, list[int], list[int], list[int], float]:
    if context is not None and bar < len(context.harmonic_target_pcs_per_bar):
        root = int(context.harmonic_root_pc_per_bar[bar])
        stable = [int(x) for x in context.harmonic_target_pcs_per_bar[bar]]
        passing = [int(x) for x in context.harmonic_passing_pcs_per_bar[bar]]
        avoid = [int(x) for x in context.harmonic_avoid_pcs_per_bar[bar]]
        conf = float(context.harmonic_confidence_per_bar[bar]) if bar < len(context.harmonic_confidence_per_bar) else 0.2
        return root, stable, passing, avoid, conf

    key_pc = mt.key_root_pc(key)
    intervals = mt.scale_intervals(scale)
    root = key_pc
    stable = [key_pc, (key_pc + intervals[2 % len(intervals)]) % 12, (key_pc + intervals[4 % len(intervals)]) % 12]
    passing = [(key_pc + x) % 12 for x in intervals if ((key_pc + x) % 12) not in stable]
    avoid = [pc for pc in range(12) if pc not in [(key_pc + x) % 12 for x in intervals]]
    return root, stable, passing, avoid, 0.2


def _pick_pitch(
    slot: int,
    role: str,
    *,
    root_pc: int,
    stable_pcs: list[int],
    passing_pcs: list[int],
    avoid_pcs: list[int],
    conf: float,
) -> int:
    strong = slot % 4 == 0
    if strong or role == "anchor":
        return _pc_to_bass_register(root_pc, octave=2)
    if passing_pcs and random.random() < min(0.5, 0.15 + 0.5 * conf):
        return _pc_to_bass_register(random.choice(passing_pcs), octave=2)
    pick_pc = random.choice(stable_pcs or [root_pc])
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
) -> tuple[bytes, str]:
    style = normalize_bass_style(bass_style)
    player = normalize_bass_player(bass_player)
    bi = normalize_bass_instrument(bass_instrument)
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    inst = pretty_midi.Instrument(program=bass_midi_program(bi, style), name="Bass")
    spb = 60.0 / float(tempo)
    sixteenth = spb / 4.0
    bar_anchor = float(context.bar_start_anchor_sec) if context is not None else 0.0
    role_span = 4 if bar_count >= 4 else 2

    for bar in range(max(1, bar_count)):
        role = _bar_role(bar, role_span)
        kick_slots = _kick_guided_slots(context, bar) if context is not None and context.anchor_lane == "drums" else []
        slots = _phrase_slots(role, kick_slots)
        root_pc, stable_pcs, passing_pcs, avoid_pcs, conf = _harmonic_bar_plan(bar, key=key, scale=scale, context=context)
        bar_t0 = bar_anchor + bar * 4.0 * spb
        bar_t1 = bar_anchor + (bar + 1) * 4.0 * spb

        for slot in slots:
            if context is not None:
                pressure = slot_pressure(context, bar, slot)
                kick = drum_kick_weight(context, bar, slot) if context.anchor_lane == "drums" else 0.0
                # Rest-space rule: avoid busy non-kick slots.
                if pressure > 0.72 and kick < 0.18 and slot % 4 != 0 and random.random() < 0.45:
                    continue
            pitch = _pick_pitch(
                slot,
                role,
                root_pc=root_pc,
                stable_pcs=stable_pcs,
                passing_pcs=passing_pcs,
                avoid_pcs=avoid_pcs,
                conf=conf,
            )
            start = bar_t0 + slot * sixteenth
            if context is not None and context.anchor_lane == "drums":
                start += sixteenth * 0.05 * drum_kick_weight(context, bar, slot)
            start += random.uniform(0.0, 0.008) * spb
            dur = sixteenth * (1.2 if slot % 4 == 0 else 0.85)
            if role == "release":
                dur *= 0.9
            end = min(bar_t1 - 1e-4, start + dur)
            if end <= start:
                continue
            vel = 92 if slot % 4 == 0 else 78
            if role == "push":
                vel += 4
            elif role == "release":
                vel -= 5
            inst.notes.append(
                pretty_midi.Note(
                    velocity=max(54, min(112, vel + random.randint(-6, 6))),
                    pitch=pitch,
                    start=start,
                    end=end,
                )
            )

    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    preview = (
        f"Bass [phrase_v2, {bi}, {style}{', ' + player if player else ''}]: "
        f"{mt.normalize_key(key)} {mt.describe_scale(scale)}, {bar_count} bar(s), {tempo} BPM — "
        "kick-aware phrase roles, rest-space gating, and bar-level harmonic targets."
    )
    return buf.getvalue(), preview
