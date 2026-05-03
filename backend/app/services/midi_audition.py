"""MIDI output discovery for live audition features.

Patch 1 only enumerates output ports. Playback/open-port behavior belongs in
later patches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


_UNAVAILABLE_HINT = (
    "MIDI output enumeration is unavailable. Install mido and python-rtmidi, "
    "then restart the backend."
)
_NO_OUTPUTS_HINT = (
    "No MIDI output ports were found. On macOS, enable the IAC Driver in "
    "Audio MIDI Setup to create a virtual MIDI output."
)


@dataclass(frozen=True)
class MidiOutputInfo:
    id: str
    name: str


class MidiOutput(Protocol):
    id: str
    name: str


@dataclass(frozen=True)
class MidiOutputList:
    outputs: tuple[MidiOutput, ...]
    default: str | None = None
    hint: str | None = None


class MidiOutputBackend(Protocol):
    def list_outputs(self) -> MidiOutputList:
        """Return available MIDI outputs without opening any port."""


class RtMidiBackend:
    """Production backend backed by mido's python-rtmidi integration."""

    def list_outputs(self) -> MidiOutputList:
        try:
            import mido
        except Exception:
            return MidiOutputList(outputs=(), hint=_UNAVAILABLE_HINT)

        try:
            names = tuple(str(name) for name in mido.get_output_names())
        except Exception as exc:
            return MidiOutputList(
                outputs=(),
                hint=f"{_UNAVAILABLE_HINT} MIDI backend error: {exc}",
            )

        outputs = tuple(MidiOutputInfo(id=_port_id(index, name), name=name) for index, name in enumerate(names))
        if not outputs:
            return MidiOutputList(outputs=(), hint=_NO_OUTPUTS_HINT)
        return MidiOutputList(outputs=outputs, default=outputs[0].id)


class EmptyMidiBackend:
    """Fallback backend for unavailable MIDI runtimes or hosts with no ports."""

    def __init__(self, hint: str | None = None) -> None:
        self._hint = hint or _NO_OUTPUTS_HINT

    def list_outputs(self) -> MidiOutputList:
        return MidiOutputList(outputs=(), hint=self._hint)


class FakeMidiBackend:
    """Small test backend with deterministic outputs."""

    def __init__(
        self,
        outputs: tuple[MidiOutput, ...] | list[MidiOutput],
        *,
        default: str | None = None,
        hint: str | None = None,
    ) -> None:
        self._outputs = tuple(outputs)
        self._default = default
        self._hint = hint

    def list_outputs(self) -> MidiOutputList:
        default = self._default
        if default is None and self._outputs:
            default = self._outputs[0].id
        return MidiOutputList(outputs=self._outputs, default=default, hint=self._hint)


def _port_id(index: int, name: str) -> str:
    cleaned = name.strip()
    return cleaned or f"midi-output-{index}"
