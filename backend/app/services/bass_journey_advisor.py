"""Evidence-backed musical orientation for the bass plugin journey.

Genre words are deliberately secondary. The advisor measures neutral traits,
then uses familiar genre language as a producer-facing orientation and offers
non-destructive Accompany / Counterpoint / Explore routes.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from typing import Final, Literal

from app.services.bass_instrument_profiles import (
    BassInstrumentProfile,
    bass_instrument_profile,
    constrain_instrument_controls,
)
from app.services.conditioning import UnifiedConditioning


JourneyPathId = Literal["accompany", "counterpoint", "explore"]


@dataclass(frozen=True, slots=True)
class SourceTraitAdvice:
    id: str
    label: str
    strength: float
    confidence: float
    evidence: str


@dataclass(frozen=True, slots=True)
class StyleOrientation:
    id: str
    label: str
    strength: float
    confidence: float


@dataclass(frozen=True, slots=True)
class BassJourneyPath:
    id: JourneyPathId
    label: str
    summary: str
    preserves: tuple[str, ...]
    changes: tuple[str, ...]
    suggested_style: str
    suggested_expression: float
    suggested_lock_to_groove: float
    suggested_density_bias: float


@dataclass(frozen=True, slots=True)
class BassJourneyAdvice:
    summary: str
    confidence: float
    confidence_label: str
    instrument_profile: BassInstrumentProfile
    traits: tuple[SourceTraitAdvice, ...]
    style_orientation: tuple[StyleOrientation, ...]
    paths: tuple[BassJourneyPath, ...]
    advisory_only: bool = True


_BEAT_SLOTS: Final[frozenset[int]] = frozenset((0, 4, 8, 12))
_EIGHTH_OFFBEATS: Final[frozenset[int]] = frozenset((2, 6, 10, 14))


def build_bass_journey_advice(
    conditioning: UnifiedConditioning,
    *,
    instrument_family: str | None,
) -> BassJourneyAdvice:
    profile = bass_instrument_profile(instrument_family)
    groove_conf = _clamp01(float(conditioning.groove_profile.confidence))
    syncopation = _clamp01(float(conditioning.groove_profile.syncopation_score))
    offbeat, offbeat_conf = _offbeat_emphasis(conditioning)
    density = _density_estimate(conditioning)
    space = _clamp01(1.0 - (density / 8.0))
    harmonic_colour, harmonic_conf = _harmonic_colour(conditioning)
    accent = _clamp01(float(conditioning.groove_profile.accent_strength))

    traits = (
        SourceTraitAdvice(
            id="syncopation",
            label="syncopated pocket",
            strength=syncopation,
            confidence=groove_conf,
            evidence=(
                f"syncopation {syncopation:.2f} from the analysed onset grid"
            ),
        ),
        SourceTraitAdvice(
            id="offbeat_space",
            label="offbeat emphasis",
            strength=offbeat,
            confidence=offbeat_conf,
            evidence=(
                f"{offbeat:.2f} of weighted rhythmic emphasis favours eighth-note offbeats"
            ),
        ),
        SourceTraitAdvice(
            id="space",
            label="musical space",
            strength=space,
            confidence=groove_conf,
            evidence=f"estimated rhythmic density {density:.2f} events per bar",
        ),
        SourceTraitAdvice(
            id="harmonic_colour",
            label="harmonic colour",
            strength=harmonic_colour,
            confidence=harmonic_conf,
            evidence="confirmed bar-level targets and harmonic movement",
        ),
    )

    orientations = _style_orientations(
        syncopation=syncopation,
        offbeat=offbeat,
        space=space,
        harmonic_colour=harmonic_colour,
        accent=accent,
        groove_conf=groove_conf,
        harmonic_conf=harmonic_conf,
        offbeat_conf=offbeat_conf,
    )
    confidence = _advice_confidence(traits)
    summary = _summary(traits, orientations, confidence)
    paths = _paths(
        profile,
        traits=traits,
        syncopation=syncopation,
        offbeat=offbeat,
        space=space,
        harmonic_colour=harmonic_colour,
    )
    return BassJourneyAdvice(
        summary=summary,
        confidence=confidence,
        confidence_label=_confidence_label(confidence),
        instrument_profile=profile,
        traits=traits,
        style_orientation=orientations,
        paths=paths,
    )


def _offbeat_emphasis(conditioning: UnifiedConditioning) -> tuple[float, float]:
    rows = conditioning.source_onset_weight or conditioning.source_slot_pressure
    if not rows:
        return 0.0, 0.0
    beat = 0.0
    offbeat = 0.0
    other = 0.0
    for row in rows:
        for slot, raw in enumerate(row[:16]):
            value = max(0.0, float(raw))
            if slot in _BEAT_SLOTS:
                beat += value
            elif slot in _EIGHTH_OFFBEATS:
                offbeat += value
            else:
                other += value
    total = beat + offbeat + other
    if total <= 1e-9:
        return 0.0, 0.0
    # Compare musically salient eighth offbeats with quarter-note anchors;
    # sixteenth decorations contribute less to this orientation.
    strength = offbeat / max(1e-9, beat + offbeat)
    row_conf = fmean(conditioning.source_groove_confidence) if conditioning.source_groove_confidence else 0.0
    return _clamp01(strength), _clamp01(row_conf)


def _density_estimate(conditioning: UnifiedConditioning) -> float:
    reported = max(0.0, float(conditioning.groove_profile.density_per_bar_estimate))
    if reported > 0.0:
        return reported
    rows = conditioning.source_slot_pressure
    if not rows:
        return 4.0
    active = [sum(1 for value in row[:16] if float(value) >= 0.35) for row in rows]
    return fmean(active) if active else 4.0


def _harmonic_colour(conditioning: UnifiedConditioning) -> tuple[float, float]:
    bars = conditioning.harmonic_bars
    if not bars:
        return 0.0, 0.0
    target_size = fmean(len(set(row.target_pcs)) for row in bars)
    root_variety = len({int(row.root_pc) % 12 for row in bars}) / max(1.0, min(8.0, len(bars)))
    strength = _clamp01(((target_size - 2.0) / 3.0) * 0.72 + root_variety * 0.28)
    confidence = _clamp01(fmean(float(row.confidence) for row in bars))
    return strength, confidence


def _style_orientations(
    *,
    syncopation: float,
    offbeat: float,
    space: float,
    harmonic_colour: float,
    accent: float,
    groove_conf: float,
    harmonic_conf: float,
    offbeat_conf: float,
) -> tuple[StyleOrientation, ...]:
    candidates = (
        StyleOrientation(
            id="funk",
            label="funk",
            strength=_clamp01(0.58 * syncopation + 0.27 * accent + 0.15 * (1.0 - space)),
            confidence=groove_conf,
        ),
        StyleOrientation(
            id="reggae",
            label="reggae",
            strength=_clamp01(0.56 * offbeat + 0.32 * space + 0.12 * (1.0 - accent)),
            confidence=min(groove_conf, offbeat_conf),
        ),
        StyleOrientation(
            id="jazz",
            label="jazz",
            strength=_clamp01(0.72 * harmonic_colour + 0.18 * syncopation + 0.10 * space),
            confidence=_clamp01(0.75 * harmonic_conf + 0.25 * groove_conf),
        ),
    )
    ranked = sorted(
        candidates,
        key=lambda item: (item.strength * item.confidence, item.strength, item.id),
        reverse=True,
    )
    return tuple(
        item
        for item in ranked
        if item.strength >= 0.25 and item.confidence >= 0.35
    )


def _paths(
    profile: BassInstrumentProfile,
    *,
    traits: tuple[SourceTraitAdvice, ...],
    syncopation: float,
    offbeat: float,
    space: float,
    harmonic_colour: float,
) -> tuple[BassJourneyPath, ...]:
    strongest = tuple(
        trait.label
        for trait in sorted(
            traits,
            key=lambda item: item.strength * item.confidence,
            reverse=True,
        )[:2]
    )
    accompany_density = -0.28 if space < 0.4 else -0.1
    accompany_expression = 0.48
    counter_style = "supportive" if max(syncopation, offbeat) >= 0.48 else "rhythmic"
    counter_density = -0.32 if counter_style == "supportive" else 0.18
    counter_expression = 0.58
    explore_style = "fusion" if harmonic_colour >= 0.45 else "melodic"
    explore_density = 0.12
    explore_expression = 0.78

    if profile.id == "fretless_bass":
        counter_style = "melodic"
        counter_expression = 0.7
        explore_style = "fusion"
    elif profile.id == "upright_bass":
        counter_style = "melodic"
        counter_density = min(counter_density, 0.05)
        explore_style = "melodic"
        explore_expression = 0.7
    elif profile.id == "sub_bass":
        counter_style = "supportive"
        counter_density = -0.42
        explore_style = "rhythmic"
        explore_density = -0.35
        explore_expression = 0.58

    accompany_density, accompany_expression = constrain_instrument_controls(
        profile.id,
        density_bias=accompany_density,
        expression_amount=accompany_expression,
    )
    counter_density, counter_expression = constrain_instrument_controls(
        profile.id,
        density_bias=counter_density,
        expression_amount=counter_expression,
    )
    explore_density, explore_expression = constrain_instrument_controls(
        profile.id,
        density_bias=explore_density,
        expression_amount=explore_expression,
    )

    instrument_clause = {
        "finger_bass": "balanced fingered articulation",
        "fretless_bass": "sustained fretless connections",
        "upright_bass": "natural upright decay and position changes",
        "sub_bass": "protected low-register space and controlled retriggers",
    }[profile.id]
    return (
        BassJourneyPath(
            id="accompany",
            label="Accompany",
            summary=f"Sit inside the source with {instrument_clause}.",
            preserves=strongest,
            changes=("reduces competing movement",),
            suggested_style="supportive",
            suggested_expression=round(accompany_expression, 3),
            suggested_lock_to_groove=0.78,
            suggested_density_bias=round(accompany_density, 3),
        ),
        BassJourneyPath(
            id="counterpoint",
            label="Counterpoint",
            summary=(
                "Ground the busy/offbeat source with a clearer shape."
                if counter_style == "supportive"
                else "Answer the steadier source with controlled rhythmic movement."
            ),
            preserves=("confirmed harmony", "phrase boundaries"),
            changes=("rhythmic relationship", "melodic contour"),
            suggested_style=counter_style,
            suggested_expression=round(counter_expression, 3),
            suggested_lock_to_groove=0.68,
            suggested_density_bias=round(counter_density, 3),
        ),
        BassJourneyPath(
            id="explore",
            label="Explore",
            summary=f"Take a bounded musical chance through {instrument_clause}.",
            preserves=("confirmed harmony", "loop boundaries"),
            changes=("phrase shape", "articulation character"),
            suggested_style=explore_style,
            suggested_expression=round(explore_expression, 3),
            suggested_lock_to_groove=0.55,
            suggested_density_bias=round(explore_density, 3),
        ),
    )


def _summary(
    traits: tuple[SourceTraitAdvice, ...],
    orientations: tuple[StyleOrientation, ...],
    confidence: float,
) -> str:
    ranked_traits = sorted(
        traits,
        key=lambda item: item.strength * item.confidence,
        reverse=True,
    )
    audible = [item for item in ranked_traits if item.strength >= 0.3 and item.confidence >= 0.2][:3]
    trait_text = ", ".join(
        f"{_strength_word(item.strength)} {item.label}" for item in audible
    ) or "a restrained, still-ambiguous musical profile"
    genre_text = "/".join(item.label for item in orientations[:3])
    orientation = f" — {genre_text} orientation" if genre_text else ""
    return f"I hear {trait_text}{orientation}; confidence {_confidence_label(confidence)}."


def _advice_confidence(traits: tuple[SourceTraitAdvice, ...]) -> float:
    # A confident harmony map does not make the rhythmic description equally
    # trustworthy. Average all listening dimensions so the summary cannot
    # overstate mixed-style certainty.
    return round(_clamp01(fmean(trait.confidence for trait in traits)), 4)


def _strength_word(value: float) -> str:
    if value >= 0.68:
        return "strong"
    if value >= 0.42:
        return "moderate"
    return "subtle"


def _confidence_label(value: float) -> str:
    if value >= 0.72:
        return "high"
    if value >= 0.42:
        return "medium"
    return "low"


def _clamp01(value: float) -> float:
    if value != value:
        return 0.0
    return max(0.0, min(1.0, float(value)))


__all__ = [
    "BassJourneyAdvice",
    "BassJourneyPath",
    "JourneyPathId",
    "SourceTraitAdvice",
    "StyleOrientation",
    "build_bass_journey_advice",
]
