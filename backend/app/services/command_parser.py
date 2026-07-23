"""Natural-language command parser for the Session Player plugin (v0.7).

"Make it busier", "like jamerson", "tighter", "redo bar 4" — the producer
talks, the parser maps to engine operations the API already supports.

Deliberately DETERMINISTIC: a transparent phrase grammar, not an LLM.
Instant, offline, testable, and honest — anything it doesn't understand is
returned in `unrecognized` rather than guessed at. An LLM layer (Max's
voice) can sit on top later and emit these same canonical phrases.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class CommandPlan:
    """Operations extracted from one command string."""

    density_delta: float = 0.0          # -1..1 nudge ("busier" / "more space")
    lock_delta: float = 0.0             # nudge lock-to-groove
    lock_set: float | None = None       # explicit lock value
    player: str | None = None           # persona id, or "none" to clear
    style: str | None = None
    bar_ranges: list[tuple[int, int]] = field(default_factory=list)  # 0-based [start, end)
    bar_operation: str = "variation"
    full_regenerate: bool = False
    applied: list[str] = field(default_factory=list)
    unrecognized: list[str] = field(default_factory=list)

    @property
    def has_ops(self) -> bool:
        return bool(
            self.density_delta
            or self.lock_delta
            or self.lock_set is not None
            or self.player
            or self.style
            or self.bar_ranges
            or self.full_regenerate
        )


_PLAYER_ALIASES = {
    "jamerson": "james_jamerson",
    "james jamerson": "james_jamerson",
    "motown": "james_jamerson",
    "pino": "pino",
    "palladino": "pino",
    "jaco": "jaco_pastorius",
    "pastorius": "jaco_pastorius",
    "bootsy": "bootsy",
    "collins": "bootsy",
    "marcus": "marcus",
    "miller": "marcus",
    "chambers": "paul_chambers",
    "paul chambers": "paul_chambers",
    "walking": "paul_chambers",
    "upright": "paul_chambers",
    "no player": "none",
    "plain": "none",
}

_STYLE_ALIASES = {
    "funky": "rhythmic",
    "funkier": "rhythmic",
    "rhythmic": "rhythmic",
    "smooth": "melodic",
    "smoother": "melodic",
    "melodic": "melodic",
    "slap": "slap",
    "slappy": "slap",
    "fusion": "fusion",
    "supportive": "supportive",
    "simple style": "supportive",
}

_BUSIER = ("busier", "busy", "more notes", "fill it out", "more movement", "double time feel")
_SPARSER = ("sparser", "more space", "less notes", "fewer notes", "simpler", "strip it back", "lay back", "calmer")
_TIGHTER = ("tighter", "lock it", "glue it", "on the kick", "follow the drums", "lock to the groove")
_LOOSER = ("looser", "loosen", "ignore the drums", "ignore the reference", "free it up", "off the grid")
_REGEN = ("regenerate", "again", "new take", "another take", "try again", "fresh take", "re-roll", "reroll")

# bar references: "bar 4", "bars 2-4", "bars 2 to 4", "the 4th bar",
# "turnaround on the 4th bar", "last bar", "first bar". 1-based in speech.
_BAR_RANGE = re.compile(r"bars?\s+(\d+)\s*(?:-|to|through)\s*(\d+)")
_BAR_ONE = re.compile(r"bar\s+(\d+)")
_BAR_ORDINAL = re.compile(r"(\d+)(?:st|nd|rd|th)\s+bar")

_TURNAROUND_WORDS = ("turnaround", "turn around", "fill", "walk into", "lead into")


def parse_command(text: str, *, bar_count: int) -> CommandPlan:
    plan = CommandPlan()
    raw = (text or "").strip()
    if not raw:
        plan.unrecognized.append("(empty command)")
        return plan
    t = " ".join(raw.lower().split())
    matched_any = False

    for phrase in _BUSIER:
        if phrase in t:
            plan.density_delta = +0.35
            plan.applied.append("busier (density +0.35)")
            matched_any = True
            break
    for phrase in _SPARSER:
        if phrase in t:
            plan.density_delta = -0.35
            plan.applied.append("sparser (density -0.35)")
            matched_any = True
            break

    for phrase in _TIGHTER:
        if phrase in t:
            plan.lock_delta = +0.25
            plan.applied.append("tighter (lock +0.25)")
            matched_any = True
            break
    for phrase in _LOOSER:
        if phrase in t:
            if "ignore" in phrase:
                plan.lock_set = 0.0
                plan.applied.append("ignoring the reference (lock = 0)")
            else:
                plan.lock_delta = -0.25
                plan.applied.append("looser (lock -0.25)")
            matched_any = True
            break

    for alias, player in _PLAYER_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", t):
            plan.player = player
            plan.applied.append(f"player -> {player}")
            matched_any = True
            break

    for alias, style in _STYLE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", t):
            plan.style = style
            plan.applied.append(f"style -> {style}")
            matched_any = True
            break

    # bar operations (1-based speech -> 0-based [start, end))
    def clamp_bar(b: int) -> int:
        return max(0, min(bar_count - 1, b))

    turnaround = any(w in t for w in _TURNAROUND_WORDS)
    if turnaround:
        plan.bar_operation = "turnaround"
    if m := _BAR_RANGE.search(t):
        a, b = clamp_bar(int(m.group(1)) - 1), clamp_bar(int(m.group(2)) - 1)
        plan.bar_ranges.append((min(a, b), max(a, b) + 1))
        matched_any = True
    elif m := (_BAR_ORDINAL.search(t) or _BAR_ONE.search(t)):
        b = clamp_bar(int(m.group(1)) - 1)
        plan.bar_ranges.append((b, b + 1))
        matched_any = True
    elif "last bar" in t:
        plan.bar_ranges.append((clamp_bar(bar_count - 1), bar_count))
        matched_any = True
    elif "first bar" in t:
        plan.bar_ranges.append((0, 1))
        matched_any = True
    elif turnaround:
        # "add a turnaround" with no bar named: the last bar is the turnaround bar
        plan.bar_ranges.append((clamp_bar(bar_count - 1), bar_count))
        matched_any = True

    if plan.bar_ranges:
        label = "turnaround/fill" if turnaround else "new take"
        spans = ", ".join(f"bar {a + 1}" if b == a + 1 else f"bars {a + 1}-{b}" for a, b in plan.bar_ranges)
        plan.applied.append(f"{label} on {spans} (fresh seed)")

    if not plan.bar_ranges:
        for phrase in _REGEN:
            if re.search(rf"\b{re.escape(phrase)}\b", t):
                plan.full_regenerate = True
                plan.applied.append("full new take")
                matched_any = True
                break

    if not matched_any:
        plan.unrecognized.append(raw)
    return plan
