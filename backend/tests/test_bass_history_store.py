from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import bass_history_store


def _session(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": "session-history-test",
        "tempo": 88,
        "key": "D",
        "scale": "natural_minor",
        "bar_count": 4,
        "chord_progression": ["Dm", "Bb", "C", "Dm"],
        "reference_audio_path": "/source.wav",
        "groove_reference_audio_path": "/groove.wav",
        "bass_bytes": b"clean-midi",
        "bass_performance_bytes": b"performance-midi",
        "bass_style": "rhythmic",
        "bass_instrument": "sub_bass",
        "bass_player": None,
        "bass_engine": "phrase_v2",
        "bass_lock_to_groove": 1.0,
        "bass_articulation_focus": "ghosted",
        "bass_expression": 0.8,
        "bass_performance_controls": {
            "ghost": 0.6,
            "mute": 0.2,
            "slide": 0.7,
            "legato": 0.5,
            "timing_humanize": 0.4,
            "velocity_humanize": 0.8,
        },
        "bass_phase_offset_beats": 0.5,
        "bass_density_bias": 0.1,
        "bass_seed": 12345,
        "bass_preview": "Original bass idea",
        "bass_locked": False,
        "current_bass_candidate_run_id": "run-1",
        "current_bass_candidate_take_id": "take-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.parametrize(
    ("field", "changed_value"),
    [
        ("bass_style", "melodic"),
        ("bass_instrument", "finger_bass"),
        ("bass_player", "custom_player"),
        ("bass_engine", "baseline"),
        ("bass_lock_to_groove", 0.25),
        ("bass_articulation_focus", "connected"),
        ("bass_expression", 0.2),
        (
            "bass_performance_controls",
            {
                "ghost": 0.1,
                "mute": 0.0,
                "slide": 0.2,
                "legato": 0.3,
                "timing_humanize": 0.9,
                "velocity_humanize": 0.25,
            },
        ),
        ("bass_phase_offset_beats", -0.5),
        ("bass_density_bias", -0.4),
        ("bass_seed", 98765),
        ("bass_preview", "Changed idea"),
        ("bass_locked", True),
        ("current_bass_candidate_run_id", "run-2"),
        ("current_bass_candidate_take_id", "take-2"),
    ],
)
def test_part_fingerprint_covers_every_restored_bass_field(
    field: str,
    changed_value: object,
) -> None:
    session = _session()
    original = bass_history_store.part_fingerprint(session)

    setattr(session, field, changed_value)

    assert bass_history_store.part_fingerprint(session) != original


def test_recall_restores_exact_midi_and_all_bass_controls() -> None:
    session = _session()
    expected_controls = {
        field: getattr(session, field)
        for field in bass_history_store._RESTORED_FIELDS  # noqa: SLF001
    }
    snapshot = bass_history_store.capture(session, kept=True)

    session.bass_bytes = b"different-clean-midi"
    session.bass_performance_bytes = b"different-performance-midi"
    for field, changed_value in (
        ("bass_style", "melodic"),
        ("bass_instrument", "finger_bass"),
        ("bass_player", "other_player"),
        ("bass_engine", "baseline"),
        ("bass_lock_to_groove", 0.0),
        ("bass_articulation_focus", "clean"),
        ("bass_expression", 0.0),
        ("bass_performance_controls", None),
        ("bass_phase_offset_beats", -1.0),
        ("bass_density_bias", -1.0),
        ("bass_seed", 999),
        ("bass_preview", "Different idea"),
        ("bass_locked", True),
        ("current_bass_candidate_run_id", "run-other"),
        ("current_bass_candidate_take_id", "take-other"),
    ):
        setattr(session, field, changed_value)

    bass_history_store.recall(session, snapshot["snapshot_id"])

    assert session.bass_bytes == b"clean-midi"
    assert session.bass_performance_bytes == b"performance-midi"
    assert {
        field: getattr(session, field)
        for field in bass_history_store._RESTORED_FIELDS  # noqa: SLF001
    } == expected_controls


def test_legacy_snapshot_preserves_current_phase_when_historical_value_is_unknown() -> None:
    original_session = _session(bass_phase_offset_beats=0.0)
    snapshot = bass_history_store.capture(original_session, kept=True)
    document = json.loads(
        bass_history_store._HISTORY_FILE.read_text(encoding="utf-8")  # noqa: SLF001
    )
    legacy_row = document["snapshots"][0]
    del legacy_row["controls"]["bass_phase_offset_beats"]
    legacy_row["part_fingerprint"] = bass_history_store._part_fingerprint_from_values(  # noqa: SLF001
        context=legacy_row["context"],
        bass_bytes=legacy_row["bass_bytes"],
        bass_performance_bytes=legacy_row["bass_performance_bytes"],
        controls=legacy_row["controls"],
    )
    bass_history_store._HISTORY_FILE.write_text(  # noqa: SLF001
        json.dumps(document),
        encoding="utf-8",
    )
    current_session = _session(bass_phase_offset_beats=0.75)

    bass_history_store.recall(current_session, snapshot["snapshot_id"])

    assert current_session.bass_phase_offset_beats == pytest.approx(0.75)
    unchanged = json.loads(
        bass_history_store._HISTORY_FILE.read_text(encoding="utf-8")  # noqa: SLF001
    )
    unchanged_row = unchanged["snapshots"][0]
    assert "bass_phase_offset_beats" not in unchanged_row["controls"]
    assert unchanged_row["part_fingerprint"] == legacy_row["part_fingerprint"]


