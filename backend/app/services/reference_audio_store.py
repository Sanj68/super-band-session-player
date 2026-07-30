"""Bounded reference-audio writes and fail-closed blob retirement."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping
from uuid import uuid4

from fastapi import UploadFile

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
UPLOAD_CHUNK_BYTES = 1024 * 1024


class ReferenceAudioStoreError(RuntimeError):
    """Reference audio could not be written or retired safely."""


class EmptyReferenceAudio(ReferenceAudioStoreError):
    """The uploaded reference contains no bytes."""


class ReferenceAudioTooLarge(ReferenceAudioStoreError):
    """The upload exceeded the bounded maximum."""


@dataclass(frozen=True)
class GarbageEntry:
    relative_path: str
    size_bytes: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class GarbagePlan:
    schema_version: int
    generated_at: str
    root: str
    protected_path_count: int
    orphan_file_count: int
    orphan_bytes: int
    entries: tuple[GarbageEntry, ...]
    unsafe_entries: tuple[str, ...]
    missing_protected_entries: tuple[str, ...]
    plan_sha256: str


def _resolved(path: str | Path) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def session_reference_paths(sessions: Mapping[str, object] | Iterable[object]) -> set[Path]:
    """Return every reference-audio path held by the supplied sessions."""

    rows = sessions.values() if isinstance(sessions, Mapping) else sessions
    paths: set[Path] = set()
    for session in rows:
        for field_name in (
            "reference_audio_path",
            "groove_reference_audio_path",
        ):
            value = getattr(session, field_name, None)
            if value:
                paths.add(_resolved(str(value)))
    return paths


def normalize_reference_paths(paths: Iterable[str | Path]) -> set[Path]:
    return {_resolved(path) for path in paths if str(path).strip()}


def _safe_session_dir(root: Path, session_id: str) -> Path:
    if not session_id or Path(session_id).name != session_id:
        raise ReferenceAudioStoreError("Unsafe session id for reference-audio storage")
    resolved_root = _resolved(root)
    target_dir = _resolved(resolved_root / session_id)
    try:
        target_dir.relative_to(resolved_root)
    except ValueError as exc:
        raise ReferenceAudioStoreError(
            "Reference-audio target escaped its storage root"
        ) from exc
    return target_dir


async def store_upload(
    file: UploadFile,
    *,
    root: Path,
    session_id: str,
    extension: str,
    name_prefix: str = "",
    max_bytes: int = MAX_UPLOAD_BYTES,
    chunk_bytes: int = UPLOAD_CHUNK_BYTES,
) -> Path:
    """Stream one upload to an atomic blob without buffering it in memory."""

    if max_bytes < 1 or chunk_bytes < 1:
        raise ValueError("Upload bounds must be positive")
    target_dir = _safe_session_dir(root, session_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    token = uuid4().hex
    target = target_dir / f"{name_prefix}{stamp}_{token[:8]}{extension}"
    temporary = target_dir / f".{target.name}.{token}.upload"
    written = 0
    try:
        with temporary.open("xb") as handle:
            while True:
                chunk = await file.read(chunk_bytes)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise ReferenceAudioTooLarge(
                        f"Reference audio exceeds {max_bytes} bytes"
                    )
                handle.write(chunk)
            if written == 0:
                raise EmptyReferenceAudio("Uploaded reference audio is empty")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(target)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReferenceAudioStoreError(
            "Reference audio could not be written safely"
        ) from exc
    except BaseException:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def discard_uncommitted(path: str | Path, *, root: Path) -> bool:
    """Remove one newly written blob that was never published."""

    candidate = Path(path).expanduser()
    resolved_root = _resolved(root)
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        return False
    if candidate.is_symlink() or not candidate.is_file():
        return False
    try:
        candidate.unlink()
    except OSError as exc:
        raise ReferenceAudioStoreError(
            f"Could not discard uncommitted reference audio: {candidate}"
        ) from exc
    _remove_empty_directories(resolved_root)
    return True


def retire_unreferenced(
    candidates: Iterable[str | Path],
    *,
    root: Path,
    active_references: Iterable[str | Path],
    recoverable_references: Iterable[str | Path],
) -> tuple[Path, ...]:
    """Unlink exact candidates only when no durable/recoverable state uses them."""

    resolved_root = _resolved(root)
    protected = normalize_reference_paths(active_references)
    protected.update(normalize_reference_paths(recoverable_references))
    retired: list[Path] = []
    for candidate_value in candidates:
        candidate = Path(candidate_value).expanduser()
        resolved_candidate = candidate.resolve(strict=False)
        if resolved_candidate in protected:
            continue
        try:
            resolved_candidate.relative_to(resolved_root)
        except ValueError:
            continue
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            candidate.unlink()
        except OSError as exc:
            raise ReferenceAudioStoreError(
                f"Could not retire reference audio: {candidate}"
            ) from exc
        retired.append(resolved_candidate)
    _remove_empty_directories(resolved_root)
    return tuple(retired)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(UPLOAD_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def _plan_digest(
    *,
    root: str,
    protected_path_count: int,
    entries: tuple[GarbageEntry, ...],
    unsafe_entries: tuple[str, ...],
    missing_protected_entries: tuple[str, ...],
) -> str:
    canonical = {
        "root": root,
        "protected_path_count": protected_path_count,
        "entries": [asdict(entry) for entry in entries],
        "unsafe_entries": list(unsafe_entries),
        "missing_protected_entries": list(missing_protected_entries),
    }
    raw = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_garbage_plan(
    *,
    root: Path,
    active_references: Iterable[str | Path],
    recoverable_references: Iterable[str | Path],
) -> GarbagePlan:
    """Hash every unreferenced regular file under ``root`` for two-phase GC."""

    resolved_root = _resolved(root)
    protected = normalize_reference_paths(active_references)
    protected.update(normalize_reference_paths(recoverable_references))
    entries: list[GarbageEntry] = []
    unsafe: list[str] = []
    managed_protected: set[Path] = set()
    for path in protected:
        try:
            path.relative_to(resolved_root)
        except ValueError:
            continue
        managed_protected.add(path)
    missing_protected = tuple(
        sorted(
            str(path.relative_to(resolved_root))
            for path in managed_protected
            if not path.is_file()
        )
    )
    if resolved_root.exists():
        for path in sorted(resolved_root.rglob("*")):
            if path.is_dir():
                continue
            relative = str(path.relative_to(resolved_root))
            if path.is_symlink():
                unsafe.append(relative)
                continue
            if not path.is_file():
                unsafe.append(relative)
                continue
            resolved_path = path.resolve(strict=True)
            if resolved_path in protected:
                continue
            stat = path.stat()
            entries.append(
                GarbageEntry(
                    relative_path=relative,
                    size_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    sha256=_sha256_file(path),
                )
            )
    frozen_entries = tuple(entries)
    frozen_unsafe = tuple(unsafe)
    root_text = str(resolved_root)
    return GarbagePlan(
        schema_version=1,
        generated_at=datetime.now(timezone.utc).isoformat(),
        root=root_text,
        protected_path_count=len(managed_protected),
        orphan_file_count=len(frozen_entries),
        orphan_bytes=sum(entry.size_bytes for entry in frozen_entries),
        entries=frozen_entries,
        unsafe_entries=frozen_unsafe,
        missing_protected_entries=missing_protected,
        plan_sha256=_plan_digest(
            root=root_text,
            protected_path_count=len(managed_protected),
            entries=frozen_entries,
            unsafe_entries=frozen_unsafe,
            missing_protected_entries=missing_protected,
        ),
    )


def apply_garbage_plan(
    plan: GarbagePlan,
    *,
    active_references: Iterable[str | Path],
    recoverable_references: Iterable[str | Path],
) -> tuple[Path, ...]:
    """Apply an unchanged hashed plan, rechecking every safety condition."""

    if plan.unsafe_entries:
        raise ReferenceAudioStoreError(
            "Garbage collection refused because unsafe entries are present"
        )
    if plan.missing_protected_entries:
        raise ReferenceAudioStoreError(
            "Garbage collection refused because protected files are missing"
        )
    root = _resolved(plan.root)
    protected = normalize_reference_paths(active_references)
    protected.update(normalize_reference_paths(recoverable_references))
    targets: list[Path] = []
    for entry in plan.entries:
        target = root / entry.relative_path
        resolved_target = target.resolve(strict=False)
        try:
            resolved_target.relative_to(root)
        except ValueError as exc:
            raise ReferenceAudioStoreError(
                f"Garbage entry escaped storage root: {entry.relative_path}"
            ) from exc
        if resolved_target in protected:
            raise ReferenceAudioStoreError(
                f"Garbage entry became referenced: {entry.relative_path}"
            )
        if target.is_symlink() or not target.is_file():
            raise ReferenceAudioStoreError(
                f"Garbage entry changed type or disappeared: {entry.relative_path}"
            )
        stat = target.stat()
        if stat.st_size != entry.size_bytes or stat.st_mtime_ns != entry.mtime_ns:
            raise ReferenceAudioStoreError(
                f"Garbage entry changed after dry run: {entry.relative_path}"
            )
        if _sha256_file(target) != entry.sha256:
            raise ReferenceAudioStoreError(
                f"Garbage entry hash changed after dry run: {entry.relative_path}"
            )
        targets.append(target)
    deleted: list[Path] = []
    for target in targets:
        try:
            target.unlink()
        except OSError as exc:
            raise ReferenceAudioStoreError(
                f"Could not delete confirmed orphan: {target}"
            ) from exc
        deleted.append(target)
    _remove_empty_directories(root)
    return tuple(deleted)


def _remove_empty_directories(root: Path) -> None:
    if not root.exists():
        return
    directories = sorted(
        (path for path in root.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        try:
            directory.rmdir()
        except OSError:
            pass
