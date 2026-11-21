"""Slots (spec 023): what lets several runs go at once without two attempts on one device.

A slot is named by what an attempt runs on: a card, by its index, the processor, or the engine's
own process, for a node that runs there. Each attempt holds its slot while it runs, so one attempt
at a time goes on each card, one on the processor and one in the engine's process, and a run on the
processor and one on a card go side by side. A step that must wait is told what it waits behind
(the place just ahead of it in the slot's line), and takes the slot when every place ahead of it
has let go: first come, first served. A wait watches the run's stop, so a step that waits stops at
once.

Standard library only: the engine's process holds one set of slots, which its scheduler is given.
"""

from __future__ import annotations

import contextlib
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from oneframe.node import Stopped

# The slot of a node that runs in the engine's own process: never two at once, since nothing there
# was written to run beside itself.
ENGINE = "engine"
# How often a wait looks at the run's stop, in seconds: a stop is an Event the line cannot wait on.
POLL_S = 0.05


@dataclass(frozen=True)
class Slot:
    """What an attempt runs on: `CUDA` with a card's index, `CPU`, or `ENGINE`."""

    device: str
    card: int | None = None

    def to_json(self) -> dict[str, Any]:
        """As `node.queued` says it: `device`, and a card's `card`."""
        return {"device": self.device, **({"card": self.card} if self.card is not None else {})}


@dataclass(eq=False)
class Place:
    """One step's place in a line: its run and step, and, for an item of a step run for each, the
    item and how many there are (spec 021). Two places are never equal, even for the same step."""

    run: str
    step: str
    tag: dict[str, int]

    def to_json(self) -> dict[str, Any]:
        """As `node.queued`'s `behind` says it."""
        return {"run": self.run, "step": self.step, **self.tag}


class Slots:
    """Each slot's line: the place that holds it first, then each that waits, in the order they
    asked."""

    def __init__(self) -> None:
        self._changed = threading.Condition()
        self._lines: dict[Slot, list[Place]] = {}

    def take(self, slot: Slot, place: Place, stop: threading.Event, queued: Callable[[Place], None]) -> bool:
        """Take `slot` for `place`, waiting behind each place already in its line; whether it
        waited. `queued` is told the place just ahead, once, when it must wait; Stopped if `stop`
        is set while it waits."""
        with self._changed:
            line = self._lines.setdefault(slot, [])
            ahead = line[-1] if line else None
            line.append(place)
        if ahead is None:
            return False
        try:
            queued(ahead)  # said outside the lock: an event is written to the app
            with self._changed:
                while line[0] is not place:
                    if stop.is_set():
                        raise Stopped()
                    self._changed.wait(POLL_S)
        except BaseException:  # stopped, or the event could not be said: the line goes on without it
            self.give(slot, place)
            raise
        return True

    def give(self, slot: Slot, place: Place) -> None:
        """Let `slot` go, or leave its line: the next place in it takes it."""
        with self._changed:
            line = self._lines.get(slot, [])
            if place in line:
                line.remove(place)
            if not line:
                self._lines.pop(slot, None)
            self._changed.notify_all()

    @contextlib.contextmanager
    def held(
        self, slot: Slot, place: Place, stop: threading.Event, queued: Callable[[Place], None]
    ) -> Iterator[None]:
        """`slot`, held by `place` for the block."""
        self.take(slot, place, stop, queued)
        try:
            yield
        finally:
            self.give(slot, place)
