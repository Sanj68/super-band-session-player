"""Super Band Session Player — FastAPI entrypoint."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.session_routes import router as session_router
from app.routes.setup_routes import router as setup_router

app = FastAPI(
    title="Super Band Session Player",
    version="0.1.0",
    description="Local MVP: rule-based MIDI session player (no DB, no auth).",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(session_router, prefix="/api/sessions")
app.include_router(setup_router, prefix="/api/setups")


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {"ok": True, "service": "super-band-session-player"}
