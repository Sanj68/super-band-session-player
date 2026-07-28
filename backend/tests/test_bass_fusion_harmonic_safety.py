"""Regression contract for Fusion bass harmonic safety.

The contract works on composed pitch classes per bar.  MIDI byte inequality,
register changes, articulation, and velocity cannot prove that a generated
line belongs to the harmony the player is hearing.
"""

from __future__ import annotations

import io

import pretty_midi
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.session import GrooveProfile, LaneName
from app.routes import session_routes
from app.services import generator
from app.services import session_store
from app.services.bass_performance import BassPerformanceNote
from app.services.conditioning import (
    ConditioningHarmonicBar,
    UnifiedConditioning,
)
from app.utils import music_theory as mt


_LIVE_KEY = "D"
_LIVE_SCALE = "natural_minor"
_LIVE_CHORDS = ("Bb", "C", "D", "Gm")
_LIVE_BAR_COUNT = 16
_LIVE_SEED = 1_951_771_943


def _live_harmonic_conditioning() -> UnifiedConditioning:
    """Recreate the confirmed bar map used by the failing Logic take."""

    scale_pcs = {
        (mt.key_root_pc(_LIVE_KEY) + interval) % 12
        for interval in mt.scale_intervals(_LIVE_SCALE)
    }
    chords = mt.progression_chords_for_bars(
        _LIVE_CHORDS,
        _LIVE_BAR_COUNT,
    )
    harmonic_bars = []
    for bar, chord in enumerate(chords):
        targets = tuple(sorted({int(pc) % 12 for pc in chord.tone_pcs}))
        passing = tuple(sorted(scale_pcs.difference(targets)))
        harmonic_bars.append(
            ConditioningHarmonicBar(
                bar_index=bar,
                root_pc=int(chord.root_pc) % 12,
                target_pcs=targets,
                passing_pcs=passing,
                avoid_pcs=tuple(
                    pc
                    for pc in range(12)
                    if pc not in targets and pc not in passing
                ),
                confidence=1.0,
                source="confirmed_chord_progression",
            )
        )

    seconds_per_beat = 60.0 / 88.0
    return UnifiedConditioning(
        tempo=88,
        bar_count=_LIVE_BAR_COUNT,
        beat_phase_offset_beats=0,
        beat_phase_confidence=0.0,
        bar_start_anchor_sec=0.0,
        beat_grid_seconds=tuple(
            beat * seconds_per_beat
            for beat in range(_LIVE_BAR_COUNT * 4)
        ),
        bar_starts_seconds=tuple(
            bar * 4.0 * seconds_per_beat
            for bar in range(_LIVE_BAR_COUNT)
        ),
        sections=(),
        groove_profile=GrooveProfile(
            pocket_feel="steady",
            syncopation_score=0.0,
            density_per_bar_estimate=0.0,
            accent_strength=0.0,
            confidence=0.0,
        ),
        harmonic_bars=tuple(harmonic_bars),
    )


def _live_fusion_notes(
    engine: str,
) -> tuple[UnifiedConditioning, tuple[BassPerformanceNote, ...]]:
    conditioning = _live_harmonic_conditioning()
    _midi, _preview, notes = generator.generate_bass(
        tempo=88,
        bar_count=_LIVE_BAR_COUNT,
        key=_LIVE_KEY,
        scale=_LIVE_SCALE,
        bass_style="fusion",
        bass_instrument="upright_bass",
        bass_engine=engine,
        chord_progression=list(_LIVE_CHORDS),
        conditioning=conditioning,
        seed=_LIVE_SEED,
        density_bias=0.87,
        expression_amount=1.0,
        bass_articulation_focus="natural",
        return_performance_notes=True,
    )
    return conditioning, notes


