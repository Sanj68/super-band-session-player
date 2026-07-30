"""Canonical manifests and receipts for immutable acceptance sessions."""

from __future__ import annotations

from dataclasses import fields
import hashlib
import json
import math
from typing import Any, Iterable

from pydantic import BaseModel

_MANIFEST_VERSION = 1
_EXCLUDED_FIELDS = frozenset(
    {
        "bridge_live_overlay_active",
        "bridge_live_base_source_analysis_override",
        "bridge_live_base_key",
        "bridge_live_base_scale",
        "acceptance_fixture_manifest",
        "acceptance_receipt_sha256",
    }
)


class AcceptanceFixtureError(ValueError):
    """A sealed fixture or its receipt is malformed or no longer matches."""


def _json_value(value: object) -> Any:
    if isinstance(value, bytes):
        return {
            "byte_length": len(value),
            "sha256": hashlib.sha256(value).hexdigest(),
        }
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, float) and not math.isfinite(value):
        raise AcceptanceFixtureError("Acceptance fixture contains a non-finite number")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise AcceptanceFixtureError(
        f"Acceptance fixture contains unsupported value {type(value).__name__}"
    )


def build_manifest(
    session: object,
    *,
    field_names: Iterable[str] | None = None,
) -> dict[str, object]:
    """Build a stable manifest without embedding potentially large MIDI blobs."""

    available = {
        item.name
        for item in fields(session)
        if item.name not in _EXCLUDED_FIELDS
    }
    selected = available if field_names is None else set(field_names)
    unknown = selected - available
    if unknown:
        raise AcceptanceFixtureError(
            "Acceptance fixture references unavailable fields: "
            + ", ".join(sorted(unknown))
        )
    return {
        "version": _MANIFEST_VERSION,
        "state": {
            name: _json_value(getattr(session, name))
            for name in sorted(selected)
        },
    }


def receipt_sha256(manifest: object) -> str:
    validate_manifest(manifest)
    encoded = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_manifest(manifest: object) -> dict[str, object]:
    if not isinstance(manifest, dict):
        raise AcceptanceFixtureError("Acceptance fixture manifest must be an object")
    if manifest.get("version") != _MANIFEST_VERSION:
        raise AcceptanceFixtureError("Unsupported acceptance fixture manifest version")
    state = manifest.get("state")
    if not isinstance(state, dict) or not state:
        raise AcceptanceFixtureError("Acceptance fixture manifest has no state")
    _json_value(manifest)
    return manifest


def observed_manifest(session: object, sealed_manifest: object) -> dict[str, object]:
    manifest = validate_manifest(sealed_manifest)
    state = manifest["state"]
    assert isinstance(state, dict)
    return build_manifest(session, field_names=state.keys())


def validate_sealed_session(session: object) -> None:
    name = getattr(session, "acceptance_fixture_name", None)
    sealed_at = getattr(session, "acceptance_sealed_at", None)
    manifest = getattr(session, "acceptance_fixture_manifest", None)
    receipt = getattr(session, "acceptance_receipt_sha256", None)
    values = (name, sealed_at, manifest, receipt)
    if all(value is None for value in values):
        return
    if (
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(sealed_at, str)
        or not sealed_at.strip()
        or not isinstance(receipt, str)
        or len(receipt) != 64
    ):
        raise AcceptanceFixtureError("Acceptance fixture seal is incomplete")
    if receipt_sha256(manifest) != receipt:
        raise AcceptanceFixtureError("Acceptance fixture receipt does not match manifest")
    if observed_manifest(session, manifest) != manifest:
        raise AcceptanceFixtureError("Acceptance fixture state does not match manifest")
