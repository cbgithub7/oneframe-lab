"""The protocol, declared (spec 018): `contracts/protocol.json` names every event and method, the
engine checks what it says against it."""

from __future__ import annotations

import pytest

from oneframe import protocol
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
