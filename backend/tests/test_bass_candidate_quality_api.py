from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

import pytest
from pydantic import ValidationError

from app.main import app
from app.models.session import BassCandidateTake, LaneNote
from app.routes import session_routes
from app.services import bass_candidate_store
from app.services.bass_quality import BassTakeQuality, analyze_bass_take


QUALITY_KEYS = {
    "harmonic_fit",
    "groove_fit",
    "phrase_shape",
    "register_discipline",
    "repetition_variation",
    "style_match",
    "avoid_tone_control",
    "space_rest_quality",
}


def _create_session(client: TestClient) -> str:
    created = client.post(
        "/api/sessions/",
        json={
            "tempo": 108,
            "key": "C",
            "scale": "major",
            "bar_count": 8,
            "bass_style": "supportive",
            "bass_engine": "baseline",
            "bass_instrument": "finger_bass",
        },
    )
    assert created.status_code == 200
    return created.json()["session"]["id"]


def _isolated_client(tmp_path: Path) -> TestClient:
    bass_candidate_store._DATA_DIR = tmp_path  # type: ignore[attr-defined]
    bass_candidate_store._RUNS_FILE = tmp_path / "bass_candidate_runs.json"  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    return TestClient(app)


def test_bass_candidates_return_quality_metadata(tmp_path: Path) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_session(client)

    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 4, "seed": 1200, "clip_id": "clip-qual"},
    )
    assert generated.status_code == 200
    run = generated.json()
    assert run["take_count"] == 4
    assert len(run["takes"]) == 4

    totals = []
    for idx, take in enumerate(run["takes"], start=1):
        assert 0.0 <= take["quality_total"] <= 1.0
        assert set(take["quality_scores"].keys()) == QUALITY_KEYS
        for value in take["quality_scores"].values():
            assert 0.0 <= float(value) <= 1.0
        assert isinstance(take["quality_reason"], str) and take["quality_reason"]
        assert take["quality_reason"].startswith(f"rank {idx}/")
        totals.append(float(take["quality_total"]))

    # Public output should be ranked strongest first.
    assert totals == sorted(totals, reverse=True)


def test_quality_scores_controlled_riff_above_root_only_one_and_three() -> None:
    tempo = 118
    spb = 60.0 / tempo
    bar_len = spb * 4.0
    root_only: list[LaneNote] = []
    riff: list[LaneNote] = []
    for bar in range(4):
        b0 = bar * bar_len
        for slot in (0, 8):
            t = b0 + slot * (spb / 4.0)
            root_only.append(LaneNote(pitch=42, start=t, end=t + 0.35, velocity=90))
        for slot, pitch in ((0, 42), (7, 54), (8, 49), (10, 45)):
            t = b0 + slot * (spb / 4.0)
            riff.append(LaneNote(pitch=pitch, start=t, end=t + 0.22, velocity=86))

    q_root = analyze_bass_take(
        root_only,
        tempo=tempo,
        bar_count=4,
        key="F#",
        scale="natural_minor",
        style="supportive",
    )
    q_riff = analyze_bass_take(
        riff,
        tempo=tempo,
        bar_count=4,
        key="F#",
        scale="natural_minor",
        style="supportive",
    )

    assert q_riff.total > q_root.total


def test_bass_candidates_hidden_pool_dedupe_structure(tmp_path: Path, monkeypatch) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_session(client)

    def fake_render(_s, *, seed: int, conditioning, context):
        return f"seed-{seed}".encode("ascii"), f"preview-{seed}"

    def fake_extract(data: bytes) -> list[LaneNote]:
        text = data.decode("ascii")
        seed = int(text.split("-")[-1])
        return [LaneNote(pitch=36 + (seed % 12), start=0.0, end=0.25, velocity=96)]

    def fake_analyze(notes: list[LaneNote], **kwargs) -> BassTakeQuality:
        seed_mod = notes[0].pitch - 36  # 0..11
        # Force duplicate signatures for 0/1, and unique afterwards.
        if seed_mod in (0, 1):
            signature = ((0, 8),) * 2
        else:
            signature = ((seed_mod,), (seed_mod + 1,))
        total = round(1.0 - (seed_mod * 0.03), 4)
        scores = {k: max(0.0, min(1.0, total)) for k in QUALITY_KEYS}
        return BassTakeQuality(total=total, scores=scores, reason=f"seed_mod {seed_mod}", signature=signature)

    monkeypatch.setattr(session_routes, "_render_bass_take_with_seed", fake_render)
    monkeypatch.setattr(session_routes, "extract_lane_notes", fake_extract)
    monkeypatch.setattr(session_routes, "analyze_bass_take", fake_analyze)

    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 3, "seed": 1200},
    )
    assert generated.status_code == 200
    run = generated.json()
    takes = run["takes"]
    assert len(takes) == 3

    # Duplicate signature should be skipped in strict pass, so top slots prioritize non-duplicate coverage.
    seeds = [t["seed"] for t in takes]
    assert seeds[:2] == [1200, 1202]

    # Structural ranking metadata from hidden-pool phase should be present.
    qualities = [float(t["quality_total"]) for t in takes]
    assert max(qualities) == qualities[0]
    assert min(qualities) >= 0.0
    for i, take in enumerate(takes, start=1):
        assert take["quality_reason"].startswith(f"rank {i}/")


