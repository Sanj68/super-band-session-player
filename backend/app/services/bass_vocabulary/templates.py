"""Abstract Sub One bass vocabulary templates.

Templates contain rhythmic slots and pitch-role intent only; they do not copy
recorded basslines and do not render MIDI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal


Density = Literal["low", "medium", "high"]


@dataclass(frozen=True)
class BassVocabularyTemplate:
    id: str
    lane: str
    display_name: str
    inspired_by: str
    slots: tuple[int, ...]
    pitch_roles: tuple[str, ...]
    density: Density
    energy: int
    grit: int
    improvisation: int
    variation_bars: tuple[int, ...]
    rules: dict[str, Any]


SUB_ONE_BASS_TEMPLATES: Final[tuple[BassVocabularyTemplate, ...]] = (
    BassVocabularyTemplate(
        id="warm_jazz_funk_01",
        lane="warm_jazz_funk",
        display_name="Warm Jazz-Funk Support",
        inspired_by="Gary Bartz - Music Is My Sanctuary",
        slots=(0, 3, 6, 10, 13),
        pitch_roles=("root", "fifth", "flat7", "minor3", "octave"),
        density="medium",
        energy=3,
        grit=2,
        improvisation=3,
        variation_bars=(2, 4),
        rules={"feel": "warm legato support", "avoid": "bright happy-funk bounce", "answer_slot_window": (10, 15)},
    ),
    BassVocabularyTemplate(
        id="dark_slinky_grit_01",
        lane="dark_slinky_grit",
        display_name="Dark Slinky Grit",
        inspired_by="Norman Connors - Skin Diver",
        slots=(0, 5, 8, 11, 14),
        pitch_roles=("root", "dead", "flat7", "fifth", "root"),
        density="medium",
        energy=3,
        grit=5,
        improvisation=2,
        variation_bars=(4,),
        rules={
            "feel": "smoky low-end crawl",
            "dead_slots": (5,),
            "velocity_boost": 8,
            "short_note_min_duration_scale": 0.78,
            "groove_feel": "dark_slinky_swing",
            "swing_amount": 0.54,
        },
    ),
    BassVocabularyTemplate(
        id="fusion_answer_01",
        lane="fusion_answer",
        display_name="Fusion Answer Phrase",
        inspired_by="Barry Miles - Magic Theatre",
        slots=(0, 2, 6, 9, 12, 15),
        pitch_roles=("root", "fifth", "minor3", "fourth", "chromatic_above_root", "octave"),
        density="high",
        energy=4,
        grit=3,
        improvisation=5,
        variation_bars=(2, 3, 4),
        rules={"feel": "expressive answer motion", "chromatic_limit": 1, "cadence_role": "octave"},
    ),
    BassVocabularyTemplate(
        id="hiphop_soul_restraint_01",
        lane="hiphop_soul_restraint",
        display_name="Hip-Hop Soul Restraint",
        inspired_by="Common - Geto Heaven",
        slots=(0, 7, 10, 14),
        pitch_roles=("root", "dead", "fifth", "flat7"),
        density="low",
        energy=2,
        grit=3,
        improvisation=1,
        variation_bars=(4,),
        rules={"feel": "dusty sample support", "space_priority": "high", "max_sustained_notes": 3},
    ),
    BassVocabularyTemplate(
        id="tight_headnod_pocket_01",
        lane="tight_headnod_pocket",
        display_name="Tight Headnod Pocket",
        inspired_by="Talib Kweli - The Blast",
        slots=(0, 4, 6, 10, 12),
        pitch_roles=("root", "ghost", "fifth", "root", "flat7"),
        density="medium",
        energy=3,
        grit=3,
        improvisation=1,
        variation_bars=(2, 4),
        rules={"feel": "tight head-nod pocket", "late_slot_weight": "subtle", "avoid": "busy fills"},
    ),
    BassVocabularyTemplate(
        id="raw_funk_discipline_01",
        lane="raw_funk_discipline",
        display_name="Raw Funk Discipline",
        inspired_by="James Brown - Give It Up or Turnit a Loose",
        slots=(0, 3, 6, 8, 11, 14),
        pitch_roles=("root", "dead", "root", "fifth", "ghost", "flat7"),
        density="high",
        energy=5,
        grit=5,
        improvisation=2,
        variation_bars=(4,),
        rules={"feel": "percussive one-chord discipline", "repeat_cell": True, "fill_only_on_variation": True},
    ),
    BassVocabularyTemplate(
        id="modal_vamp_anchor_01",
        lane="modal_vamp_anchor",
        display_name="Modal Vamp Anchor",
        inspired_by="John Coltrane - My Favorite Things",
        slots=(0, 4, 8, 10, 12),
        pitch_roles=("root", "fifth", "root", "fourth", "flat7"),
        density="medium",
        energy=4,
        grit=2,
        improvisation=3,
        variation_bars=(3, 4),
        rules={"feel": "hypnotic modal grounding", "cycle_length_bars": 2, "anchor_root": True},
    ),
    BassVocabularyTemplate(
        id="cinematic_sample_source_01",
        lane="cinematic_sample_source",
        display_name="Cinematic Sample Source",
        inspired_by="Bob James - Nautilus",
        slots=(0, 6, 9, 13),
        pitch_roles=("root", "rest", "flat7", "fifth"),
        density="low",
        energy=2,
        grit=2,
        improvisation=2,
        variation_bars=(4,),
        rules={"feel": "spacious tension", "leave_air": True, "sample_friendly": True},
    ),
    BassVocabularyTemplate(
        id="breakbeat_funk_drive_01",
        lane="breakbeat_funk_drive",
        display_name="Breakbeat Funk Drive",
        inspired_by="Jimmy Castor Bunch - It's Just Begun",
        slots=(0, 3, 5, 8, 10, 12, 14),
        pitch_roles=("root", "dead", "fifth", "root", "ghost", "flat7", "octave"),
        density="high",
        energy=5,
        grit=4,
        improvisation=2,
        variation_bars=(2, 4),
        rules={"feel": "hard repeated funk cell", "dancefloor_urgency": True, "repeatable": True},
    ),
)


def templates_by_id() -> dict[str, BassVocabularyTemplate]:
    return {template.id: template for template in SUB_ONE_BASS_TEMPLATES}
