"""Shared rhythmic law for the first coordinated Fusion rhythm section.

The contract is deliberately symbolic.  It says *where* the band agrees to
lean, answer, and leave space; harmony and instrument renderers still decide
the concrete pitches and MIDI implementation.  One immutable contract can
therefore drive drums, bass, and keys without making any lane copy another.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import random
from typing import Any, Final, Iterable, Mapping, TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.conditioning import UnifiedConditioning


FUSION_CONTRACT_VERSION: Final[int] = 1
SLOTS_PER_BAR: Final[int] = 16
PPQ: Final[int] = 960

_BASS_EVENT_ROLES: Final[frozenset[str]] = frozenset(
    {
        "anchor",
        "answer",
        "colour",
        "fifth",
        "octave",
        "connector",
        "pickup",
        "anticipation",
    }
)
_KEYS_EVENT_ROLES: Final[frozenset[str]] = frozenset(
    {"answer", "punctuation", "push", "lift"}
)
_PHRASE_ROLES: Final[tuple[str, ...]] = (
    "statement",
    "variation",
    "contrast",
    "return",
)


def _slots(values: Iterable[int]) -> tuple[int, ...]:
    return tuple(sorted({max(0, min(SLOTS_PER_BAR - 1, int(value))) for value in values}))


@dataclass(frozen=True, slots=True)
class FusionBassEvent:
    slot: int
    role: str
    duration_slots: float
    accent: float = 0.75
    optional: bool = False

    def __post_init__(self) -> None:
        if not 0 <= int(self.slot) < SLOTS_PER_BAR:
            raise ValueError(f"bass event slot out of range: {self.slot}")
        if self.role not in _BASS_EVENT_ROLES:
            raise ValueError(f"unsupported bass event role: {self.role}")
        if not 0.2 <= float(self.duration_slots) <= 8.0:
            raise ValueError(f"bass duration_slots out of range: {self.duration_slots}")
        if not 0.0 <= float(self.accent) <= 1.0:
            raise ValueError(f"bass accent out of range: {self.accent}")


@dataclass(frozen=True, slots=True)
class FusionKeysEvent:
    slot: int
    role: str
    duration_slots: float
    accent: float = 0.65

    def __post_init__(self) -> None:
        if not 0 <= int(self.slot) < SLOTS_PER_BAR:
            raise ValueError(f"keys event slot out of range: {self.slot}")
        if self.role not in _KEYS_EVENT_ROLES:
            raise ValueError(f"unsupported keys event role: {self.role}")
        if not 0.2 <= float(self.duration_slots) <= 8.0:
            raise ValueError(f"keys duration_slots out of range: {self.duration_slots}")
        if not 0.0 <= float(self.accent) <= 1.0:
            raise ValueError(f"keys accent out of range: {self.accent}")


@dataclass(frozen=True, slots=True)
class FusionPercussionEvent:
    slot: int
    pitch: int
    velocity: int

    def __post_init__(self) -> None:
        if not 0 <= int(self.slot) < SLOTS_PER_BAR:
            raise ValueError(f"percussion event slot out of range: {self.slot}")
        if not 0 <= int(self.pitch) <= 127:
            raise ValueError(f"percussion pitch out of range: {self.pitch}")
        if not 1 <= int(self.velocity) <= 127:
            raise ValueError(f"percussion velocity out of range: {self.velocity}")


@dataclass(frozen=True, slots=True)
class FusionBarContract:
    bar_index: int
    two_bar_phase: int
    phrase_role: str
    energy: float
    kick_slots: tuple[int, ...]
    snare_slots: tuple[int, ...]
    hat_slots: tuple[int, ...]
    percussion_events: tuple[FusionPercussionEvent, ...]
    bass_events: tuple[FusionBassEvent, ...]
    keys_events: tuple[FusionKeysEvent, ...]
    protected_melodic_rest_slots: tuple[int, ...]
    intentional_ensemble_slots: tuple[int, ...]
    microtiming_ticks: tuple[int, ...]
    source_adapted: bool = False

    def __post_init__(self) -> None:
        if int(self.bar_index) < 0:
            raise ValueError("bar_index must be non-negative")
        if int(self.two_bar_phase) not in (0, 1):
            raise ValueError("two_bar_phase must be 0 or 1")
        if self.phrase_role not in _PHRASE_ROLES:
            raise ValueError(f"unsupported phrase role: {self.phrase_role}")
        if not 0.0 <= float(self.energy) <= 1.0:
            raise ValueError(f"energy out of range: {self.energy}")
        if len(self.microtiming_ticks) != SLOTS_PER_BAR:
            raise ValueError("microtiming_ticks must contain exactly 16 values")
        for field_name in (
            "kick_slots",
            "snare_slots",
            "hat_slots",
            "protected_melodic_rest_slots",
            "intentional_ensemble_slots",
        ):
            values = tuple(getattr(self, field_name))
            if values != _slots(values):
                raise ValueError(f"{field_name} must be sorted, unique 16th-note slots")
        melodic_onsets = {
            *(int(event.slot) for event in self.bass_events),
            *(int(event.slot) for event in self.keys_events),
        }
        collision = melodic_onsets.intersection(self.protected_melodic_rest_slots)
        if collision:
            raise ValueError(
                "protected melodic rests contain authored onsets: "
                + ", ".join(str(slot) for slot in sorted(collision))
            )
        bass_slots = [int(event.slot) for event in self.bass_events]
        if len(bass_slots) != len(set(bass_slots)):
            raise ValueError("bass events must have unique onset slots per bar")
        keys_slots = [int(event.slot) for event in self.keys_events]
        if len(keys_slots) != len(set(keys_slots)):
            raise ValueError("keys events must have unique onset slots per bar")
        unmarked_collisions = (
            set(bass_slots).intersection(keys_slots)
            - set(self.intentional_ensemble_slots)
        )
        if unmarked_collisions:
            raise ValueError(
                "bass/keys collisions must be declared as intentional: "
                + ", ".join(str(slot) for slot in sorted(unmarked_collisions))
            )


@dataclass(frozen=True, slots=True)
class FusionGrooveContract:
    contract_id: str
    version: int
    covenant_id: str
    seed: int
    bar_count: int
    source_signature: str
    source_mode: str
    bars: tuple[FusionBarContract, ...]

    def __post_init__(self) -> None:
        if int(self.version) != FUSION_CONTRACT_VERSION:
            raise ValueError(f"unsupported Fusion contract version: {self.version}")
        if int(self.seed) < 0:
            raise ValueError("seed must be non-negative")
        if not self.covenant_id:
            raise ValueError("covenant_id is required")
        if not self.contract_id:
            raise ValueError("contract_id is required")
        if self.source_mode not in {"authored", "reference"}:
            raise ValueError(
                "source_mode must be either 'authored' or 'reference'"
            )
        expected_source_mode = (
            "authored"
            if self.source_signature == "authored"
            else "reference"
        )
        if self.source_mode != expected_source_mode:
            raise ValueError(
                "source_mode does not match the Fusion source signature"
            )
        if int(self.bar_count) < 1 or len(self.bars) != int(self.bar_count):
            raise ValueError("bars must match bar_count")
        if tuple(bar.bar_index for bar in self.bars) != tuple(range(self.bar_count)):
            raise ValueError("contract bars must be ordered and contiguous")
        expected_contract_id = _contract_id(
            version=self.version,
            covenant_id=self.covenant_id,
            seed=self.seed,
            bar_count=self.bar_count,
            source_signature=self.source_signature,
            bars=self.bars,
        )
        if self.contract_id != expected_contract_id:
            raise ValueError(
                "contract_id does not match the canonical Fusion contract payload"
            )

    def bar(self, index: int) -> FusionBarContract:
        return self.bars[max(0, min(int(index), self.bar_count - 1))]

    def to_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "FusionGrooveContract":
        bars: list[FusionBarContract] = []
        for raw_bar in payload.get("bars", []):
            if not isinstance(raw_bar, Mapping):
                raise ValueError("Fusion contract bar payload must be an object")
            bars.append(
                FusionBarContract(
                    bar_index=int(raw_bar["bar_index"]),
                    two_bar_phase=int(raw_bar["two_bar_phase"]),
                    phrase_role=str(raw_bar["phrase_role"]),
                    energy=float(raw_bar["energy"]),
                    kick_slots=tuple(int(value) for value in raw_bar["kick_slots"]),
                    snare_slots=tuple(int(value) for value in raw_bar["snare_slots"]),
                    hat_slots=tuple(int(value) for value in raw_bar["hat_slots"]),
                    percussion_events=tuple(
                        FusionPercussionEvent(
                            slot=int(event["slot"]),
                            pitch=int(event["pitch"]),
                            velocity=int(event["velocity"]),
                        )
                        for event in raw_bar["percussion_events"]
                    ),
                    bass_events=tuple(
                        FusionBassEvent(
                            slot=int(event["slot"]),
                            role=str(event["role"]),
                            duration_slots=float(event["duration_slots"]),
                            accent=float(event["accent"]),
                            optional=bool(event.get("optional", False)),
                        )
                        for event in raw_bar["bass_events"]
                    ),
                    keys_events=tuple(
                        FusionKeysEvent(
                            slot=int(event["slot"]),
                            role=str(event["role"]),
                            duration_slots=float(event["duration_slots"]),
                            accent=float(event["accent"]),
                        )
                        for event in raw_bar["keys_events"]
                    ),
                    protected_melodic_rest_slots=tuple(
                        int(value)
                        for value in raw_bar["protected_melodic_rest_slots"]
                    ),
                    intentional_ensemble_slots=tuple(
                        int(value) for value in raw_bar["intentional_ensemble_slots"]
                    ),
                    microtiming_ticks=tuple(
                        int(value) for value in raw_bar["microtiming_ticks"]
                    ),
                    source_adapted=bool(raw_bar.get("source_adapted", False)),
                )
            )
        return cls(
            contract_id=str(payload["contract_id"]),
            version=int(payload["version"]),
            covenant_id=str(payload["covenant_id"]),
            seed=int(payload["seed"]),
            bar_count=int(payload["bar_count"]),
            source_signature=str(payload["source_signature"]),
            source_mode=str(payload.get("source_mode", "authored")),
            bars=tuple(bars),
        )


def _contract_id(
    *,
    version: int,
    covenant_id: str,
    seed: int,
    bar_count: int,
    source_signature: str,
    bars: Iterable[FusionBarContract],
) -> str:
    """Return the content-addressed identity used by history/candidate guards."""

    identity_document = {
        "version": int(version),
        "covenant_id": str(covenant_id),
        "seed": int(seed),
        "bar_count": int(bar_count),
        "source_signature": str(source_signature),
        "bars": [asdict(bar) for bar in bars],
    }
    digest = hashlib.sha256(
        json.dumps(
            identity_document,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"fc{int(version)}-{covenant_id}-{digest}"


@dataclass(frozen=True, slots=True)
class _BarTemplate:
    kick_slots: tuple[int, ...]
    snare_slots: tuple[int, ...]
    percussion_events: tuple[tuple[int, int, int], ...]
    bass_events: tuple[tuple[int, str, float, float, bool], ...]
    keys_events: tuple[tuple[int, str, float, float], ...]
    protected_rests: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class _CovenantTemplate:
    covenant_id: str
    phases: tuple[_BarTemplate, _BarTemplate]


# Original, deliberately small two-bar laws.  They are relationship grammars,
# not transcriptions: each has a different rest/anticipation architecture.
_COVENANTS: Final[tuple[_CovenantTemplate, ...]] = (
    _CovenantTemplate(
        covenant_id="anticipated_tumbao",
        phases=(
            _BarTemplate(
                kick_slots=(0, 6, 10),
                snare_slots=(4, 12),
                percussion_events=((3, 62, 60), (8, 37, 70), (15, 63, 56)),
                bass_events=(
                    (0, "anchor", 4.8, 0.96, False),
                    (6, "fifth", 1.7, 0.72, False),
                    (10, "colour", 1.4, 0.65, True),
                    (14, "anticipation", 4.6, 0.88, False),
                ),
                keys_events=((3, "answer", 1.4, 0.62), (11, "punctuation", 1.1, 0.70)),
                protected_rests=(4, 8, 12, 15),
            ),
            _BarTemplate(
                kick_slots=(3, 8, 14),
                snare_slots=(4, 12),
                percussion_events=((0, 37, 68), (6, 62, 62), (12, 37, 72)),
                bass_events=(
                    (4, "answer", 2.1, 0.78, False),
                    (7, "connector", 0.7, 0.42, True),
                    (10, "octave", 1.5, 0.76, False),
                    (14, "anticipation", 4.3, 0.90, False),
                ),
                keys_events=((2, "answer", 1.2, 0.60), (11, "push", 1.2, 0.68)),
                protected_rests=(0, 1, 8, 12, 15),
            ),
        ),
    ),
    _CovenantTemplate(
        covenant_id="clave_answer",
        phases=(
            _BarTemplate(
                kick_slots=(0, 5, 11),
                snare_slots=(4, 12),
                percussion_events=((4, 37, 66), (8, 56, 58), (14, 62, 62)),
                bass_events=(
                    (0, "anchor", 3.6, 0.94, False),
                    (5, "answer", 1.8, 0.72, False),
                    (9, "fifth", 1.5, 0.68, True),
                    (13, "octave", 1.8, 0.82, False),
                ),
                keys_events=((3, "punctuation", 1.0, 0.67), (11, "answer", 1.5, 0.62)),
                protected_rests=(2, 4, 8, 12, 15),
            ),
            _BarTemplate(
                kick_slots=(2, 6, 12),
                snare_slots=(4, 12),
                percussion_events=((0, 37, 70), (6, 62, 64), (12, 37, 68)),
                bass_events=(
                    (2, "pickup", 1.3, 0.58, True),
                    (6, "anchor", 2.7, 0.88, False),
                    (11, "colour", 1.5, 0.68, False),
                    (15, "anticipation", 3.7, 0.86, False),
                ),
                keys_events=((4, "answer", 1.2, 0.60), (9, "push", 1.3, 0.70)),
                protected_rests=(0, 1, 5, 8, 12, 14),
            ),
        ),
    ),
    _CovenantTemplate(
        covenant_id="broken_soul_space",
        phases=(
            _BarTemplate(
                kick_slots=(0, 10),
                snare_slots=(4, 12),
                percussion_events=((6, 62, 54), (12, 37, 62)),
                bass_events=(
                    (0, "anchor", 6.4, 0.92, False),
                    (10, "answer", 2.8, 0.68, False),
                    (15, "pickup", 2.2, 0.55, True),
                ),
                keys_events=((7, "answer", 2.2, 0.57),),
                protected_rests=(4, 8, 9, 12, 13, 14),
            ),
            _BarTemplate(
                kick_slots=(3, 9, 14),
                snare_slots=(4, 12),
                percussion_events=((1, 37, 56), (7, 62, 58), (12, 37, 64)),
                bass_events=(
                    (3, "anchor", 4.6, 0.86, False),
                    (9, "fifth", 3.5, 0.70, False),
                    (14, "anticipation", 4.0, 0.84, False),
                ),
                keys_events=((6, "answer", 1.8, 0.58), (12, "lift", 1.4, 0.62)),
                protected_rests=(0, 1, 2, 5, 8, 13, 15),
            ),
        ),
    ),
    _CovenantTemplate(
        covenant_id="open_funk_reply",
        phases=(
            _BarTemplate(
                kick_slots=(0, 3, 7, 11),
                snare_slots=(4, 12),
                percussion_events=((5, 62, 58), (10, 37, 64), (14, 63, 56)),
                bass_events=(
                    (0, "anchor", 2.5, 0.96, False),
                    (3, "connector", 0.7, 0.42, True),
                    (7, "fifth", 1.3, 0.72, False),
                    (11, "octave", 0.9, 0.82, False),
                    (14, "anticipation", 3.2, 0.88, False),
                ),
                keys_events=((5, "answer", 1.0, 0.66), (13, "punctuation", 0.9, 0.73)),
                protected_rests=(1, 4, 8, 9, 12, 15),
            ),
            _BarTemplate(
                kick_slots=(0, 6, 10, 13),
                snare_slots=(4, 12),
                percussion_events=((2, 37, 58), (8, 56, 56), (15, 62, 62)),
                bass_events=(
                    (0, "anchor", 2.1, 0.92, False),
                    (6, "colour", 1.3, 0.66, False),
                    (10, "connector", 0.7, 0.40, True),
                    (13, "octave", 1.6, 0.80, False),
                ),
                keys_events=((3, "answer", 1.1, 0.62), (11, "push", 1.0, 0.70)),
                protected_rests=(1, 4, 5, 8, 9, 12, 15),
            ),
        ),
    ),
)


def covenant_ids() -> tuple[str, ...]:
    return tuple(template.covenant_id for template in _COVENANTS)


def covenant_index_for_seed(seed: int) -> int:
    return abs(int(seed)) % len(_COVENANTS)


def _source_signature(
    conditioning: "UnifiedConditioning | None",
    *,
    bar_count: int,
) -> str:
    if conditioning is None:
        return "authored"
    decisions = [
        _source_bar_decision(conditioning, bar)
        for bar in range(max(1, int(bar_count)))
    ]
    if not any(decision is not None for decision in decisions):
        return "authored"
    document = {
        "bar_count": int(bar_count),
        "resolution": conditioning.source_groove_resolution,
        # Hash only the coarse decisions the contract actually consumes.
        # Continued capture of the same beat can nudge analyser floats without
        # falsely making accepted DNA stale.
        "decisions": decisions,
    }
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def fusion_source_signature(
    conditioning: "UnifiedConditioning | None",
    *,
    bar_count: int,
) -> str:
    """Public signature used to report whether a stored contract is stale."""

    return _source_signature(conditioning, bar_count=bar_count)


def _strong_source_kick(
    conditioning: "UnifiedConditioning | None",
    bar: int,
) -> int | None:
    if conditioning is None:
        return None
    if bar >= len(conditioning.source_kick_weight):
        return None
    confidence = (
        float(conditioning.source_groove_confidence[bar])
        if bar < len(conditioning.source_groove_confidence)
        else 0.0
    )
    if confidence < 0.45:
        return None
    row = conditioning.source_kick_weight[bar]
    candidates = [
        (float(weight), int(slot))
        for slot, weight in enumerate(row[:SLOTS_PER_BAR])
        if float(weight) >= 0.45
    ]
    if not candidates:
        return None
    # Prefer a characteristic syncopation over merely rediscovering beat one.
    return max(candidates, key=lambda item: (item[0], item[1] % 4 != 0, -item[1]))[1]


def _source_bar_decision(
    conditioning: "UnifiedConditioning | None",
    bar: int,
) -> tuple[int, str] | None:
    """Return the stable, contract-relevant source decision for one bar."""

    kick_slot = _strong_source_kick(conditioning, bar)
    if conditioning is None or kick_slot is None:
        return None
    active_slots = 0
    source_rows = (
        conditioning.source_onset_weight,
        conditioning.source_kick_weight,
        conditioning.source_snare_weight,
        conditioning.source_slot_pressure,
    )
    for slot in range(SLOTS_PER_BAR):
        peak = max(
            (
                float(rows[bar][slot])
                for rows in source_rows
                if bar < len(rows) and slot < len(rows[bar])
            ),
            default=0.0,
        )
        if peak >= 0.35:
            active_slots += 1
    if active_slots <= 2:
        activity = "sparse"
    elif active_slots <= 6:
        activity = "medium"
    else:
        activity = "busy"
    return int(kick_slot), activity


def _hat_slots_for_source_decision(
    decision: tuple[int, str] | None,
) -> tuple[int, ...]:
    if decision is None or decision[1] == "medium":
        return tuple(range(0, SLOTS_PER_BAR, 2))
    if decision[1] == "sparse":
        return (0, 4, 8, 12)
    return (0, 2, 3, 4, 6, 7, 8, 10, 11, 12, 14, 15)


def _microtiming(seed: int, bar: int, snare_slots: tuple[int, ...]) -> tuple[int, ...]:
    rng = random.Random((int(seed) << 7) ^ (int(bar) * 0x9E3779B1))
    offsets: list[int] = []
    for slot in range(SLOTS_PER_BAR):
        if slot in snare_slots:
            offsets.append(7)
        elif slot % 4 == 0:
            offsets.append(0)
        elif slot % 2 == 0:
            offsets.append(3)
        else:
            offsets.append(rng.choice((-3, -1, 2, 4)))
    return tuple(offsets)


def _develop_bass_events(
    events: tuple[FusionBassEvent, ...],
    *,
    phrase_role: str,
    bar: int,
    protected: set[int],
    rng: random.Random,
) -> tuple[FusionBassEvent, ...]:
    developed = list(events)
    optional_indexes = [
        index for index, event in enumerate(developed) if event.optional
    ]
    if phrase_role == "variation" and optional_indexes:
        index = optional_indexes[bar % len(optional_indexes)]
        event = developed[index]
        candidates = [
            event.slot + shift
            for shift in (2, -2, 1, -1)
            if 0 <= event.slot + shift < SLOTS_PER_BAR
            and event.slot + shift not in protected
            and all(
                other_index == index or other.slot != event.slot + shift
                for other_index, other in enumerate(developed)
            )
        ]
        if candidates:
            developed[index] = FusionBassEvent(
                slot=candidates[rng.randrange(len(candidates))],
                role=event.role,
                duration_slots=event.duration_slots,
                accent=event.accent,
                optional=event.optional,
            )
    elif phrase_role == "contrast" and optional_indexes:
        developed.pop(optional_indexes[-1])
    return tuple(sorted(developed, key=lambda event: event.slot))


def _develop_keys_events(
    events: tuple[FusionKeysEvent, ...],
    *,
    phrase_role: str,
    bar: int,
    protected: set[int],
) -> tuple[FusionKeysEvent, ...]:
    if phrase_role == "contrast" and len(events) > 1:
        return events[:1]
    if phrase_role != "variation" or not events:
        return events
    developed = list(events)
    index = bar % len(developed)
    event = developed[index]
    for shift in (2, -2, 1, -1):
        slot = event.slot + shift
        if (
            0 <= slot < SLOTS_PER_BAR
            and slot not in protected
            and all(other_index == index or other.slot != slot for other_index, other in enumerate(developed))
        ):
            developed[index] = FusionKeysEvent(
                slot=slot,
                role=event.role,
                duration_slots=event.duration_slots,
                accent=event.accent,
            )
            break
    return tuple(sorted(developed, key=lambda event: event.slot))


def _adapt_to_source_kick(
    *,
    source_slot: int | None,
    kick_slots: tuple[int, ...],
    bass_events: tuple[FusionBassEvent, ...],
    keys_slots: set[int],
    protected: set[int],
) -> tuple[tuple[int, ...], tuple[FusionBassEvent, ...], set[int], bool]:
    if source_slot is None or source_slot in kick_slots:
        return kick_slots, bass_events, protected, False

    essential_kicks = {0} if 0 in kick_slots else {kick_slots[0]}
    replaceable_kicks = [slot for slot in reversed(kick_slots) if slot not in essential_kicks]
    old_kick = replaceable_kicks[0] if replaceable_kicks else None
    adapted_kicks = set(kick_slots)
    if old_kick is not None:
        adapted_kicks.remove(old_kick)
    adapted_kicks.add(source_slot)

    events = list(bass_events)
    replaceable_events = [
        index
        for index, event in enumerate(events)
        if event.optional or event.role in {"answer", "colour", "connector"}
    ]
    if (
        replaceable_events
        and source_slot not in keys_slots
        and all(event.slot != source_slot for event in events)
    ):
        index = min(
            replaceable_events,
            key=lambda candidate: abs(events[candidate].slot - source_slot),
        )
        replaced_slot = events[index].slot
        event = events[index]
        events[index] = FusionBassEvent(
            slot=source_slot,
            role=event.role,
            duration_slots=event.duration_slots,
            accent=max(0.72, event.accent),
            optional=event.optional,
        )
        protected.discard(source_slot)
        if (
            replaced_slot not in {item.slot for item in events}
            and replaced_slot not in adapted_kicks
        ):
            protected.add(replaced_slot)
    else:
        protected.discard(source_slot)

    return (
        _slots(adapted_kicks),
        tuple(sorted(events, key=lambda event: event.slot)),
        protected,
        True,
    )


def build_fusion_contract(
    *,
    seed: int,
    bar_count: int,
    conditioning: "UnifiedConditioning | None" = None,
    covenant_id: str | None = None,
) -> FusionGrooveContract:
    """Build one deterministic, shared drums/bass/keys contract."""

    count = max(1, int(bar_count))
    if covenant_id is None:
        template = _COVENANTS[covenant_index_for_seed(seed)]
    else:
        matches = [item for item in _COVENANTS if item.covenant_id == covenant_id]
        if not matches:
            raise ValueError(f"unknown Fusion covenant: {covenant_id}")
        template = matches[0]

    source_signature = _source_signature(conditioning, bar_count=count)
    source_mode = "reference" if source_signature != "authored" else "authored"
    bars: list[FusionBarContract] = []
    for bar in range(count):
        phase = bar % 2
        arc_index = (bar // 4) % len(_PHRASE_ROLES)
        phrase_role = _PHRASE_ROLES[arc_index]
        raw = template.phases[phase]
        source_decision = _source_bar_decision(conditioning, bar)
        rng = random.Random(
            int(seed)
            ^ (bar * 0x45D9F3B)
            ^ (phase * 0xA5A5)
        )
        protected = set(raw.protected_rests)
        bass_events = tuple(
            FusionBassEvent(
                slot=slot,
                role=role,
                duration_slots=duration,
                accent=accent,
                optional=optional,
            )
            for slot, role, duration, accent, optional in raw.bass_events
        )
        keys_events = tuple(
            FusionKeysEvent(
                slot=slot,
                role=role,
                duration_slots=duration,
                accent=accent,
            )
            for slot, role, duration, accent in raw.keys_events
        )
        bass_events = _develop_bass_events(
            bass_events,
            phrase_role=phrase_role,
            bar=bar,
            protected=protected,
            rng=rng,
        )
        keys_events = _develop_keys_events(
            keys_events,
            phrase_role=phrase_role,
            bar=bar,
            protected=protected,
        )
        (
            kick_slots,
            bass_events,
            protected,
            _kick_adapted,
        ) = _adapt_to_source_kick(
            source_slot=(
                source_decision[0]
                if source_decision is not None
                else None
            ),
            kick_slots=raw.kick_slots,
            bass_events=bass_events,
            keys_slots={event.slot for event in keys_events},
            protected=protected,
        )

        melodic_onsets = {
            *(event.slot for event in bass_events),
            *(event.slot for event in keys_events),
        }
        protected.difference_update(melodic_onsets)
        bass_slots = {event.slot for event in bass_events}
        keys_slots = {event.slot for event in keys_events}
        intentional = bass_slots.intersection(keys_slots)
        energy = {
            "statement": 0.62,
            "variation": 0.70,
            "contrast": 0.48,
            "return": 0.78,
        }[phrase_role]
        bars.append(
            FusionBarContract(
                bar_index=bar,
                two_bar_phase=phase,
                phrase_role=phrase_role,
                energy=energy,
                kick_slots=kick_slots,
                snare_slots=_slots(raw.snare_slots),
                hat_slots=_hat_slots_for_source_decision(source_decision),
                percussion_events=tuple(
                    FusionPercussionEvent(
                        slot=slot,
                        pitch=pitch,
                        velocity=velocity,
                    )
                    for slot, pitch, velocity in raw.percussion_events
                ),
                bass_events=bass_events,
                keys_events=keys_events,
                protected_melodic_rest_slots=_slots(protected),
                intentional_ensemble_slots=_slots(intentional),
                microtiming_ticks=_microtiming(seed, bar, raw.snare_slots),
                source_adapted=source_decision is not None,
            )
        )

    return FusionGrooveContract(
        contract_id=_contract_id(
            version=FUSION_CONTRACT_VERSION,
            covenant_id=template.covenant_id,
            seed=int(seed),
            bar_count=count,
            source_signature=source_signature,
            bars=bars,
        ),
        version=FUSION_CONTRACT_VERSION,
        covenant_id=template.covenant_id,
        seed=int(seed),
        bar_count=count,
        source_signature=source_signature,
        source_mode=source_mode,
        bars=tuple(bars),
    )


def slot_time_seconds(
    *,
    slot: int,
    microtiming_ticks: tuple[int, ...],
    seconds_per_beat: float,
) -> float:
    """Return a slot offset using the contract's shared integer-PPQ timing."""

    clamped = max(0, min(SLOTS_PER_BAR - 1, int(slot)))
    return (
        clamped * float(seconds_per_beat) / 4.0
        + int(microtiming_ticks[clamped]) * float(seconds_per_beat) / PPQ
    )
