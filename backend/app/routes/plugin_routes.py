"""Tiny dedicated surface for the Session Player MIDI FX plugin (v0.6).

The plugin is intentionally dumb: it asks "what's the current bass part?"
and plays it in sync with the host transport, and it can ask for a
regenerate with a style / lock tweak. Contract kept minimal so the plugin
never grows session-management UI (the product surface is style options
and one knob — BUILD_NOTES §6b).

Session selection: the most recently CREATED session that has a bass
part. The producer's working session is, in practice, the newest one.
"""

from __future__ import annotations

import io

import pretty_midi
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.session import LaneName, RegenerateSelectedBody
from app.routes import session_routes

router = APIRouter()


def _latest_bass_session() -> session_routes.StoredSession | None:
    candidates = [
        s for s in session_routes._SESSIONS.values()  # noqa: SLF001
        if s.bass_bytes or s.bass_performance_bytes
    ]
    if not candidates:
        return None
    # dict preserves insertion order; the last created wins
    return candidates[-1]


class PluginNote(BaseModel):
    pitch: int
    velocity: int
    start_beats: float
    dur_beats: float


class PluginBassPart(BaseModel):
    session_id: str
    tempo: int
    bar_count: int
    beats_per_bar: int = 4
    source: str = Field(description="clean or performance render")
    preview: str
    lock_to_groove: float | None
    bass_style: str
    bass_player: str | None
    notes: list[PluginNote]


@router.get("/bass-part", response_model=PluginBassPart)
def get_bass_part() -> PluginBassPart:
    s = _latest_bass_session()
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "no_bass_part"})
    raw = s.bass_performance_bytes or s.bass_bytes
    source = "performance" if s.bass_performance_bytes else "clean"
    assert raw is not None
    pm = pretty_midi.PrettyMIDI(io.BytesIO(raw))
    spb = 60.0 / float(max(1, s.tempo))
    notes: list[PluginNote] = []
    for inst in pm.instruments:
        for n in inst.notes:
            notes.append(
                PluginNote(
                    pitch=int(n.pitch),
                    velocity=int(n.velocity),
                    start_beats=round(float(n.start) / spb, 6),
                    dur_beats=round(max(0.01, float(n.end) - float(n.start)) / spb, 6),
                )
            )
    notes.sort(key=lambda n: n.start_beats)
    return PluginBassPart(
        session_id=s.id,
        tempo=int(s.tempo),
        bar_count=int(s.bar_count),
        source=source,
        preview=s.bass_preview or "",
        lock_to_groove=s.bass_lock_to_groove,
        bass_style=s.bass_style,
        bass_player=s.bass_player,
        notes=notes,
    )


class PluginRegenerateBody(BaseModel):
    bass_style: str | None = Field(default=None)
    bass_player: str | None = Field(
        default=None,
        description=(
            "Player persona id (bootsy, marcus, pino, paul_chambers, "
            "jaco_pastorius, james_jamerson) — the musical depth lives here. "
            "Send 'none' to clear back to style-only generation."
        ),
    )
    lock_to_groove: float | None = Field(default=None, ge=0.0, le=1.0)


# Persona -> engine routing. The two bass engines deliberately stay separate
# (BUILD_NOTES §6: no consolidation before it's provably painful), but their
# strengths differ: phrase_v2 carries the reference lock and the dedicated
# pino/chambers paths; the baseline engine carries the rich
# jamerson/jaco/bootsy/marcus vocabularies (measured 06-12: baseline
# jamerson = 14 notes / 5 pitch classes / syncopated 16ths vs 11/3/straight
# through phrase_v2's generic path — the "result is quite basic" report).
_PHRASE_V2_PERSONAS = {None, "pino", "paul_chambers"}


def _engine_for_player(player: str | None) -> str:
    return "phrase_v2" if player in _PHRASE_V2_PERSONAS else "baseline"


@router.post("/regenerate", response_model=PluginBassPart)
def plugin_regenerate(body: PluginRegenerateBody) -> PluginBassPart:
    s = _latest_bass_session()
    if s is None:
        raise HTTPException(status_code=404, detail={"error": "no_bass_part"})
    if body.bass_style is not None:
        s.bass_style = body.bass_style
    if body.bass_player is not None:
        s.bass_player = None if body.bass_player.strip().lower() in ("", "none") else body.bass_player
    if body.lock_to_groove is not None:
        s.bass_lock_to_groove = float(body.lock_to_groove)
    s.bass_engine = _engine_for_player(s.bass_player)
    session_routes.regenerate_selected(s.id, RegenerateSelectedBody(lanes=[LaneName.bass]))
    return get_bass_part()
