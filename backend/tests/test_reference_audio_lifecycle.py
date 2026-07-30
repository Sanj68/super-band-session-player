from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.routes import session_routes
from app.services import (
    bass_history_store,
    reference_audio_store,
    session_store,
)


class _ChunkedUpload:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = iter(chunks)
        self.read_sizes: list[int] = []

    async def read(self, size: int) -> bytes:
        self.read_sizes.append(size)
        return next(self._chunks, b"")


@pytest.fixture(autouse=True)
def isolate_live_sessions() -> None:
    previous = dict(session_routes._SESSIONS)  # type: ignore[attr-defined]
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    yield
    session_routes._SESSIONS.clear()  # type: ignore[attr-defined]
    session_routes._SESSIONS.update(previous)  # type: ignore[attr-defined]


def _session_with_reference(path: Path, *, session_id: str = "upload-session"):
    return session_routes.StoredSession(
        id=session_id,
        tempo=116,
        key="C",
        scale="major",
        bar_count=4,
        reference_audio_path=str(path),
        reference_audio_filename=path.name,
        bass_bytes=b"MThd",
    )


def _enable_persistence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        app.state,
        "session_persistence_enabled",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        app.state,
        "session_persistence_active",
        True,
        raising=False,
    )
    monkeypatch.setattr(
        app.state,
        "session_persistence_degraded",
        False,
        raising=False,
    )


def test_upload_stream_is_bounded_and_oversize_temp_is_removed(
    tmp_path: Path,
) -> None:
    upload = _ChunkedUpload([b"ab", b"cd", b"ef"])

    with pytest.raises(reference_audio_store.ReferenceAudioTooLarge):
        asyncio.run(
            reference_audio_store.store_upload(
                upload,  # type: ignore[arg-type]
                root=tmp_path,
                session_id="bounded",
                extension=".wav",
                max_bytes=5,
                chunk_bytes=2,
            )
        )

    assert upload.read_sizes == [2, 2, 2]
    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_empty_upload_leaves_no_blob(tmp_path: Path) -> None:
    upload = _ChunkedUpload([])

    with pytest.raises(reference_audio_store.EmptyReferenceAudio):
        asyncio.run(
            reference_audio_store.store_upload(
                upload,  # type: ignore[arg-type]
                root=tmp_path,
                session_id="empty",
                extension=".wav",
            )
        )

    assert not [path for path in tmp_path.rglob("*") if path.is_file()]


def test_replacement_retires_old_blob_only_after_snapshot_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "reference_audio"
    old = root / "upload-session" / "old.wav"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    stored = _session_with_reference(old)
    session_routes._SESSIONS[stored.id] = stored  # type: ignore[attr-defined]
    monkeypatch.setattr(session_routes, "_REFERENCE_AUDIO_ROOT", root)
    _enable_persistence(monkeypatch)
    saves: list[str] = []

    def committed(sessions: object) -> int:
        rows = sessions  # type: ignore[assignment]
        current = rows[stored.id]  # type: ignore[index]
        assert old.exists()
        assert Path(current.reference_audio_path).is_file()
        saves.append(current.reference_audio_path)
        return 1

    monkeypatch.setattr(session_store, "save_sessions", committed)

    response = TestClient(app).post(
        f"/api/sessions/{stored.id}/reference-audio",
        files={"file": ("new.wav", b"new audio", "audio/wav")},
    )

    assert response.status_code == 200
    assert len(saves) == 1
    assert not old.exists()
    new_path = Path(
        session_routes._SESSIONS[stored.id].reference_audio_path  # type: ignore[attr-defined]
    )
    assert new_path.read_bytes() == b"new audio"


def test_snapshot_failure_rolls_back_reference_and_discards_new_blob(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "reference_audio"
    old = root / "upload-session" / "old.wav"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    stored = _session_with_reference(old)
    session_routes._SESSIONS[stored.id] = stored  # type: ignore[attr-defined]
    monkeypatch.setattr(session_routes, "_REFERENCE_AUDIO_ROOT", root)
    _enable_persistence(monkeypatch)

    def fail_save(_sessions: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr(session_store, "save_sessions", fail_save)

    response = TestClient(app).post(
        f"/api/sessions/{stored.id}/reference-audio",
        files={"file": ("new.wav", b"new audio", "audio/wav")},
    )

    assert response.status_code == 503
    restored = session_routes._SESSIONS[stored.id]  # type: ignore[attr-defined]
    assert restored.reference_audio_path == str(old)
    assert old.read_bytes() == b"old"
    assert [path for path in root.rglob("*") if path.is_file()] == [old]


def test_replacement_retains_blob_used_by_recoverable_bass_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "reference_audio"
    old = root / "upload-session" / "old.wav"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    stored = _session_with_reference(old)
    session_routes._SESSIONS[stored.id] = stored  # type: ignore[attr-defined]
    bass_history_store.capture(stored, kept=True)
    monkeypatch.setattr(session_routes, "_REFERENCE_AUDIO_ROOT", root)
    _enable_persistence(monkeypatch)
    monkeypatch.setattr(session_store, "save_sessions", lambda _sessions: 1)

    response = TestClient(app).post(
        f"/api/sessions/{stored.id}/reference-audio",
        files={"file": ("new.wav", b"new audio", "audio/wav")},
    )

    assert response.status_code == 200
    assert old.exists()
    assert str(old) in bass_history_store.referenced_audio_paths()


def test_hashed_gc_deletes_only_unreferenced_files(tmp_path: Path) -> None:
    active = tmp_path / "active.wav"
    recoverable = tmp_path / "history.wav"
    orphan = tmp_path / "nested" / "orphan.wav"
    orphan.parent.mkdir()
    active.write_bytes(b"active")
    recoverable.write_bytes(b"history")
    orphan.write_bytes(b"orphan")

    plan = reference_audio_store.build_garbage_plan(
        root=tmp_path,
        active_references={active},
        recoverable_references={recoverable},
    )
    deleted = reference_audio_store.apply_garbage_plan(
        plan,
        active_references={active},
        recoverable_references={recoverable},
    )

    assert plan.orphan_file_count == 1
    assert plan.orphan_bytes == len(b"orphan")
    assert deleted == (orphan,)
    assert active.exists()
    assert recoverable.exists()
    assert not orphan.exists()


def test_hashed_gc_refuses_a_file_changed_after_dry_run(tmp_path: Path) -> None:
    orphan = tmp_path / "orphan.wav"
    orphan.write_bytes(b"before")
    plan = reference_audio_store.build_garbage_plan(
        root=tmp_path,
        active_references=set(),
        recoverable_references=set(),
    )
    orphan.write_bytes(b"changed after planning")

    with pytest.raises(
        reference_audio_store.ReferenceAudioStoreError,
        match="changed after dry run",
    ):
        reference_audio_store.apply_garbage_plan(
            plan,
            active_references=set(),
            recoverable_references=set(),
        )

    assert orphan.exists()


def test_hashed_gc_refuses_when_a_protected_file_is_missing(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.wav"
    orphan = tmp_path / "orphan.wav"
    orphan.write_bytes(b"orphan")
    plan = reference_audio_store.build_garbage_plan(
        root=tmp_path,
        active_references={missing},
        recoverable_references=set(),
    )

    assert plan.missing_protected_entries == ("missing.wav",)
    with pytest.raises(
        reference_audio_store.ReferenceAudioStoreError,
        match="protected files are missing",
    ):
        reference_audio_store.apply_garbage_plan(
            plan,
            active_references={missing},
            recoverable_references=set(),
        )

    assert orphan.exists()
