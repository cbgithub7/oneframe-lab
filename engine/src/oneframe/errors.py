"""Every way the engine reports a failure, declared once.

A failure the engine reports, as a node failure, an install failure or an error reply, carries:

    kind      what failed, from KINDS
    reason    a snake_case word for why, from the kind's reasons, where it has one
    message   a sentence for a person
    next      what to do about it, where that is known
    retry     whether the same request may succeed later unchanged

A kind or reason that is not declared here cannot be raised: `Failure` and `check` refuse it, so a
misspelled reason fails a test instead of reaching the page as something it cannot show. The table
in docs/architecture.md is this declaration in words; a test holds the two equal.

Whose fault a failure is matters. A node that breaks its manifest fails with kind `contract`,
against that node. A value whose facets do not fit the port it reaches fails with kind `edge`,
against the edge, since the node receiving it did nothing wrong. Anything else that goes wrong
inside the engine is the engine's own bug: kind `engine`, with its trace, never blamed on a node.

Stop is not a failure. The one `Stopped` is child.py's, re-exported here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from oneframe.child import Stopped

__all__ = ["KINDS", "ContractError", "EdgeMismatch", "Failure", "Kind", "Stopped", "UndeclaredFailure"]


@dataclass(frozen=True)
class Kind:
    meaning: str
    retry: bool = False
    # reason -> (retry, meaning); a reason's retry overrides its kind's
    reasons: Mapping[str, tuple[bool, str]] = field(default_factory=dict)


KINDS: dict[str, Kind] = {
    "oom": Kind("The node ran out of memory on its device, after one retry with a smaller fit.", retry=True),
    "memory": Kind("Nothing fits on any device now; the message says what would help.", retry=True),
    "fetch": Kind("The node tried to download something; a run reads only what Download fetched."),
    "missing": Kind("The node imports something its runtime does not have."),
    "node": Kind("The node failed, and said why."),
    "error": Kind("The node's code raised an error."),
    "died": Kind("The node's process ended without a word."),
    "contract": Kind("A node broke its manifest: an output it did not declare, or one outside its folder."),
    "edge": Kind("A value reached a port whose facets it does not fit; reported against the edge."),
    "runtime": Kind(
        "A runtime cannot run, or cannot be installed or removed, now.",
        reasons={
            "not_installed": (False, "It is not installed."),
            "out_of_date": (False, "Its lock or definition changed since it was installed."),
            "installing": (True, "It is being installed."),
            "blocked": (False, "None of its builds can run on this machine."),
            "unknown": (False, "No runtime by that name is defined."),
            "moved": (False, "It was built in another data root and moved here."),
            "newer_format": (False, "A newer version of the app installed it; it is left as it is."),
            "locked": (True, "Another process is installing or removing it."),
            "busy": (True, "Another runtime is being installed; they install one at a time."),
            "in_use": (True, "A run is using it."),
            "no_uv": (False, "uv was not found."),
            "wrong_build": (False, "Only the build this machine runs is installed."),
            "unsafe_path": (
                False,
                "Its folder is not a runtime folder in the data root; nothing was deleted.",
            ),
            "install_failed": (True, "A step of the install failed; installing again resumes it."),
        },
    ),
    "graph": Kind(
        "The graph cannot run; `problems` says why.",
        reasons={"busy": (True, "Another run is going; runs go one at a time.")},
    ),
    "root": Kind(
        "The engine cannot use this data root; nothing under it was changed.",
        reasons={
            "newer_layout": (False, "A newer version of the app laid it out."),
            "unreadable": (False, "Its root file cannot be read, or does not say its format."),
        },
    ),
    "request": Kind(
        "The request was refused before any work began.",
        reasons={
            "unknown_method": (False, "The engine has no such method."),
            "refused": (False, "The app refused it: an unknown page, or a method or params not allowed."),
            "not_running": (True, "The engine is not running, or stopped while answering."),
            "timed_out": (True, "The engine did not answer in time."),
        },
    ),
    "app": Kind(
        "The app could not start the engine.",
        reasons={
            "no_uv": (False, "uv was not found."),
            "start_failed": (False, "The engine exited before it was ready."),
        },
    ),
    "engine": Kind("A bug in the engine itself, with its trace; never blamed on a node."),
}


class UndeclaredFailure(ValueError):
    """A kind or reason that KINDS does not declare."""


def check(kind: str, reason: str | None = None) -> Kind:
    found = KINDS.get(kind)
    if found is None:
        raise UndeclaredFailure(f"{kind!r} is not a declared kind of failure")
    if reason is not None and reason not in found.reasons:
        raise UndeclaredFailure(f"{reason!r} is not a declared reason for a {kind!r} failure")
    return found


def retry(kind: str, reason: str | None = None) -> bool:
    found = check(kind, reason)
    return found.reasons[reason][0] if reason is not None else found.retry


def failure(
    kind: str, message: str, reason: str | None = None, next: str | None = None, **extra: Any
) -> dict[str, Any]:
    """A failure as the engine reports it, checked against the declaration."""
    row: dict[str, Any] = {"kind": kind}
    if reason is not None:
        row["reason"] = reason
    row["message"] = message
    if next is not None:
        row["next"] = next
    row["retry"] = retry(kind, reason)
    row.update(extra)
    return row


class Failure(Exception):
    """An error the engine reports with a declared kind and reason, and what to do next."""

    kind = "engine"

    def __init__(
        self, message: str, reason: str | None = None, next: str | None = None, kind: str | None = None
    ):
        super().__init__(message)
        if kind is not None:
            self.kind = kind
        check(self.kind, reason)
        self.reason = reason
        self.next = next

    def to_json(self, **extra: Any) -> dict[str, Any]:
        return failure(self.kind, str(self), self.reason, self.next, **extra)


class ContractError(Failure, ValueError):
    """A node broke its manifest: an output written outside the run's own folder."""

    kind = "contract"


class EdgeMismatch(Failure, ValueError):
    """A value reached a port whose facets it does not fit: a facet the planner could only learn
    at run time. No node is at fault; the failure names the edge."""

    kind = "edge"

    def __init__(self, message: str, source: str, target: str):
        super().__init__(message)
        self.edge = {"from": source, "to": target}
