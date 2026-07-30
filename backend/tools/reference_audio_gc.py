"""Two-phase garbage collection for local reference-audio blobs."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from uuid import uuid4

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.routes import session_routes  # noqa: E402
from app.services import (  # noqa: E402
    bass_history_store,
    reference_audio_store,
    session_store,
)

DEFAULT_AUDIO_ROOT = BACKEND_ROOT / "data" / "reference_audio"
DEFAULT_RECEIPT_DIR = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Session Player"
    / "GC Receipts"
)


class ReferenceAudioGcError(RuntimeError):
    """Garbage collection could not be proven safe."""


def _load_references() -> tuple[set[Path], set[str]]:
    try:
        sessions = session_store.load_sessions(session_routes.StoredSession)
        recoverable = bass_history_store.referenced_audio_paths()
    except (
        OSError,
        bass_history_store.BassHistoryStoreError,
        session_store.SessionStoreError,
    ) as exc:
        raise ReferenceAudioGcError(
            f"Reference stores could not be validated: {exc}"
        ) from exc
    return (
        reference_audio_store.session_reference_paths(sessions),
        recoverable,
    )


def _write_receipt(
    *,
    receipt_dir: Path,
    action: str,
    plan: reference_audio_store.GarbagePlan,
    deleted_count: int,
    deleted_bytes: int,
) -> Path:
    receipt_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = receipt_dir / (
        f"reference-audio-gc-{stamp}-{action}-{plan.plan_sha256[:12]}.json"
    )
    document = {
        "action": action,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "deleted_count": deleted_count,
        "deleted_bytes": deleted_bytes,
        "plan": asdict(plan),
    }
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(document, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(target)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReferenceAudioGcError(
            f"Could not write GC receipt under {receipt_dir}"
        ) from exc
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Hash unreferenced reference-audio blobs. Deletion requires a "
            "second run with --apply and the exact dry-run plan digest."
        )
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_AUDIO_ROOT)
    parser.add_argument(
        "--receipt-dir",
        type=Path,
        default=DEFAULT_RECEIPT_DIR,
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-plan-sha256")
    args = parser.parse_args()

    try:
        active, recoverable = _load_references()
        plan = reference_audio_store.build_garbage_plan(
            root=args.root,
            active_references=active,
            recoverable_references=recoverable,
        )
        if args.apply:
            if not args.confirm_plan_sha256:
                raise ReferenceAudioGcError(
                    "--apply requires --confirm-plan-sha256 from a dry run"
                )
            if args.confirm_plan_sha256 != plan.plan_sha256:
                raise ReferenceAudioGcError(
                    "Current orphan inventory does not match the confirmed dry-run plan"
                )
            deleted = reference_audio_store.apply_garbage_plan(
                plan,
                active_references=active,
                recoverable_references=recoverable,
            )
            receipt = _write_receipt(
                receipt_dir=args.receipt_dir,
                action="applied",
                plan=plan,
                deleted_count=len(deleted),
                deleted_bytes=plan.orphan_bytes,
            )
            action = "applied"
        else:
            receipt = _write_receipt(
                receipt_dir=args.receipt_dir,
                action="dry-run",
                plan=plan,
                deleted_count=0,
                deleted_bytes=0,
            )
            action = "dry-run"
    except (
        OSError,
        ReferenceAudioGcError,
        reference_audio_store.ReferenceAudioStoreError,
    ) as exc:
        print(f"Reference-audio GC refused: {exc}", file=sys.stderr)
        return 78

    print(
        json.dumps(
            {
                "action": action,
                "orphan_file_count": plan.orphan_file_count,
                "orphan_bytes": plan.orphan_bytes,
                "plan_sha256": plan.plan_sha256,
                "missing_protected_count": len(
                    plan.missing_protected_entries
                ),
                "protected_path_count": plan.protected_path_count,
                "receipt": str(receipt),
                "unsafe_entry_count": len(plan.unsafe_entries),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