@pytest.mark.parametrize("engine", ("baseline", "phrase_v2"))
def test_fusion_pitch_classes_stay_inside_each_confirmed_bar(
    engine: str,
) -> None:
    """Fusion may use chord colour and scale passing tones, not mystery notes."""

    conditioning, notes = _live_fusion_notes(engine)

    violations = []
    for note in notes:
        assert note.bar_index is not None
        assert note.slot_index is not None
        harmonic_bar = conditioning.harmonic_bar(int(note.bar_index))
        assert harmonic_bar is not None
        allowed = set(harmonic_bar.target_pcs) | set(
            harmonic_bar.passing_pcs
        )
        pitch_class = int(note.pitch) % 12
        if pitch_class not in allowed:
            violations.append(
                {
                    "bar": int(note.bar_index) + 1,
                    "slot": int(note.slot_index),
                    "pitch": int(note.pitch),
                    "pitch_class": pitch_class,
                    "chord": _LIVE_CHORDS[
                        int(note.bar_index) % len(_LIVE_CHORDS)
                    ],
                    "allowed_pitch_classes": sorted(allowed),
                }
            )

    assert not violations, (
        f"{engine}/Fusion emitted pitches outside the confirmed chord tones "
        f"and {_LIVE_KEY} {_LIVE_SCALE} passing tones: {violations}"
    )


def test_live_phrase_v2_fusion_strong_grid_attacks_are_chord_tones() -> None:
    """Quarter-note grid attacks must state the current chord, not a colour."""

    conditioning, notes = _live_fusion_notes("phrase_v2")
    violations = []
    for note in notes:
        assert note.bar_index is not None
        assert note.slot_index is not None
        if int(note.slot_index) not in {0, 4, 8, 12}:
            continue
        harmonic_bar = conditioning.harmonic_bar(int(note.bar_index))
        assert harmonic_bar is not None
        pitch_class = int(note.pitch) % 12
        if pitch_class not in set(harmonic_bar.target_pcs):
            violations.append(
                {
                    "bar": int(note.bar_index) + 1,
                    "slot": int(note.slot_index),
                    "pitch": int(note.pitch),
                    "pitch_class": pitch_class,
                    "chord": _LIVE_CHORDS[
                        int(note.bar_index) % len(_LIVE_CHORDS)
                    ],
                    "chord_tones": sorted(harmonic_bar.target_pcs),
                }
            )

    assert not violations, (
        "Phrase-v2 Fusion put non-chord colours on strong grid attacks: "
        f"{violations}"
    )


def test_live_phrase_v2_fusion_is_chord_tone_led() -> None:
    """Fusion can decorate the harmony, but at least 75% must state it."""

    conditioning, notes = _live_fusion_notes("phrase_v2")
    assert notes
    chord_tone_count = 0
    for note in notes:
        assert note.bar_index is not None
        harmonic_bar = conditioning.harmonic_bar(int(note.bar_index))
        assert harmonic_bar is not None
        chord_tone_count += (
            int(note.pitch) % 12 in set(harmonic_bar.target_pcs)
        )
    share = chord_tone_count / len(notes)

    assert share >= 0.75, (
        "Phrase-v2 Fusion is led by passing/foreign tones rather than the "
        f"confirmed chords; chord-tone share was {share:.1%}"
    )


def test_live_phrase_v2_upright_fusion_has_playable_adjacent_leaps() -> None:
    """An upright lead line must not whip between remote registers."""

    _conditioning, notes = _live_fusion_notes("phrase_v2")
    ordered = sorted(
        notes,
        key=lambda note: (
            int(note.bar_index) if note.bar_index is not None else -1,
            int(note.slot_index) if note.slot_index is not None else -1,
            float(note.start),
            int(note.pitch),
        ),
    )
    leaps = [
        {
            "semitones": abs(int(right.pitch) - int(left.pitch)),
            "from": (
                int(left.bar_index) + 1,
                int(left.slot_index),
                int(left.pitch),
            ),
            "to": (
                int(right.bar_index) + 1,
                int(right.slot_index),
                int(right.pitch),
            ),
        }
        for left, right in zip(ordered, ordered[1:], strict=False)
    ]
    oversized = [
        leap
        for leap in leaps
        if int(leap["semitones"]) > 17
    ]

    assert not oversized, (
        "Phrase-v2 Upright Fusion exceeded the 17-semitone adjacent-leap "
        f"ceiling: {oversized}"
    )


