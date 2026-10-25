"""Decide what to do with each event coming from the panel queue.

Pure function by design: this part decides whether a touch event triggers an
action on the PC, and must be testable without network, Windows, or delays.
Rules come from the contract (docs/service.md):

- only `toque` triggers actions; `acordou`, `mensagem`, and connection errors
  are only logged;
- `dados.destino_indisponivel` = the button belonged to another connection
  (e.g. Home Assistant): not ours;
- unknown name = not ours (a View can have buttons from another service);
- old touches (queue accumulated while PC was off) are **not** executed:
  opening a folder from 30 minutes ago is worse than not opening it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

RUN = "run"
OTHER = "other"
FOREIGN = "foreign"
OLD = "old"
MALFORMED = "malformed"


@dataclass(frozen=True)
class Decision:
    kind: str
    tool: str = ""
    reason: str = ""
    presentation: str = ""
    event_id: int | None = None

    @property
    def should_run(self) -> bool:
        return self.kind == RUN


def classify(
    event: Any,
    is_known: Callable[[str], bool],
    now: float,
    max_age_s: int,
) -> Decision:
    if not isinstance(event, dict):
        return Decision(MALFORMED, reason="event is not an object")

    event_type = event.get("tipo")
    presentation = str(event.get("apresentacao") or "")
    event_id = event.get("id") if isinstance(event.get("id"), int) else None

    if event_type != "toque":
        # "acordou" (screen woke up from touch): do not reapply brightness, just log
        return Decision(OTHER, reason=f"event {event_type!r}", presentation=presentation,
                        event_id=event_id)

    data = event.get("dados")
    if not isinstance(data, dict):
        return Decision(MALFORMED, reason="touch event without data", presentation=presentation,
                        event_id=event_id)
    if data.get("destino_indisponivel"):
        return Decision(FOREIGN, reason="connection destination did not respond",
                        presentation=presentation, event_id=event_id)

    tool = str(data.get("tool") or "").strip()
    if not tool:
        return Decision(MALFORMED, reason="touch event without tool name",
                        presentation=presentation, event_id=event_id)
    if not is_known(tool):
        return Decision(FOREIGN, tool=tool, reason="action not found in configuration",
                        presentation=presentation, event_id=event_id)

    ts = event.get("em")
    if isinstance(ts, (int, float)) and max_age_s > 0:
        age = now - ts / 1000.0
        if age > max_age_s:
            return Decision(
                OLD,
                tool=tool,
                reason=f"touch from {age:.0f}s ago (limit {max_age_s}s)",
                presentation=presentation,
                event_id=event_id,
            )

    return Decision(RUN, tool=tool, presentation=presentation, event_id=event_id)
