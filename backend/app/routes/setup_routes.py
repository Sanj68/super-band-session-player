"""CRUD for saved band setups (local JSON file)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.models.setup import (
    BandSetup,
    BandSetupCreate,
    BandSetupCreated,
    BandSetupDeleted,
    BandSetupListResponse,
    SavedSetupAsSessionPatchResponse,
)
from app.services import setup_store as store
from app.services.setup_apply import band_setup_to_session_patch

router = APIRouter()


@router.get("", response_model=BandSetupListResponse)
def list_setups() -> BandSetupListResponse:
    setups = sorted(store.load_setups(), key=lambda s: s.name.lower())
    return BandSetupListResponse(setups=setups)


@router.post("", response_model=BandSetupCreated, status_code=201)
def create_setup(body: BandSetupCreate) -> BandSetupCreated:
    setups = store.load_setups()
    if store.find_by_name(setups, body.name) is not None:
        raise HTTPException(
            status_code=409,
            detail={"error": "duplicate_name", "message": "A setup with this name already exists.", "name": body.name},
        )
    setup = BandSetup.model_validate(body.model_dump())
    setups.append(setup)
    store.save_setups(setups)
    return BandSetupCreated(setup=setup)


@router.get("/{name}/as-session-patch", response_model=SavedSetupAsSessionPatchResponse)
def saved_setup_as_session_patch(name: str) -> SavedSetupAsSessionPatchResponse:
    """Return a validated PATCH body for /api/sessions/{id} from the named saved setup."""
    decoded = name.strip()
    if not decoded:
        raise HTTPException(status_code=400, detail={"error": "invalid_name", "message": "Name is required."})
    setups = store.load_setups()
    idx = store.find_by_name(setups, decoded)
    if idx is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "setup_not_found", "message": "No saved setup with that name.", "name": decoded},
        )
    setup = setups[idx]
    return SavedSetupAsSessionPatchResponse(patch=band_setup_to_session_patch(setup))


@router.delete("/{name}", response_model=BandSetupDeleted)
def delete_setup(name: str) -> BandSetupDeleted:
    decoded = name.strip()
    if not decoded:
        raise HTTPException(status_code=400, detail={"error": "invalid_name", "message": "Name is required."})
    setups = store.load_setups()
    idx = store.find_by_name(setups, decoded)
    if idx is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "setup_not_found", "message": "No saved setup with that name.", "name": decoded},
        )
    deleted_name = setups.pop(idx).name
    store.save_setups(setups)
    return BandSetupDeleted(deleted=deleted_name)
