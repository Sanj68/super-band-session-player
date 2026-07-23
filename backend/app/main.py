"""Super Band Session Player — FastAPI entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import session_routes
from app.routes.bridge_routes import router as bridge_router
from app.routes.plugin_routes import router as plugin_router
from app.routes.evaluation_routes import router as evaluation_router
from app.routes.midi_routes import router as midi_router
from app.routes.setup_routes import router as setup_router
from app.services import session_store

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    try:
        restored = session_store.load_sessions(session_routes.StoredSession)
    except session_store.SessionStoreError:
        logger.exception("Session snapshot could not be restored; persistence is disabled")
        app.state.session_persistence_enabled = False
    else:
        session_routes._SESSIONS.clear()  # noqa: SLF001
        session_routes._SESSIONS.update(restored)  # noqa: SLF001
        app.state.session_persistence_enabled = True
    try:
        yield
    finally:
        if bool(getattr(app.state, "session_persistence_enabled", False)):
            try:
                session_store.save_sessions(session_routes._SESSIONS)  # noqa: SLF001
            except OSError:
                logger.exception("Could not persist Session Player sessions during shutdown")
        app.state.session_persistence_enabled = False


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


@app.middleware("http")
async def persist_session_mutations(request, call_next):
    response = await call_next(request)
    path = request.url.path
    relevant_path = path.startswith(("/api/sessions", "/api/plugin", "/api/bridge"))
    enabled = bool(getattr(request.app.state, "session_persistence_enabled", False))
    if enabled and relevant_path and request.method in {"POST", "PATCH", "DELETE"} and response.status_code < 400:
        try:
            session_store.save_sessions(session_routes._SESSIONS)  # noqa: SLF001
        except OSError:
            logger.exception("Could not persist Session Player sessions")
    return response


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {"ok": True, "service": "super-band-session-player"}