def test_bass_candidates_style_locked_diversity_gate(tmp_path: Path, monkeypatch) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_session(client)

    def fake_render(_s, *, seed: int, conditioning, context):
        return f"seed-{seed}".encode("ascii"), f"preview-{seed}"

    def fake_extract(data: bytes) -> list[LaneNote]:
        seed = int(data.decode("ascii").split("-")[-1])
        return [LaneNote(pitch=36 + (seed % 12), start=0.0, end=0.25, velocity=96)]

    sigs = {
        1200: ((0, 8), (0, 8)),
        1201: ((0, 8), (0, 8, 12)),  # near-duplicate to 1200
        1202: ((0, 6, 10), (0, 8)),
        1203: ((0, 4, 8, 12), (0, 7, 12)),
        1204: ((0, 8, 14), (0, 8, 14)),  # same family as 1200 in patched family map
        1205: ((0, 5, 8, 13), (0, 8, 12)),
    }
    totals = {1200: 0.98, 1201: 0.97, 1202: 0.96, 1203: 0.95, 1204: 0.94, 1205: 0.93}

    def fake_analyze(notes: list[LaneNote], **kwargs) -> BassTakeQuality:
        seed = 1200 + (notes[0].pitch - 36)
        signature = sigs.get(seed, ((0, 8), (0, 8)))
        total = totals.get(seed, 0.7)
        scores = {k: total for k in QUALITY_KEYS}
        return BassTakeQuality(total=total, scores=scores, reason=f"seed {seed}", signature=signature)

    def fake_motif_family(signature, *, style: str) -> str:
        if signature in (sigs[1200], sigs[1204]):
            return "fam_a"
        if signature in (sigs[1202], sigs[1205]):
            return "fam_b"
        return "fam_c"

    def fake_sig_distance(a, b) -> float:
        near_pairs = {(sigs[1200], sigs[1201]), (sigs[1201], sigs[1200])}
        if (a, b) in near_pairs:
            return 0.1
        return 0.6

    monkeypatch.setattr(session_routes, "_render_bass_take_with_seed", fake_render)
    monkeypatch.setattr(session_routes, "extract_lane_notes", fake_extract)
    monkeypatch.setattr(session_routes, "analyze_bass_take", fake_analyze)
    monkeypatch.setattr(session_routes, "_motif_family", fake_motif_family)
    monkeypatch.setattr(session_routes, "_signature_distance", fake_sig_distance)
    monkeypatch.setattr(session_routes, "_style_diversity_gate", lambda style: (0.25, 1))

    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 3, "seed": 1200},
    )
    assert generated.status_code == 200
    run = generated.json()
    takes = run["takes"]
    assert len(takes) == 3

    seeds = [t["seed"] for t in takes]
    # 1201 is near-duplicate of 1200 (filtered by distance); 1204 is same family cap overflow.
    assert 1201 not in seeds
    assert 1204 not in seeds
    assert seeds == [1200, 1202, 1203]


