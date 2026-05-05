from __future__ import annotations

from app.services.bass_vocabulary.profile import SUB_ONE_BASS_VOCABULARY_PROFILE_V1


def test_profile_contains_exactly_13_references() -> None:
    assert len(SUB_ONE_BASS_VOCABULARY_PROFILE_V1.references) == 13


def test_billy_jean_is_not_present() -> None:
    titles = {reference.title for reference in SUB_ONE_BASS_VOCABULARY_PROFILE_V1.references}
    artists = {reference.artist for reference in SUB_ONE_BASS_VOCABULARY_PROFILE_V1.references}
    assert "Billie Jean" not in titles
    assert "Michael Jackson" not in artists


def test_the_blast_is_present() -> None:
    references = SUB_ONE_BASS_VOCABULARY_PROFILE_V1.references
    assert any(reference.artist == "Talib Kweli" and reference.title == "The Blast" for reference in references)


def test_all_lanes_are_unique() -> None:
    lanes = [reference.lane for reference in SUB_ONE_BASS_VOCABULARY_PROFILE_V1.references]
    assert len(lanes) == len(set(lanes))


def test_avoid_rules_include_required_constraints() -> None:
    avoid_rules = set(SUB_ONE_BASS_VOCABULARY_PROFILE_V1.avoid_rules)
    assert "smooth jazz wandering" in avoid_rules
    assert "generic root on 1 and 3" in avoid_rules
