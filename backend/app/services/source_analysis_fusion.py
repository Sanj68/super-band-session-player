"""Fuse harmony-led source analysis with a separate groove reference."""

from __future__ import annotations

from app.models.session import SourceAnalysis


_GROOVE_TIMING_FIELDS = (
    "tempo",
    "tempo_estimate_bpm",
    "tempo_confidence",
    "beat_grid_seconds",
    "bar_starts_seconds",
    "beat_phase_offset_beats",
    "beat_phase_scores",
    "beat_phase_confidence",
    "phase_offset_used_for_generation_beats",
    "bar_start_anchor_used_seconds",
    "generation_aligned_to_anchor",
    "downbeat_guess_bar_index",
    "downbeat_confidence",
    "bar_start_confidence",
    "bar_energy",
    "bar_accent_profile",
    "bar_confidence_profile",
    "source_groove_resolution",
    "source_onset_weight",
    "source_kick_weight",
    "source_snare_weight",
    "source_slot_pressure",
    "source_groove_confidence",
)


def fuse_source_and_groove(
    musical_source: SourceAnalysis,
    groove_reference: SourceAnalysis | None,
) -> SourceAnalysis:
    """Return one conditioning view with source harmony and groove-reference rhythm.

    Tonal centre, scale and musical sections remain owned by ``musical_source``.
    Timing, kick/snare maps and rhythmic confidence come from the optional
    ``groove_reference``. Keeping this fusion explicit prevents a drum loop's
    noisy chroma from silently becoming the session harmony.
    """

    if groove_reference is None:
        return musical_source

    updates = {
        field: getattr(groove_reference, field)
        for field in _GROOVE_TIMING_FIELDS
    }
    updates["source_lane"] = "musical_source+groove_reference"
    updates["source_metadata"] = {
        **musical_source.source_metadata,
        "analysis_fusion": {
            "harmony_source": musical_source.source_lane,
            "groove_source": groove_reference.source_lane,
            "groove_tempo_bpm": groove_reference.tempo_estimate_bpm,
            "groove_tempo_confidence": groove_reference.tempo_confidence,
        },
    }
    return musical_source.model_copy(update=updates)
