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
    is_dark_slinky = template.id == "dark_slinky_grit_01"
    if role == "ghost":
        base = 54 if is_dark_slinky else 46
    elif role == "dead":
        base = 58 if is_dark_slinky else 42
    elif slot == 0:
        base = 92
    else:
        base = 76 + (template.energy * 3) + (template.grit * 2)
    if is_dark_slinky:
        base += int(template.rules.get("velocity_boost", 0) or 0)
    return max(34, min(108, int(round(base + source_weight * 10))))


def _event_duration(
    role: str,
    duration_slots: int,
    sixteenth: float,
    *,
    template: BassVocabularyTemplate,
) -> float:
    is_dark_slinky = template.id == "dark_slinky_grit_01"
    if role in {"ghost", "dead"}:
        short_scale = float(template.rules.get("short_note_min_duration_scale", 0.55) or 0.55)
        return sixteenth * max(0.55, short_scale if is_dark_slinky else 0.55)
    sustain = 0.72 if template.density == "high" else 0.92 if template.density == "medium" else 1.12
    return max(sixteenth * 0.5, sixteenth * float(duration_slots) * sustain)


def _timing_offset_seconds(
    *,
    template: BassVocabularyTemplate,
    role: str,
    slot: int,
    source_weight: float,
    sixteenth: float,
    bar: int,
    delayed_entry: bool,
) -> float:
    is_dark_slinky = template.id == "dark_slinky_grit_01"
    if bar == 0 and slot == 0 and not delayed_entry:
        return 0.0
    if not is_dark_slinky:
        return sixteenth * 0.035 * max(0.0, min(1.0, source_weight))
    w = max(0.0, min(1.0, float(source_weight)))
    centered = (w * 2.0) - 1.0
    if role in {"ghost", "dead"}:
        return max(-0.0020, min(0.0020, centered * 0.0020))
    return max(-0.0040, min(0.0040, centered * 0.0040))


_RESOLVING_PITCH_ROLES: Final[frozenset[str]] = frozenset(
    {"root", "fifth", "flat7", "octave", "minor3"}
)

# Minor-seven chord vocabulary: root / minor 3rd / fifth / minor 7 (natural minor chord tones).
_MINOR7_VOCAB_INTERVALS_FROM_ROOT: Final[tuple[int, ...]] = (0, 3, 7, 10)

_DEPRECATED_CHROMATIC_RENDER_ROLES: Final[frozenset[str]] = frozenset(
    {"chromatic_below_root", "chromatic_above_root"}
)


def _minor7_vocabulary_allowed_pitch_classes(root_pc: int) -> frozenset[int]:
    rp = int(root_pc) % 12
    return frozenset((rp + iv) % 12 for iv in _MINOR7_VOCAB_INTERVALS_FROM_ROOT)


def _nearest_pitch_to_allowed_classes(pitch: int, *, allowed_pcs: frozenset[int]) -> int:
    """Prefer smallest MIDI delta to chord-safe pitch classes in bass register."""
    pc = int(pitch) % 12
    if pc in allowed_pcs:
        return max(0, min(127, int(pitch)))
    best_pitch = max(0, min(127, int(pitch)))
    best_delta = 128
    for target in range(22, 64):
        if int(target) % 12 not in allowed_pcs:
            continue
        delta = abs(int(target) - int(pitch))
        if delta < best_delta:
            best_delta = delta
            best_pitch = int(target)
    return best_pitch


def _apply_vocabulary_minor7_harmonic_guard(notes: list[LaneNote], *, harmonic_root_pc: int) -> list[LaneNote]:
    allowed = _minor7_vocabulary_allowed_pitch_classes(harmonic_root_pc)
    out: list[LaneNote] = []
    for note in notes:
        new_pitch = _nearest_pitch_to_allowed_classes(int(note.pitch), allowed_pcs=allowed)
        out.append(
            LaneNote(
                pitch=new_pitch,
                start=float(note.start),
                end=float(note.end),
                velocity=int(note.velocity),
            )
        )
    return out


def _template_delayed_entry(template: BassVocabularyTemplate) -> bool:
    return bool(template.rules.get("delayed_entry"))


def _pick_tail_resolution_slot(conditioning: UnifiedConditioning | None, bar: int) -> int:
    for slot in (14, 12, 15, 13):
        if conditioning is None:
            return slot
        kick = source_kick_weight(conditioning, bar, slot)
        snare = source_snare_weight(conditioning, bar, slot)
        if snare >= 0.62 and kick < 0.28:
            continue
        return slot
    return 14


