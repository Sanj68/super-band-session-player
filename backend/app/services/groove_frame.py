"""Conversion helpers between SourceAnalysis groove fields and GrooveFrame DTOs."""

from __future__ import annotations

from app.models.groove_frame import GROOVE_SLOTS, GrooveFrame
from app.models.session import SourceAnalysis


def _row_or_zero(rows: list[list[float]] | None, i: int) -> list[float]:
    if not rows or i >= len(rows) or rows[i] is None:
        return [0.0] * GROOVE_SLOTS
    row = list(rows[i])[:GROOVE_SLOTS]
    if len(row) < GROOVE_SLOTS:
        row.extend([0.0] * (GROOVE_SLOTS - len(row)))
    return [float(x) for x in row]


def _conf_or_zero(values: list[float] | None, i: int) -> float:
    if not values or i >= len(values):
        return 0.0
    try:
        return float(values[i])
    except (TypeError, ValueError):
        return 0.0


def groove_frames_from_source_analysis(source_analysis: SourceAnalysis) -> list[GrooveFrame]:
    """Project each bar's groove rows into a GrooveFrame list (lossless for groove fields)."""
    bars = max(
        len(source_analysis.source_slot_pressure or []),
        len(source_analysis.source_kick_weight or []),
        len(source_analysis.source_snare_weight or []),
        len(source_analysis.source_onset_weight or []),
        len(source_analysis.bar_energy or []),
    )
    tempo_bpm = float(source_analysis.tempo_estimate_bpm) if source_analysis.tempo_estimate_bpm else float(source_analysis.tempo)
    resolution = int(source_analysis.source_groove_resolution or GROOVE_SLOTS)
    source_tag = source_analysis.source_lane or "reference_audio"
    metadata = dict(source_analysis.source_metadata or {})

    out: list[GrooveFrame] = []
    for i in range(bars):
        out.append(
            GrooveFrame(
                bar_index=i,
                tempo_bpm=tempo_bpm,
                resolution=resolution,
                onset_weight=_row_or_zero(source_analysis.source_onset_weight, i),
                kick_weight=_row_or_zero(source_analysis.source_kick_weight, i),
                snare_weight=_row_or_zero(source_analysis.source_snare_weight, i),
                slot_pressure=_row_or_zero(source_analysis.source_slot_pressure, i),
                confidence=_conf_or_zero(source_analysis.source_groove_confidence, i),
                source_tag=source_tag,
                source_metadata=metadata,
            )
        )
    return out


def _frames_by_bar(frames: list[GrooveFrame]) -> dict[int, GrooveFrame]:
    by_bar: dict[int, GrooveFrame] = {}
    for f in frames:
        by_bar[int(f.bar_index)] = f
    return by_bar


def source_analysis_from_groove_frames(
    existing_source_analysis: SourceAnalysis,
    frames: list[GrooveFrame],
) -> SourceAnalysis:
    """Replace groove matrices on an existing SourceAnalysis from frames; preserve all other fields."""
    return merge_groove_frames(existing_source_analysis, frames, replace_existing=True)


def merge_groove_frames(
    existing_source_analysis: SourceAnalysis,
    frames: list[GrooveFrame],
    replace_existing: bool = False,
) -> SourceAnalysis:
    """Merge frames into an existing SourceAnalysis.

    - Preserves tempo estimates, beat grid, bar starts, key/mode guesses, sections, and bar_*
      profiles.
    - Only the source groove map fields are replaced/merged from the frames.
    - replace_existing=True: groove matrices are replaced from frames (missing bars become zero rows).
    - replace_existing=False: per-bar merge by bar_index; bars not in `frames` keep current values.
    """
    base = existing_source_analysis
    n_bars = max(
        len(base.source_slot_pressure or []),
        len(base.source_kick_weight or []),
        len(base.source_snare_weight or []),
        len(base.source_onset_weight or []),
        len(base.bar_energy or []),
        max((int(f.bar_index) + 1 for f in frames), default=0),
        1,
    )

    by_bar = _frames_by_bar(frames)

    onset = list(base.source_onset_weight or [])
    kick = list(base.source_kick_weight or [])
    snare = list(base.source_snare_weight or [])
    pressure = list(base.source_slot_pressure or [])
    conf = list(base.source_groove_confidence or [])

    def _ensure_len(rows: list, target: int, filler: list[float]) -> list:
        while len(rows) < target:
            rows.append(list(filler))
        return rows

    if replace_existing:
        onset = []
        kick = []
        snare = []
        pressure = []
        conf = []

    _ensure_len(onset, n_bars, [0.0] * GROOVE_SLOTS)
    _ensure_len(kick, n_bars, [0.0] * GROOVE_SLOTS)
    _ensure_len(snare, n_bars, [0.0] * GROOVE_SLOTS)
    _ensure_len(pressure, n_bars, [0.0] * GROOVE_SLOTS)
    while len(conf) < n_bars:
        conf.append(0.0)

    for bar_index in range(n_bars):
        f = by_bar.get(bar_index)
        if f is None:
            continue
        onset[bar_index] = list(f.onset_weight)
        kick[bar_index] = list(f.kick_weight)
        snare[bar_index] = list(f.snare_weight)
        pressure[bar_index] = list(f.slot_pressure)
        conf[bar_index] = float(f.confidence)

    metadata = dict(base.source_metadata or {})
    if frames:
        metadata = {**metadata}
        # Tag the most recent contributing source for diagnostics; do not overwrite version keys.
        metadata.setdefault("groove_map_version", metadata.get("groove_map_version", "v0.7.0"))
        metadata["last_groove_source_tag"] = frames[-1].source_tag

    payload = base.model_dump()
    payload.update(
        {
            "source_groove_resolution": GROOVE_SLOTS,
            "source_onset_weight": onset,
            "source_kick_weight": kick,
            "source_snare_weight": snare,
            "source_slot_pressure": pressure,
            "source_groove_confidence": conf,
            "source_metadata": metadata,
        }
    )
    return SourceAnalysis(**payload)
