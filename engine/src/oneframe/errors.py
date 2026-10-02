"""Errors that say whose fault a failure is.

A node that breaks its manifest fails with kind `contract`, reported against that node. Anything
else that goes wrong inside the engine is the engine's own bug: it is reported as one (`run.failed`
with kind `engine` and its trace), never blamed on the node that happened to be running.
"""

from __future__ import annotations


class ContractError(ValueError):
    """A node broke its manifest: a value whose facets do not fit the port it reaches, or an output
    written outside the run's own folder."""
