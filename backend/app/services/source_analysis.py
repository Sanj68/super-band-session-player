"""Phase 2: structured source analysis for Session Player engine data."""

from __future__ import annotations

import math
from typing import Any

from app.models.session import (
    GrooveProfile,
    HarmonyPlan,
    HarmonyPlanBar,
    SectionSpan,
    SourceAnalysis,
)
from app.services.midi_note_extract import extract_lane_notes
from app.services.session_context import SessionAnchorContext, build_session_context, normalize_anchor_lane
from app.utils import music_theory as mt


_PREFERRED_SOURCE_LANES: tuple[str, ...] = ("bass", "chords", "drums", "lead")


def _lane_midi_bytes(session: Any, lane: str) -> bytes | None:
    if lane == "drums":
        return getattr(session, "drum_bytes", None)
    if lane == "bass":
        return getattr(session, "bass_bytes", None)
    if lane == "chords":
        return getattr(session, "chords_bytes", None)
    if lane == "lead":
        return getattr(session, "lead_bytes", None)
    return None


def _pick_source_lane(session: Any) -> str | None:
    anchor = normalize_anchor_lane(getattr(session, "anchor_lane", None))
    if anchor and _lane_midi_bytes(session, anchor):
        return anchor
    for lane in _PREFERRED_SOURCE_LANES:
        if _lane_midi_bytes(session, lane):
            return lane
    return None


def _section_label(index: int) -> str:
    labels = ("intro", "verse", "lift", "chorus", "bridge", "outro")
    return labels[min(index, len(labels) - 1)]


def _moving_average(values: list[float], radius: int = 1) -> list[float]:
    out: list[float] = []
    n = len(values)
    for i in range(n):
        lo = max(0, i - radius)
        hi = min(n, i + radius + 1)
        w = values[lo:hi]
        out.append(sum(w) / len(w) if w else 0.0)
    return out


def _build_sections(energy: list[float], accent: list[float], bars: int) -> list[SectionSpan]:
    if bars <= 0:
        return []
    e_sm = _moving_average(energy, radius=1)
    a_sm = _moving_average(accent, radius=1)
    novelty: list[float] = [0.0 for _ in range(bars)]
    for i in range(1, bars):
        jump_e = abs(e_sm[i] - e_sm[i - 1])
        jump_a = abs(a_sm[i] - a_sm[i - 1])
        grid_bias = 0.0
        if i % 4 == 0:
            grid_bias += 0.08
        if i % 8 == 0:
            grid_bias += 0.05
        novelty[i] = (0.65 * jump_e) + (0.35 * jump_a) + grid_bias
    mean_n = sum(novelty) / float(len(novelty)) if novelty else 0.0
    std_n = math.sqrt(sum((x - mean_n) ** 2 for x in novelty) / float(len(novelty))) if novelty else 0.0
    threshold = mean_n + (0.45 * std_n)

    boundaries: list[int] = [0]
    for i in range(1, bars):
        if novelty[i] >= threshold and (i - boundaries[-1]) >= 4:
            boundaries.append(i)
    # Keep long forms readable even when novelty is flat.
    if bars >= 12 and all((b % 8) != 0 for b in boundaries[1:]):
        for i in range(8, bars, 8):
            if (i - boundaries[-1]) >= 4:
                boundaries.append(i)
    boundaries = sorted(set(boundaries))
    if boundaries[-1] != bars:
        boundaries.append(bars)
    out: list[SectionSpan] = []
    for idx in range(len(boundaries) - 1):
        start_bar = boundaries[idx]
        end_bar = boundaries[idx + 1] - 1
        out.append(SectionSpan(label=_section_label(idx), start_bar=start_bar, end_bar=end_bar))
    return out


def _estimate_beat_phase_offset(
    notes: list[Any],
    beat_len: float,
) -> tuple[int, list[float], float]:
    """
    Estimate which quarter-note phase (0..3) behaves most like downbeat.
    Returns (phase_index, normalized_scores, confidence).
    """
    if beat_len <= 1e-9 or not notes:
        return 0, [0.0, 0.0, 0.0, 0.0], 0.0

    scores = [0.0, 0.0, 0.0, 0.0]
    for n in notes:
        onset_beat = int(round(float(n.start) / beat_len))
        phase = onset_beat % 4
        vel = float(max(1, min(127, n.velocity))) / 127.0
        dur = max(0.04, float(n.end - n.start))
        # Slightly favor stronger/longer onsets as structural markers.
        scores[phase] += vel * min(1.5, 0.5 + dur)

    total = sum(scores)
    norm = [round((s / total), 4) if total > 1e-9 else 0.0 for s in scores]
    best = max(range(4), key=lambda i: scores[i])
    sorted_scores = sorted(scores, reverse=True)
    if total <= 1e-9:
        conf = 0.0
    elif len(sorted_scores) < 2:
        conf = 0.3
    else:
        sep = max(0.0, sorted_scores[0] - sorted_scores[1]) / total
        conf = min(1.0, 0.25 + (1.8 * sep))
    return best, norm, round(conf, 4)


