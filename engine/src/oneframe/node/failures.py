"""What a failure is called: the kind a node's run ends with, in a child or in the engine's process.

The engine acts on the kind (errors.py declares them): oom is retried with a smaller fit; the rest
stop the run. A node's own words never decide it.
"""

from __future__ import annotations

from oneframe.node import NodeFailed, Stopped


class NetworkForbidden(ConnectionError):
    """Something tried to download during a run.

    A ConnectionError, so an OSError: urllib wraps it in URLError, and libraries that fall back to
    their cache when offline do so here too (torch.hub asks github.com for the default branch even
    when the repo is cached, and only a URLError sends it to the cache)."""


_OOM_TEXT = ("out of memory", "cublas_status_alloc_failed", "cudnn_status_alloc_failed")
_FETCH_TEXT = ("download was attempted", "OfflineModeIsEnabled", "LocalEntryNotFound")


def _chain(exc: BaseException) -> list[BaseException]:
    """The error and every error it was raised from or while handling."""
    out: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        out.append(current)
        current = current.__cause__ or current.__context__
    return out


def _causes(exc: BaseException) -> list[BaseException]:
    """The chain without the node's own words: a NodeFailed's message is what the node told the
    person ("this would run out of memory at 12000 pixels"), never evidence of what happened. What
    it was raised from, or while handling, still counts."""
    return [error for error in _chain(exc) if not isinstance(error, NodeFailed)]


def is_oom(exc: BaseException) -> bool:
    """Out of memory in any of its common forms: torch's OutOfMemoryError, a CUDA, cuBLAS or cuDNN
    allocation failure, Python's MemoryError, or any of these as the cause of another error."""
    for error in _causes(exc):
        text = str(error).lower()
        if isinstance(error, MemoryError) or "OutOfMemory" in type(error).__name__:
            return True
        if any(needle in text for needle in _OOM_TEXT):
            return True
    return False


def is_torch_oom(exc: BaseException) -> bool:
    """torch's own OutOfMemoryError, by its name, anywhere in the chain. After it the CUDA context
    is still usable; after other CUDA memory errors it may not be, so those go to the engine."""
    return any(type(error).__name__ == "OutOfMemoryError" for error in _chain(exc))


def describe(exc: BaseException) -> str:
    """A failure's message: a node's own words as it wrote them, anything else with its type."""
    return str(exc) if isinstance(exc, NodeFailed) else f"{type(exc).__name__}: {exc}"


def classify(exc: BaseException) -> str:
    """The kinds the engine acts on: oom is retried with a smaller fit; the rest stop the run."""
    if isinstance(exc, Stopped):
        return "stopped"
    for error in _causes(exc):
        text = f"{type(error).__name__}: {error}"
        if isinstance(error, NetworkForbidden) or any(needle in text for needle in _FETCH_TEXT):
            return "fetch"
    if is_oom(exc):
        return "oom"
    if isinstance(exc, ImportError):
        return "missing"
    if isinstance(exc, NodeFailed):
        return "node"
    return "error"