def test_bass_candidates_floor_protection_prefers_stronger_set(tmp_path: Path, monkeypatch) -> None:
    client = _isolated_client(tmp_path)
    session_id = _create_session(client)

    def fake_render(_s, *, seed: int, conditioning, context):
        return f"seed-{seed}".encode("ascii"), f"preview-{seed}"

    def fake_extract(data: bytes) -> list[LaneNote]:
        seed = int(data.decode("ascii").split("-")[-1])
        return [LaneNote(pitch=36 + (seed % 12), start=0.0, end=0.25, velocity=96)]

    totals = {
        1200: 0.98,
        1201: 0.96,
        1202: 0.95,
        1203: 0.92,
        1204: 0.78,  # should be floor-filtered in strict/relaxed passes
        1205: 0.76,
    }

    def fake_analyze(notes: list[LaneNote], **kwargs) -> BassTakeQuality:
        seed = 1200 + (notes[0].pitch - 36)
        signature = ((seed % 8, 8), (0, (seed + 2) % 16))
        total = totals.get(seed, 0.7)
        scores = {k: total for k in QUALITY_KEYS}
        return BassTakeQuality(total=total, scores=scores, reason=f"seed {seed}", signature=signature)

    monkeypatch.setattr(session_routes, "_render_bass_take_with_seed", fake_render)
    monkeypatch.setattr(session_routes, "extract_lane_notes", fake_extract)
    monkeypatch.setattr(session_routes, "analyze_bass_take", fake_analyze)
    monkeypatch.setattr(session_routes, "_style_diversity_gate", lambda style: (0.0, 10))
    monkeypatch.setattr(session_routes, "_style_floor_margin", lambda style: 0.08)  # floor cutoff 0.90

    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 3, "seed": 1200},
    )
    assert generated.status_code == 200
    takes = generated.json()["takes"]
    assert len(takes) == 3
    seeds = [t["seed"] for t in takes]
    assert seeds == [1200, 1201, 1202]


def test_bass_candidates_selection_stage_all_three_passes(tmp_path: Path, monkeypatch) -> None:
    """
    Scenario that produces all three selection stages in one run.

    - strict:     seed 1200 – first pick; no prior sigs; passes family cap (count 0 < 1).
    - relaxed:    seed 1201 – distance 0.4 to sig_1200 fails strict threshold (0.5),
                              but passes relaxed threshold (0.3); family cap already full so
                              strict would reject it on that alone too.
    - final_fill: seed 1202 – distance 0.2 to sig_1200 and sig_1201 fails both passes;
                              accepted unconditionally by final_fill.

    Seeds 1203-1208 score 0.88, which falls below floor_cutoff (0.99 - 0.08 = 0.91),
    so they never enter strict or relaxed and 1202 is the only candidate available for
    final_fill.
    """
    client = _isolated_client(tmp_path)
    session_id = _create_session(client)

    SIG_A = ((0, 8), (0, 8))
    SIG_B = ((0, 8, 12), (0, 8))
    SIG_C = ((0, 4), (0, 8))

    def fake_render(_s, *, seed: int, conditioning, context):
        return f"seed-{seed}".encode("ascii"), f"preview-{seed}"

    def fake_extract(data: bytes) -> list[LaneNote]:
        seed = int(data.decode("ascii").split("-")[-1])
        return [LaneNote(pitch=36 + (seed % 12), start=0.0, end=0.25, velocity=96)]

    scores = {1200: 0.99, 1201: 0.98, 1202: 0.97}

    def fake_analyze(notes: list[LaneNote], **kwargs) -> BassTakeQuality:
        seed = 1200 + (notes[0].pitch - 36)
        sig = {1200: SIG_A, 1201: SIG_B, 1202: SIG_C}.get(seed, ((seed % 8,), ((seed + 1) % 16,)))
        total = scores.get(seed, 0.88)
        quality_scores = {k: total for k in QUALITY_KEYS}
        return BassTakeQuality(total=total, scores=quality_scores, reason=f"seed {seed}", signature=sig)

    def fake_motif_family(signature, *, style: str) -> str:
        return "f1"  # all same family; cap of 1 means only one take may pass strict

    def fake_sig_distance(a, b) -> float:
        pair = frozenset([a, b])
        if pair == frozenset([SIG_A, SIG_B]):
            return 0.4  # fails strict (< 0.5), passes relaxed (>= 0.3)
        if SIG_C in pair:
            return 0.2  # fails strict and relaxed (< 0.3)
        return 0.9

    monkeypatch.setattr(session_routes, "_render_bass_take_with_seed", fake_render)
    monkeypatch.setattr(session_routes, "extract_lane_notes", fake_extract)
    monkeypatch.setattr(session_routes, "analyze_bass_take", fake_analyze)
    monkeypatch.setattr(session_routes, "_motif_family", fake_motif_family)
    monkeypatch.setattr(session_routes, "_signature_distance", fake_sig_distance)
    monkeypatch.setattr(session_routes, "_style_diversity_gate", lambda style: (0.5, 1))
    monkeypatch.setattr(session_routes, "_style_floor_margin", lambda style: 0.08)

    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 3, "seed": 1200},
    )
    assert generated.status_code == 200
    takes = generated.json()["takes"]
    assert len(takes) == 3
    seeds = [t["seed"] for t in takes]
    assert seeds == [1200, 1201, 1202]

    # Selection stage labels.
    assert takes[0]["selection_stage"] == "strict"
    assert takes[1]["selection_stage"] == "relaxed"
    assert takes[2]["selection_stage"] == "final_fill"

    # Motif family present on all takes.
    for take in takes:
        assert take["motif_family"] == "f1"

    # Signature distance: None for first pick; measured for subsequent picks.
    assert takes[0]["signature_distance"] is None
    assert abs(takes[1]["signature_distance"] - 0.4) < 1e-9
    assert abs(takes[2]["signature_distance"] - 0.2) < 1e-9


