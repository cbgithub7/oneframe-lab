"""Errors that say whose fault a failure is.

A node that breaks its manifest fails with kind `contract`, reported against that node. Anything
else that goes wrong inside the engine is the engine's own bug: it is reported as one (`run.failed`
with kind `engine` and its trace), never blamed on the node that happened to be running.
"""

from __future__ import annotations


class ContractError(ValueError):
    """A contract between nodes was broken: an output written outside the run's own folder, or a
    value that reached a port whose facets it does not fit. The second may be no node's fault (a
    facet the planner could only learn at run time); spec 006 gives it its own kind, naming the
    edge."""
