"""v0.3b reference-aware bass groove (BUILD_NOTES §6).

The demo proof point, as a test: same chord chart, same seed, lock off vs
lock on — the difference must be unmistakable. Plus the confidence-gated
honesty contract.
"""

from __future__ import annotations

import dataclasses
import io

import pretty_midi
import pytest

from app.models.session import GrooveProfile, HarmonyPlan
from app.services.bass_phrase_engine_v2 import (
    _resolve_reference_lock,
    _space_score,
    generate_bass_phrase_v2,
)
from app.services.conditioning import UnifiedConditioning

BARS = 4
# Groove: kick on slots 0 and 8, snare on 4 and 12, pressure high on the
# snare slots and the hat slots around them.
_KICK_ROW = tuple(1.0 if s in (0, 8) else 0.0 for s in range(16))
_SNARE_ROW = tuple(1.0 if s in (4, 12) else 0.0 for s in range(16))
_PRESSURE_ROW = tuple(
    0.9 if s in (4, 12) else (0.5 if s in (2, 6, 10, 14) else 0.1) for s in range(16)
)


def _conditioning(*, tempo_conf: float = 0.9, phase_conf: float = 0.9) -> UnifiedConditioning:
    return UnifiedConditioning(
        tempo=100,
        bar_count=BARS,
        beat_phase_offset_beats=0,
        beat_phase_confidence=phase_conf,
        bar_start_anchor_sec=0.0,
        beat_grid_seconds=(),
        bar_starts_seconds=(),
        sections=(),
        groove_profile=GrooveProfile(
            pocket_feel="straight",
            syncopation_score=0.3,
            density_per_bar_estimate=4.0,
            accent_strength=0.7,
            confidence=0.8,
        ),
        harmonic_bars=(),
        tempo_confidence=tempo_conf,
        bar_start_confidence=0.8,
        source_groove_resolution=16,
        source_onset_weight=tuple(_KICK_ROW for _ in range(BARS)),
        source_kick_weight=tuple(_KICK_ROW for _ in range(BARS)),
        source_snare_weight=tuple(_SNARE_ROW for _ in range(BARS)),
        source_slot_pressure=tuple(_PRESSURE_ROW for _ in range(BARS)),
        source_groove_confidence=tuple(0.8 for _ in range(BARS)),
    )


def _generate(lock: float | None, conditioning: UnifiedConditioning | None, *, seed: int = 7):
    return generate_bass_phrase_v2(
        tempo=100,
        bar_count=BARS,
        key="C",
        scale="major",
        conditioning=conditioning,
        seed=seed,
        lock_to_groove=lock,
    )


def _note_slots(midi_bytes: bytes, tempo: int = 100) -> list[tuple[int, int]]:
    pm = pretty_midi.PrettyMIDI(io.BytesIO(midi_bytes))
    spb = 60.0 / tempo
    sixteenth = spb / 4.0
    out = []
    for n in pm.instruments[0].notes:
        bar = int(n.start // (4 * spb))
        slot = int(round((n.start - bar * 4 * spb) / sixteenth))
        out.append((bar, max(0, min(15, slot))))
    return out


def test_space_score_formula() -> None:
    cond = _conditioning()
    # kick slot: 1 - 0 - 0.5*0.1 + 1.0 = 1.95
    assert _space_score(None, cond, 0, 0) == pytest.approx(1.95)
    # snare slot: 1 - 1 - 0.5*0.9 + 0 = -0.45
    assert _space_score(None, cond, 0, 4) == pytest.approx(-0.45)
    # quiet slot: 1 - 0 - 0.5*0.1 + 0 = 0.95
    assert _space_score(None, cond, 0, 1) == pytest.approx(0.95)
    # no evidence at all
    assert _space_score(None, None, 0, 0) is None


def test_lock_resolution_states() -> None:
    assert _resolve_reference_lock(0.8, None) == (0.0, "none")
    lock, state = _resolve_reference_lock(0.8, _conditioning())
    assert state == "locked" and lock == pytest.approx(0.8)
    assert _resolve_reference_lock(0.0, _conditioning())[1] == "off"
    # thin evidence: low analysis confidence AND no groove-row confidence
    thin = dataclasses.replace(
        _conditioning(tempo_conf=0.1, phase_conf=0.1), source_groove_confidence=()
    )
    assert _resolve_reference_lock(0.8, thin) == (0.0, "thin")
    # default knob resolves to 0.5 under strong evidence
    lock, state = _resolve_reference_lock(None, _conditioning())
    assert state == "locked" and lock == pytest.approx(0.5)


def test_lock_gates_snare_slots_and_changes_output() -> None:
    cond = _conditioning()
    loose_bytes, loose_prev = _generate(0.0, cond)
    glued_bytes, glued_prev = _generate(1.0, cond)

    assert loose_bytes != glued_bytes, "lock off vs on must be unmistakable (spec §6)"
    assert "lock dialled to 0" in loose_prev
    assert "locked to the reference groove (lock 1.00)" in glued_prev

    glued_slots = _note_slots(glued_bytes)
    # full lock: nothing lands on the snare slots (4, 12) — they carry
    # space scores far below any threshold
    assert all(slot not in (4, 12) for _bar, slot in glued_slots), glued_slots
    # the part still exists and keeps the one
    assert any(slot == 0 for _bar, slot in glued_slots)


def test_thin_evidence_falls_back_honestly() -> None:
    thin = dataclasses.replace(
        _conditioning(tempo_conf=0.1, phase_conf=0.1), source_groove_confidence=()
    )
    midi_bytes, preview = _generate(1.0, thin)
    assert "reference evidence too thin" in preview
    assert "locked" not in preview


def test_no_reference_keeps_legacy_preview() -> None:
    midi_bytes, preview = _generate(None, None)
    assert preview.endswith("bar-level harmonic targets.")
    assert "groove" not in preview.split("—")[-1] or "lock" not in preview


def test_same_seed_same_lock_is_deterministic() -> None:
    cond = _conditioning()
    a, _ = _generate(0.7, cond, seed=42)
    b, _ = _generate(0.7, cond, seed=42)
    assert a == b


def test_lock_knob_round_trips_through_the_api() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    res = client.post(
        "/api/sessions/",
        json={
            "tempo": 100,
            "key": "C",
            "scale": "major",
            "bar_count": 2,
            "bass_style": "supportive",
            "bass_engine": "phrase_v2",
            "bass_lock_to_groove": 0.8,
        },
    )
    assert res.status_code == 200, res.text
    created = res.json()["session"]
    sid = created["id"]
    assert created["bass_lock_to_groove"] == pytest.approx(0.8)

    patched = client.patch(f"/api/sessions/{sid}", json={"bass_lock_to_groove": 0.2})
    assert patched.status_code == 200, patched.text
    assert patched.json()["bass_lock_to_groove"] == pytest.approx(0.2)
    assert "lock-to-groove" in patched.json()["message"].lower()

    # regeneration consumes the stored knob without error (no reference
    # uploaded here, so the engine honestly reports no lock)
    gen = client.post(f"/api/sessions/{sid}/regenerate-selected", json={"lanes": ["bass"]})
    assert gen.status_code == 200, gen.text
    assert "phrase_v2" in gen.json()["lanes"]["bass"]["preview"]
