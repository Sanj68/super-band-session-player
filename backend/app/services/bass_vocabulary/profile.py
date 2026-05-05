"""Sub One Bass Vocabulary profile v1.

The profile stores high-level source lessons and negative constraints only.
It is intentionally not wired into any bass generation path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class BassVocabularyReference:
    artist: str
    title: str
    lane: str
    lesson: str


@dataclass(frozen=True)
class BassVocabularyProfile:
    id: str
    name: str
    version: int
    references: tuple[BassVocabularyReference, ...]
    avoid_rules: tuple[str, ...]


SUB_ONE_BASS_REFERENCES: Final[tuple[BassVocabularyReference, ...]] = (
    BassVocabularyReference(
        artist="Gary Bartz",
        title="Music Is My Sanctuary",
        lane="warm_jazz_funk",
        lesson="soulful jazz-funk warmth, movement, melodic support",
    ),
    BassVocabularyReference(
        artist="Norman Connors",
        title="Skin Diver",
        lane="dark_slinky_grit",
        lesson="deep smoky/slinky jazz-funk grit, darker low-end pocket",
    ),
    BassVocabularyReference(
        artist="Barry Miles",
        title="Magic Theatre",
        lane="fusion_answer",
        lesson="fusion movement, chromatic answer phrases, expressive musician energy",
    ),
    BassVocabularyReference(
        artist="Common",
        title="Geto Heaven",
        lane="hiphop_soul_restraint",
        lesson="hip-hop soul restraint, dusty low-end, sample-support pocket",
    ),
    BassVocabularyReference(
        artist="Talib Kweli",
        title="The Blast",
        lane="tight_headnod_pocket",
        lesson="head-nod hip-hop pocket, tight sample-based groove, low-end restraint",
    ),
    BassVocabularyReference(
        artist="James Brown",
        title="Give It Up or Turnit a Loose",
        lane="raw_funk_discipline",
        lesson="raw funk pocket discipline, one-chord groove, percussive repetition",
    ),
    BassVocabularyReference(
        artist="Byron Morris & Unity",
        title="Kitty Bey",
        lane="afrocentric_jazz_dance",
        lesson="modal urgency, percussion-driven movement, spiritual jazz-funk drive",
    ),
    BassVocabularyReference(
        artist="Grant Green",
        title="Walk in the Night",
        lane="smoky_late_night_jazz_funk",
        lesson="laid-back swing, bluesy movement, understated bass support",
    ),
    BassVocabularyReference(
        artist="Cal Tjader",
        title="Curacao",
        lane="latin_jazz_percussion_lock",
        lesson="rolling syncopation, percussion-aware bass support",
    ),
    BassVocabularyReference(
        artist="John Coltrane",
        title="My Favorite Things",
        lane="modal_vamp_anchor",
        lesson="modal repetition, hypnotic cycle, bass as grounding force",
    ),
    BassVocabularyReference(
        artist="Bob James",
        title="Nautilus",
        lane="cinematic_sample_source",
        lesson="spacious groove, tension, atmosphere, sample-friendly low-end",
    ),
    BassVocabularyReference(
        artist="Jimmy Castor Bunch",
        title="It's Just Begun",
        lane="breakbeat_funk_drive",
        lesson="hard rhythmic momentum, repeated funk cells, dancefloor urgency",
    ),
    BassVocabularyReference(
        artist="Yellow Sunshine",
        title="Yellow Sunshine",
        lane="psychedelic_bboy_funk",
        lesson="loose dancefloor groove, break-friendly pocket, raw funk looseness",
    ),
)


SUB_ONE_AVOID_RULES: Final[tuple[str, ...]] = (
    "happy funk",
    "smooth jazz wandering",
    "generic root on 1 and 3",
    "over-busy bass",
    "random chromatic runs",
    "EDM-style shiny bass movement",
)


SUB_ONE_BASS_VOCABULARY_PROFILE_V1: Final[BassVocabularyProfile] = BassVocabularyProfile(
    id="sub_one_bass_vocabulary_v1",
    name="Sub One Bass Vocabulary",
    version=1,
    references=SUB_ONE_BASS_REFERENCES,
    avoid_rules=SUB_ONE_AVOID_RULES,
)


def valid_lanes() -> set[str]:
    return {reference.lane for reference in SUB_ONE_BASS_REFERENCES}
