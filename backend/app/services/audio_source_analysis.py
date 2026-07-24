"""Audio-driven source analysis helpers for reference-audio workflow."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import librosa
import numpy as np

from app.models.session import SectionSpan, SourceAnalysis
from app.utils import music_theory as mt

_GROOVE_SLOTS = 16

_HOP_LENGTH = 512
_TARGET_SR = 22050
_MODE_CANDIDATES: tuple[str, ...] = (
    "major",
    "minor",
)
_CHORD_ROOT_NAMES: tuple[str, ...] = (
    "C",
    "C#",
    "D",
    "Eb",
    "E",
    "F",
    "F#",
    "G",
    "Ab",
    "A",
    "Bb",
    "B",
)

# Albrecht-Shanahan (2013) key profiles — derived from an audio corpus rather
# than probe-tone ratings, so they fit chroma vectors far better than
# Krumhansl-Kessler. Backported 2026-06-12 from Pocket Export's hardened
# KeyBPMAnalyzer (7/20 -> 11/20 exact keys on real loops); the v0.3a
# validation pack showed the same circle-of-fifths confusion KK caused there.
_MAJOR_PROFILE = np.asarray([0.238, 0.006, 0.111, 0.006, 0.137, 0.094, 0.016, 0.214, 0.009, 0.080, 0.008, 0.081], dtype=float)
_MINOR_PROFILE = np.asarray([0.220, 0.006, 0.104, 0.123, 0.019, 0.103, 0.012, 0.214, 0.062, 0.022, 0.061, 0.052], dtype=float)


@dataclass
class AudioAnalysisResult:
    source_analysis: SourceAnalysis
    duration_seconds: float
    head_trim_seconds: float


@dataclass(frozen=True)
class FilenameMusicalHints:
    tempo_bpm: int | None = None
    key: str | None = None
    scale: str | None = None


def parse_filename_musical_hints(filename: str | None) -> FilenameMusicalHints:
    """Extract strict sample-pack BPM/key tokens without guessing from prose."""
    stem = Path(str(filename or "")).stem
    tokens = [token for token in re.split(r"[\s_-]+", stem) if token]

    tempo_bpm: int | None = None
    for token in tokens:
        if token.isdigit():
            value = int(token)
            if 40 <= value <= 240:
                tempo_bpm = value

    key: str | None = None
    scale: str | None = None
    for token in tokens:
        match = re.fullmatch(r"([A-Ga-g])([#b]?)(maj|major|min|minor|m)", token)
        if not match:
            continue
        root, accidental, mode = match.groups()
        key = f"{root.upper()}{accidental}"
        scale = "major" if mode.lower() in {"maj", "major"} else "natural_minor"

    return FilenameMusicalHints(tempo_bpm=tempo_bpm, key=key, scale=scale)


def _profile_cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    denominator = float(np.linalg.norm(left) * np.linalg.norm(right))
    if denominator <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, float(np.dot(left, right)) / denominator))


def infer_tentative_chord_map_from_profiles(
    bar_profiles: list[np.ndarray],
) -> dict[str, object]:
    """Return an explicitly tentative chord map from per-bar pitch-class energy.

    This is a suggestion surface, not clearance for generation. Repeating
    material is folded only when the measured profiles actually recur.
    """
    profiles: list[np.ndarray] = []
    for raw in bar_profiles:
        row = np.asarray(raw, dtype=float).reshape(-1)
        if row.size != 12:
            continue
        row = np.maximum(row, 0.0)
        total = float(np.sum(row))
        if total <= 1e-12:
            continue
        profiles.append(row / total)
    if not profiles:
        return {
            "chords": [],
            "confidence": [],
            "period_bars": 0,
            "repeat_similarity": 0.0,
            "source": "uploaded_audio_chroma_tentative",
        }

    period = len(profiles)
    repeat_similarity = 0.0
    for candidate in (1, 2, 4, 8):
        if candidate >= len(profiles) or len(profiles) % candidate != 0:
            continue
        similarities = [
            _profile_cosine_similarity(profiles[i], profiles[i % candidate])
            for i in range(candidate, len(profiles))
        ]
        mean_similarity = float(np.mean(similarities)) if similarities else 0.0
        if mean_similarity >= 0.94:
            period = candidate
            repeat_similarity = mean_similarity
            break

    folded = [
        np.mean(profiles[offset::period], axis=0)
        for offset in range(period)
    ]
    root_rows: list[tuple[int, float, float]] = []
    confidences: list[float] = []
    for profile in folded:
        candidates: list[tuple[float, int, str]] = []
        for root_pc, _root_name in enumerate(_CHORD_ROOT_NAMES):
            for suffix, intervals in (("", (0, 4, 7)), ("m", (0, 3, 7))):
                chord_pcs = tuple((root_pc + interval) % 12 for interval in intervals)
                score = (0.22 * float(profile[root_pc])) + (
                    0.30 * sum(float(profile[pc]) for pc in chord_pcs)
                )
                candidates.append((score, root_pc, suffix))
        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        top_score, top_root, _top_suffix = candidates[0]
        second_score = candidates[1][0] if len(candidates) > 1 else 0.0
        confidence = (
            max(0.0, min(1.0, (top_score - second_score) / top_score))
            if top_score > 1e-12
            else 0.0
        )
        major_score = next(
            score for score, root, suffix in candidates if root == top_root and suffix == ""
        )
        minor_score = next(
            score for score, root, suffix in candidates if root == top_root and suffix == "m"
        )
        root_rows.append((top_root, major_score, minor_score))
        confidences.append(round(confidence, 4))

    # Resolve weak major/minor ties against one common diatonic pitch set.
    # This corrects noisy-third flips (for example Bb -> Bbm in a D-minor
    # source) without overriding clear borrowed-chord evidence such as D major.
    scale_candidates: list[tuple[float, set[int]]] = []
    for tonic in range(12):
        for mode in ("major", "natural_minor"):
            scale_pcs = {
                (tonic + interval) % 12
                for interval in mt.scale_intervals(mode)
            }
            compatibility = 0.0
            for root_pc, _major_score, _minor_score in root_rows:
                major = {(root_pc + interval) % 12 for interval in (0, 4, 7)}
                minor = {(root_pc + interval) % 12 for interval in (0, 3, 7)}
                compatibility += max(
                    len(major & scale_pcs),
                    len(minor & scale_pcs),
                ) / 3.0
            scale_candidates.append((compatibility, scale_pcs))
    common_scale = max(scale_candidates, key=lambda item: item[0])[1]

    chords: list[str] = []
    for (root_pc, major_score, minor_score), confidence in zip(
        root_rows,
        confidences,
        strict=False,
    ):
        suffix = "" if major_score >= minor_score else "m"
        if confidence < 0.08:
            major = {(root_pc + interval) % 12 for interval in (0, 4, 7)}
            minor = {(root_pc + interval) % 12 for interval in (0, 3, 7)}
            major_diatonic = major.issubset(common_scale)
            minor_diatonic = minor.issubset(common_scale)
            if major_diatonic != minor_diatonic:
                suffix = "" if major_diatonic else "m"
        chords.append(f"{_CHORD_ROOT_NAMES[root_pc]}{suffix}")

    return {
        "chords": chords,
        "confidence": confidences,
        "period_bars": period,
        "repeat_similarity": round(repeat_similarity, 4),
        "source": "uploaded_audio_chroma_tentative",
    }


def infer_bar_count_from_duration(duration_seconds: float, tempo_bpm: float) -> int | None:
    """Return an integer 4/4 loop length only when duration is a close fit."""
    if duration_seconds <= 0.0 or tempo_bpm <= 0.0:
        return None
    raw_bars = (float(duration_seconds) * float(tempo_bpm)) / 240.0
    nearest = int(round(raw_bars))
    if not 1 <= nearest <= 128:
        return None
    if abs(raw_bars - nearest) > 0.12:
        return None
    return nearest


def infer_bar_count_from_beats(beat_count: int) -> int | None:
    """Infer 4/4 length from a tracked beat train, tolerating clipped edge beats."""
    if beat_count < 3:
        return None
    nearest = int(round(float(beat_count) / 4.0))
    if not 1 <= nearest <= 128:
        return None
    # Beat trackers commonly omit the first or last transient. More than two
    # missing/extra beats is no longer a trustworthy whole-bar observation.
    if abs(int(beat_count) - (nearest * 4)) > 2:
        return None
    return nearest


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
        frame_rate = float(sr) / float(_HOP_LENGTH)
        order = np.argsort(tg)[::-1]
        for idx in order:
            bpm = float(freqs[idx])
            if not np.isfinite(bpm) or bpm < 30.0 or bpm > 300.0:
                continue
            # Parabolic interpolation on the tempogram peak: the integer-lag
            # grid quantizes BPM (~2.5% near 120 at 22050/512 — the
            # validation pack's 120->117.19 flat bias). The true peak sits
            # between bins; refine the lag before converting to BPM.
            k = int(idx)
            if 1 <= k < len(tg) - 1:
                denom = tg[k - 1] - (2.0 * tg[k]) + tg[k + 1]
                if abs(denom) > 1e-12:
                    delta = 0.5 * (tg[k - 1] - tg[k + 1]) / denom
                    if -1.0 < delta < 1.0:
                        lag = k + float(delta)
                        if lag > 1e-6:
                            refined = frame_rate * 60.0 / lag
                            if np.isfinite(refined) and 30.0 <= refined <= 300.0:
                                bpm = float(refined)
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


def _comb_pulse_fractional(onset_env: np.ndarray, sr: int, bpm: float) -> float:
    """Comb autocorrelation at a FRACTIONAL beat lag (linear interpolation).

    Integer-lag autocorrelation quantizes tempo; interpolating the shifted
    envelope lets nearby BPM values be compared at sub-grid resolution. The
    comb (lag + 2*lag) sharpens the true tempo against its neighbours —
    same idea as the spectral-flux reference script that read the dense
    Beat 1 mix correctly.
    """
    if bpm <= 1e-6 or onset_env.size < 64:
        return 0.0
    env = onset_env.astype(float) - float(np.mean(onset_env))
    denom = float(np.dot(env, env))
    if denom <= 1e-9:
        return 0.0
    frame_rate = float(sr) / float(_HOP_LENGTH)
    total = 0.0
    weight_sum = 0.0
    for mult, w in ((1.0, 1.0), (2.0, 0.5)):
        lag = frame_rate * 60.0 / bpm * mult
        i0 = int(np.floor(lag))
        frac = lag - i0
        if i0 + 1 >= env.size - 8:
            continue
        n = env.size - i0 - 1
        shifted = ((1.0 - frac) * env[i0 : i0 + n]) + (frac * env[i0 + 1 : i0 + 1 + n])
        total += w * (float(np.dot(env[:n], shifted)) / denom)
        weight_sum += w
    return max(0.0, total / weight_sum) if weight_sum > 0 else 0.0


def _refine_tempo(onset_env: np.ndarray, sr: int, bpm: float) -> float:
    """Polish a chosen BPM on a fine local grid (±5%, 0.05 BPM steps)."""
    if bpm <= 1e-6:
        return bpm
    best_bpm = float(bpm)
    best_p = _comb_pulse_fractional(onset_env, sr, best_bpm)
    for cand in np.arange(bpm * 0.95, bpm * 1.05 + 1e-9, 0.05):
        p = _comb_pulse_fractional(onset_env, sr, float(cand))
        if p > best_p:
            best_bpm, best_p = float(cand), p
    return best_bpm


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
    dot = float(np.dot(pc_dist, templ))
    # Tonic emphasis + fifth/fourth penalties (Pocket Export hardening,
    # adapted): reward energy on the candidate tonic; penalise candidates
    # whose *fifth* dominates (true key read as IV) and — the mirror case the
    # CQT-chroma path needs — candidates whose *fourth* carries heavy energy
    # (true key read as V: G-for-C on a C-G7 vamp puts C, the fourth of G,
    # under the wrong tonic).
    t = int(tonic) % 12
    root_boost = 0.30 * float(pc_dist[t])
    fifth_penalty = 0.08 * float(pc_dist[(t + 7) % 12])
    fourth_penalty = 0.06 * float(pc_dist[(t + 5) % 12])
    return dot + root_boost - fifth_penalty - fourth_penalty


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
            bar_w += 0.55  # phrase-entry — where the tonic lives
        if i % 4 == 3:
            # phrase-end/cadence bars carry the DOMINANT in vamp/loop
            # material; over-weighting them was a systematic vote for V
            # (validation pack: G-for-C, Bb-for-Eb). Keep a mild cue only.
            bar_w += 0.20
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
    # Sub-grid polish: beat_track's value sits on the integer-lag grid
    # (120 -> 117.19 class of error); refine on a fine local grid with
    # fractional-lag comb autocorrelation.
    chosen = _refine_tempo(onset_env, sr, chosen)
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
    global_pcp: np.ndarray | None = None,
) -> tuple[int, float, str, float]:
    fallback_pc = mt.key_root_pc(fallback_key)
    fallback_mode = _normalize_mode_label(mt.describe_scale(fallback_scale))
    if chroma.size == 0:
        return fallback_pc, 0.2, fallback_mode, 0.2
    pc_dist = _pc_distribution(chroma)
    # When a harmonic pitch-class profile is provided it is the SOLE voter
    # for the key decision: the matrix study + decomposition probe
    # (docs/VALIDATION_RESULTS_2026-06-12.md) showed that blending even 20%
    # of CQT-derived components erases the AS-profile scorer's tonic margin
    # on vamp material (G-for-C etc.). Low/tail/structural cues survive as
    # tie-breakers (relative-key disambiguation), not as voters.
    hpcp_drive = False
    if global_pcp is not None:
        gp = np.asarray(global_pcp, dtype=float).reshape(-1)
        if gp.size == 12 and float(np.sum(gp)) > 1e-9:
            pc_dist = gp / float(np.sum(gp))
            hpcp_drive = True
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

    # Weights re-balanced 2026-06-12 against the validation pack: the HPCP
    # global component is the reliable tonal signal (matrix study in
    # docs/VALIDATION_RESULTS_2026-06-12.md); heavy low-chroma weighting fed
    # the vamp dominant's bass bars straight into the key vote. Low/tail/
    # structural keep a voice as tiebreakers, not as voters.
    if hpcp_drive:
        # HPCP is the sole voter — no dilution (see comment above).
        w_global, w_low, w_tail, w_struct = 1.0, 0.0, 0.0, 0.0
    else:
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
            # Net root support: bass/tail/structural energy on the candidate
            # tonic MINUS the same evidence for its fifth — on vamp material
            # the dominant's bass bars otherwise hand the win to V even after
            # the profile-level penalties. Disabled entirely under HPCP drive:
            # the probe showed any support term re-leaks the dominant.
            if hpcp_drive:
                combos.append((tonic, mode, score))
            else:
                support_at = lambda pc: (  # noqa: E731
                    (0.42 * low_dist[pc]) + (0.28 * tail_dist[pc]) + (0.30 * structural_dist[pc])
                )
                root_support = support_at(tonic) - 0.5 * support_at((tonic + 7) % 12)
                combos.append((tonic, mode, score + (0.15 * float(root_support))))
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
        not hpcp_drive  # under HPCP drive the scorer's verdict stands —
        # CQT low-chroma flipped correct answers to their relative key
        # (C-minor-for-Eb-major on the validation pack)
        and rel_score > -1e8
        and abs(best_score - rel_score) <= 0.08
        and float(low_dist[rel_tonic]) > float(low_dist[best_tonic]) + 0.02
    ):
        best_tonic, best_mode, best_score = rel_tonic, rel_mode, rel_score

    # Mode by the third degree. The Albrecht-Shanahan profiles weight the
    # tonic asymmetrically (major 0.238 vs minor 0.220), so on root-heavy
    # CQT chroma the profile match alone tips toward major regardless of the
    # actual third. Mode lives in the third: once the tonic is settled,
    # prefer the mode whose third clearly dominates.
    maj3 = float(blend[(best_tonic + 4) % 12])
    min3 = float(blend[(best_tonic + 3) % 12])
    if maj3 > min3 * 1.15 and best_mode != "major":
        best_mode = "major"
        best_score = float(combo_lookup.get((best_tonic, "major"), best_score))
    elif min3 > maj3 * 1.15 and best_mode != "minor":
        best_mode = "minor"
        best_score = float(combo_lookup.get((best_tonic, "minor"), best_score))

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


def _soft_backbeat_snare_prior() -> np.ndarray:
    """Gentle weighting around sixteenth slots 4 and 12 (beats 2 and 4); not hard hits."""
    s = np.arange(_GROOVE_SLOTS, dtype=float)
    bump = 0.07 * np.exp(-0.5 * ((s - 4.0) / 1.35) ** 2) + 0.07 * np.exp(-0.5 * ((s - 12.0) / 1.35) ** 2)
    return 1.0 + bump


def _derive_source_groove_slot_maps(
    *,
    y_perc: np.ndarray,
    y_trimmed: np.ndarray,
    sr: int,
    onset_env: np.ndarray,
    bar_starts_abs: list[float],
    bar_count: int,
    head_trim_seconds: float,
    tempo_bpm: float,
    bar_confidence: list[float],
) -> tuple[list[list[float]], list[list[float]], list[list[float]], list[list[float]], list[float]]:
    """Crude per-bar 16th-slot maps from percussive audio; values are later clamped by ``SourceAnalysis``."""
    onset_weight: list[list[float]] = [[0.0] * _GROOVE_SLOTS for _ in range(bar_count)]
    kick_weight: list[list[float]] = [[0.0] * _GROOVE_SLOTS for _ in range(bar_count)]
    snare_weight: list[list[float]] = [[0.0] * _GROOVE_SLOTS for _ in range(bar_count)]
    slot_pressure: list[list[float]] = [[0.0] * _GROOVE_SLOTS for _ in range(bar_count)]
    groove_conf: list[float] = [0.0 for _ in range(bar_count)]

    if bar_count <= 0 or y_trimmed.size < 8:
        return onset_weight, kick_weight, snare_weight, slot_pressure, groove_conf

    bpm = max(40.0, min(240.0, float(tempo_bpm)))
    bar_len_sec = 4.0 * (60.0 / bpm)

    onset = np.asarray(onset_env, dtype=float).ravel()
    n = int(onset.size)
    if n < 2:
        return onset_weight, kick_weight, snare_weight, slot_pressure, groove_conf

    S = np.abs(librosa.stft(y_perc, n_fft=2048, hop_length=_HOP_LENGTH, center=True))
    n = min(n, int(S.shape[1]))
    onset = onset[:n]
    S = S[:, :n]
    freqs = librosa.fft_frequencies(sr=sr, n_fft=2048)
    low_m = (freqs > 1.0) & (freqs < 220.0)
    mid_m = (freqs >= 220.0) & (freqs < 7000.0)
    kick_band = np.mean(S[low_m, :], axis=0) if np.any(low_m) else np.zeros(n, dtype=float)
    snare_band = np.mean(S[mid_m, :], axis=0) if np.any(mid_m) else np.zeros(n, dtype=float)
    kick_onset = np.maximum(0.0, np.diff(kick_band, prepend=float(kick_band[0])))
    snare_onset = np.maximum(0.0, np.diff(snare_band, prepend=float(snare_band[0])))

    rms = librosa.feature.rms(y=y_trimmed, frame_length=2048, hop_length=_HOP_LENGTH, center=True)[0]
    rms = np.asarray(rms, dtype=float).ravel()
    if rms.size < n:
        rms = np.pad(rms, (0, n - int(rms.size)))
    else:
        rms = rms[:n]

    def _abs_time_to_frame(t_abs: float) -> int:
        local_t = max(0.0, float(t_abs) - float(head_trim_seconds))
        fr_arr = librosa.time_to_frames(local_t, sr=sr, hop_length=_HOP_LENGTH)
        fr = int(float(np.asarray(fr_arr).ravel()[0]))
        return max(0, min(n - 1, fr))

    prior = _soft_backbeat_snare_prior()
    global_onset_peak = float(np.max(onset)) + 1e-9

    for bar_i in range(bar_count):
        t0 = float(bar_starts_abs[bar_i]) if bar_i < len(bar_starts_abs) else float(head_trim_seconds) + bar_i * bar_len_sec
        if bar_i + 1 < len(bar_starts_abs):
            t1 = float(bar_starts_abs[bar_i + 1])
        else:
            t1 = t0 + bar_len_sec
        if t1 <= t0:
            t1 = t0 + bar_len_sec
        slot_w = (t1 - t0) / float(_GROOVE_SLOTS)

        o_row = np.zeros(_GROOVE_SLOTS, dtype=float)
        k_row = np.zeros(_GROOVE_SLOTS, dtype=float)
        sn_row = np.zeros(_GROOVE_SLOTS, dtype=float)
        r_row = np.zeros(_GROOVE_SLOTS, dtype=float)

        for s in range(_GROOVE_SLOTS):
            ts0 = t0 + s * slot_w
            ts1 = t0 + (s + 1) * slot_w
            f0 = _abs_time_to_frame(ts0)
            f1 = _abs_time_to_frame(max(ts0 + 1e-5, ts1 - 1e-6))
            if f1 < f0:
                f1 = f0
            sl = slice(f0, min(n, f1 + 1))
            o_row[s] = float(np.max(onset[sl])) if sl.stop > sl.start else 0.0
            k_slot = float(np.max(kick_band[sl])) + 0.55 * float(np.max(kick_onset[sl])) if sl.stop > sl.start else 0.0
            sn_slot = float(np.max(snare_band[sl])) + 0.45 * float(np.max(snare_onset[sl])) if sl.stop > sl.start else 0.0
            k_row[s] = k_slot
            sn_row[s] = sn_slot
            r_row[s] = float(np.max(rms[sl])) if sl.stop > sl.start else 0.0

        sn_row = sn_row * prior
        for row in (o_row, k_row, sn_row, r_row):
            peak = float(np.max(row)) + 1e-9
            row[:] = row / peak

        p_row = 0.36 * o_row + 0.30 * k_row + 0.27 * sn_row + 0.07 * r_row
        pp = float(np.max(p_row)) + 1e-9
        p_row = p_row / pp

        onset_weight[bar_i] = [round(float(x), 6) for x in o_row]
        kick_weight[bar_i] = [round(float(x), 6) for x in k_row]
        snare_weight[bar_i] = [round(float(x), 6) for x in sn_row]
        slot_pressure[bar_i] = [round(float(x), 6) for x in p_row]

        bar_peak = float(np.max(o_row))
        activity = min(1.0, bar_peak / global_onset_peak)
        bc = float(bar_confidence[bar_i]) if bar_i < len(bar_confidence) else 0.0
        groove_conf[bar_i] = round(max(0.0, min(1.0, 0.52 * bc + 0.48 * activity)), 4)

    return onset_weight, kick_weight, snare_weight, slot_pressure, groove_conf


def analyze_reference_audio(
    *,
    audio_path: Path,
    session_tempo: int,
    bar_count: int,
    session_key: str,
    session_scale: str,
    source_filename: str | None = None,
    trust_session_bar_count: bool = True,
) -> AudioAnalysisResult:
    y, sr = librosa.load(str(audio_path), sr=_TARGET_SR, mono=True)
    if y.size == 0:
        raise ValueError("Reference audio is empty.")
    duration_sec = float(y.size) / float(sr)
    filename_hints = parse_filename_musical_hints(source_filename)
    if filename_hints.tempo_bpm is not None:
        session_tempo = filename_hints.tempo_bpm
        inferred_bars = infer_bar_count_from_duration(duration_sec, filename_hints.tempo_bpm)
        if inferred_bars is not None:
            bar_count = inferred_bars
    if filename_hints.key is not None:
        session_key = filename_hints.key
    if filename_hints.scale is not None:
        session_scale = filename_hints.scale

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
    anchor_bpm = None
    if trust_session_bar_count and bar_count > 0:
        trimmed_duration = float(len(y_trimmed)) / float(sr)
        if trimmed_duration > 1e-6:
            anchor_bpm = (240.0 * float(bar_count)) / trimmed_duration
    # Upload-first callers mark the screen's bar-count placeholder untrusted.
    # Otherwise it creates a circular half/double-time trap (for example:
    # unknown 16-bar 88 BPM bounce + placeholder 8 bars => 44).
    tempo_est, tempo_conf = _select_tempo(onset_env, sr, session_tempo, anchor_bpm=anchor_bpm)
    if filename_hints.tempo_bpm is not None:
        tempo_est = float(filename_hints.tempo_bpm)
        tempo_conf = max(float(tempo_conf), 0.9)

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

    auto_bar_count: int | None = None
    if not trust_session_bar_count and filename_hints.tempo_bpm is None and tempo_conf >= 0.5:
        auto_bar_count = infer_bar_count_from_beats(len(beat_times))
        if auto_bar_count is not None:
            bar_count = auto_bar_count

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

    onset_w, kick_w, snare_w, pressure_w, groove_bar_conf = _derive_source_groove_slot_maps(
        y_perc=y_perc,
        y_trimmed=y_trimmed,
        sr=sr,
        onset_env=onset_env,
        bar_starts_abs=bar_starts,
        bar_count=bar_count,
        head_trim_seconds=head_trim,
        tempo_bpm=float(tempo_est),
        bar_confidence=bar_conf,
    )
    groove_meta = {
        "groove_map_version": "v0.7.0",
        "groove_slots_per_bar": _GROOVE_SLOTS,
        "hop_length": _HOP_LENGTH,
        "filename_hints": {
            "tempo_bpm": filename_hints.tempo_bpm,
            "key": filename_hints.key,
            "scale": filename_hints.scale,
            "bar_count": bar_count if filename_hints.tempo_bpm is not None else None,
        },
        "auto_bar_count": auto_bar_count,
    }

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
    from app.services.harmonic_analysis import extract_fft_chroma

    global_pcp = np.asarray(extract_fft_chroma(y_trimmed, sample_rate=sr), dtype=float)
    tonal_pc, tonal_conf, mode_guess, mode_conf = _estimate_tonal_center_mode(
        chroma,
        low_chroma,
        structural_pc,
        phase_confidence=float(phase_conf),
        bar_start_confidence=float(min(phase_conf, tempo_conf)),
        fallback_key=session_key,
        fallback_scale=session_scale,
        global_pcp=global_pcp,
    )
    if filename_hints.key is not None:
        tonal_pc = mt.key_root_pc(filename_hints.key)
        tonal_conf = max(float(tonal_conf), 0.9)
    if filename_hints.scale is not None:
        mode_guess = _normalize_mode_label(filename_hints.scale)
        mode_conf = max(float(mode_conf), 0.9)

    bar_profiles: list[np.ndarray] = []
    bar_duration_seconds = 240.0 / max(40.0, min(240.0, float(tempo_est)))
    local_duration_seconds = float(len(y_trimmed)) / float(sr)
    for bar in range(max(1, int(bar_count))):
        start_seconds = float(bar) * bar_duration_seconds
        end_seconds = min(local_duration_seconds, start_seconds + bar_duration_seconds)
        if end_seconds - start_seconds < 0.4:
            break
        start_frame = max(
            0,
            int(librosa.time_to_frames(start_seconds, sr=sr, hop_length=_HOP_LENGTH)),
        )
        end_frame = min(
            int(chroma.shape[1]),
            int(librosa.time_to_frames(end_seconds, sr=sr, hop_length=_HOP_LENGTH)),
        )
        if end_frame <= start_frame:
            continue
        profile = (
            0.72 * np.mean(chroma[:, start_frame:end_frame], axis=1)
            + 0.28 * np.mean(low_chroma[:, start_frame:end_frame], axis=1)
        )
        bar_profiles.append(np.asarray(profile, dtype=float))
    groove_meta["harmony_suggestions"] = infer_tentative_chord_map_from_profiles(bar_profiles)

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
        source_groove_resolution=16,
        source_onset_weight=onset_w,
        source_kick_weight=kick_w,
        source_snare_weight=snare_w,
        source_slot_pressure=pressure_w,
        source_groove_confidence=groove_bar_conf,
        source_metadata=groove_meta,
    )
    return AudioAnalysisResult(
        source_analysis=source,
        duration_seconds=round(duration_sec, 4),
        head_trim_seconds=round(head_trim, 4),
    )
