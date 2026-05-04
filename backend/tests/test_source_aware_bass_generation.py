"""Source-upload groove maps influence supportive bass generation (no drum MIDI anchor)."""

from __future__ import annotations

import io
from types import SimpleNamespace

import pretty_midi

from app.models.session import GrooveProfile, HarmonyPlan, LaneNote, SourceAnalysis
from app.services.bass_generator import generate_bass
from app.services.bass_phrase_plan import build_phrase_plan
from app.services.bass_quality import analyze_bass_take
from app.services.conditioning import build_unified_conditioning
from app.services.session_context import SessionAnchorContext


def _src(
    *,
    bar_count: int,
    kick: list[list[float]],
    pressure: list[list[float]],
    snare: list[list[float]] | None = None,
    groove_conf: list[float] | None = None,
) -> SourceAnalysis:
    snare = snare or [[0.08] * 16 for _ in range(bar_count)]
    gc = groove_conf or [0.75] * bar_count
    return SourceAnalysis(
        source_lane="reference_audio",
        tempo=120,
        tempo_estimate_bpm=120.0,
        tempo_confidence=0.8,
        beat_grid_seconds=[0.5 * j for j in range(bar_count * 4)],
        bar_starts_seconds=[2.0 * j for j in range(bar_count)],
        beat_phase_offset_beats=0,
        beat_phase_scores=[1.0, 0.0, 0.0, 0.0],
        beat_phase_confidence=0.7,
        phase_offset_used_for_generation_beats=0,
        bar_start_anchor_used_seconds=0.0,
        generation_aligned_to_anchor=False,
        downbeat_guess_bar_index=0,
        downbeat_confidence=0.6,
        bar_start_confidence=0.65,
        tonal_center_pc_guess=0,
        tonal_center_confidence=0.5,
        scale_mode_guess="major",
        scale_mode_confidence=0.5,
        sections=[],
        bar_energy=[0.5] * bar_count,
        bar_accent_profile=[0.4] * bar_count,
        bar_confidence_profile=[0.6] * bar_count,
        source_kick_weight=kick,
        source_slot_pressure=pressure,
        source_snare_weight=snare,
        source_onset_weight=pressure,
        source_groove_confidence=gc,
    )


def _uc(
    *,
    bar_count: int,
    kick: list[list[float]],
    pressure: list[list[float]],
    snare: list[list[float]] | None = None,
    groove_conf: list[float] | None = None,
):
    groove = GrooveProfile(
        pocket_feel="steady",
        syncopation_score=0.2,
        density_per_bar_estimate=4.0,
        accent_strength=0.5,
        confidence=0.85,
    )
    harmony = HarmonyPlan(key_center="C", scale="major", source="static_progression", bars=[])
    src = _src(bar_count=bar_count, kick=kick, pressure=pressure, snare=snare, groove_conf=groove_conf)
    return build_unified_conditioning(
        session=SimpleNamespace(bar_count=bar_count, tempo=120),
        source=src,
        groove=groove,
        harmony=harmony,
        context=None,
    )


