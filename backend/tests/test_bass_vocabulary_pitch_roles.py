from __future__ import annotations

from app.services.bass_vocabulary.pitch_roles import resolve_pitch_role, template_to_note_events
from app.services.bass_vocabulary.templates import templates_by_id


def test_f_sharp_minor_pitch_roles_resolve_expected_intervals() -> None:
    root_midi = 42
    assert resolve_pitch_role("root", root_midi, "minor") == 42
    assert resolve_pitch_role("octave", root_midi, "minor") == 54
    assert resolve_pitch_role("fifth", root_midi, "minor") == 49
    assert resolve_pitch_role("minor3", root_midi, "minor") == 45
    assert resolve_pitch_role("flat7", root_midi, "minor") == 52


def test_rest_ghost_dead_behave_safely() -> None:
    root_midi = 42
    assert resolve_pitch_role("rest", root_midi) is None
    assert resolve_pitch_role("ghost", root_midi) is None
    assert resolve_pitch_role("dead", root_midi) is None


def test_template_to_note_events_produces_deterministic_output() -> None:
    template = templates_by_id()["warm_jazz_funk_01"]
    first = template_to_note_events(template, root_midi=42, chord_quality="minor", bar_index=2)
    second = template_to_note_events(template, root_midi=42, chord_quality="minor", bar_index=2)

    assert first == second
    assert first[0].bar_index == 2
    assert first[0].slot == 0
    assert first[0].pitch_role == "root"
    assert first[0].midi_pitch == 42
    assert all(event.duration_slots >= 1 for event in first)


def test_template_to_note_events_does_not_emit_rest_events() -> None:
    template = templates_by_id()["cinematic_sample_source_01"]
    events = template_to_note_events(template, root_midi=42)

    assert len(events) == len(template.slots) - 1
    assert all(event.pitch_role != "rest" for event in events)
    assert all(event.articulation != "rest" for event in events)
