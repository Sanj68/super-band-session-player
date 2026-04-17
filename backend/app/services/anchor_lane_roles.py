"""Anchor-driven musical roles: each generating lane shifts behavior with ``context.anchor_lane``.

Roles are chosen *before* note generation; downstream code merges role knobs into style/player
traits (or timing helpers) so density, repetition, length, syncopation, and phrase shape change
together — not as a post-pass filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Mapping, cast

# --- Role names (per generating lane, keyed by anchor lane) ---

BASS_ROLE_BY_ANCHOR: Final[dict[str, str]] = {
    "drums": "groove_lock",
    "chords": "harmonic_support",
    "bass": "lead_anchor",
    "lead": "pocket_follow",
}

CHORD_ROLE_BY_ANCHOR: Final[dict[str, str]] = {
    "drums": "comping",
    "chords": "primary",
    "bass": "space_fill",
    "lead": "reactive_comp",
}

LEAD_ROLE_BY_ANCHOR: Final[dict[str, str]] = {
    "drums": "rhythmic",
    "chords": "melodic",
    "bass": "conversational",
    "lead": "framed",
}


def bass_role_for_anchor(anchor_lane: str) -> str:
    return BASS_ROLE_BY_ANCHOR.get(anchor_lane, "groove_lock")


def chord_role_for_anchor(anchor_lane: str) -> str:
    return CHORD_ROLE_BY_ANCHOR.get(anchor_lane, "primary")


def lead_role_for_anchor(anchor_lane: str) -> str:
    return LEAD_ROLE_BY_ANCHOR.get(anchor_lane, "melodic")


def _f(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass(frozen=True)
class BassRoleKnobs:
    """Multipliers / deltas applied to bass engine traits and drum-coupling strengths."""

    density_ceiling_mult: float = 1.0
    groove_repetition_add: float = 0.0
    articulation_mult: float = 1.0
    syncopation_bias_add: float = 0.0
    offbeat_bias_add: float = 0.0
    root_anchor_strength_add: float = 0.0
    fill_activity_add: float = 0.0
    ghost_note_bias_add: float = 0.0
    kick_lock_mult: float = 1.0
    bounce_mult: float = 1.0
    restraint_mult: float = 1.0


def bass_knobs_for_role(role: str) -> BassRoleKnobs:
    if role == "groove_lock":
        return BassRoleKnobs(
            density_ceiling_mult=0.96,
            groove_repetition_add=0.1,
            articulation_mult=0.97,
            syncopation_bias_add=-0.05,
            root_anchor_strength_add=0.06,
            fill_activity_add=-0.05,
            kick_lock_mult=1.08,
            bounce_mult=1.06,
            restraint_mult=1.05,
        )
    if role == "harmonic_support":
        return BassRoleKnobs(
            density_ceiling_mult=0.88,
            groove_repetition_add=0.12,
            articulation_mult=1.08,
            syncopation_bias_add=-0.07,
            offbeat_bias_add=-0.06,
            root_anchor_strength_add=0.1,
            fill_activity_add=-0.12,
            ghost_note_bias_add=-0.04,
            kick_lock_mult=0.94,
            bounce_mult=0.96,
            restraint_mult=1.04,
        )
    if role == "lead_anchor":
        return BassRoleKnobs(
            density_ceiling_mult=1.1,
            groove_repetition_add=-0.1,
            articulation_mult=0.93,
            syncopation_bias_add=0.07,
            root_anchor_strength_add=-0.05,
            fill_activity_add=0.14,
            ghost_note_bias_add=0.04,
            kick_lock_mult=0.98,
            bounce_mult=1.04,
            restraint_mult=0.94,
        )
    # pocket_follow (anchor lead)
    return BassRoleKnobs(
        density_ceiling_mult=0.92,
        groove_repetition_add=0.06,
        articulation_mult=1.02,
        syncopation_bias_add=0.03,
        root_anchor_strength_add=0.04,
        fill_activity_add=-0.04,
        kick_lock_mult=1.0,
        bounce_mult=1.0,
        restraint_mult=1.02,
    )


def merge_bass_profile(base: Mapping[str, Any], k: BassRoleKnobs) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        {
            **base,
            "density_ceiling": max(2, min(8, int(round(base["density_ceiling"] * k.density_ceiling_mult)))),
            "groove_repetition_strength": _f(base["groove_repetition_strength"] + k.groove_repetition_add),
            "syncopation_bias": _f(base["syncopation_bias"] + k.syncopation_bias_add),
            "offbeat_bias": _f(base["offbeat_bias"] + k.offbeat_bias_add),
            "root_anchor_strength": _f(base["root_anchor_strength"] + k.root_anchor_strength_add),
            "fill_activity": _f(base["fill_activity"] + k.fill_activity_add),
            "ghost_note_bias": _f(base["ghost_note_bias"] + k.ghost_note_bias_add),
            "octave_jump_bias": base["octave_jump_bias"],
            "rest_preference": base["rest_preference"],
            "register_min": base["register_min"],
            "register_max": base["register_max"],
            "articulation_length_bias": max(0.35, min(1.45, base["articulation_length_bias"] * k.articulation_mult)),
            "contour_preference": base["contour_preference"],
        },
    )


@dataclass(frozen=True)
class ChordRoleKnobs:
    """Chord harmonic rhythm / density — applied to merged ChordProfile + per-bar timing scales."""

    voicing_density_mult: float = 1.0
    repetition_strength_add: float = 0.0
    sustain_bias_add: float = 0.0
    rhythmic_comping_add: float = 0.0
    harmonic_movement_add: float = 0.0
    inversion_activity_add: float = 0.0
    color_tone_bias_add: float = 0.0
    gap_pull_mult: float = 1.0
    sync_push_mult: float = 1.0


def chord_knobs_for_role(role: str) -> ChordRoleKnobs:
    if role == "comping":
        return ChordRoleKnobs(
            voicing_density_mult=0.9,
            repetition_strength_add=0.1,
            sustain_bias_add=-0.1,
            rhythmic_comping_add=0.12,
            harmonic_movement_add=-0.06,
            inversion_activity_add=-0.04,
            gap_pull_mult=1.12,
            sync_push_mult=1.14,
        )
    if role == "primary":
        return ChordRoleKnobs(
            voicing_density_mult=1.1,
            repetition_strength_add=-0.08,
            sustain_bias_add=0.1,
            rhythmic_comping_add=-0.08,
            harmonic_movement_add=0.1,
            inversion_activity_add=0.05,
            color_tone_bias_add=0.04,
            gap_pull_mult=0.86,
            sync_push_mult=0.88,
        )
    if role == "space_fill":
        return ChordRoleKnobs(
            voicing_density_mult=0.82,
            repetition_strength_add=0.14,
            sustain_bias_add=-0.14,
            rhythmic_comping_add=0.06,
            harmonic_movement_add=-0.14,
            inversion_activity_add=-0.06,
            gap_pull_mult=1.2,
            sync_push_mult=1.06,
        )
    # reactive_comp
    return ChordRoleKnobs(
        voicing_density_mult=0.9,
        repetition_strength_add=0.08,
        sustain_bias_add=-0.06,
        rhythmic_comping_add=0.08,
        harmonic_movement_add=-0.05,
        gap_pull_mult=1.06,
        sync_push_mult=1.1,
    )


def merge_chord_profile(base: Mapping[str, Any], k: ChordRoleKnobs) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        {
            **base,
            "voicing_density": _f(base["voicing_density"] * k.voicing_density_mult),
            "repetition_strength": _f(base["repetition_strength"] + k.repetition_strength_add),
            "sustain_bias": _f(base["sustain_bias"] + k.sustain_bias_add),
            "rhythmic_comping_bias": _f(base["rhythmic_comping_bias"] + k.rhythmic_comping_add),
            "harmonic_movement_bias": _f(base["harmonic_movement_bias"] + k.harmonic_movement_add),
            "inversion_activity": _f(base["inversion_activity"] + k.inversion_activity_add),
            "color_tone_bias": _f(base["color_tone_bias"] + k.color_tone_bias_add),
            "stagger_bias": base["stagger_bias"],
            "register_spread_bias": base["register_spread_bias"],
        },
    )


@dataclass(frozen=True)
class LeadRoleKnobs:
    """Lead phrase / pocket — does not replace ``lead_style``; scales engine intensity."""

    burst_mult: float = 1.0
    dur_lo_scale: float = 1.0
    dur_hi_scale: float = 1.0
    rhythmic_displacement_add: float = 0.0
    phrase_occ_bias: float = 0.0
    lag_add_16: int = 0
    bar_rest_roll_mult: float = 1.0


def lead_knobs_for_role(role: str) -> LeadRoleKnobs:
    if role == "rhythmic":
        return LeadRoleKnobs(
            burst_mult=1.12,
            dur_lo_scale=0.92,
            dur_hi_scale=0.94,
            rhythmic_displacement_add=0.1,
            phrase_occ_bias=0.04,
            lag_add_16=0,
            bar_rest_roll_mult=0.94,
        )
    if role == "melodic":
        return LeadRoleKnobs(
            burst_mult=0.9,
            dur_lo_scale=1.04,
            dur_hi_scale=1.06,
            rhythmic_displacement_add=-0.07,
            phrase_occ_bias=-0.04,
            lag_add_16=0,
            bar_rest_roll_mult=1.04,
        )
    if role == "conversational":
        return LeadRoleKnobs(
            burst_mult=0.86,
            dur_lo_scale=1.0,
            dur_hi_scale=1.04,
            rhythmic_displacement_add=0.08,
            phrase_occ_bias=0.055,
            lag_add_16=2,
            bar_rest_roll_mult=1.08,
        )
    # framed (anchor is lead)
    return LeadRoleKnobs()

