"""Neutral bass-family capability profiles.

The player brain produces universal musical intent. These profiles constrain
that intent so it remains credible for a broad instrument family before a
vendor-specific renderer (Trilian, MODO, Kontakt, and so on) translates it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal


BassInstrumentFamily = Literal[
    "finger_bass",
    "fretless_bass",
    "upright_bass",
    "sub_bass",
]


@dataclass(frozen=True, slots=True)
class BassInstrumentProfile:
    id: BassInstrumentFamily
    label: str
    description: str
    density_ceiling: float
    expression_ceiling: float
    connected_bias: float
    allow_ghost: bool
    allow_dead: bool
    allow_grace: bool
    sustain_multiplier: float
    timing_scale: float


_PROFILES: Final[dict[str, BassInstrumentProfile]] = {
    "finger_bass": BassInstrumentProfile(
        id="finger_bass",
        label="Fingered",
        description="Balanced attacks, space, ghost notes, and connected phrasing.",
        density_ceiling=1.0,
        expression_ceiling=1.0,
        connected_bias=1.0,
        allow_ghost=True,
        allow_dead=True,
        allow_grace=True,
        sustain_multiplier=1.0,
        timing_scale=1.0,
    ),
    "fretless_bass": BassInstrumentProfile(
        id="fretless_bass",
        label="Fretless",
        description="Sustained connections, legato arrivals, and selective slides.",
        density_ceiling=0.55,
        expression_ceiling=1.0,
        connected_bias=1.75,
        allow_ghost=False,
        allow_dead=False,
        allow_grace=True,
        sustain_multiplier=1.08,
        timing_scale=0.8,
    ),
    "sub_bass": BassInstrumentProfile(
        id="sub_bass",
        label="Sub / Synth",
        description="Protected low-register space, longer tones, and controlled retriggers.",
        density_ceiling=-0.35,
        expression_ceiling=0.6,
        connected_bias=0.0,
        allow_ghost=False,
        allow_dead=False,
        allow_grace=False,
        sustain_multiplier=1.12,
        timing_scale=0.45,
    ),
    "upright_bass": BassInstrumentProfile(
        id="upright_bass",
        label="Upright (Pizzicato)",
        description="Natural decay, voice-leading movement, and believable position changes.",
        density_ceiling=0.65,
        expression_ceiling=0.85,
        connected_bias=0.35,
        allow_ghost=True,
        allow_dead=False,
        allow_grace=True,
        sustain_multiplier=0.9,
        timing_scale=0.85,
    ),
}

_ALIASES: Final[dict[str, str]] = {
    "finger": "finger_bass",
    "fingered": "finger_bass",
    "slap_bass": "finger_bass",
    "fretless": "fretless_bass",
    "double_bass": "upright_bass",
    "upright": "upright_bass",
    "acoustic_bass": "upright_bass",
    "sub": "sub_bass",
    "sub_synth": "sub_bass",
    "synth_bass": "sub_bass",
}


def normalize_bass_instrument_family(value: str | None) -> BassInstrumentFamily:
    raw = str(value or "finger_bass").strip().lower()
    normalized = _ALIASES.get(raw, raw)
    if normalized not in _PROFILES:
        normalized = "finger_bass"
    return normalized  # type: ignore[return-value]


def bass_instrument_profile(value: str | None) -> BassInstrumentProfile:
    return _PROFILES[normalize_bass_instrument_family(value)]


def constrain_instrument_controls(
    value: str | None,
    *,
    density_bias: float,
    expression_amount: float,
) -> tuple[float, float]:
    profile = bass_instrument_profile(value)
    density = max(-1.0, min(float(profile.density_ceiling), float(density_bias)))
    expression = max(0.0, min(float(profile.expression_ceiling), float(expression_amount)))
    return density, expression


def public_bass_instrument_profiles() -> tuple[BassInstrumentProfile, ...]:
    return tuple(
        _PROFILES[key]
        for key in ("finger_bass", "fretless_bass", "upright_bass", "sub_bass")
    )


__all__ = [
    "BassInstrumentFamily",
    "BassInstrumentProfile",
    "bass_instrument_profile",
    "constrain_instrument_controls",
    "normalize_bass_instrument_family",
    "public_bass_instrument_profiles",
]
