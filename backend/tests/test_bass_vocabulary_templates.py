from __future__ import annotations

from app.services.bass_vocabulary.pitch_roles import SUPPORTED_PITCH_ROLES
from app.services.bass_vocabulary.profile import valid_lanes
from app.services.bass_vocabulary.templates import SUB_ONE_BASS_TEMPLATES


def test_at_least_9_templates_exist() -> None:
    assert len(SUB_ONE_BASS_TEMPLATES) >= 9


def test_every_template_references_valid_lane() -> None:
    lanes = valid_lanes()
    assert all(template.lane in lanes for template in SUB_ONE_BASS_TEMPLATES)


def test_template_slots_are_within_one_bar() -> None:
    for template in SUB_ONE_BASS_TEMPLATES:
        assert all(0 <= slot <= 15 for slot in template.slots)
        assert tuple(sorted(set(template.slots))) == template.slots


def test_pitch_roles_length_matches_slots_length() -> None:
    for template in SUB_ONE_BASS_TEMPLATES:
        assert len(template.pitch_roles) == len(template.slots)
        assert set(template.pitch_roles).issubset(SUPPORTED_PITCH_ROLES)


def test_no_template_is_root_only() -> None:
    for template in SUB_ONE_BASS_TEMPLATES:
        assert set(template.pitch_roles) != {"root"}


def test_no_template_uses_only_slots_0_and_8() -> None:
    for template in SUB_ONE_BASS_TEMPLATES:
        assert set(template.slots) != {0, 8}


def test_density_energy_grit_and_improvisation_are_valid() -> None:
    for template in SUB_ONE_BASS_TEMPLATES:
        assert template.density in {"low", "medium", "high"}
        assert 1 <= template.energy <= 5
        assert 1 <= template.grit <= 5
        assert 1 <= template.improvisation <= 5
