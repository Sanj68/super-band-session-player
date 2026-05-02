"""Splice regenerated bass bars into an existing bass MIDI lane."""

from __future__ import annotations

import io
from dataclasses import dataclass

import mido


@dataclass(frozen=True)
class _TimedMessage:
    tick: int
    order: int
    message: mido.Message | mido.MetaMessage


def _to_absolute(track: mido.MidiTrack) -> list[_TimedMessage]:
    tick = 0
    out: list[_TimedMessage] = []
    for order, msg in enumerate(track):
        tick += int(msg.time)
        out.append(_TimedMessage(tick=tick, order=order, message=msg.copy(time=0)))
    return out


def _is_note_on(msg: mido.Message | mido.MetaMessage) -> bool:
    return msg.type == "note_on" and int(getattr(msg, "velocity", 0)) > 0


def _is_note_end(msg: mido.Message | mido.MetaMessage) -> bool:
    return msg.type == "note_off" or (msg.type == "note_on" and int(getattr(msg, "velocity", 0)) == 0)


def _note_key(msg: mido.Message | mido.MetaMessage) -> tuple[int, int]:
    return int(getattr(msg, "channel", 0)), int(getattr(msg, "note", 0))


def _is_replaceable_event(msg: mido.Message | mido.MetaMessage) -> bool:
    return msg.type in {"control_change", "pitchwheel", "polytouch", "aftertouch", "program_change"}


def _message_priority(msg: mido.Message | mido.MetaMessage) -> int:
    if msg.is_meta:
        return 0
    if _is_note_end(msg):
        return 1
    if _is_note_on(msg):
        return 2
    return 3


def _selected_message_indexes(messages: list[_TimedMessage], *, start_tick: int, end_tick: int) -> set[int]:
    selected: set[int] = set()
    active: dict[tuple[int, int], list[tuple[int, int]]] = {}

    for idx, timed in enumerate(messages):
        msg = timed.message
        if _is_note_on(msg):
            active.setdefault(_note_key(msg), []).append((idx, timed.tick))
        elif _is_note_end(msg):
            stack = active.get(_note_key(msg))
            if stack:
                start_idx, note_start_tick = stack.pop()
                if start_tick <= note_start_tick < end_tick:
                    selected.add(start_idx)
                    selected.add(idx)
        elif _is_replaceable_event(msg) and start_tick <= timed.tick < end_tick:
            selected.add(idx)

    for stack in active.values():
        for start_idx, note_start_tick in stack:
            if start_tick <= note_start_tick < end_tick:
                selected.add(start_idx)
    return selected


def _without_selected_events(messages: list[_TimedMessage], *, start_tick: int, end_tick: int) -> list[_TimedMessage]:
    selected = _selected_message_indexes(messages, start_tick=start_tick, end_tick=end_tick)
    return [timed for idx, timed in enumerate(messages) if idx not in selected]


def _only_selected_events(messages: list[_TimedMessage], *, start_tick: int, end_tick: int) -> list[_TimedMessage]:
    selected = _selected_message_indexes(messages, start_tick=start_tick, end_tick=end_tick)
    return [timed for idx, timed in enumerate(messages) if idx in selected and not timed.message.is_meta]


def _to_delta_track(messages: list[_TimedMessage]) -> mido.MidiTrack:
    ordered = sorted(messages, key=lambda timed: (timed.tick, _message_priority(timed.message), timed.order))
    out = mido.MidiTrack()
    last_tick = 0
    for timed in ordered:
        delta = max(0, int(timed.tick) - last_tick)
        out.append(timed.message.copy(time=delta))
        last_tick = int(timed.tick)
    if not out or out[-1].type != "end_of_track":
        out.append(mido.MetaMessage("end_of_track", time=0))
    return out


def splice_bass_bars(
    *,
    existing_midi: bytes,
    replacement_midi: bytes,
    tempo: int,
    bar_start: int,
    bar_end: int,
) -> bytes:
    """Return MIDI with events in [bar_start, bar_end) replaced from a regenerated lane."""
    existing = mido.MidiFile(file=io.BytesIO(existing_midi))
    replacement = mido.MidiFile(file=io.BytesIO(replacement_midi))
    ticks_per_bar = int(existing.ticks_per_beat) * 4
    start_tick = int(bar_start) * ticks_per_bar
    end_tick = int(bar_end) * ticks_per_bar

    out = mido.MidiFile(
        type=existing.type,
        ticks_per_beat=existing.ticks_per_beat,
        charset=existing.charset,
        debug=existing.debug,
        clip=existing.clip,
    )

    max_tracks = max(len(existing.tracks), len(replacement.tracks))
    insert_order_offset = 1_000_000
    for idx in range(max_tracks):
        existing_messages = _to_absolute(existing.tracks[idx]) if idx < len(existing.tracks) else []
        replacement_messages = _to_absolute(replacement.tracks[idx]) if idx < len(replacement.tracks) else []
        kept = _without_selected_events(existing_messages, start_tick=start_tick, end_tick=end_tick)
        inserted = [
            _TimedMessage(tick=timed.tick, order=insert_order_offset + timed.order, message=timed.message)
            for timed in _only_selected_events(replacement_messages, start_tick=start_tick, end_tick=end_tick)
        ]
        merged = kept + inserted
        if merged:
            out.tracks.append(_to_delta_track(merged))

    buf = io.BytesIO()
    out.save(file=buf)
    return buf.getvalue()
