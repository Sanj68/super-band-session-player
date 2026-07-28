from __future__ import annotations

import io
import random

import pretty_midi
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import session_routes
from app.services import generator
from app.services import session_store
from app.utils import music_theory as mt


_CHART = ["Bb", "C", "D", "Gm"]
_STYLES = ("simple", "jazzy", "wide", "dense", "stabs", "warm_broken")


@pytest.fixture(autouse=True)
def _preserve_random_state():
    state = random.getstate()
    try:
        yield
    finally:
        random.setstate(state)


def _pitch_classes_by_bar(
    midi_bytes: bytes,
    *,
    tempo: int,
    bar_count: int,
) -> list[set[int]]:
    midi = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
    notes = [
        note
        for instrument in midi.instruments
        for note in instrument.notes
    ]
    bar_seconds = 4.0 * (60.0 / float(tempo))
    return [
        {
            int(note.pitch) % 12
            for note in notes
            if bar * bar_seconds <= float(note.start) < (bar + 1) * bar_seconds
        }
        for bar in range(bar_count)
    ]


@pytest.mark.parametrize("style", _STYLES)
def test_chord_styles_keep_explicit_chart_pitch_classes(style: str) -> None:
    random.seed(90210)

    midi_bytes, preview = generator.generate_chords(
        tempo=88,
        bar_count=4,
        key="D",
        scale="natural_minor",
        chord_style=style,
        chord_progression=list(_CHART),
    )

    heard = _pitch_classes_by_bar(midi_bytes, tempo=88, bar_count=4)
    expected = [
        set(chord.tone_pcs)
        for chord in mt.progression_chords_for_bars(_CHART, 4)
    ]

    assert heard == expected
    assert "Chart: Bb | C | D | Gm." in preview


def test_explicit_seventh_quality_is_not_rebuilt_from_session_scale() -> None:
    chart = ["F#m7", "B7", "Emaj7", "C#m7"]
    random.seed(1776)

    midi_bytes, _ = generator.generate_chords(
        tempo=97,
        bar_count=4,
        key="D",
        scale="natural_minor",
        chord_style="simple",
        chord_progression=chart,
    )

    heard = _pitch_classes_by_bar(midi_bytes, tempo=97, bar_count=4)
    expected = [
        set(chord.tone_pcs)
        for chord in mt.progression_chords_for_bars(chart, 4)
    ]
    assert heard == expected


def test_session_chord_regeneration_forwards_confirmed_chart(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(session_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        session_store,
        "_SESSIONS_FILE",
        tmp_path / "sessions.json",
    )
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]

    with TestClient(app) as client:
        created = client.post(
            "/api/sessions/",
            json={
                "tempo": 88,
                "key": "D",
                "scale": "natural_minor",
                "bar_count": 4,
                "chord_progression": list(_CHART),
                "chord_style": "simple",
            },
        )
        assert created.status_code == 200, created.text
        session_id = created.json()["session"]["id"]

        regenerated = client.post(
            f"/api/sessions/{session_id}/lanes/chords/regenerate"
        )
        assert regenerated.status_code == 200, regenerated.text
        lane = client.get(f"/api/sessions/{session_id}/midi/chords")
        assert lane.status_code == 200, lane.text

    heard = _pitch_classes_by_bar(lane.content, tempo=88, bar_count=4)
    expected = [
        set(chord.tone_pcs)
        for chord in mt.progression_chords_for_bars(_CHART, 4)
    ]
    assert heard == expected