def test_bass_candidates_floor_metadata_present(tmp_path: Path, monkeypatch) -> None:
    """quality_floor_cutoff and top_pool_score are present on every returned take."""
    client = _isolated_client(tmp_path)
    session_id = _create_session(client)

    def fake_render(_s, *, seed: int, conditioning, context):
        return f"seed-{seed}".encode("ascii"), f"preview-{seed}"

    def fake_extract(data: bytes) -> list[LaneNote]:
        seed = int(data.decode("ascii").split("-")[-1])
        return [LaneNote(pitch=36 + (seed % 12), start=0.0, end=0.25, velocity=96)]

    def fake_analyze(notes: list[LaneNote], **kwargs) -> BassTakeQuality:
        seed = 1200 + (notes[0].pitch - 36)
        total = round(0.99 - (seed - 1200) * 0.03, 4)
        quality_scores = {k: max(0.0, total) for k in QUALITY_KEYS}
        return BassTakeQuality(
            total=total,
            scores=quality_scores,
            reason=f"seed {seed}",
            signature=((seed % 8, (seed + 4) % 16),),
        )

    monkeypatch.setattr(session_routes, "_render_bass_take_with_seed", fake_render)
    monkeypatch.setattr(session_routes, "extract_lane_notes", fake_extract)
    monkeypatch.setattr(session_routes, "analyze_bass_take", fake_analyze)
    monkeypatch.setattr(session_routes, "_style_floor_margin", lambda style: 0.1)

    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 3, "seed": 1200},
    )
    assert generated.status_code == 200
    takes = generated.json()["takes"]
    assert len(takes) == 3

    for take in takes:
        assert take["quality_floor_cutoff"] is not None
        assert take["top_pool_score"] is not None
        assert 0.0 <= take["quality_floor_cutoff"] <= 1.0
        assert 0.0 <= take["top_pool_score"] <= 1.0
        assert take["quality_floor_cutoff"] <= take["top_pool_score"]

    # All takes in the same run share the same pool-level constants.
    cutoffs = [t["quality_floor_cutoff"] for t in takes]
    tops = [t["top_pool_score"] for t in takes]
    assert len(set(cutoffs)) == 1
    assert len(set(tops)) == 1


