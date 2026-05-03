"""MIDI output discovery for live audition features.

Patch 1 only enumerates output ports. Playback/open-port behavior belongs in
later patches.
"""

from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from io import BytesIO
import threading
import time
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


class MidiOutputPort(Protocol):
    def send(self, message: object) -> None:
        """Send one MIDI message to the output port."""

    def close(self) -> None:
        """Close the output port."""


@dataclass(frozen=True)
class MidiOutputList:
    outputs: tuple[MidiOutput, ...]
    default: str | None = None
    hint: str | None = None


class MidiOutputBackend(Protocol):
    def list_outputs(self) -> MidiOutputList:
        """Return available MIDI outputs without opening any port."""

    def open_output(self, output_id: str) -> MidiOutputPort:
        """Open an output by id or name."""


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

    def open_output(self, output_id: str) -> MidiOutputPort:
        try:
            import mido
        except Exception as exc:
            raise MidiOutputUnavailable(_UNAVAILABLE_HINT) from exc

        result = self.list_outputs()
        output = _find_output(result.outputs, output_id)
        if output is None:
            raise MidiOutputUnavailable(_missing_output_hint(output_id, result))
        try:
            return mido.open_output(output.name)
        except Exception as exc:
            raise MidiOutputUnavailable(f"Unable to open MIDI output '{output.name}': {exc}") from exc


class EmptyMidiBackend:
    """Fallback backend for unavailable MIDI runtimes or hosts with no ports."""

    def __init__(self, hint: str | None = None) -> None:
        self._hint = hint or _NO_OUTPUTS_HINT

    def list_outputs(self) -> MidiOutputList:
        return MidiOutputList(outputs=(), hint=self._hint)

    def open_output(self, output_id: str) -> MidiOutputPort:
        raise MidiOutputUnavailable(self._hint)


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
        self.opened_outputs: list[str] = []
        self.sent_messages: list[object] = []
        self.closed_outputs: list[str] = []

    def list_outputs(self) -> MidiOutputList:
        default = self._default
        if default is None and self._outputs:
            default = self._outputs[0].id
        return MidiOutputList(outputs=self._outputs, default=default, hint=self._hint)

    def open_output(self, output_id: str) -> MidiOutputPort:
        result = self.list_outputs()
        output = _find_output(result.outputs, output_id)
        if output is None:
            raise MidiOutputUnavailable(_missing_output_hint(output_id, result))
        self.opened_outputs.append(output.name)
        return FakeMidiOutputPort(output.name, self.sent_messages, self.closed_outputs)


class FakeMidiOutputPort:
    def __init__(self, name: str, sent_messages: list[object], closed_outputs: list[str]) -> None:
        self.name = name
        self._sent_messages = sent_messages
        self._closed_outputs = closed_outputs

    def send(self, message: object) -> None:
        self._sent_messages.append(message)

    def close(self) -> None:
        self._closed_outputs.append(self.name)


class MidiOutputUnavailable(RuntimeError):
    """Raised when a requested MIDI output cannot be opened."""


@dataclass(frozen=True)
class AuditionState:
    playing: bool = False
    session_id: str | None = None
    mode: str | None = None
    output: str | None = None
    started_at: float | None = None
    duration_seconds: float | None = None

    def to_dict(self) -> dict[str, bool | str | float | None]:
        return asdict(self)


@dataclass(frozen=True)
class AuditionStartResult:
    status: str
    session_id: str
    mode: str
    output: str
    duration_seconds: float