def _finalize_vocabulary_phrase_notes(
    notes: list[LaneNote],
    *,
    template: BassVocabularyTemplate,
    tempo: int,
    bar_count: int,
    root_midi: int,
    conditioning: UnifiedConditioning | None,
) -> list[LaneNote]:
    """Ensure downbeat and loop-tail phrase boundaries when groove gates drop template hits."""
    if _template_delayed_entry(template):
        return notes
    bars = max(1, int(bar_count))
    spb = 60.0 / float(max(40, min(240, int(tempo))))
    sixteenth = spb / 4.0
    anchor = 0.0
    bar_len = 4.0 * spb
    root_pc = int(root_midi) % 12
    last_b = bars - 1
    last_origin = anchor + last_b * bar_len
    loop_end = anchor + bars * bar_len

    def slot_at(bar_index: int, t: float) -> int:
        origin = anchor + bar_index * bar_len
        rel = float(t) - origin
        return max(0, min(15, int(round(rel / sixteenth))))

    bar0 = [n for n in notes if anchor <= n.start < anchor + bar_len]
    slot0_roots = [
        n
        for n in bar0
        if slot_at(0, n.start) == 0 and int(n.pitch) % 12 == root_pc and n.end > n.start
    ]
    if not slot0_roots:
        sk0 = source_kick_weight(conditioning, 0, 0) if conditioning is not None else 0.0
        start = anchor
        duration = _event_duration("root", 4, sixteenth, template=template)
        end = min(anchor + bar_len - 1e-4, start + duration)
        if end > start:
            notes.append(
                LaneNote(
                    pitch=max(0, min(127, int(root_midi))),
                    start=float(start),
                    end=float(end),
                    velocity=_event_velocity("root", 0, source_weight=sk0, template=template),
                )
            )

    last_bar = [n for n in notes if last_origin - 1e-6 <= n.start < loop_end]
    allowed_tail = _minor7_vocabulary_allowed_pitch_classes(root_pc)
    has_late_resolve = any(
        slot_at(last_b, n.start) >= 12 and int(n.pitch) % 12 in allowed_tail for n in last_bar
    )
    if not has_late_resolve:
        tail_slot = _pick_tail_resolution_slot(conditioning, last_b)
        sk = source_kick_weight(conditioning, last_b, tail_slot) if conditioning is not None else 0.0
        start = last_origin + tail_slot * sixteenth + _timing_offset_seconds(
            template=template,
            role="root",
            slot=tail_slot,
            source_weight=sk,
            sixteenth=sixteenth,
            bar=last_b,
            delayed_entry=False,
        )
        duration = _event_duration("root", max(2, 16 - tail_slot), sixteenth, template=template)
        end = min(loop_end - 1e-4, start + duration)
        if end > start and not any(
            abs(float(n.start) - start) < sixteenth * 0.28 and int(n.pitch) % 12 == root_pc for n in last_bar
        ):
            notes.append(
                LaneNote(
                    pitch=max(0, min(127, int(root_midi))),
                    start=float(start),
                    end=float(end),
                    velocity=_event_velocity("root", tail_slot, source_weight=sk, template=template),
                )
            )

    notes.sort(key=lambda n: (n.start, n.pitch))
    return notes


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
    harmonic_root_pc: int,
    conditioning: UnifiedConditioning | None,
) -> tuple[LaneNote, ...]:
    spb = 60.0 / float(max(40, min(240, int(tempo))))
    sixteenth = spb / 4.0
    # Candidate MIDI should be loop-local (bar 1 starts at 0s) for export/audition parity.
    anchor = 0.0
    out: list[LaneNote] = []
    bars = max(1, int(bar_count))
    delayed_entry = _template_delayed_entry(template)
    for bar in range(bars):
        abstract_events = template_to_note_events(
            template,
            root_midi=root_midi,
            chord_quality=chord_quality,
            bar_index=bar,
        )
        for event in abstract_events:
            if event.pitch_role in _DEPRECATED_CHROMATIC_RENDER_ROLES:
                continue
            if event.midi_pitch is None:
                if event.pitch_role == "ghost":
                    pitch = root_midi
                elif event.pitch_role == "dead":
                    pitch = int(root_midi)
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
                    phrase_exempt = (
                        (bar == 0 and slot == 0 and not delayed_entry)
                        or (
                            bar == bars - 1
                            and slot >= 12
                            and event.pitch_role in _RESOLVING_PITCH_ROLES
                        )
                    )
                    if not phrase_exempt:
                        continue
                source_weight = max(0.0, min(1.0, kick))
            start = anchor + (bar * 4.0 * spb) + (slot * sixteenth) + _timing_offset_seconds(
                template=template,
                role=event.pitch_role,
                slot=slot,
                source_weight=source_weight,
                sixteenth=sixteenth,
                bar=bar,
                delayed_entry=delayed_entry,
            )
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
    hrp = int(harmonic_root_pc) % 12
    out = _apply_vocabulary_minor7_harmonic_guard(out, harmonic_root_pc=hrp)
    out = _finalize_vocabulary_phrase_notes(
        out,
        template=template,
        tempo=tempo,
        bar_count=bar_count,
        root_midi=root_midi,
        conditioning=conditioning,
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
            harmonic_root_pc=root_pc,
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
