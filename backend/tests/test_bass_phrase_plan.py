from __future__ import annotations

from app.services.bass_phrase_plan import build_phrase_plan


def test_phrase_plan_roles_repeat_in_4_bar_cycle() -> None:
    plan = build_phrase_plan(bar_count=8, style="supportive", salt=42, context=None)
    roles = [x.role for x in plan]
    assert roles == ["anchor", "answer", "push", "release", "anchor", "answer", "push", "release"]


def test_phrase_plan_slots_and_tone_path_shape() -> None:
    plan = build_phrase_plan(bar_count=12, style="fusion", salt=7, context=None)
    assert len(plan) == 12
    for bar in plan:
        assert len(bar.slots) > 0
        assert 0 in bar.slots
        assert all(0 <= s <= 15 for s in bar.slots)
        assert tuple(sorted(set(bar.slots))) == bar.slots
        assert len(bar.tone_path) >= 1
        assert len(bar.tone_path) <= len(bar.slots)
        assert all(i in (0, 1, 2) for i in bar.tone_path)


def test_release_bar_cadence_bias_is_highest_in_cell() -> None:
    plan = build_phrase_plan(bar_count=4, style="supportive", salt=99, context=None)
    assert plan[3].role == "release"
    assert plan[3].cadence_bias > plan[2].cadence_bias > plan[1].cadence_bias >= plan[0].cadence_bias
