"""The protocol, declared (spec 018): `contracts/protocol.json` names every event and method, the
engine checks what it says against it, and architecture.md's list of events says the same."""

from __future__ import annotations

import re

import pytest

from oneframe import REPO_ROOT, protocol
from oneframe.protocol import ProtocolError, parse
from oneframe.server import AT_ONCE, STOPS

RUN_STOPPED = {"run": "abc", "step": "s"}


def test_an_undeclared_event_is_a_problem() -> None:
    """AC1: each way an event can break its declaration is a problem naming the event and the
    field, and the message is sent all the same; a declared event is none."""
    assert protocol.event("run.stopped", **RUN_STOPPED) == {"event": "run.stopped", **RUN_STOPPED}
    assert protocol.problems() == []
    cases = {
        "an event the contract does not name": (
            protocol.event("run.paused", run="abc"),
            "'run.paused' is not a declared event",
        ),
        "a required field missing": (
            protocol.event("run.stopped", run="abc"),
            "run.stopped: no field step",
        ),
        "a field the contract does not declare": (
            protocol.event("run.stopped", **RUN_STOPPED, why="asked"),
            "run.stopped: undeclared field why",
        ),
        "a field of the wrong type": (
            protocol.event("run.stopped", run="abc", step=3),
            "run.stopped: step should be string, not integer",
        ),
        "a field of a nested shape": (
            protocol.event("run.done", run="abc", outputs={"s": {"text": {"type": "Text"}}}),
            "run.done: no field outputs.s.text.key",
        ),
    }
    for case, (sent, problem) in cases.items():
        assert sent["event"] in ("run.paused", "run.stopped", "run.done"), case  # sent all the same
        assert problem in protocol.problems(), case
    protocol.clear()
    # What a node's child says is checked as it is passed on, and an answer as it is given.
    protocol.check_event(
        {"event": "progress", "run": "abc", "step": "s", "stage": "", "done": 1, "total": None}
    )
    protocol.check_answer("graph.run", {"run": "abc"})
    protocol.check_answer("graph.run", None, stopped=True)
    protocol.check_error(
        "no.such", {"kind": "request", "reason": "unknown_method", "message": "m", "retry": False}
    )
    assert protocol.problems() == ["progress: no field what"]
    protocol.check_answer("engine.hello", None, stopped=True)
    protocol.check_answer("graph.run", {"run": 1})
    assert protocol.problems()[1:] == [
        "engine.hello: answered stopped, but it is not queued",
        "graph.run: run should be string, not integer",
    ]
    protocol.clear()


def test_queued_methods_are_those_the_server_queues() -> None:
    """A method the contract marks `queued` may be answered {"stopped": true}: exactly those the
    server's Requests puts on its queue, every engine method but the reads AT_ONCE and the stops."""
    c = protocol.contract()
    engine = {name for name, method in c.methods.items() if method.by == "engine"}
    assert AT_ONCE | set(STOPS) <= engine
    assert {name for name in engine if c.methods[name].queued} == engine - AT_ONCE - set(STOPS)
    assert all(not c.methods[name].queued for name in c.methods if name not in engine), "the app's own"


def test_the_contract_says_its_problems_at_once() -> None:
    """A contract that names a type that does not exist, a shape it does not have, or no sender is
    refused, every problem in one error, before any message is checked against it."""
    with pytest.raises(ProtocolError) as refused:
        parse(
            {
                "format": 1,
                "shapes": {},
                "events": {
                    "a.b": {"by": ["engine"], "with": ["nothing"], "fields": {"x": {"type": "text"}}},
                    "c.d": {"fields": {"y": {"type": "string", "optinal": True}}},
                },
                "methods": {"e.f": {"by": "engine", "params": {"fields": {}}}},
            }
        )
    assert refused.value.problems == [
        "events.a.b: with 'nothing', which is not another shape",
        "events.a.b.x: 'text' is neither a type nor a shape",
        "events.c.d.y: unknown key 'optinal'",
        "events.c.d: by should list who sends it: app, engine",
        "methods.e.f.answer: should be an object with `fields`",
    ]


def _events_section() -> str:
    text = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    start = text.index("\n## Events\n")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def test_architecture_names_every_declared_event() -> None:
    """AC3: the list that closes architecture.md's Events section names exactly the contract's
    events, both ways; the methods its prose names are not in it."""
    section = _events_section()
    last = section.strip().split("\n\n")[-1]
    assert last.startswith("**Every event"), "the Events section ends with the list of every event"
    listed = re.findall(r"`([a-z_]+(?:\.[a-z_]+)*)`", last.partition(":")[2])
    assert len(listed) == len(set(listed)), "an event is listed twice"
    c = protocol.contract()
    assert set(listed) == set(c.events), "architecture.md's list and contracts/protocol.json differ"
    prose = set(re.findall(r"`([a-z_]+\.[a-z_]+)", section.removesuffix(last)))
    named = prose & set(c.methods)
    assert named, "the section's prose names methods"
    assert not named & set(listed), "a method is in the list of events"


def test_a_contract_that_cannot_be_read_stops_no_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """The check never stops a message: with no contract to read, each still goes, and why is
    kept; a queued request of a method the engine does not have may be answered stopped."""

    def unreadable(_path: object = None) -> protocol.Contract:
        raise ProtocolError("contracts/protocol.json", ["cannot read: gone"])

    monkeypatch.setattr(protocol, "contract", unreadable)
    assert protocol.event("run.stopped", **RUN_STOPPED) == {"event": "run.stopped", **RUN_STOPPED}
    protocol.check_answer("graph.run", {"run": "abc"})
    assert protocol.problems() == ["contracts/protocol.json: cannot read: gone"]
    protocol.clear()
    monkeypatch.undo()
    protocol.check_answer("no.such", None, stopped=True)
    assert protocol.problems() == []
