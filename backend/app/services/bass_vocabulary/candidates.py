"""Candidate helpers for Sub One bass vocabulary templates."""

from __future__ import annotations

from dataclasses import dataclass
import io
from typing import Final

import pretty_midi

from app.models.session import LaneNote
from app.services.bass_vocabulary.pitch_roles import template_to_note_events
from app.services.bass_vocabulary.templates import BassVocabularyTemplate, templates_by_id
from app.services.conditioning import (
    UnifiedConditioning,
    has_source_groove,
    source_kick_weight,
    source_snare_weight,
)
from app.services.session_context import SessionAnchorContext
from app.utils import music_theory as mt


_CANDIDATE_TEMPLATE_IDS: Final[tuple[str, ...]] = (
    "warm_jazz_funk_01",
    "dark_slinky_grit_01",
    "fusion_answer_01",
    "hiphop_soul_restraint_01",
    "tight_headnod_pocket_01",
)

_LABEL_BY_TEMPLATE_ID: Final[dict[str, str]] = {
    "warm_jazz_funk_01": "Warm Jazz-Funk",
    "dark_slinky_grit_01": "Dark Slinky Grit",
    "fusion_answer_01": "Fusion Answer",
    "hiphop_soul_restraint_01": "Hip-Hop Soul Restraint",
    "tight_headnod_pocket_01": "Tight Head-Nod Pocket",
}


@dataclass(frozen=True)
class VocabularyCandidate:
    template_id: str
    label: str
    seed: int
    notes: tuple[LaneNote, ...]
    midi_bytes: bytes
    preview: str


def _source_groove_available(
    conditioning: UnifiedConditioning | None,
    context: SessionAnchorContext | None,
) -> bool:
    return conditioning is not None and has_source_groove(conditioning) and (
        context is None or context.anchor_lane != "drums"
    )


def _repeated_minor_progression(chord_progression: list[str] | None, bar_count: int) -> tuple[int, str] | None:
    try:
        chords = mt.progression_chords_for_bars(chord_progression, max(1, int(bar_count)))
    except ValueError:
        return None
    if not chords:
        return None
    root_pc = int(chords[0].root_pc) % 12
    quality = str(chords[0].quality)
    if not quality.startswith("minor"):
        return None
    for chord in chords[1:]:
        if int(chord.root_pc) % 12 != root_pc or str(chord.quality) != quality:
            return None
    return root_pc, quality


def should_generate_vocabulary_candidates(
    *,
    bass_style: str,
    conditioning: UnifiedConditioning | None,
    context: SessionAnchorContext | None,
    chord_progression: list[str] | None,
    bar_count: int,
) -> bool:
    if bass_style != "supportive":
        return False
    if not _source_groove_available(conditioning, context):
        return False
    return _repeated_minor_progression(chord_progression, bar_count) is not None


def select_templates_for_context(
    *,
    bass_style: str,
    conditioning: UnifiedConditioning | None,
    context: SessionAnchorContext | None,
    chord_progression: list[str] | None,
    bar_count: int,
) -> tuple[BassVocabularyTemplate, ...]:
    if not should_generate_vocabulary_candidates(
        bass_style=bass_style,
        conditioning=conditioning,
        context=context,
        chord_progression=chord_progression,
        bar_count=bar_count,
    ):
        return ()
    by_id = templates_by_id()
    return tuple(by_id[template_id] for template_id in _CANDIDATE_TEMPLATE_IDS if template_id in by_id)


def _root_midi_for_pc(root_pc: int, *, lo: int = 30, hi: int = 54) -> int:
    pitch = mt.pc_to_midi_note(root_pc % 12, 2)
    while pitch < lo:
        pitch += 12
    while pitch > hi:
        pitch -= 12
    return max(lo, min(hi, pitch))


def _event_velocity(role: str, slot: int, *, source_weight: float, template: BassVocabularyTemplate) -> int:
    if role == "ghost":
        base = 46
    elif role == "dead":
        base = 42
    elif slot == 0:
        base = 92
    else:
        base = 76 + (template.energy * 3) + (template.grit * 2)
    return max(34, min(108, int(round(base + source_weight * 10))))


def _event_duration(
    role: str,
    duration_slots: int,
    sixteenth: float,
    *,
    template: BassVocabularyTemplate,
) -> float:
    if role in {"ghost", "dead"}:
        return sixteenth * 0.55
    sustain = 0.72 if template.density == "high" else 0.92 if template.density == "medium" else 1.12
    return max(sixteenth * 0.5, sixteenth * float(duration_slots) * sustain)


