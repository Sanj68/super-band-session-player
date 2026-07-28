"""Resolve producer-facing bass performance controls.

Composition controls (Style, Activity, Character, Groove Lock) decide which
notes are written.  These controls decide how that frozen phrase is played.
Keeping the two layers separate lets a producer compare Clean and Expressive
renders without moving a single structural note.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Mapping

from app.services.bass_instrument_profiles import bass_instrument_profile


CONTROL_KEYS: Final[tuple[str, ...]] = (
    "ghost",
    "mute",
    "slide",
    "legato",
    "timing_humanize",
    "velocity_humanize",
)


@dataclass(frozen=True, slots=True)
class BassPerformanceControlResolution:
    requested: dict[str, float]
    effective: dict[str, float]
    notices: tuple[str, ...] = ()

    @property
    def notice(self) -> str | None:
        return " ".join(self.notices) if self.notices else None


def _unit(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if number != number:
        number = float(default)
    return max(0.0, min(1.0, number))


def _plain_mapping(value: object | None) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python")
        if isinstance(dumped, Mapping):
            return dumped
    return None


def legacy_performance_controls(
    *,
    focus: str | None,
    expression_amount: float,
    style: str | None,
) -> dict[str, float]:
    """Translate the old one-choice Touch control into a six-axis mix."""

    touch = str(focus or "natural").strip().lower()
    amount = _unit(expression_amount, 0.5)
    bass_style = str(style or "supportive").strip().lower()
    timing = _unit(0.18 + 0.52 * amount)
    velocity = _unit(0.22 + 0.58 * amount)

    if touch == "clean":
        return {
            "ghost": 0.0,
            "mute": 0.0,
            "slide": 0.0,
            "legato": 0.0,
            "timing_humanize": _unit(0.12 + 0.18 * amount),
            "velocity_humanize": _unit(0.16 + 0.22 * amount),
        }
    if touch == "ghosted":
        return {
            "ghost": _unit(0.35 + 0.6 * amount),
            "mute": 0.0,
            "slide": 0.0,
            "legato": 0.0,
            "timing_humanize": timing,
            "velocity_humanize": velocity,
        }
    if touch == "muted":
        return {
            "ghost": 0.0,
            "mute": _unit(0.3 + 0.62 * amount),
            "slide": 0.0,
            "legato": 0.0,
            "timing_humanize": timing,
            "velocity_humanize": velocity,
        }
    if touch == "connected":
        return {
            "ghost": 0.0,
            "mute": 0.0,
            "slide": _unit(0.25 + 0.62 * amount),
            "legato": _unit(0.3 + 0.66 * amount),
            "timing_humanize": timing,
            "velocity_humanize": velocity,
        }

    # Natural is now a restrained style-aware blend.  It remains deliberately
    # lighter than the explicit Expressive Fusion preset exposed by the UIs.
    style_mix: dict[str, tuple[float, float, float, float]] = {
        "supportive": (0.08, 0.02, 0.1, 0.12),
        "melodic": (0.08, 0.0, 0.34, 0.38),
        "rhythmic": (0.3, 0.22, 0.08, 0.12),
        "slap": (0.48, 0.42, 0.14, 0.2),
        "fusion": (0.34, 0.16, 0.58, 0.48),
    }
    ghost, mute, slide, legato = style_mix.get(
        bass_style, style_mix["supportive"]
    )
    return {
        "ghost": _unit(ghost * amount),
        "mute": _unit(mute * amount),
        "slide": _unit(slide * amount),
        "legato": _unit(legato * amount),
        "timing_humanize": timing,
        "velocity_humanize": velocity,
    }


def resolve_bass_performance_controls(
    value: object | None,
    *,
    focus: str | None,
    expression_amount: float,
    style: str | None,
    instrument_family: str | None,
) -> BassPerformanceControlResolution:
    """Resolve explicit/legacy controls against broad instrument capability."""

    derived = legacy_performance_controls(
        focus=focus,
        expression_amount=expression_amount,
        style=style,
    )
    supplied = _plain_mapping(value)
    requested = {
        key: _unit(
            supplied.get(key, derived[key]) if supplied is not None else derived[key],
            derived[key],
        )
        for key in CONTROL_KEYS
    }

    family = bass_instrument_profile(instrument_family)
    caps = {
        "finger_bass": {
            "ghost": 1.0,
            "mute": 1.0,
            "slide": 1.0,
            "legato": 1.0,
        },
        "fretless_bass": {
            "ghost": 0.0,
            "mute": 0.0,
            "slide": 1.0,
            "legato": 1.0,
        },
        "upright_bass": {
            "ghost": 0.75,
            "mute": 0.0,
            "slide": 0.7,
            "legato": 0.7,
        },
        "sub_bass": {
            "ghost": 0.0,
            "mute": 0.0,
            "slide": 0.0,
            "legato": 0.0,
        },
    }[family.id]
    effective = dict(requested)
    notices: list[str] = []
    labels = {
        "ghost": "Ghost notes",
        "mute": "Muted/dead notes",
        "slide": "Slides",
        "legato": "Legato",
    }
    for key, cap in caps.items():
        effective[key] = min(requested[key], float(cap))
        if requested[key] > 0.0 and cap <= 0.0:
            notices.append(f"{labels[key]} are unavailable for {family.label}.")
        elif requested[key] > cap:
            notices.append(
                f"{labels[key]} are limited for {family.label} "
                f"({cap:.2f} maximum)."
            )

    return BassPerformanceControlResolution(
        requested=requested,
        effective=effective,
        notices=tuple(notices),
    )


def expressive_fusion_controls() -> dict[str, float]:
    """Producer preset used by both the web and Logic UIs."""

    return {
        "ghost": 0.62,
        "mute": 0.24,
        "slide": 0.8,
        "legato": 0.68,
        "timing_humanize": 0.58,
        "velocity_humanize": 0.72,
    }


__all__ = [
    "BassPerformanceControlResolution",
    "CONTROL_KEYS",
    "expressive_fusion_controls",
    "legacy_performance_controls",
    "resolve_bass_performance_controls",
]