def _estimate_tempo_from_onsets(notes: list[Any], fallback_tempo: int) -> tuple[float, float]:
    if len(notes) < 4:
        return float(fallback_tempo), 0.2 if notes else 0.0
    onsets = sorted({round(float(n.start), 4) for n in notes})
    if len(onsets) < 4:
        return float(fallback_tempo), 0.2
    deltas: list[float] = []
    for i in range(1, len(onsets)):
        d = onsets[i] - onsets[i - 1]
        if 0.06 <= d <= 2.0:
            deltas.append(d)
    if not deltas:
        return float(fallback_tempo), 0.15
    deltas.sort()
    med = deltas[len(deltas) // 2]
    if med <= 1e-9:
        return float(fallback_tempo), 0.1
    bpm = 60.0 / med
    while bpm < 40.0:
        bpm *= 2.0
    while bpm > 240.0:
        bpm *= 0.5
    est = max(40.0, min(240.0, bpm))
    dev = abs(est - float(fallback_tempo)) / max(1.0, float(fallback_tempo))
    density = min(1.0, len(deltas) / 32.0)
    conf = max(0.0, min(1.0, (0.7 * density) + (0.3 * max(0.0, 1.0 - dev))))
    return round(est, 3), round(conf, 4)


def _estimate_tonal_center_and_mode(session: Any, context: SessionAnchorContext | None) -> tuple[int, float, str, float]:
    key = str(getattr(session, "key", "C") or "C")
    scale = str(getattr(session, "scale", "major") or "major")
    fallback_pc = mt.key_root_pc(key)
    fallback_mode = mt.describe_scale(scale)
    if context is None or not context.harmonic_root_pc_per_bar:
        return fallback_pc, 0.2, fallback_mode, 0.2

    root_hist: dict[int, float] = {}
    for i, pc in enumerate(context.harmonic_root_pc_per_bar):
        w = context.harmonic_confidence_per_bar[i] if i < len(context.harmonic_confidence_per_bar) else 0.2
        root_hist[int(pc) % 12] = root_hist.get(int(pc) % 12, 0.0) + float(max(0.05, w))
    ranked = sorted(root_hist.items(), key=lambda kv: kv[1], reverse=True)
    if not ranked:
        return fallback_pc, 0.2, fallback_mode, 0.2
    best_pc, best_w = ranked[0]
    second_w = ranked[1][1] if len(ranked) > 1 else 0.0
    total_w = sum(root_hist.values())
    tonal_conf = max(0.0, min(1.0, (best_w / max(total_w, 1e-9)) * 0.7 + ((best_w - second_w) / max(best_w, 1e-9)) * 0.3))

    # Scale-mode guess among common modes used in app.
    candidate_modes = ("major", "natural_minor", "dorian", "mixolydian", "pentatonic_major", "pentatonic_minor")
    best_mode = fallback_mode
    best_mode_score = -1.0
    for mode in candidate_modes:
        mode_pcs = {(best_pc + x) % 12 for x in mt.scale_intervals(mode)}
        covered = 0.0
        penalty = 0.0
        for i, tpcs in enumerate(context.harmonic_target_pcs_per_bar):
            w = context.harmonic_confidence_per_bar[i] if i < len(context.harmonic_confidence_per_bar) else 0.2
            for pc in tpcs:
                if int(pc) % 12 in mode_pcs:
                    covered += w
                else:
                    penalty += w * 0.8
        score = covered - penalty
        if score > best_mode_score:
            best_mode_score = score
            best_mode = mode
    mode_conf = 0.25
    if best_mode_score > 0:
        mode_conf = min(1.0, 0.25 + (best_mode_score / max(1.0, total_w * 3.0)))
    return int(best_pc), round(tonal_conf, 4), str(best_mode), round(mode_conf, 4)


def build_source_analysis(session: Any, *, context: SessionAnchorContext | None = None) -> SourceAnalysis:
    tempo = int(getattr(session, "tempo", 120) or 120)
    bar_count = max(1, int(getattr(session, "bar_count", 8) or 8))
    beat_len = 60.0 / float(max(40, min(240, tempo)))
    bar_len = 4.0 * beat_len
    source_lane = _pick_source_lane(session)
    source_notes = extract_lane_notes(_lane_midi_bytes(session, source_lane)) if source_lane else []
    tempo_estimate, tempo_conf = _estimate_tempo_from_onsets(source_notes, tempo)
    phase_offset, phase_scores, phase_confidence = _estimate_beat_phase_offset(source_notes, beat_len)
    gen_phase_offset = int(context.beat_phase_offset_beats) if context is not None else int(phase_offset)
    gen_anchor_sec = float(context.bar_start_anchor_sec) if context is not None else float(phase_offset) * beat_len
    tonal_center_pc, tonal_conf, scale_mode_guess, scale_mode_conf = _estimate_tonal_center_and_mode(session, context)

    beat_grid = [round((i * beat_len), 6) for i in range(bar_count * 4)]
    bar_starts = [round((phase_offset * beat_len) + (i * bar_len), 6) for i in range(bar_count)]

    # Per-bar energy/accent derived from note velocity and first-beat weighting.
    vel_totals = [0.0] * bar_count
    onsets = [0] * bar_count
    first_beat_accents = [0.0] * bar_count
    for n in source_notes:
        bar_idx = int(n.start / bar_len) if bar_len > 1e-9 else 0
        if bar_idx < 0 or bar_idx >= bar_count:
            continue
        vel = float(max(1, min(127, n.velocity)))
        vel_totals[bar_idx] += vel
        onsets[bar_idx] += 1
        rel = n.start - ((phase_offset * beat_len) + (bar_idx * bar_len))
        if rel < 0.0:
            rel += bar_len
        if rel <= beat_len:  # first beat
            first_beat_accents[bar_idx] += vel

    max_vel = max(vel_totals) if vel_totals else 0.0
    max_acc = max(first_beat_accents) if first_beat_accents else 0.0
    bar_energy = [round((v / max_vel) if max_vel > 0 else 0.0, 4) for v in vel_totals]
    bar_accent = [round((v / max_acc) if max_acc > 0 else 0.0, 4) for v in first_beat_accents]
    bar_conf = []
    for i in range(bar_count):
        onset_conf = min(1.0, float(onsets[i]) / 4.0)
        accent_conf = first_beat_accents[i] / max_acc if max_acc > 0 else 0.0
        bar_conf.append(round((0.6 * onset_conf) + (0.4 * accent_conf), 4))

    # Downbeat guess: first-beat accent + phrase boundary bias + confidence from separation.
    scores = [0.0 for _ in range(bar_count)]
    for i in range(bar_count):
        accent_norm = first_beat_accents[i] / max_acc if max_acc > 0 else 0.0
        rise = 0.0
        if i > 0:
            rise = max(0.0, bar_energy[i] - bar_energy[i - 1])
        grid_bias = 0.12 if (i % 4 == 0) else 0.0
        if i % 8 == 0:
            grid_bias += 0.08
        scores[i] = (0.55 * accent_norm) + (0.2 * rise) + (0.1 * bar_conf[i]) + grid_bias + (0.15 * phase_confidence)
    downbeat_guess = max(range(bar_count), key=lambda i: scores[i] if i < len(scores) else 0.0)
    sorted_scores = sorted(scores, reverse=True)
    if len(sorted_scores) >= 2 and sorted_scores[0] > 0:
        sep = max(0.0, sorted_scores[0] - sorted_scores[1])
        downbeat_confidence = min(1.0, 0.25 + sep + (0.35 * phase_confidence))
    else:
        downbeat_confidence = 0.25 if source_notes else 0.0
    bar_start_conf = max(0.0, min(1.0, (0.5 * phase_confidence) + (0.5 * downbeat_confidence)))
    sections = _build_sections(bar_energy, bar_accent, bar_count)

    return SourceAnalysis(
        source_lane=source_lane or "none",
        tempo=tempo,
        tempo_estimate_bpm=tempo_estimate,
        tempo_confidence=tempo_conf,
        beat_grid_seconds=beat_grid,
        bar_starts_seconds=bar_starts,
        beat_phase_offset_beats=phase_offset,
        beat_phase_scores=phase_scores,
        beat_phase_confidence=phase_confidence,
        phase_offset_used_for_generation_beats=gen_phase_offset,
        bar_start_anchor_used_seconds=round(max(0.0, gen_anchor_sec), 6),
        generation_aligned_to_anchor=context is not None,
        downbeat_guess_bar_index=downbeat_guess,
        downbeat_confidence=round(downbeat_confidence, 4),
        bar_start_confidence=round(bar_start_conf, 4),
        tonal_center_pc_guess=tonal_center_pc,
        tonal_center_confidence=tonal_conf,
        scale_mode_guess=scale_mode_guess,
        scale_mode_confidence=scale_mode_conf,
        sections=sections,
        bar_energy=bar_energy,
        bar_accent_profile=bar_accent,
        bar_confidence_profile=bar_conf,
    )


def build_groove_profile(source: SourceAnalysis, *, context: SessionAnchorContext | None = None) -> GrooveProfile:
    mean_energy = sum(source.bar_energy) / len(source.bar_energy) if source.bar_energy else 0.0
    density = 0.0
    sync = 0.0
    if context is not None:
        density = float(context.mean_density)
        sync = float(context.syncopation_score)
    pocket_feel = "steady"
    if sync >= 0.6:
        pocket_feel = "syncopated"
    elif mean_energy < 0.3:
        pocket_feel = "laid_back"
    elif mean_energy > 0.7:
        pocket_feel = "driving"
    return GrooveProfile(
        pocket_feel=pocket_feel,
        syncopation_score=round(sync, 4),
        density_per_bar_estimate=round(density, 4),
        accent_strength=round((sum(source.bar_accent_profile) / len(source.bar_accent_profile)) if source.bar_accent_profile else 0.0, 4),
        confidence=round(
            max(
                0.0,
                min(
                    1.0,
                    (0.5 * source.beat_phase_confidence) + (0.5 * source.downbeat_confidence),
                ),
            ),
            4,
        ),
    )


def build_harmony_plan(session: Any, _source: SourceAnalysis) -> HarmonyPlan:
    key = str(getattr(session, "key", "C") or "C")
    scale = str(getattr(session, "scale", "major") or "major")
    bars = max(1, int(getattr(session, "bar_count", 8) or 8))
    context = build_session_context(session)
    if context is not None and context.harmonic_target_pcs_per_bar:
        out: list[HarmonyPlanBar] = []
        for bar in range(bars):
            i = bar if bar < len(context.harmonic_target_pcs_per_bar) else (len(context.harmonic_target_pcs_per_bar) - 1)
            out.append(
                HarmonyPlanBar(
                    bar_index=bar,
                    root_pc=int(context.harmonic_root_pc_per_bar[i]),
                    target_pcs=[int(x) for x in context.harmonic_target_pcs_per_bar[i]],
                    passing_pcs=[int(x) for x in context.harmonic_passing_pcs_per_bar[i]],
                    avoid_pcs=[int(x) for x in context.harmonic_avoid_pcs_per_bar[i]],
                    confidence=float(context.harmonic_confidence_per_bar[i]),
                    source=str(context.harmonic_source_per_bar[i]),
                )
            )
        return HarmonyPlan(
            key_center=key,
            scale=scale,
            source="bar_level_targets",
            bars=out,
        )
    key_pc = mt.key_root_pc(key)
    scale_pcs = {(key_pc + x) % 12 for x in mt.scale_intervals(scale)}
    fallback_rows: list[HarmonyPlanBar] = []
    for bar, degree in enumerate(mt.progression_degrees_for_bars(bars, scale)):
        tones = mt.chord_tones_midi(key, scale, degree, octave=2, seventh=True)
        target = sorted({int(p) % 12 for p in tones[:3]})
        passing = sorted(pc for pc in scale_pcs if pc not in target)
        avoid = sorted(pc for pc in range(12) if pc not in scale_pcs)
        fallback_rows.append(
            HarmonyPlanBar(
                bar_index=bar,
                root_pc=int(tones[0]) % 12,
                target_pcs=target,
                passing_pcs=passing,
                avoid_pcs=avoid,
                confidence=0.25,
                source="static_progression",
            )
        )
    return HarmonyPlan(
        key_center=key,
        scale=scale,
        source="static_progression",
        bars=fallback_rows,
    )