class AuditionPlayer:
    def __init__(self, backend: MidiOutputBackend) -> None:
        self._backend = backend
        self._lock = threading.Lock()
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._state = AuditionState()

    def set_backend(self, backend: MidiOutputBackend) -> None:
        self.stop()
        with self._lock:
            self._backend = backend

    def start(
        self,
        *,
        session_id: str,
        mode: str,
        output_id: str,
        midi_bytes: bytes,
        loop: bool = False,
    ) -> AuditionStartResult:
        midi_file = _parse_midi(midi_bytes)
        duration = float(midi_file.length or 0.0)
        output_info = self._resolve_output(output_id)
        port = self._backend.open_output(output_info.id)

        self.stop()

        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._play_worker,
            args=(midi_bytes, port, stop_event, loop),
            name=f"midi-audition-{session_id}",
            daemon=True,
        )
        started_at = time.time()
        with self._lock:
            self._stop_event = stop_event
            self._thread = thread
            self._state = AuditionState(
                playing=True,
                session_id=session_id,
                mode=mode,
                output=output_info.name,
                started_at=started_at,
                duration_seconds=duration,
            )
        thread.start()
        return AuditionStartResult(
            status="playing",
            session_id=session_id,
            mode=mode,
            output=output_info.name,
            duration_seconds=duration,
        )

    def stop(self) -> None:
        with self._lock:
            stop_event = self._stop_event
            thread = self._thread
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            if self._thread is thread:
                self._stop_event = None
                self._thread = None
                self._state = AuditionState()

    def state(self) -> AuditionState:
        with self._lock:
            thread = self._thread
            state = self._state
        if state.playing and thread is not None and not thread.is_alive():
            with self._lock:
                if self._thread is thread:
                    self._stop_event = None
                    self._thread = None
                    self._state = AuditionState()
                    return self._state
        return state

    def _resolve_output(self, output_id: str) -> MidiOutput:
        result = self._backend.list_outputs()
        output = _find_output(result.outputs, output_id)
        if output is None:
            raise MidiOutputUnavailable(_missing_output_hint(output_id, result))
        return output

    def _play_worker(
        self,
        midi_bytes: bytes,
        port: MidiOutputPort,
        stop_event: threading.Event,
        loop: bool,
    ) -> None:
        active_notes: set[tuple[int, int]] = set()
        try:
            while True:
                midi_file = _parse_midi(midi_bytes)
                start = time.perf_counter()
                elapsed = 0.0
                for message in midi_file:
                    if stop_event.is_set():
                        return
                    elapsed += float(message.time)
                    wait_until = start + elapsed
                    while not stop_event.is_set():
                        remaining = wait_until - time.perf_counter()
                        if remaining <= 0:
                            break
                        stop_event.wait(min(remaining, 0.05))
                    if stop_event.is_set():
                        return
                    if message.is_meta:
                        continue
                    port.send(message)
                    _track_active_note(active_notes, message)
                if not loop or stop_event.is_set():
                    return
        finally:
            _send_panic(port, active_notes)
            port.close()
            with self._lock:
                if self._stop_event is stop_event:
                    self._stop_event = None
                    self._thread = None
                    self._state = AuditionState()


def _port_id(index: int, name: str) -> str:
    cleaned = name.strip()
    return cleaned or f"midi-output-{index}"


def _find_output(outputs: tuple[MidiOutput, ...], output_id: str) -> MidiOutput | None:
    wanted = output_id.strip()
    for output in outputs:
        if output.id == wanted or output.name == wanted:
            return output
    return None


def _missing_output_hint(output_id: str, result: MidiOutputList) -> str:
    if result.hint:
        return result.hint
    available = ", ".join(output.name for output in result.outputs) or "none"
    return f"MIDI output '{output_id}' is not available. Available outputs: {available}."


def _parse_midi(midi_bytes: bytes):
    try:
        import mido
    except Exception as exc:
        raise MidiOutputUnavailable(_UNAVAILABLE_HINT) from exc
    return mido.MidiFile(file=BytesIO(midi_bytes))


def _track_active_note(active_notes: set[tuple[int, int]], message: object) -> None:
    msg_type = getattr(message, "type", "")
    channel = int(getattr(message, "channel", 0))
    note = getattr(message, "note", None)
    if msg_type == "note_on" and note is not None and int(getattr(message, "velocity", 0)) > 0:
        active_notes.add((channel, int(note)))
    elif msg_type in ("note_off", "note_on") and note is not None:
        active_notes.discard((channel, int(note)))


def _send_panic(port: MidiOutputPort, active_notes: set[tuple[int, int]]) -> None:
    try:
        import mido
    except Exception:
        return
    for channel, note in sorted(active_notes):
        port.send(mido.Message("note_off", channel=channel, note=note, velocity=0))
    for channel in range(16):
        port.send(mido.Message("control_change", channel=channel, control=123, value=0))
