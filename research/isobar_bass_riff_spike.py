"""Research spike: deterministic source-aware bass riff cells.

This script is intentionally isolated from production Session Player paths.
It probes whether an isobar-style pattern layer would simplify riff-cell
construction for a repeated F#m source-groove pocket.

Run from repo root:
    backend/.venv/bin/python research/isobar_bass_riff_spike.py

Optional dependency for the isobar adapter path:
    backend/.venv/bin/python -m pip install isobar
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib
import random
from statistics import mean
from typing import Iterable


TEMPO = 118
BAR_COUNT = 8
SLOTS_PER_BAR = 16
SEED = 8101

PITCHES = {
    "root": 42,       # F#2
    "octave": 54,     # F#3
    "fifth": 49,      # C#3
    "flat7": 52,      # E3
    "minor3": 45,     # A2
}


@dataclass(frozen=True)
class NoteEvent:
    bar_index: int
    slot_index: int
    pitch_role: str
    midi_pitch: int
    velocity: int
    duration_slots: float


def _try_isobar():
    try:
        return importlib.import_module("isobar")
    except ModuleNotFoundError:
        return None


def _cycle(values: tuple[str, ...], count: int, *, iso_module) -> list[str]:
    """Cycle pattern roles, using isobar PSeq when available."""
    if iso_module is not None:
        pseq = getattr(iso_module, "PSeq", None) or getattr(iso_module, "PSequence", None)
        if pseq is not None:
            try:
                stream = iter(pseq(values, repeats=0))
                return [next(stream) for _ in range(count)]
            except Exception:
                pass
    return [values[i % len(values)] for i in range(count)]


def _source_maps() -> tuple[list[list[float]], list[list[float]], list[list[float]]]:
    kick = [[0.05] * SLOTS_PER_BAR for _ in range(BAR_COUNT)]
    pressure = [[0.14] * SLOTS_PER_BAR for _ in range(BAR_COUNT)]
    snare = [[0.04] * SLOTS_PER_BAR for _ in range(BAR_COUNT)]
    for bar in range(BAR_COUNT):
        kick[bar][0] = 0.95
        kick[bar][8] = 0.72
        snare[bar][4] = 0.9
        snare[bar][12] = 0.82

        # Simulated Billie Jean-like offbeat/pickup pressure without copying it.
        first = 7 if bar % 2 == 0 else 10
        second = 14 if bar in (3, 7) else (6 if bar % 2 == 0 else 11)
        kick[bar][first] = 0.82
        pressure[bar][first] = 0.86
        pressure[bar][second] = 0.68
        if second not in (4, 12):
            kick[bar][second] = max(kick[bar][second], 0.42)
    return kick, pressure, snare


def _slot_score(kick: list[list[float]], pressure: list[list[float]], snare: list[list[float]], bar: int, slot: int) -> float:
    if slot in (0, 8):
        return -1.0
    score = 0.62 * kick[bar][slot] + 0.34 * pressure[bar][slot]
    if snare[bar][slot] >= 0.55 and kick[bar][slot] < 0.3:
        score -= 0.8 * snare[bar][slot]
    if slot % 4 == 0:
        score -= 0.1
    return score


def _pick_extra_slots(
    kick: list[list[float]],
    pressure: list[list[float]],
    snare: list[list[float]],
    *,
    bar: int,
    rng: random.Random,
    recall: tuple[int, ...] = (),
) -> tuple[int, ...]:
    preferred = [s for s in recall if _slot_score(kick, pressure, snare, bar, s) > 0.12]
    ranked = sorted(
        range(1, SLOTS_PER_BAR),
        key=lambda s: (_slot_score(kick, pressure, snare, bar, s), -abs(s - 8), -(s * 17 % 5)),
        reverse=True,
    )
    out: list[int] = []
    for s in preferred + ranked:
        if s == 8 or s in out:
            continue
        if snare[bar][s] >= 0.55 and kick[bar][s] < 0.3:
            continue
        if _slot_score(kick, pressure, snare, bar, s) < 0.18 and out:
            continue
        out.append(s)
        if len(out) >= 2:
            break
    if not out:
        out = [rng.choice((6, 7, 10, 11, 14))]
    return tuple(sorted(out[:2]))


def generate_trivial_baseline() -> list[NoteEvent]:
    return [
        NoteEvent(bar, slot, "root", PITCHES["root"], 90 if slot == 0 else 82, 3.0)
        for bar in range(BAR_COUNT)
        for slot in (0, 8)
    ]


def generate_source_riff(*, seed: int, iso_module) -> list[NoteEvent]:
    rng = random.Random(seed)
    kick, pressure, snare = _source_maps()
    cell_a = _pick_extra_slots(kick, pressure, snare, bar=0, rng=rng)
    cell_b = _pick_extra_slots(kick, pressure, snare, bar=1, rng=rng)
    roles = _cycle(("root", "octave", "fifth", "flat7", "minor3"), 4, iso_module=iso_module)

    events: list[NoteEvent] = []
    for bar in range(BAR_COUNT):
        extras = cell_a if bar % 2 == 0 else cell_b
        extras = _pick_extra_slots(kick, pressure, snare, bar=bar, rng=rng, recall=extras)
        if bar in (3, 7):
            variation = _pick_extra_slots(kick, pressure, snare, bar=bar, rng=rng, recall=(14, 15, *extras))
            extras = tuple(sorted(set((*extras[:1], variation[-1]))))
        slots = tuple(sorted({0, 8, *extras}))[:4]
        for i, slot in enumerate(slots):
            if slot == 0:
                role = "root"
            elif slot == 8 and bar % 4 != 2:
                role = "fifth" if rng.random() < 0.55 else "octave"
            else:
                role = roles[(i + bar) % len(roles)]
                if role == "root" and slot not in (0, 8):
                    role = "octave"
            velocity = 92 if slot == 0 else 84 if slot == 8 else 76
            if kick[bar][slot] > 0.75:
                velocity += 5
            duration = 2.6 if slot in (0, 8) else 1.25
            if bar in (3, 7) and slot >= 14:
                duration = 1.0
            events.append(NoteEvent(bar, slot, role, PITCHES[role], min(108, velocity), duration))
    return events


def _signature(events: Iterable[NoteEvent]) -> tuple[tuple[int, ...], ...]:
    rows: list[list[int]] = [[] for _ in range(BAR_COUNT)]
    for event in events:
        rows[event.bar_index].append(event.slot_index)
    return tuple(tuple(sorted(row)) for row in rows)


def score(events: list[NoteEvent]) -> dict[str, object]:
    kick, pressure, snare = _source_maps()
    sig = _signature(events)
    offbeats = [e for e in events if e.slot_index % 4 != 0]
    conflicts = [
        e for e in events
        if snare[e.bar_index][e.slot_index] >= 0.55 and kick[e.bar_index][e.slot_index] < 0.3
    ]
    recalls = []
    for bar in range(2, BAR_COUNT):
        a = set(sig[bar - 2])
        b = set(sig[bar])
        recalls.append(len(a & b) / (len(a | b) or 1))
    densities = [len(row) for row in sig]
    return {
        "unique_pitch_roles": sorted({e.pitch_role for e in events}),
        "offbeat_note_count": len(offbeats),
        "motif_recall": round(mean(recalls), 3) if recalls else 0.0,
        "snare_conflict_count": len(conflicts),
        "density_per_bar": densities,
    }


def print_events(label: str, events: list[NoteEvent]) -> None:
    print(f"\n{label}")
    print("-" * len(label))
    for e in events:
        print(
            {
                "bar_index": e.bar_index,
                "slot_index": e.slot_index,
                "pitch_role": e.pitch_role,
                "midi_pitch": e.midi_pitch,
                "velocity": e.velocity,
                "duration_slots": e.duration_slots,
            }
        )
    print("score:", score(events))


def main() -> None:
    iso_module = _try_isobar()
    print("Session Player isobar bass riff spike")
    print(f"isobar_available: {iso_module is not None}")
    if iso_module is None:
        print("optional_install: backend/.venv/bin/python -m pip install isobar")
        print("adapter_path: pure-python fallback active")
    else:
        print("adapter_path: isobar pattern adapter active where possible")
    print(f"scenario: F# natural_minor, F#m repeated, {TEMPO} BPM, {BAR_COUNT} bars, seed={SEED}")

    baseline = generate_trivial_baseline()
    riff = generate_source_riff(seed=SEED, iso_module=iso_module)
    riff_again = generate_source_riff(seed=SEED, iso_module=iso_module)
    print(f"deterministic_same_seed: {riff == riff_again}")

    print_events("trivial baseline: root on slots 0 and 8", baseline)
    print_events("source-aware riff cell prototype", riff)

    print("\nassessment")
    print("isobar_meaningfully_helps: maybe for pattern syntax and future live pattern scheduling, not for this core logic.")
    print("integrate_now: no")
    print("simpler_path: keep Session Player's own riff-cell engine; it already needs custom source-map scoring, snare avoidance, and harmonic role rules.")


if __name__ == "__main__":
    main()
