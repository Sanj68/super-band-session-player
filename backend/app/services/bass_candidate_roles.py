"""Neutral, bounded roles for a purposeful four-take bass comparison.

These are not public artist identities and they are not randomisation presets.
Each role changes one musical dimension while the confirmed harmony and shared
groove lock remain authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal


BassCandidateRole = Literal[
    "pocket_keeper",
    "rhythmic_alternative",
    "harmonic_alternative",
    "performance_alternative",
]


@dataclass(frozen=True, slots=True)
class BassCandidateRoleSpec:
    id: BassCandidateRole
    label: str
    description: str
    density_delta: float


_ROLE_SPECS: Final[tuple[BassCandidateRoleSpec, ...]] = (
    BassCandidateRoleSpec(
        id="pocket_keeper",
        label="Pocket Keeper",
        description="Most restrained take: protects the one, the kick relationship, and the confirmed chord roots.",
        density_delta=-0.35,
    ),
    BassCandidateRoleSpec(
        id="rhythmic_alternative",
        label="Rhythmic Alternative",
        description="Keeps the same pocket but explores a few more syncopated answers and shorter attacks.",
        density_delta=0.4,
    ),
    BassCandidateRoleSpec(
        id="harmonic_alternative",
        label="Harmonic Alternative",
        description="Keeps the rhythmic frame while using more safe chord-tone voice leading.",
        density_delta=0.0,
    ),
    BassCandidateRoleSpec(
        id="performance_alternative",
        label="Performance Alternative",
        description="Keeps the notes conservative but changes accents, note lengths, and laid-back feel.",
        density_delta=-0.05,
    ),
)

ROLE_ORDER: Final[tuple[BassCandidateRole, ...]] = tuple(spec.id for spec in _ROLE_SPECS)
_ROLE_BY_ID: Final[dict[str, BassCandidateRoleSpec]] = {spec.id: spec for spec in _ROLE_SPECS}


def bass_candidate_role_spec(role: str | None) -> BassCandidateRoleSpec | None:
    if role is None:
        return None
    return _ROLE_BY_ID.get(str(role).strip().lower())


def bass_candidate_role_for_index(index: int) -> BassCandidateRole:
    return ROLE_ORDER[int(index) % len(ROLE_ORDER)]


__all__ = [
    "BassCandidateRole",
    "BassCandidateRoleSpec",
    "ROLE_ORDER",
    "bass_candidate_role_for_index",
    "bass_candidate_role_spec",
]
