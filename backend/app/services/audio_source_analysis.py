"""Audio-driven source analysis helpers for reference-audio workflow."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

from app.models.session import SectionSpan, SourceAnalysis
from app.utils import music_theory as mt

_HOP_LENGTH = 512
_TARGET_SR = 22050
_MODE_CANDIDATES: tuple[str, ...] = (
    "major",
    "minor",
)

# Krumhansl-like key profiles (normalized later in scoring).
_MAJOR_PROFILE = np.asarray([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88], dtype=float)
_MINOR_PROFILE = np.asarray([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17], dtype=float)


@dataclass
class AudioAnalysisResult:
    source_analysis: SourceAnalysis
    duration_seconds: float
    head_trim_seconds: float


def _moving_average(values: list[float], radius: int = 1) -> list[float]:
    out: list[float] = []
    n = len(values)
    for i in range(n):
        lo = max(0, i - radius)
        hi = min(n, i + radius + 1)
        window = values[lo:hi]
        out.append(sum(window) / len(window) if window else 0.0)
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
        grid_bias = 0.08 if i % 4 == 0 else 0.0
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
    if boundaries[-1] != bars:
        boundaries.append(bars)
    out: list[SectionSpan] = []
    for idx in range(len(boundaries) - 1):
        start_bar = boundaries[idx]
        end_bar = boundaries[idx + 1] - 1
        out.append(SectionSpan(label=f"S{idx + 1}", start_bar=start_bar, end_bar=end_bar))
    return out


def _tempo_candidates(onset_env: np.ndarray, sr: int, fallback_tempo: int) -> list[float]:
    tempos = librosa.feature.tempo(onset_envelope=onset_env, sr=sr, hop_length=_HOP_LENGTH, aggregate=None)
    arr = np.asarray(tempos, dtype=float).ravel()
    arr = arr[np.isfinite(arr)]
    arr = arr[(arr >= 30.0) & (arr <= 300.0)]
    primary = float(np.median(arr)) if arr.size else float(fallback_tempo)

    tempogram = librosa.feature.tempogram(onset_envelope=onset_env, sr=sr, hop_length=_HOP_LENGTH)
    tg = np.mean(tempogram, axis=1).astype(float) if tempogram.size else np.asarray([], dtype=float)
    freqs = librosa.tempo_frequencies(len(tg), sr=sr, hop_length=_HOP_LENGTH) if tg.size else np.asarray([], dtype=float)
    peaks: list[float] = []
    if tg.size and freqs.size:
        order = np.argsort(tg)[::-1]
        for idx in order:
            bpm = float(freqs[idx])
            if not np.isfinite(bpm) or bpm < 30.0 or bpm > 300.0:
                continue
            if any(abs(bpm - p) < 4.0 for p in peaks):
                continue
            peaks.append(bpm)
            if len(peaks) >= 4:
                break

    raw = [primary, float(fallback_tempo), 90.0, 100.0, 110.0, 120.0, 130.0]
    for p in peaks:
        raw.extend([p, p * 0.5, p * 2.0])
    raw.extend([primary * 0.5, primary * 2.0, float(fallback_tempo) * 0.5, float(fallback_tempo) * 2.0])
    out: list[float] = []
    for v in raw:
        vv = max(40.0, min(240.0, float(v)))
        if not any(abs(vv - y) < 0.75 for y in out):
            out.append(vv)
    return out


def _pulse_strength(onset_env: np.ndarray, sr: int, bpm: float) -> float:
    if bpm <= 1e-6 or onset_env.size < 4:
        return 0.0
    beat_sec = 60.0 / bpm
    lag_frames = librosa.time_to_frames(beat_sec, sr=sr, hop_length=_HOP_LENGTH)
    lag = int(round(float(np.asarray(lag_frames).ravel()[0])))
    if lag <= 1 or lag >= onset_env.size:
        return 0.0
    env = onset_env - np.mean(onset_env)
    denom = float(np.dot(env, env))
    if denom <= 1e-9:
        return 0.0
    corr = float(np.dot(env[:-lag], env[lag:])) / denom
    return max(0.0, corr)


def _beat_alignment_score(onset_env: np.ndarray, beat_frames: np.ndarray) -> float:
    if onset_env.size == 0 or beat_frames.size == 0:
        return 0.0
    idx = np.clip(beat_frames.astype(int), 0, onset_env.size - 1)
    vals = onset_env[idx]
    top = float(np.mean(vals))
    denom = float(np.max(onset_env)) + 1e-9
    return max(0.0, min(1.0, top / denom))


def _beat_regularity_score(beat_frames: np.ndarray, sr: int) -> float:
    if beat_frames.size < 4:
        return 0.0
    times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=_HOP_LENGTH)
    intervals = np.diff(times)
    if intervals.size < 3:
        return 0.0
    mean_i = float(np.mean(intervals))
    if mean_i <= 1e-9:
        return 0.0
    cv = float(np.std(intervals) / mean_i)
    return max(0.0, min(1.0, 1.0 - cv))


def _normalize_mode_label(mode_guess: str) -> str:
    mg = str(mode_guess).strip().lower()
    if mg == "natural_minor":
        return "minor"
    return mg or "major"


def _pc_distribution(chroma: np.ndarray) -> np.ndarray:
    if chroma.size == 0:
        return np.zeros(12, dtype=float)
    pc = np.mean(chroma, axis=1).astype(float)
    pc = np.clip(pc, 0.0, None)
    total = float(np.sum(pc))
    if total <= 1e-9:
        return np.zeros(12, dtype=float)
    return pc / total


def _mode_template(mode: str) -> np.ndarray:
    m = _normalize_mode_label(mode)
    if m == "major":
        base = _MAJOR_PROFILE.copy()
    elif m == "minor":
        base = _MINOR_PROFILE.copy()
    else:
        base = np.zeros(12, dtype=float)
        for i in mt.scale_intervals(m):
            base[int(i) % 12] = 1.0
        # Mild emphasis on tonic / dominant to avoid flat mode templates.
        base[0] += 0.8
        base[7] += 0.35
    base = np.clip(base, 0.0, None)
    s = float(np.sum(base))
    return base / s if s > 1e-9 else np.ones(12, dtype=float) / 12.0


def _profile_score(pc_dist: np.ndarray, tonic: int, mode: str) -> float:
    templ = np.roll(_mode_template(mode), int(tonic) % 12)
    # Correlation-ish score with root emphasis.
    dot = float(np.dot(pc_dist, templ))
    root_boost = 0.12 * float(pc_dist[int(tonic) % 12])
    return dot + root_boost


def _structural_pc_support(
    *,
    chroma: np.ndarray,
    low_chroma: np.ndarray,
    bar_starts_seconds: list[float],
    head_trim_seconds: float,
    sr: int,
) -> np.ndarray:
    if chroma.size == 0 or low_chroma.size == 0:
        return np.zeros(12, dtype=float)
    scores = np.zeros(12, dtype=float)
    landing_counts = np.zeros(12, dtype=float)
    n_frames = chroma.shape[1]
    for i, t_abs in enumerate(bar_starts_seconds):
        local_t = max(0.0, float(t_abs) - float(head_trim_seconds))
        fr_arr = librosa.time_to_frames(local_t, sr=sr, hop_length=_HOP_LENGTH)
        fr = int(float(np.asarray(fr_arr).ravel()[0]))
        fr = max(0, min(n_frames - 1, fr))
        bar_w = 1.0
        if i % 4 == 0:
            bar_w += 0.35  # phrase-entry
        if i % 4 == 3:
            bar_w += 0.55  # phrase-end/cadence tendency
        scores += (bar_w * chroma[:, fr]) + ((bar_w + 0.2) * low_chroma[:, fr])
        landing_pc = int(np.argmax(low_chroma[:, fr]) % 12)
        landing_counts[landing_pc] += bar_w
    # Repeated landing tones get extra structural support.
    if np.sum(landing_counts) > 0:
        scores += 0.65 * (landing_counts / np.sum(landing_counts))
    total = float(np.sum(scores))
    if total <= 1e-9:
        return np.zeros(12, dtype=float)
    return scores / total


def _tempo_objective(onset_env: np.ndarray, sr: int, bpm: float) -> tuple[float, float]:
    bt_tempo, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=_HOP_LENGTH,
        start_bpm=float(bpm),
        units="frames",
    )
    resolved = float(np.asarray(bt_tempo).ravel()[0]) if np.asarray(bt_tempo).size else float(bpm)
    while resolved < 40.0:
        resolved *= 2.0
    while resolved > 240.0:
        resolved *= 0.5
    resolved = max(40.0, min(240.0, resolved))

    beat_frames_arr = np.asarray(beat_frames).ravel()
    pulse = _pulse_strength(onset_env, sr, resolved)
    half = _pulse_strength(onset_env, sr, resolved / 2.0) if resolved >= 80.0 else 0.0
    align = _beat_alignment_score(onset_env, beat_frames_arr)
    regularity = _beat_regularity_score(beat_frames_arr, sr)
    # Favor practical musical BPM range for this project.
    band_bias = 1.0 - min(1.0, abs(resolved - 118.0) / 90.0)
    octave_penalty = max(0.0, half - pulse)
    score = (
        (0.40 * pulse)
        + (0.28 * align)
        + (0.15 * regularity)
        + (0.17 * band_bias)
        - (0.20 * octave_penalty)
    )
    return resolved, score


def _select_tempo(
    onset_env: np.ndarray,
    sr: int,
    fallback_tempo: int,
    *,
    anchor_bpm: float | None = None,
) -> tuple[float, float]:
    candidates = _tempo_candidates(onset_env, sr, fallback_tempo)
    if anchor_bpm is not None and np.isfinite(anchor_bpm):
        candidates.append(max(40.0, min(240.0, float(anchor_bpm))))
        candidates.append(max(40.0, min(240.0, float(anchor_bpm) * 0.5)))
        candidates.append(max(40.0, min(240.0, float(anchor_bpm) * 2.0)))
    if not candidates:
        return float(fallback_tempo), 0.15

    rows: list[tuple[float, float]] = []
    seen: list[float] = []
    for cand in candidates:
        bpm, score = _tempo_objective(onset_env, sr, cand)
        if anchor_bpm is not None and np.isfinite(anchor_bpm) and anchor_bpm > 1e-6:
            rel = abs(bpm - float(anchor_bpm)) / float(anchor_bpm)
            anchor_bias = max(0.0, 1.0 - min(1.0, rel))
            score += 0.18 * anchor_bias
        if any(abs(bpm - x) < 0.5 for x in seen):
            continue
        seen.append(bpm)
        rows.append((bpm, score))

    if not rows:
        return float(fallback_tempo), 0.15
    rows.sort(key=lambda x: x[1], reverse=True)
    chosen, score0 = rows[0]
    # Final octave check against direct half/double alternatives.
    octave_candidates = [chosen]
    if chosen >= 80.0:
        octave_candidates.append(chosen / 2.0)
    if chosen <= 120.0:
        octave_candidates.append(chosen * 2.0)
    best_oct_bpm = chosen
    best_oct_score = score0
    for oc in octave_candidates:
        resolved, sc = _tempo_objective(onset_env, sr, oc)
        if sc > best_oct_score + 0.02:
            best_oct_bpm, best_oct_score = resolved, sc
    chosen, score0 = best_oct_bpm, best_oct_score
    score1 = rows[1][1] if len(rows) > 1 else 0.0
    sep = max(0.0, score0 - score1)
    conf = max(0.0, min(1.0, 0.2 + (0.7 * score0) + (0.3 * sep)))
    if anchor_bpm is not None and np.isfinite(anchor_bpm):
        anchor = max(40.0, min(240.0, float(anchor_bpm)))
        ratio = chosen / anchor if anchor > 1e-9 else 1.0
        octave_like = (0.45 <= ratio <= 0.55) or (1.9 <= ratio <= 2.1)
        far_apart = abs(chosen - anchor) >= 18.0
        sparse_under_pick = far_apart and (anchor >= 100.0 and anchor <= 130.0) and (chosen < 105.0) and (conf < 0.8)
        if octave_like or far_apart:
            chosen_pulse = _pulse_strength(onset_env, sr, chosen)
            anchor_pulse = _pulse_strength(onset_env, sr, anchor)
            if anchor_pulse >= 0.35 * max(chosen_pulse, 1e-9) or sparse_under_pick:
                chosen = anchor
                conf = max(0.35, min(conf, 0.75))
        elif (anchor >= 100.0 and anchor <= 130.0) and (abs(chosen - anchor) <= 8.0) and (conf < 0.8):
            # Small stabilization toward loop-tempo anchor on sparse/ambiguous material.
            chosen = (0.55 * chosen) + (0.45 * anchor)
    return round(chosen, 3), round(conf, 4)


def _estimate_tonal_center_mode(
    chroma: np.ndarray,
    low_chroma: np.ndarray | None,
    structural_pc: np.ndarray | None,
    *,
    phase_confidence: float,
    bar_start_confidence: float,
    fallback_key: str,
    fallback_scale: str,
) -> tuple[int, float, str, float]:
    fallback_pc = mt.key_root_pc(fallback_key)
    fallback_mode = _normalize_mode_label(mt.describe_scale(fallback_scale))
    if chroma.size == 0:
        return fallback_pc, 0.2, fallback_mode, 0.2
    pc_dist = _pc_distribution(chroma)
    total = float(np.sum(pc_dist))
    if total <= 1e-9:
        return fallback_pc, 0.2, fallback_mode, 0.2

    # Blend whole-clip and tail-biased chroma, plus low-frequency and structural cues for tonic/root.
    # Confidence-gate structural/tail influence: when bar/phase alignment is weak,
    # prioritize stable global + low-frequency chroma over structure-derived features.
    tail_cols = max(1, chroma.shape[1] // 4)
    tail_dist = _pc_distribution(chroma[:, -tail_cols:])
    low_dist = _pc_distribution(low_chroma) if low_chroma is not None and low_chroma.size else np.zeros(12, dtype=float)
    structural_dist = (
        _pc_distribution(structural_pc.reshape(12, 1))
        if structural_pc is not None and np.asarray(structural_pc).size == 12
        else np.zeros(12, dtype=float)
    )
    phase_rel = max(0.0, min(1.0, float(phase_confidence)))
    bar_rel = max(0.0, min(1.0, float(bar_start_confidence)))
    structural_reliability = max(0.0, min(1.0, 0.55 * phase_rel + 0.45 * bar_rel))
    # Below ~0.35 reliability, structural cues are effectively disabled.
    struct_gate = max(0.0, min(1.0, (structural_reliability - 0.35) / 0.30))
    # Tail gets partially gated too: tail-only harmonic motion can be misleading
    # when downbeat/phase confidence is weak.
    tail_gate = max(0.0, min(1.0, (structural_reliability - 0.25) / 0.35))

    w_global = 0.50
    w_low = 0.34
    w_tail = 0.16 * tail_gate
    w_struct = 0.18 * struct_gate
    w_sum = max(1e-9, w_global + w_low + w_tail + w_struct)
    blend = (
        (w_global * pc_dist)
        + (w_low * low_dist)
        + (w_tail * tail_dist)
        + (w_struct * structural_dist)
    ) / w_sum
    blend = blend / max(1e-9, float(np.sum(blend)))

    combos: list[tuple[int, str, float]] = []
    for tonic in range(12):
        for mode in _MODE_CANDIDATES:
            score = _profile_score(blend, tonic, mode)
            root_support = (
                (0.42 * low_dist[tonic])
                + (0.28 * tail_dist[tonic])
                + (0.30 * structural_dist[tonic])
            )
            combos.append((tonic, mode, score + (0.24 * float(root_support))))
    combos.sort(key=lambda x: x[2], reverse=True)
    best_tonic, best_mode, best_score = combos[0]
    second_score = combos[1][2] if len(combos) > 1 else 0.0
    combo_lookup = {(t, m): s for t, m, s in combos}

    # Relative major/minor disambiguation using low-frequency root support.
    if best_mode == "major":
        rel_tonic, rel_mode = (best_tonic + 9) % 12, "minor"
    else:
        rel_tonic, rel_mode = (best_tonic + 3) % 12, "major"
    rel_score = float(combo_lookup.get((rel_tonic, rel_mode), -1e9))
    if (
        rel_score > -1e8
        and abs(best_score - rel_score) <= 0.08
        and float(low_dist[rel_tonic]) > float(low_dist[best_tonic]) + 0.02
    ):
        best_tonic, best_mode, best_score = rel_tonic, rel_mode, rel_score

    tonic_scores = [max(score for t, _m, score in combos if t == tonic) for tonic in range(12)]
    tonic_scores_sorted = sorted(tonic_scores, reverse=True)
    tonic_sep = max(0.0, tonic_scores_sorted[0] - (tonic_scores_sorted[1] if len(tonic_scores_sorted) > 1 else 0.0))
    combo_sep = max(0.0, best_score - second_score)
    concentration = float(np.max(blend))
    structural_conc = float(np.max(structural_dist)) if structural_dist.size else 0.0

    tonal_conf = max(
        0.0,
        min(1.0, 0.13 + (1.65 * tonic_sep) + (0.43 * concentration) + (0.28 * structural_conc)),
    )
    mode_conf = max(
        0.0,
        min(1.0, 0.10 + (2.0 * combo_sep) + (0.28 * concentration) + (0.22 * structural_conc)),
    )

    # Keep confidence honest; do not force fallback key/mode on ambiguity.
    # We still return a best-guess hypothesis but with low confidence.
    if tonal_conf < 0.25:
        tonal_conf = max(tonal_conf, 0.25)
    if mode_conf < 0.20:
        mode_conf = max(mode_conf, 0.20)

    return int(best_tonic), round(tonal_conf, 4), _normalize_mode_label(best_mode), round(mode_conf, 4)


def analyze_reference_audio(
    *,
    audio_path: Path,
    session_tempo: int,
    bar_count: int,
    session_key: str,
    session_scale: str,
) -> AudioAnalysisResult:
    y, sr = librosa.load(str(audio_path), sr=_TARGET_SR, mono=True)
    if y.size == 0:
        raise ValueError("Reference audio is empty.")
    duration_sec = float(y.size) / float(sr)

    _ignored_trimmed, idx = librosa.effects.trim(y, top_db=35)
    head_samples = int(idx[0]) if len(idx) > 0 else 0
    head_trim = float(head_samples) / float(sr)
    # Head-trim only: keep full tail to preserve clip/loop duration semantics.
    y_trimmed = y[head_samples:]
    if y_trimmed.size < int(0.4 * sr):
        y_trimmed = y
        head_trim = 0.0

    y_harm, y_perc = librosa.effects.hpss(y_trimmed)
    onset_env_perc = librosa.onset.onset_strength(y=y_perc, sr=sr, hop_length=_HOP_LENGTH)
    onset_env_full = librosa.onset.onset_strength(y=y_trimmed, sr=sr, hop_length=_HOP_LENGTH)
    # Prefer percussive onset envelope for tempo tracking; fallback when too weak.
    if np.max(onset_env_perc) > 1e-6 and np.mean(onset_env_perc) > 0.08 * max(1e-9, float(np.max(onset_env_perc))):
        onset_env = onset_env_perc
        rms_source = y_perc
    else:
        onset_env = onset_env_full
        rms_source = y_trimmed
    trimmed_duration = float(len(y_trimmed)) / float(sr)
    anchor_bpm = None
    if bar_count > 0 and trimmed_duration > 1e-6:
        anchor_bpm = (240.0 * float(bar_count)) / trimmed_duration
    tempo_est, tempo_conf = _select_tempo(onset_env, sr, session_tempo, anchor_bpm=anchor_bpm)

    _, beat_frames = librosa.beat.beat_track(
        onset_envelope=onset_env,
        sr=sr,
        hop_length=_HOP_LENGTH,
        start_bpm=float(tempo_est),
        units="frames",
    )
    beat_frames_arr = np.asarray(beat_frames, dtype=int).ravel()
    beat_times_local = librosa.frames_to_time(beat_frames_arr, sr=sr, hop_length=_HOP_LENGTH)
    beat_times = [round(float(t + head_trim), 6) for t in beat_times_local]

    if len(beat_times) < 4:
        beat_len = 60.0 / max(40.0, min(240.0, tempo_est))
        beat_times = [round(head_trim + (i * beat_len), 6) for i in range(max(4, bar_count * 4))]
        beat_energy = np.asarray([1.0 for _ in beat_times], dtype=float)
    else:
        rms = librosa.feature.rms(y=rms_source, frame_length=2048, hop_length=_HOP_LENGTH)[0]
        beat_energy = np.asarray(
            [float(rms[min(max(int(f), 0), len(rms) - 1)]) for f in beat_frames_arr],
            dtype=float,
        )

    phase_scores_raw = [0.0, 0.0, 0.0, 0.0]
    for i, e in enumerate(beat_energy):
        phase_scores_raw[i % 4] += float(max(0.0, e))
    total_phase = sum(phase_scores_raw)
    phase_scores = [round((x / total_phase), 4) if total_phase > 1e-9 else 0.0 for x in phase_scores_raw]
    phase_offset = int(max(range(4), key=lambda i: phase_scores_raw[i]))
    sorted_phase = sorted(phase_scores_raw, reverse=True)
    if total_phase <= 1e-9:
        phase_conf = 0.0
    else:
        sep = max(0.0, sorted_phase[0] - (sorted_phase[1] if len(sorted_phase) > 1 else 0.0)) / total_phase
        phase_conf = min(1.0, 0.25 + (1.8 * sep))

    selected_downbeat = beat_times[phase_offset] if phase_offset < len(beat_times) else beat_times[0]
    bar_starts = [round(beat_times[i], 6) for i in range(phase_offset, len(beat_times), 4)]
    if not bar_starts:
        bar_starts = [round(selected_downbeat, 6)]
    while len(bar_starts) < bar_count:
        beat_len = 60.0 / max(40.0, min(240.0, tempo_est))
        next_bar = bar_starts[-1] + (4.0 * beat_len)
        bar_starts.append(round(next_bar, 6))
    bar_starts = bar_starts[:bar_count]

    beat_grid = beat_times[: max(bar_count * 4, 4)]

    bar_energy_raw = [0.0 for _ in range(bar_count)]
    bar_accent_raw = [0.0 for _ in range(bar_count)]
    for i, e in enumerate(beat_energy):
        bar_idx = (i - phase_offset) // 4
        if 0 <= bar_idx < bar_count:
            ev = float(max(0.0, e))
            bar_energy_raw[bar_idx] += ev
            if i % 4 == phase_offset:
                bar_accent_raw[bar_idx] += ev
    max_e = max(bar_energy_raw) if bar_energy_raw else 0.0
    max_a = max(bar_accent_raw) if bar_accent_raw else 0.0
    bar_energy = [round((v / max_e) if max_e > 1e-9 else 0.0, 4) for v in bar_energy_raw]
    bar_accent = [round((v / max_a) if max_a > 1e-9 else 0.0, 4) for v in bar_accent_raw]
    bar_conf = [round((0.6 * e) + (0.4 * a), 4) for e, a in zip(bar_energy, bar_accent)]

    chroma = librosa.feature.chroma_cqt(y=y_harm, sr=sr, hop_length=_HOP_LENGTH)
    low_chroma = librosa.feature.chroma_cqt(
        y=y_harm,
        sr=sr,
        hop_length=_HOP_LENGTH,
        fmin=librosa.note_to_hz("C1"),
        n_octaves=3,
    )
    structural_pc = _structural_pc_support(
        chroma=chroma,
        low_chroma=low_chroma,
        bar_starts_seconds=bar_starts,
        head_trim_seconds=head_trim,
        sr=sr,
    )
    tonal_pc, tonal_conf, mode_guess, mode_conf = _estimate_tonal_center_mode(
        chroma,
        low_chroma,
        structural_pc,
        phase_confidence=float(phase_conf),
        bar_start_confidence=float(min(phase_conf, tempo_conf)),
        fallback_key=session_key,
        fallback_scale=session_scale,
    )

    sections = _build_sections(bar_energy, bar_accent, bar_count)
    source = SourceAnalysis(
        source_lane="reference_audio",
        tempo=int(session_tempo),
        tempo_estimate_bpm=float(tempo_est),
        tempo_confidence=float(tempo_conf),
        beat_grid_seconds=[float(x) for x in beat_grid],
        bar_starts_seconds=[float(x) for x in bar_starts],
        beat_phase_offset_beats=int(phase_offset),
        beat_phase_scores=[float(x) for x in phase_scores],
        beat_phase_confidence=round(float(phase_conf), 4),
        phase_offset_used_for_generation_beats=int(phase_offset),
        bar_start_anchor_used_seconds=round(float(selected_downbeat), 6),
        generation_aligned_to_anchor=False,
        downbeat_guess_bar_index=0,
        downbeat_confidence=round(float(phase_conf), 4),
        # Bar starts are only as reliable as the weaker of tempo and phase.
        bar_start_confidence=round(float(min(phase_conf, tempo_conf)), 4),
        tonal_center_pc_guess=int(tonal_pc),
        tonal_center_confidence=float(tonal_conf),
        scale_mode_guess=str(mode_guess),
        scale_mode_confidence=float(mode_conf),
        sections=sections,
        bar_energy=bar_energy,
        bar_accent_profile=bar_accent,
        bar_confidence_profile=bar_conf,
    )
    return AudioAnalysisResult(
        source_analysis=source,
        duration_seconds=round(duration_sec, 4),
        head_trim_seconds=round(head_trim, 4),
    )