def _slots_signature(midi_bytes: bytes, *, tempo: int, bar_count: int) -> tuple[tuple[int, ...], ...]:
    pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
    spb = 60.0 / float(tempo)
    bar_len = 4.0 * spb
    per_bar: list[list[int]] = [[] for _ in range(bar_count)]
    for ins in pm.instruments:
        for n in ins.notes:
            bi = int(n.start // bar_len) if bar_len > 1e-9 else 0
            if bi < 0 or bi >= bar_count:
                continue
            rel = (n.start - bi * bar_len) / bar_len
            rel = rel - int(rel)
            if rel < 0:
                rel += 1.0
            slot = min(15, int(rel * 16.0 + 1e-9))
            per_bar[bi].append(slot)
    return tuple(tuple(sorted(set(row))) for row in per_bar)


def _note_count_at_slot(midi_bytes: bytes, *, tempo: int, bar: int, slot: int) -> int:
    pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
    spb = 60.0 / float(tempo)
    bar_len = 4.0 * spb
    t0 = bar * bar_len
    t1 = t0 + bar_len
    c = 0
    for ins in pm.instruments:
        for n in ins.notes:
            if not (t0 <= n.start < t1 - 1e-6):
                continue
            rel = (n.start - t0) / bar_len
            rel = rel - int(rel)
            if rel < 0:
                rel += 1.0
            s = min(15, int(rel * 16.0 + 1e-9))
            if s == slot:
                c += 1
    return c


def _notes(midi_bytes: bytes) -> list[pretty_midi.Note]:
    pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
    return sorted(
        [n for ins in pm.instruments for n in ins.notes],
        key=lambda n: (float(n.start), int(n.pitch)),
    )


def test_different_source_kick_maps_change_onset_slots() -> None:
    z = [[0.06] * 16 for _ in range(2)]
    p = [[0.35] * 16 for _ in range(2)]
    kick_a = [row[:] for row in z]
    kick_a[0][7] = 0.95
    kick_b = [row[:] for row in z]
    kick_b[0][13] = 0.95
    uc_a = _uc(bar_count=2, kick=kick_a, pressure=p)
    uc_b = _uc(bar_count=2, kick=kick_b, pressure=p)
    a = generate_bass(
        tempo=120,
        bar_count=2,
        key="C",
        scale="major",
        bass_style="supportive",
        chord_progression=["Cmaj7", "Cmaj7"],
        seed=2027,
        conditioning=uc_a,
    )
    b = generate_bass(
        tempo=120,
        bar_count=2,
        key="C",
        scale="major",
        bass_style="supportive",
        chord_progression=["Cmaj7", "Cmaj7"],
        seed=2027,
        conditioning=uc_b,
    )
    assert isinstance(a, tuple)
    sig_a = _slots_signature(a[0], tempo=120, bar_count=2)
    sig_b = _slots_signature(b[0], tempo=120, bar_count=2)
    assert sig_a != sig_b


def test_strong_kick_slot_increases_phrase_plan_weight_at_that_slot() -> None:
    flat_k = [[0.1] * 16 for _ in range(4)]
    peaked = [row[:] for row in flat_k]
    peaked[0][15] = 0.92
    pr = [[0.4] * 16 for _ in range(4)]
    uc_flat = _uc(bar_count=4, kick=flat_k, pressure=pr)
    uc_peak = _uc(bar_count=4, kick=peaked, pressure=pr)
    p_flat = build_phrase_plan(bar_count=4, style="supportive", salt=55, context=None, conditioning=uc_flat)
    p_peak = build_phrase_plan(bar_count=4, style="supportive", salt=55, context=None, conditioning=uc_peak)
    assert 15 in p_peak[0].slots
    assert 15 not in p_flat[0].slots


def test_strong_snare_without_kick_reduces_notes_on_that_slot() -> None:
    base_k = [[0.12] * 16 for _ in range(2)]
    pr = [[0.4] * 16 for _ in range(2)]
    snare_low = [[0.05] * 16 for _ in range(2)]
    snare_hot = [row[:] for row in snare_low]
    snare_hot[0][10] = 0.92
    base_k[0][10] = 0.08
    uc_ctrl = _uc(bar_count=2, kick=[r[:] for r in base_k], pressure=pr, snare=snare_low)
    uc_sn = _uc(bar_count=2, kick=[r[:] for r in base_k], pressure=pr, snare=snare_hot)
    ctrl = generate_bass(
        tempo=120,
        bar_count=2,
        key="C",
        scale="major",
        bass_style="supportive",
        chord_progression=["Cmaj7", "Cmaj7"],
        seed=9001,
        conditioning=uc_ctrl,
    )
    hot = generate_bass(
        tempo=120,
        bar_count=2,
        key="C",
        scale="major",
        bass_style="supportive",
        chord_progression=["Cmaj7", "Cmaj7"],
        seed=9001,
        conditioning=uc_sn,
    )
    assert _note_count_at_slot(ctrl[0], tempo=120, bar=0, slot=10) >= _note_count_at_slot(hot[0], tempo=120, bar=0, slot=10)


def test_high_source_pressure_increases_intent_density_mult() -> None:
    from app.services.bass_generator import _apply_source_groove_intent_nudge

    kick = [[0.15] * 16 for _ in range(2)]
    pr = [[0.92] * 16, [0.06] * 16]
    uc = _uc(bar_count=2, kick=kick, pressure=pr, groove_conf=[0.9, 0.9])
    base = {
        "role": "anchor",
        "density_mult": 1.0,
        "rest_bias": 0.2,
        "offbeat_push": 0.1,
        "cadence_strength": 0.2,
        "sustain_mult": 1.0,
        "allow_fill": False,
    }
    hi = _apply_source_groove_intent_nudge(base, conditioning=uc, context=None, bar=0)
    lo = _apply_source_groove_intent_nudge(base, conditioning=uc, context=None, bar=1)
    assert float(hi["density_mult"]) > float(lo["density_mult"])
    assert float(hi["rest_bias"]) < float(lo["rest_bias"])


def test_empty_source_groove_same_as_no_conditioning() -> None:
    dead = [[0.0] * 16 for _ in range(2)]
    src = _src(bar_count=2, kick=dead, pressure=dead)
    groove = GrooveProfile(
        pocket_feel="steady",
        syncopation_score=0.2,
        density_per_bar_estimate=4.0,
        accent_strength=0.5,
        confidence=0.85,
    )
    harmony = HarmonyPlan(key_center="C", scale="major", source="static_progression", bars=[])
    uc_dead = build_unified_conditioning(
        session=SimpleNamespace(bar_count=2, tempo=120),
        source=src,
        groove=groove,
        harmony=harmony,
        context=None,
    )
    a = generate_bass(
        tempo=120,
        bar_count=2,
        key="C",
        scale="major",
        bass_style="supportive",
        chord_progression=["Am7", "Am7"],
        seed=123,
        conditioning=None,
    )
    b = generate_bass(
        tempo=120,
        bar_count=2,
        key="C",
        scale="major",
        bass_style="supportive",
        chord_progression=["Am7", "Am7"],
        seed=123,
        conditioning=uc_dead,
    )
    assert a[0] == b[0]


def _drum_anchor_ctx(bar_count: int = 2) -> SessionAnchorContext:
    z = tuple(0.0 for _ in range(16))
    kr = list(z)
    kr[0] = 0.92
    kr[4] = 0.55
    kick_rows = tuple(tuple(kr) for _ in range(bar_count))
    occ = tuple(tuple(0.2 if i % 4 == 0 else 0.05 for i in range(16)) for _ in range(bar_count))
    return SessionAnchorContext(
        tempo=120,
        bar_count=bar_count,
        anchor_lane="drums",
        bar_len_sec=2.0,
        beat_len_sec=0.5,
        sixteenth_len_sec=0.125,
        density_per_bar=tuple(4.0 for _ in range(bar_count)),
        onsets_norm_per_bar=tuple((0.0,) for _ in range(bar_count)),
        gap_sec_per_bar=tuple(() for _ in range(bar_count)),
        mean_gap_sec_per_bar=tuple(0.0 for _ in range(bar_count)),
        pitch_min=36,
        pitch_max=50,
        pitch_span=14,
        syncopation_score=0.2,
        mean_density=4.0,
        slot_occupancy=occ,
        kick_slot_weight=kick_rows,
        snare_slot_weight=tuple(z for _ in range(bar_count)),
        beat_phase_offset_beats=0,
        beat_phase_confidence=0.7,
        bar_start_anchor_sec=0.0,
        harmonic_root_pc_per_bar=tuple(0 for _ in range(bar_count)),
        harmonic_target_pcs_per_bar=tuple((0, 4, 7) for _ in range(bar_count)),
        harmonic_passing_pcs_per_bar=tuple((2, 9) for _ in range(bar_count)),
        harmonic_avoid_pcs_per_bar=tuple(() for _ in range(bar_count)),
        harmonic_confidence_per_bar=tuple(0.5 for _ in range(bar_count)),
        harmonic_source_per_bar=tuple("scale_fallback" for _ in range(bar_count)),
    )


def test_drum_anchor_phrase_plan_ignores_conflicting_source_kick_maps() -> None:
    ctx = _drum_anchor_ctx(4)
    kick_a = [[0.1] * 16 for _ in range(4)]
    kick_a[0][14] = 0.95
    kick_b = [[0.1] * 16 for _ in range(4)]
    kick_b[0][3] = 0.95
    pr = [[0.4] * 16 for _ in range(4)]
    uc_a = _uc(bar_count=4, kick=kick_a, pressure=pr)
    uc_b = _uc(bar_count=4, kick=kick_b, pressure=pr)
    p_a = build_phrase_plan(bar_count=4, style="supportive", salt=77, context=ctx, conditioning=uc_a)
    p_b = build_phrase_plan(bar_count=4, style="supportive", salt=77, context=ctx, conditioning=uc_b)
    assert p_a[0].slots == p_b[0].slots


def test_analyze_bass_take_boosts_groove_when_aligned_with_source_kick() -> None:
    tempo = 120
    six = (60.0 / tempo) / 4.0
    notes = []
    for slot in (0, 5, 8):
        t = slot * six
        notes.append(LaneNote(pitch=36, start=t, end=t + 0.2, velocity=90))
    kick_match = [[0.05] * 16]
    kick_match[0][5] = 0.95
    pr = [[0.5] * 16 for _ in range(1)]
    uc_match = _uc(bar_count=1, kick=kick_match, pressure=pr)
    kick_miss = [[0.05] * 16]
    kick_miss[0][11] = 0.95
    uc_miss = _uc(bar_count=1, kick=kick_miss, pressure=pr)
    q_match = analyze_bass_take(
        notes,
        tempo=tempo,
        bar_count=1,
        key="C",
        scale="major",
        style="supportive",
        conditioning=uc_match,
        context=None,
    )
    q_miss = analyze_bass_take(
        notes,
        tempo=tempo,
        bar_count=1,
        key="C",
        scale="major",
        style="supportive",
        conditioning=uc_miss,
        context=None,
    )
    assert q_match.scores["groove_fit"] > q_miss.scores["groove_fit"]


def test_repeated_minor_source_groove_generates_more_than_slots_0_and_8() -> None:
    kick = [[0.08] * 16 for _ in range(8)]
    pressure = [[0.22] * 16 for _ in range(8)]
    for bar in range(8):
        kick[bar][0] = 0.92
        kick[bar][8] = 0.72
        kick[bar][7 if bar % 2 == 0 else 10] = 0.86
        pressure[bar][7 if bar % 2 == 0 else 10] = 0.78
    uc = _uc(bar_count=8, kick=kick, pressure=pressure, groove_conf=[0.55] * 8)

    data, _preview = generate_bass(
        tempo=118,
        bar_count=8,
        key="F#",
        scale="natural_minor",
        bass_style="supportive",
        chord_progression=["F#m"],
        seed=8101,
        conditioning=uc,
    )

    sig = _slots_signature(data, tempo=118, bar_count=8)
    all_slots = {s for row in sig for s in row}
    assert 0 in all_slots
    assert any(s not in (0, 8) for s in all_slots)
    assert max(len(row) for row in sig) <= 4


def test_repeated_minor_source_groove_adds_controlled_colour_or_octave() -> None:
    kick = [[0.08] * 16 for _ in range(8)]
    pressure = [[0.22] * 16 for _ in range(8)]
    for bar in range(8):
        kick[bar][0] = 0.9
        kick[bar][8] = 0.74
        kick[bar][6] = 0.88
        pressure[bar][6] = 0.82
    uc = _uc(bar_count=8, kick=kick, pressure=pressure, groove_conf=[0.55] * 8)

    data, _preview = generate_bass(
        tempo=118,
        bar_count=8,
        key="F#",
        scale="natural_minor",
        bass_style="supportive",
        chord_progression=["F#m"],
        seed=8102,
        conditioning=uc,
    )

    notes = _notes(data)
    root_pc = 6
    safe_colour = {1, 4, 9}  # C# fifth, A minor third, E flat-seven.
    assert any(n.pitch % 12 in safe_colour for n in notes) or any(n.pitch >= 54 and n.pitch % 12 == root_pc for n in notes)


def test_repeated_minor_source_groove_follows_strong_offbeat_kick_pressure() -> None:
    kick = [[0.04] * 16 for _ in range(4)]
    pressure = [[0.12] * 16 for _ in range(4)]
    for bar in range(4):
        kick[bar][0] = 0.9
        kick[bar][8] = 0.68
    kick[0][7] = 0.98
    pressure[0][7] = 0.92
    uc = _uc(bar_count=4, kick=kick, pressure=pressure, groove_conf=[0.6] * 4)

    data, _preview = generate_bass(
        tempo=118,
        bar_count=4,
        key="F#",
        scale="natural_minor",
        bass_style="supportive",
        chord_progression=["F#m"],
        seed=8103,
        conditioning=uc,
    )

    assert _note_count_at_slot(data, tempo=118, bar=0, slot=7) >= 1


def test_repeated_minor_source_groove_avoids_strong_snare_without_kick() -> None:
    kick = [[0.04] * 16 for _ in range(4)]
    pressure = [[0.12] * 16 for _ in range(4)]
    snare_low = [[0.04] * 16 for _ in range(4)]
    snare_hot = [[0.04] * 16 for _ in range(4)]
    for bar in range(4):
        kick[bar][0] = 0.9
        kick[bar][8] = 0.68
    pressure[0][7] = 0.95
    snare_hot[0][7] = 0.98
    uc_low = _uc(bar_count=4, kick=[r[:] for r in kick], pressure=pressure, snare=snare_low, groove_conf=[0.6] * 4)
    uc_hot = _uc(bar_count=4, kick=[r[:] for r in kick], pressure=pressure, snare=snare_hot, groove_conf=[0.6] * 4)

    low, _ = generate_bass(
        tempo=118,
        bar_count=4,
        key="F#",
        scale="natural_minor",
        bass_style="supportive",
        chord_progression=["F#m"],
        seed=8104,
        conditioning=uc_low,
    )
    hot, _ = generate_bass(
        tempo=118,
        bar_count=4,
        key="F#",
        scale="natural_minor",
        bass_style="supportive",
        chord_progression=["F#m"],
        seed=8104,
        conditioning=uc_hot,
    )

    assert _note_count_at_slot(low, tempo=118, bar=0, slot=7) >= _note_count_at_slot(hot, tempo=118, bar=0, slot=7)
