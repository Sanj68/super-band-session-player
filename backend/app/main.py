"""Super Band Session Player — FastAPI entrypoint."""

from __future__ import annotations

import asyncio
from copy import deepcopy
import logging
import math
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.routes import session_routes
from app.routes.bridge_routes import router as bridge_router
from app.routes.plugin_routes import router as plugin_router
from app.routes.evaluation_routes import router as evaluation_router
from app.routes.midi_routes import router as midi_router
from app.routes.setup_routes import router as setup_router
from app.services import (
    bass_history_store,
    reference_audio_store,
    session_mutation_gate,
    session_store,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    app.state.session_persistence_active = True
    try:
        restored = session_store.load_sessions(session_routes.StoredSession)
    except session_store.SessionStoreError:
        logger.exception("Session snapshot could not be restored; persistence is disabled")
        app.state.session_persistence_enabled = False
        app.state.session_persistence_degraded = True
    else:
        session_routes._SESSIONS.clear()  # noqa: SLF001
        session_routes._SESSIONS.update(restored)  # noqa: SLF001
        app.state.session_persistence_enabled = True
        app.state.session_persistence_degraded = False
    try:
        yield
    finally:
        if bool(getattr(app.state, "session_persistence_enabled", False)):
            try:
                session_store.save_sessions(session_routes._SESSIONS)  # noqa: SLF001
            except (OSError, session_store.SessionStoreError):
                logger.exception("Could not persist Session Player sessions during shutdown")
        app.state.session_persistence_enabled = False
        app.state.session_persistence_active = False


app = FastAPI(
    title="Super Band Session Player",
    version="0.1.0",
    description="Source-aware MIDI session player with local session persistence (no auth).",
    lifespan=_lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
        # Tauri production desktop app origins
        "tauri://localhost",         # macOS / Linux
        "https://tauri.localhost",   # Windows
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(session_routes.router, prefix="/api/sessions")
app.include_router(setup_router, prefix="/api/setups")
app.include_router(evaluation_router, prefix="/api/evaluations")
app.include_router(midi_router, prefix="/api/midi")
app.include_router(bridge_router, prefix="/api/bridge")
app.include_router(plugin_router, prefix="/api/plugin")


def _finite_json(value):
    """Sanitize non-standard JSON numbers embedded in validation details."""

    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _finite_json(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_finite_json(item) for item in value]
    return value


@app.exception_handler(RequestValidationError)
async def request_validation_error(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    # Raw clients can send NaN/Infinity even though strict JSON encoders will
    # not. Pydantic correctly rejects them, but its error payload echoes the
    # offending float; sanitize that echo so the 422 itself remains encodable.
    return JSONResponse(
        status_code=422,
        content={
            "detail": _finite_json(
                jsonable_encoder(exc.errors())
            )
        },
    )


def _is_durable_session_mutation(path: str) -> bool:
    if path.startswith(("/api/sessions", "/api/plugin")):
        return True
    # Heartbeats, transport, and live analysis frames are high-frequency,
    # in-memory Bridge traffic. Only the explicit commit endpoint promotes
    # Bridge analysis to a durable session mutation.
    return (
        path.startswith("/api/bridge/sessions/")
        and path.endswith("/commit-source-groove")
    )


def _requires_session_snapshot(path: str) -> bool:
    """Whether a successful POST/PATCH changed the StoredSession mapping."""

    normalized = path.rstrip("/")
    # Candidate generation writes its dedicated candidate store but does not
    # mutate the session. Audition starts external playback from a coherent
    # read. Both still use the mutation gate for a stable input snapshot, but
    # neither participates in the session-store transaction.
    return not (
        normalized.endswith("/bass-candidates")
        or normalized.endswith("/audition/bass")
        or normalized == "/api/plugin/keep"
    )


def _session_mutation_lock(app: FastAPI) -> asyncio.Lock:
    """Return the per-app lock for all requests that can touch session state."""

    loop = asyncio.get_running_loop()
    lock = getattr(app.state, "session_mutation_lock", None)
    lock_loop = getattr(app.state, "session_mutation_lock_loop", None)
    if not isinstance(lock, asyncio.Lock) or lock_loop is not loop:
        lock = asyncio.Lock()
        app.state.session_mutation_lock = lock
        app.state.session_mutation_lock_loop = loop
    return lock


@asynccontextmanager
async def _session_mutation_gate_claim():
    """Acquire the cross-thread gate without leaving an orphaned waiter."""

    acquired = False
    try:
        while not session_mutation_gate.try_acquire():
            await asyncio.sleep(0.005)
        acquired = True
        yield
    finally:
        if acquired:
            session_mutation_gate.release()


def _session_audio_paths(sessions: dict[str, object]) -> set:
    return reference_audio_store.session_reference_paths(sessions)


def _retire_audio_paths(
    candidates: set,
    *,
    sessions: dict[str, object],
) -> None:
    if not candidates:
        return
    try:
        reference_audio_store.retire_unreferenced(
            candidates,
            root=session_routes._REFERENCE_AUDIO_ROOT,  # noqa: SLF001
            active_references=_session_audio_paths(sessions),
            recoverable_references=bass_history_store.referenced_audio_paths(),
        )
    except (
        OSError,
        bass_history_store.BassHistoryStoreError,
        reference_audio_store.ReferenceAudioStoreError,
    ):
        # The session transaction is already coherent. Fail closed by retaining
        # the blob; the two-phase GC tool can retry it later.
        logger.exception("Could not retire detached reference audio")


@app.middleware("http")
async def persist_session_mutations(request, call_next):
    path = request.url.path
    mutating_session_request = (
        path.startswith(("/api/sessions", "/api/plugin", "/api/bridge"))
        and request.method in {"POST", "PATCH", "DELETE"}
    )
    if not mutating_session_request:
        return await call_next(request)

    durable_mutation = _is_durable_session_mutation(path)
    if not durable_mutation:
        # AU heartbeats and live feature batches have sub-second client
        # deadlines. Their thread-safe bridge-store writes must not queue
        # behind audio analysis or candidate generation. Bridge handlers defer
        # the tiny StoredSession overlay step while a durable mutation is live.
        return await call_next(request)

    async with _session_mutation_lock(request.app):
        # Sync FastAPI handlers run in worker threads. This cross-thread gate
        # lets live Bridge handlers make a non-blocking claim for their tiny
        # overlay update, closing the check/apply race without delaying AU
        # frame ingestion.
        async with _session_mutation_gate_claim():
            requires_snapshot = _requires_session_snapshot(path)
            persistence_degraded = bool(
                getattr(
                    request.app.state,
                    "session_persistence_active",
                    False,
                )
                and getattr(
                    request.app.state,
                    "session_persistence_degraded",
                    False,
                )
            )
            if requires_snapshot and persistence_degraded:
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": {
                            "error": "session_persistence_unavailable",
                            "message": (
                                "Session persistence could not be restored. "
                                "Durable changes are disabled until the local "
                                "snapshot is repaired."
                            ),
                        }
                    },
                    headers={"Retry-After": "1"},
                )
            before = (
                deepcopy(session_routes._SESSIONS)  # noqa: SLF001
                if requires_snapshot
                else None
            )
            before_audio_paths = (
                _session_audio_paths(before)
                if before is not None
                else set()
            )
            try:
                response = await call_next(request)
            except BaseException:
                # Includes cancellation and response-model validation errors:
                # neither may strand a half-applied in-memory revision.
                if before is not None:
                    attempted_audio_paths = _session_audio_paths(
                        session_routes._SESSIONS  # noqa: SLF001
                    )
                    session_routes._SESSIONS.clear()  # noqa: SLF001
                    session_routes._SESSIONS.update(before)  # noqa: SLF001
                    _retire_audio_paths(
                        attempted_audio_paths - before_audio_paths,
                        sessions=session_routes._SESSIONS,  # noqa: SLF001
                    )
                raise
            if response.status_code >= 400 and before is not None:
                # Route implementations are staged where practical, but this
                # gives every failed durable request the same atomic contract.
                attempted_audio_paths = _session_audio_paths(
                    session_routes._SESSIONS  # noqa: SLF001
                )
                session_routes._SESSIONS.clear()  # noqa: SLF001
                session_routes._SESSIONS.update(before)  # noqa: SLF001
                _retire_audio_paths(
                    attempted_audio_paths - before_audio_paths,
                    sessions=session_routes._SESSIONS,  # noqa: SLF001
                )
                return response
            enabled = bool(
                getattr(request.app.state, "session_persistence_enabled", False)
            )
            if enabled and requires_snapshot:
                try:
                    session_store.save_sessions(session_routes._SESSIONS)  # noqa: SLF001
                except (OSError, session_store.SessionStoreError):
                    logger.exception("Could not persist Session Player sessions")
                    # Never acknowledge a durable mutation that cannot survive
                    # restart. Restore the coherent pre-request snapshot and
                    # let the caller retry after storage recovers.
                    attempted_audio_paths = _session_audio_paths(
                        session_routes._SESSIONS  # noqa: SLF001
                    )
                    session_routes._SESSIONS.clear()  # noqa: SLF001
                    session_routes._SESSIONS.update(before)  # noqa: SLF001
                    _retire_audio_paths(
                        attempted_audio_paths - before_audio_paths,
                        sessions=session_routes._SESSIONS,  # noqa: SLF001
                    )
                    return JSONResponse(
                        status_code=503,
                        content={
                            "detail": {
                                "error": "session_persistence_unavailable",
                                "message": (
                                    "The session change was not committed "
                                    "because local persistence failed. "
                                    "No in-memory session change was kept."
                                ),
                            }
                        },
                        headers={"Retry-After": "1"},
                    )
                current_audio_paths = _session_audio_paths(
                    session_routes._SESSIONS  # noqa: SLF001
                )
                _retire_audio_paths(
                    before_audio_paths - current_audio_paths,
                    sessions=session_routes._SESSIONS,  # noqa: SLF001
                )
            return response


@app.get("/health")
def health() -> dict[str, str | bool]:
    persistence_degraded = bool(
        getattr(app.state, "session_persistence_active", False)
        and getattr(app.state, "session_persistence_degraded", False)
    )
    return {
        "ok": not persistence_degraded,
        "service": "super-band-session-player",
        "session_persistence": (
            "degraded"
            if persistence_degraded
            else "ready"
        ),
    }