def test_legacy_snapshot_with_invalid_original_fingerprint_is_quarantined() -> None:
    bass_history_store.capture(_session(bass_phase_offset_beats=0.0), kept=True)
    document = json.loads(
        bass_history_store._HISTORY_FILE.read_text(encoding="utf-8")  # noqa: SLF001
    )
    legacy_row = document["snapshots"][0]
    del legacy_row["controls"]["bass_phase_offset_beats"]
    legacy_row["part_fingerprint"] = "tampered-before-migration"
    bad_history = json.dumps(document)
    bass_history_store._HISTORY_FILE.write_text(  # noqa: SLF001
        bad_history,
        encoding="utf-8",
    )

    with pytest.raises(bass_history_store.BassHistoryStoreError):
        bass_history_store.history_state(_session())

    quarantined = list(
        bass_history_store._DATA_DIR.glob(  # noqa: SLF001
            "bass_history.json.quarantine-invalid-fingerprint-*"
        )
    )
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == bad_history


@pytest.mark.parametrize(
    ("corrupt_field", "corrupt_value"),
    [
        ("bass_style", None),
        ("bass_expression", "loud"),
        ("bass_phase_offset_beats", float("inf")),
        ("bass_locked", 1),
    ],
)
def test_invalid_control_schema_is_quarantined(
    corrupt_field: str,
    corrupt_value: object,
) -> None:
    bass_history_store.capture(_session(), kept=True)
    document = json.loads(
        bass_history_store._HISTORY_FILE.read_text(encoding="utf-8")  # noqa: SLF001
    )
    document["snapshots"][0]["controls"][corrupt_field] = corrupt_value
    bad_history = json.dumps(document)
    bass_history_store._HISTORY_FILE.write_text(  # noqa: SLF001
        bad_history,
        encoding="utf-8",
    )

    with pytest.raises(bass_history_store.BassHistoryStoreError):
        bass_history_store.history_state(_session())

    quarantined = list(
        bass_history_store._DATA_DIR.glob(  # noqa: SLF001
            "bass_history.json.quarantine-*"
        )
    )
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == bad_history


@pytest.mark.parametrize(
    "bad_history",
    [
        "{not valid json",
        json.dumps({"schema_version": True, "snapshots": []}),
        json.dumps({"schema_version": 999, "snapshots": []}),
        json.dumps({"schema_version": 1, "snapshots": "not-a-list"}),
        json.dumps({"schema_version": 1, "snapshots": [{"snapshot_id": "incomplete"}]}),
        json.dumps(
            {
                "schema_version": 1,
                "snapshots": [
                    {
                        "snapshot_id": "idea_bad_midi",
                        "session_id": "session-history-test",
                        "created_at": "2026-07-26T00:00:00+00:00",
                        "kept": False,
                        "kept_at": None,
                        "context_fingerprint": "context",
                        "part_fingerprint": "part",
                        "context": {},
                        "controls": {},
                        "bass_bytes": "not base64!",
                        "bass_performance_bytes": None,
                    }
                ],
            }
        ),
    ],
)
def test_invalid_history_is_quarantined_before_fresh_history_is_written(
    bad_history: str,
) -> None:
    bass_history_store._HISTORY_FILE.write_text(bad_history, encoding="utf-8")  # noqa: SLF001

    with pytest.raises(
        bass_history_store.BassHistoryStoreError,
        match="preserved at bass_history.json.quarantine-",
    ):
        bass_history_store.history_state(_session())

    quarantined = list(
        bass_history_store._DATA_DIR.glob(  # noqa: SLF001
            "bass_history.json.quarantine-*"
        )
    )
    assert len(quarantined) == 1
    assert quarantined[0].read_text(encoding="utf-8") == bad_history
    assert not bass_history_store._HISTORY_FILE.exists()  # noqa: SLF001
    assert not list(
        bass_history_store._DATA_DIR.glob("bass_history.json.tmp")  # noqa: SLF001
    )

    # A later, explicit operation may start a fresh store only after the bad
    # document has been safely quarantined and the failing operation has ended.
    bass_history_store.capture(_session(), kept=True)

    fresh = json.loads(
        bass_history_store._HISTORY_FILE.read_text(encoding="utf-8")  # noqa: SLF001
    )
    assert fresh["schema_version"] == 1
    assert len(fresh["snapshots"]) == 1
    assert quarantined[0].read_text(encoding="utf-8") == bad_history
    assert not list(
        bass_history_store._DATA_DIR.glob("bass_history.json.tmp")  # noqa: SLF001
    )


def test_invalid_utf8_history_is_quarantined_and_reported() -> None:
    invalid = b"\xff\xfe\x00"
    bass_history_store._HISTORY_FILE.write_bytes(invalid)  # noqa: SLF001

    with pytest.raises(
        bass_history_store.BassHistoryStoreError,
        match="preserved at bass_history.json.quarantine-invalid-json-",
    ):
        bass_history_store.history_state(_session())

    quarantined = list(
        bass_history_store._DATA_DIR.glob(  # noqa: SLF001
            "bass_history.json.quarantine-invalid-json-*"
        )
    )
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == invalid
    assert not bass_history_store._HISTORY_FILE.exists()  # noqa: SLF001


def test_write_oserror_is_reported_as_history_store_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(
        _path: Path,
        _data: str,
        *,
        encoding: str,
    ) -> int:
        del encoding
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", fail_write)

    with pytest.raises(
        bass_history_store.BassHistoryStoreError,
        match="could not be written safely",
    ):
        bass_history_store.capture(_session(), kept=True)