def test_direct_phrase_v2_regeneration_retries_before_publishing_bad_harmony(
    monkeypatch,
) -> None:
    """The plug-in Generate path must not publish its first unsupported take."""

    conditioning = _live_harmonic_conditioning()
    stored = session_routes.StoredSession(
        id="harmonic-guard",
        tempo=88,
        key=_LIVE_KEY,
        scale=_LIVE_SCALE,
        bar_count=_LIVE_BAR_COUNT,
        bass_style="fusion",
        bass_instrument="upright_bass",
        bass_engine="phrase_v2",
        bass_density_bias=0.87,
        bass_expression=1.0,
        chord_progression=list(_LIVE_CHORDS),
    )
    monkeypatch.setattr(session_routes, "_new_bass_seed", lambda: 500)
    monkeypatch.setattr(
        session_routes,
        "_conditioning_for_generation",
        lambda _session, *, context: conditioning,
    )
    real_guard = session_routes.count_unsupported_structural_notes
    guard_calls = 0

    def reject_first_take(notes, **kwargs):
        nonlocal guard_calls
        guard_calls += 1
        if guard_calls == 1:
            return 1
        return real_guard(notes, **kwargs)

    monkeypatch.setattr(
        session_routes,
        "count_unsupported_structural_notes",
        reject_first_take,
    )

    session_routes._regenerate_lane_on_stored_session(
        stored,
        LaneName.bass,
        context=None,
    )

    assert guard_calls == 2
    assert stored.bass_seed == 501
    assert stored.bass_bytes
    assert stored.bass_performance_bytes
    assert real_guard(
        session_routes.extract_lane_notes(stored.bass_bytes),
        tempo=stored.tempo,
        conditioning=conditioning,
        style=stored.bass_style,
    ) == 0


def test_generated_chord_lane_honors_confirmed_custom_progression(
    tmp_path,
    monkeypatch,
) -> None:
    """The chord lane must play the same map used to condition the bass."""

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
                "key": _LIVE_KEY,
                "scale": _LIVE_SCALE,
                "bar_count": 4,
                "chord_progression": list(_LIVE_CHORDS),
                "chord_style": "simple",
            },
        )
        assert created.status_code == 200, created.text
        session_id = created.json()["session"]["id"]
        generated = client.post(f"/api/sessions/{session_id}/generate")
        assert generated.status_code == 200, generated.text
        lane = client.get(f"/api/sessions/{session_id}/midi/chords")
        assert lane.status_code == 200, lane.text

    midi = pretty_midi.PrettyMIDI(io.BytesIO(lane.content))
    notes = [
        note
        for instrument in midi.instruments
        for note in instrument.notes
    ]
    seconds_per_bar = 4.0 * (60.0 / 88.0)
    pitch_classes_by_bar = []
    for bar in range(4):
        start = bar * seconds_per_bar
        end = (bar + 1) * seconds_per_bar
        pitch_classes_by_bar.append(
            {
                int(note.pitch) % 12
                for note in notes
                if start <= float(note.start) < end
            }
        )

    missing = []
    for bar, chord in enumerate(
        mt.progression_chords_for_bars(_LIVE_CHORDS, 4)
    ):
        expected = set(chord.tone_pcs)
        if not expected.issubset(pitch_classes_by_bar[bar]):
            missing.append(
                {
                    "bar": bar + 1,
                    "chord": chord.symbol,
                    "expected": sorted(expected),
                    "heard": sorted(pitch_classes_by_bar[bar]),
                }
            )

    assert not missing, (
        "Generated chord MIDI diverged from the session's confirmed custom "
        f"progression: {missing}"
    )
