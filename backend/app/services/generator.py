"""Rule-based MIDI generation for drums, bass, and chords."""

from __future__ import annotations

import io
import pretty_midi

from app.services.bass_generator import generate_bass as generate_bass_impl
from app.services.chord_generator import generate_chords as generate_chords_impl
from app.services.conditioning import UnifiedConditioning
from app.services.drum_generator import generate_drums as generate_drums_impl
from app.services.lead_generator import generate_lead as generate_lead_impl
from app.services.session_context import SessionAnchorContext
from app.utils import music_theory as mt

def _pm_to_bytes(pm: pretty_midi.PrettyMIDI) -> bytes:
    buf = io.BytesIO()
    pm.write(buf)
    return buf.getvalue()


def generate_drums(
    *,
    tempo: int,
    bar_count: int,
    drum_style: str | None = None,
    drum_kit: str | None = None,
    drum_player: str | None = None,
    session_preset: str | None = None,
    context: SessionAnchorContext | None = None,
) -> tuple[bytes, str]:
    """Delegate to modular drum generator (styles: straight, broken, shuffle, funk, latin, laid_back_soul)."""
    return generate_drums_impl(
        tempo=tempo,
        bar_count=bar_count,
        drum_style=drum_style,
        drum_kit=drum_kit,
        drum_player=drum_player,
        session_preset=session_preset,
        context=context,
    )


def generate_bass(
    *,
    tempo: int,
    bar_count: int,
    key: str,
    scale: str,
    bass_style: str | None = None,
    bass_instrument: str | None = None,
    bass_player: str | None = None,
    bass_engine: str | None = None,
    chord_progression: list[str] | None = None,
    session_preset: str | None = None,
    context: SessionAnchorContext | None = None,
    conditioning: UnifiedConditioning | None = None,
    seed: int | None = None,
) -> tuple[bytes, str]:
    """Delegate to modular bass generator (styles: supportive, melodic, rhythmic, slap, fusion)."""
    return generate_bass_impl(
        tempo=tempo,
        bar_count=bar_count,
        key=key,
        scale=scale,
        bass_style=bass_style,
        bass_instrument=bass_instrument,
        bass_player=bass_player,
        bass_engine=bass_engine,
        chord_progression=chord_progression,
        session_preset=session_preset,
        context=context,
        conditioning=conditioning,
        seed=seed,
    )


def generate_chords(
    *,
    tempo: int,
    bar_count: int,
    key: str,
    scale: str,
    chord_style: str | None = None,
    chord_instrument: str | None = None,
    chord_player: str | None = None,
    session_preset: str | None = None,
    context: SessionAnchorContext | None = None,
) -> tuple[bytes, str]:
    """Delegate to modular chord generator (styles: simple, jazzy, wide, dense, stabs, warm_broken)."""
    return generate_chords_impl(
        tempo=tempo,
        bar_count=bar_count,
        key=key,
        scale=scale,
        chord_style=chord_style,
        chord_instrument=chord_instrument,
        chord_player=chord_player,
        session_preset=session_preset,
        context=context,
    )


def generate_lead(
    *,
    tempo: int,
    bar_count: int,
    key: str,
    scale: str,
    lead_style: str | None = None,
    lead_instrument: str | None = None,
    lead_player: str | None = None,
    suit_mode: str | None = None,
    suit_bass_density: float = 0.0,
    suit_chord_density: float = 0.0,
    suit_lead_density: float = 0.0,
    suit_chord_style: str = "",
    suit_bass_style: str = "",
    session_preset: str | None = None,
    context: SessionAnchorContext | None = None,
) -> tuple[bytes, str]:
    """Delegate to modular lead generator (styles: sparse, sparse_emotional, melodic, rhythmic, bluesy, fusion)."""
    return generate_lead_impl(
        tempo=tempo,
        bar_count=bar_count,
        key=key,
        scale=scale,
        lead_style=lead_style,
        lead_instrument=lead_instrument,
        lead_player=lead_player,
        suit_mode=suit_mode,
        suit_bass_density=suit_bass_density,
        suit_chord_density=suit_chord_density,
        suit_lead_density=suit_lead_density,
        suit_chord_style=suit_chord_style,
        suit_bass_style=suit_bass_style,
        session_preset=session_preset,
        context=context,
    )