def test_bass_candidate_quality_and_selection_metadata_round_trips(tmp_path: Path, monkeypatch) -> None:
    """GET /bass-candidates should preserve stored quality and selection metadata."""
    client = _isolated_client(tmp_path)
    session_id = _create_session(client)

    def fake_render(_s, *, seed: int, conditioning, context):
        return f"seed-{seed}".encode("ascii"), f"preview-{seed}"

    def fake_extract(data: bytes) -> list[LaneNote]:
        seed = int(data.decode("ascii").split("-")[-1])
        return [LaneNote(pitch=36 + (seed % 12), start=0.0, end=0.25, velocity=96)]

    def fake_analyze(notes: list[LaneNote], **kwargs) -> BassTakeQuality:
        seed = 1200 + (notes[0].pitch - 36)
        total = round(0.99 - (seed - 1200) * 0.02, 4)
        quality_scores = {k: total for k in QUALITY_KEYS}
        return BassTakeQuality(
            total=total,
            scores=quality_scores,
            reason=f"seed {seed}",
            signature=((seed % 8, (seed + 4) % 16),),
        )

    monkeypatch.setattr(session_routes, "_render_bass_take_with_seed", fake_render)
    monkeypatch.setattr(session_routes, "extract_lane_notes", fake_extract)
    monkeypatch.setattr(session_routes, "analyze_bass_take", fake_analyze)
    monkeypatch.setattr(session_routes, "_style_floor_margin", lambda style: 0.1)

    generated = client.post(
        f"/api/sessions/{session_id}/bass-candidates",
        json={"take_count": 3, "seed": 1200},
    )
    assert generated.status_code == 200
    posted_takes = generated.json()["takes"]

    listed = client.get(f"/api/sessions/{session_id}/bass-candidates")
    assert listed.status_code == 200
    listed_takes = listed.json()[0]["takes"]
    assert len(listed_takes) == len(posted_takes)

    for posted, listed_take in zip(posted_takes, listed_takes):
        assert listed_take["take_id"] == posted["take_id"]
        assert listed_take["quality_total"] == posted["quality_total"]
        assert listed_take["quality_scores"] == posted["quality_scores"]
        assert listed_take["quality_reason"] == posted["quality_reason"]
        assert listed_take["selection_stage"] == posted["selection_stage"]
        assert listed_take["motif_family"] == posted["motif_family"]
        assert listed_take["signature_distance"] == posted["signature_distance"]
        assert listed_take["quality_floor_cutoff"] == posted["quality_floor_cutoff"]
        assert listed_take["top_pool_score"] == posted["top_pool_score"]


def test_bass_candidate_take_selection_stage_literal_rejects_invalid() -> None:
    """BassCandidateTake rejects any selection_stage value outside the allowed Literal."""
    base = {
        "take_id": "run_t1",
        "seed": 1,
        "note_count": 4,
        "byte_length": 128,
    }

    # Valid values must not raise.
    for valid in ("strict", "relaxed", "final_fill", None):
        BassCandidateTake(**base, selection_stage=valid)

    # Any other string must raise ValidationError.
    for invalid in ("Strict", "STRICT", "fill", "final-fill", "unknown", "", "0"):
        with pytest.raises(ValidationError):
            BassCandidateTake(**base, selection_stage=invalid)


def test_bass_candidates_get_handles_old_stored_format(tmp_path: Path) -> None:
    """GET /bass-candidates must not crash when stored takes lack the new metadata fields.

    Simulates a run that was persisted before selection_stage / motif_family /
    quality_floor_cutoff / top_pool_score / quality_* were introduced.
    All missing fields should come back as None / 0.0 / {} / "" without a 500 error.
    """
    client = _isolated_client(tmp_path)
    session_id = _create_session(client)

    old_run: dict = {
        "run_id": "cand_legacy_run",
        "session_id": session_id,
        "created_at": "2024-01-01T00:00:00+00:00",
        "take_count": 2,
        "bass_style": "supportive",
        "bass_engine": "baseline",
        "bass_player": None,
        "bass_instrument": "finger_bass",
        "clip_id": None,
        "conditioning_tempo": 108,
        "conditioning_phase_offset": 0,
        "conditioning_phase_confidence": 0.0,
        "conditioning_sections_count": 0,
        "conditioning_harmonic_bar_count": 0,
        "takes": [
            # Old-format take: only the fields that existed before the metadata work.
            {
                "take_id": "cand_legacy_run_t1",
                "seed": 500,
                "note_count": 14,
                "byte_length": 300,
                "preview": "legacy take 1",
                "midi_b64": "AAAA",
                # Intentionally absent: selection_stage, motif_family,
                # signature_distance, quality_floor_cutoff, top_pool_score,
                # quality_total, quality_scores, quality_reason.
            },
            {
                "take_id": "cand_legacy_run_t2",
                "seed": 501,
                "note_count": 10,
                "byte_length": 260,
                "preview": "legacy take 2",
                "midi_b64": "AAAA",
            },
        ],
    }
    bass_candidate_store.append_run(old_run)

    listed = client.get(f"/api/sessions/{session_id}/bass-candidates")
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    assert rows[0]["run_id"] == "cand_legacy_run"
    takes = rows[0]["takes"]
    assert len(takes) == 2

    for take in takes:
        # New metadata fields must be None when absent from stored data.
        assert take["selection_stage"] is None
        assert take["motif_family"] is None
        assert take["signature_distance"] is None
        assert take["quality_floor_cutoff"] is None
        assert take["top_pool_score"] is None
        # Quality fields must fall back to safe zero-values.
        assert take["quality_total"] == 0.0
        assert take["quality_scores"] == {}
        assert take["quality_reason"] == ""
