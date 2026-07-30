from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Barrier

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.models.evaluation import ClipEvaluationRecord
from app.models.setup import BandSetup
from app.services import evaluation_store, setup_store


@pytest.fixture
def isolated_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, Path]:
    setups_file = tmp_path / "band_setups.json"
    evaluations_file = tmp_path / "bass_take_evaluations.json"
    monkeypatch.setattr(setup_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(setup_store, "_SETUPS_FILE", setups_file)
    monkeypatch.setattr(evaluation_store, "_DATA_DIR", tmp_path)
    monkeypatch.setattr(evaluation_store, "_EVALS_FILE", evaluations_file)
    return setups_file, evaluations_file


def _setup(name: str) -> BandSetup:
    return BandSetup(
        name=name,
        drum_style="straight",
        bass_style="supportive",
        chord_style="simple",
        lead_style="melodic",
    )


def _setup_payload(name: str) -> dict[str, str]:
    return {
        "name": name,
        "drum_style": "straight",
        "bass_style": "supportive",
        "chord_style": "simple",
        "lead_style": "melodic",
    }


def test_corrupt_setup_file_is_quarantined_and_store_remains_blocked(
    isolated_stores: tuple[Path, Path],
) -> None:
    setups_file, _ = isolated_stores
    corrupt = b"{preserve this broken setup file"
    setups_file.write_bytes(corrupt)

    with pytest.raises(setup_store.SetupStoreError, match="preserved"):
        setup_store.load_setups()

    quarantines = list(setups_file.parent.glob(f"{setups_file.name}.quarantine-*"))
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == corrupt
    assert not setups_file.exists()
    with pytest.raises(setup_store.SetupStoreError, match="require recovery"):
        setup_store.load_setups()
    assert not setups_file.exists()


def test_invalid_evaluation_record_is_quarantined_without_partial_load(
    isolated_stores: tuple[Path, Path],
) -> None:
    _, evaluations_file = isolated_stores
    document = {
        "schema_version": 1,
        "clips": [
            {"clip_id": "valid", "reference_notes": "", "takes": []},
            {"clip_id": "", "reference_notes": "", "takes": []},
        ],
    }
    original = json.dumps(document).encode("utf-8")
    evaluations_file.write_bytes(original)

    with pytest.raises(
        evaluation_store.EvaluationStoreError,
        match="records were preserved",
    ):
        evaluation_store.load_records()

    quarantines = list(
        evaluations_file.parent.glob(f"{evaluations_file.name}.quarantine-*")
    )
    assert len(quarantines) == 1
    assert quarantines[0].read_bytes() == original
    assert not evaluations_file.exists()


def test_nonstandard_json_constant_is_quarantined(
    isolated_stores: tuple[Path, Path],
) -> None:
    setups_file, _ = isolated_stores
    setups_file.write_text(
        '{"setups": [], "ignored": NaN}',
        encoding="utf-8",
    )

    with pytest.raises(setup_store.SetupStoreError, match="preserved"):
        setup_store.load_setups()

    quarantine = next(
        setups_file.parent.glob(f"{setups_file.name}.quarantine-*")
    )
    assert b"NaN" in quarantine.read_bytes()
    assert not setups_file.exists()


def test_transient_read_error_preserves_setup_file_in_quarantine(
    isolated_stores: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    setups_file, _ = isolated_stores
    setup_store.save_setups([_setup("Preserve Me")])
    original = setups_file.read_bytes()
    original_read_text = Path.read_text

    def fail_target_read(path: Path, *args, **kwargs):
        if path == setups_file:
            raise OSError("transient read failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_target_read)

    with pytest.raises(setup_store.SetupStoreError, match="preserved"):
        setup_store.load_setups()

    quarantine = next(
        setups_file.parent.glob(f"{setups_file.name}.quarantine-*")
    )
    assert quarantine.read_bytes() == original
    assert not setups_file.exists()


def test_atomic_write_failure_keeps_previous_evaluations(
    isolated_stores: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, evaluations_file = isolated_stores
    evaluation_store.save_records(
        [ClipEvaluationRecord(clip_id="before", reference_notes="", takes=[])]
    )
    original = evaluations_file.read_bytes()
    original_replace = Path.replace

    def fail_temporary_replace(path: Path, target: Path):
        if path.name.startswith(f".{evaluations_file.name}."):
            raise OSError("replace failed")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_temporary_replace)

    with pytest.raises(
        evaluation_store.EvaluationStoreError,
        match="written safely",
    ):
        evaluation_store.save_records(
            [ClipEvaluationRecord(clip_id="after", reference_notes="", takes=[])]
        )

    assert evaluations_file.read_bytes() == original
    assert not list(
        evaluations_file.parent.glob(f".{evaluations_file.name}.*.tmp")
    )


def test_concurrent_setup_transactions_do_not_lose_updates(
    isolated_stores: tuple[Path, Path],
) -> None:
    setup_store.save_setups([])
    count = 20
    barrier = Barrier(count)

    def add(index: int) -> None:
        barrier.wait()

        def mutation(setups: list[BandSetup]) -> None:
            setups.append(_setup(f"Setup {index:02d}"))

        setup_store.mutate_setups(mutation)

    with ThreadPoolExecutor(max_workers=count) as pool:
        list(pool.map(add, range(count)))

    setups = setup_store.load_setups()
    assert len(setups) == count
    assert {setup.name for setup in setups} == {
        f"Setup {index:02d}"
        for index in range(count)
    }


def test_concurrent_duplicate_setup_requests_are_serialized(
    isolated_stores: tuple[Path, Path],
) -> None:
    setup_store.save_setups([])
    barrier = Barrier(2)

    def create() -> int:
        barrier.wait()
        return TestClient(app).post(
            "/api/setups",
            json=_setup_payload("Only Once"),
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(lambda _index: create(), range(2)))

    assert statuses == [201, 409]
    assert [setup.name for setup in setup_store.load_setups()] == ["Only Once"]


def test_concurrent_take_requests_preserve_every_unique_take(
    isolated_stores: tuple[Path, Path],
) -> None:
    evaluation_store.save_records([])
    count = 16
    barrier = Barrier(count)

    def create(index: int) -> int:
        barrier.wait()
        return TestClient(app).post(
            "/api/evaluations/takes",
            json={
                "clip_id": "shared-clip",
                "take_id": f"take-{index:02d}",
                "session_id": "session",
                "scores": {
                    "groove_fit": 4,
                    "harmonic_fit": 4,
                    "phrase_feel": 4,
                    "articulation_feel": 4,
                    "usefulness": 4,
                },
            },
        ).status_code

    with ThreadPoolExecutor(max_workers=count) as pool:
        statuses = list(pool.map(create, range(count)))

    assert statuses == [200] * count
    records = evaluation_store.load_records()
    assert len(records) == 1
    assert {take.take_id for take in records[0].takes} == {
        f"take-{index:02d}"
        for index in range(count)
    }


@pytest.mark.parametrize(
    ("path", "error_type"),
    [
        ("/api/setups", setup_store.SetupStoreError),
        ("/api/evaluations/summary", evaluation_store.EvaluationStoreError),
    ],
)
def test_api_returns_recovery_required_instead_of_empty_data(
    isolated_stores: tuple[Path, Path],
    path: str,
    error_type: type[Exception],
) -> None:
    setups_file, evaluations_file = isolated_stores
    target = (
        setups_file
        if error_type is setup_store.SetupStoreError
        else evaluations_file
    )
    target.write_text("{broken", encoding="utf-8")

    response = TestClient(app).get(path)

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "local_store_recovery_required"
    assert response.headers["retry-after"] == "1"
    assert not target.exists()