def _render_notes_to_midi(notes: tuple[LaneNote, ...], *, tempo: int) -> bytes:
    pm = pretty_midi.PrettyMIDI(initial_tempo=float(tempo))
    inst = pretty_midi.Instrument(program=33, name="Bass")
    for note in notes:
        if note.end <= note.start:
            continue
        inst.notes.append(
            pretty_midi.Note(
                velocity=int(note.velocity),
                pitch=int(note.pitch),
                start=float(note.start),
                end=float(note.end),
            )
        )
    pm.instruments.append(inst)
    buf = io.BytesIO()
    pm.write(buf)
    return buf.getvalue()


def generate_template_candidate_events(
    *,
    template: BassVocabularyTemplate,
    tempo: int,
    bar_count: int,
    root_midi: int,
    chord_quality: str,
    conditioning: UnifiedConditioning | None,
) -> tuple[LaneNote, ...]:
    spb = 60.0 / float(max(40, min(240, int(tempo))))
    sixteenth = spb / 4.0
    anchor = float(conditioning.bar_start_anchor_sec) if conditioning is not None else 0.0
    out: list[LaneNote] = []
    bars = max(1, int(bar_count))
    for bar in range(bars):
        abstract_events = template_to_note_events(
            template,
            root_midi=root_midi,
            chord_quality=chord_quality,
            bar_index=bar,
        )
        for event in abstract_events:
            if event.midi_pitch is None:
                if event.pitch_role == "ghost":
                    pitch = root_midi
                elif event.pitch_role == "dead":
                    pitch = max(0, root_midi - 12)
                else:
                    continue
            else:
                pitch = event.midi_pitch
            slot = int(event.slot)
            source_weight = 0.0
            if conditioning is not None:
                kick = source_kick_weight(conditioning, bar, slot)
                snare = source_snare_weight(conditioning, bar, slot)
                if snare >= 0.62 and kick < 0.28 and event.pitch_role not in {"ghost", "dead"}:
                    continue
                source_weight = max(0.0, min(1.0, kick))
            start = anchor + (bar * 4.0 * spb) + (slot * sixteenth) + (sixteenth * 0.035 * source_weight)
            duration = _event_duration(
                event.pitch_role,
                event.duration_slots,
                sixteenth,
                template=template,
            )
            end = min(anchor + ((bar + 1) * 4.0 * spb) - 1e-4, start + duration)
            if end <= start:
                continue
            out.append(
                LaneNote(
                    pitch=max(0, min(127, int(pitch))),
                    start=float(start),
                    end=float(end),
                    velocity=_event_velocity(
                        event.pitch_role,
                        slot,
                        source_weight=source_weight,
                        template=template,
                    ),
                )
            )
    return tuple(out)


def generate_vocabulary_candidates(
    *,
    tempo: int,
    bar_count: int,
    bass_style: str,
    chord_progression: list[str] | None,
    conditioning: UnifiedConditioning | None,
    context: SessionAnchorContext | None,
    seed: int,
) -> tuple[VocabularyCandidate, ...]:
    repeated = _repeated_minor_progression(chord_progression, bar_count)
    if repeated is None:
        return ()
    root_pc, chord_quality = repeated
    templates = select_templates_for_context(
        bass_style=bass_style,
        conditioning=conditioning,
        context=context,
        chord_progression=chord_progression,
        bar_count=bar_count,
    )
    root_midi = _root_midi_for_pc(root_pc)
    out: list[VocabularyCandidate] = []
    for idx, template in enumerate(templates):
        notes = generate_template_candidate_events(
            template=template,
            tempo=tempo,
            bar_count=bar_count,
            root_midi=root_midi,
            chord_quality=chord_quality,
            conditioning=conditioning,
        )
        if not notes:
            continue
        label = _LABEL_BY_TEMPLATE_ID.get(template.id, template.display_name)
        preview = f"Sub One vocabulary: {label} ({template.id})"
        out.append(
            VocabularyCandidate(
                template_id=template.id,
                label=label,
                seed=int(seed) + 10_000 + idx,
                notes=notes,
                midi_bytes=_render_notes_to_midi(notes, tempo=tempo),
                preview=preview,
            )
        )
    return tuple(out)
