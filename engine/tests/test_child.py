"""The child's side of fitting to memory (spec 002): what is classified out of memory (AC6), the cap
on the card with and without torch (the uncapped half of AC7), what a node is told about its
memory, and the peaks a run reports.

The capped runs use a stand-in `torch` written by the test: a few functions that answer as a card
would, so the cap's arithmetic runs in a real child process on a machine without a GPU. What it
proves is the arithmetic and the events; that torch honours the cap is AC8's, on real hardware."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from oneframe import child
from oneframe.child import NetworkForbidden, NodeFailed, Stopped, classify, is_oom
from oneframe.executors import CHILD, child_env


class OutOfMemoryError(RuntimeError):
    """Named as torch's is; classify goes by the name, since the engine never imports torch."""


def _raised(
    exc: BaseException, cause: BaseException | None = None, *, context: bool = False
) -> BaseException:
    """`exc` as it looks once raised from `cause` (or while handling it, with `context`)."""
    try:
        if cause is None:
            raise exc
        try:
            raise cause
        except BaseException as inner:
            if context:
                raise exc  # noqa: B904 -- the implicit chain is what is being tested
            raise exc from inner
    except BaseException as caught:
        return caught


# -- AC6 -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB"),
        RuntimeError("CUDA error: out of memory"),
        RuntimeError("CUDA error: CUBLAS_STATUS_ALLOC_FAILED when calling `cublasCreate(handle)`"),
        RuntimeError("cuDNN error: CUDNN_STATUS_ALLOC_FAILED"),
        MemoryError(),
        _raised(NodeFailed("The model could not finish."), OutOfMemoryError("out of memory")),
        _raised(ValueError("while decoding"), MemoryError(), context=True),
        _raised(RuntimeError("step 3 failed"), _raised(RuntimeError("wrapped"), MemoryError())),
    ],
)
def test_ac6_out_of_memory_in_its_common_forms(exc: BaseException) -> None:
    assert classify(exc) == "oom" and is_oom(exc)


def test_ac6_an_unrelated_error_is_an_error() -> None:
    assert classify(RuntimeError("expected input with shape [1, 3, 518, 518]")) == "error"
    assert classify(_raised(RuntimeError("outer"), ValueError("inner"))) == "error"


def test_the_other_kinds_are_kept() -> None:
    assert classify(Stopped()) == "stopped"
    assert classify(ImportError("No module named 'xformers'")) == "missing"
    assert classify(NodeFailed("The image has no alpha.")) == "node"
    assert (
        classify(_raised(OSError("<urlopen error>"), NetworkForbidden("download was attempted"))) == "fetch"
    )


def test_a_chain_that_loops_ends() -> None:
    first, second = RuntimeError("a"), RuntimeError("b")
    first.__context__, second.__context__ = second, first
    assert classify(first) == "error"


# -- the child, run for real ---------------------------------------------------------------------

FAKE_TORCH = """
import json, os

_LOG = os.environ["FAKE_TORCH_LOG"]
state = {"allocated": 0}


def _log(**row):
    with open(_LOG, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\\n")


def is_available():
    return os.environ.get("FAKE_CARD", "1") == "1"


def init():
    _log(call="init")


def mem_get_info(device=None):
    return int(os.environ["FAKE_FREE"]), int(os.environ["FAKE_TOTAL"])


def set_per_process_memory_fraction(fraction, device=None):
    _log(call="fraction", fraction=fraction)


def memory_allocated(device=None):
    return state["allocated"]


def max_memory_allocated(device=None):
    return 2_500_000_000


def max_memory_reserved(device=None):
    return 3_000_000_000
"""


def _write_node(folder: Path, body: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    code = folder / "node.py"
    code.write_text(
        "import json, sys\n\n\ndef run(ctx):\n" + textwrap.indent(textwrap.dedent(body), "    "),
        encoding="utf-8",
    )
    return code


def _job(tmp_path: Path, code: Path, **extra: Any) -> dict[str, Any]:
    return {
        "node": "test.child",
        "entry": {"file": str(code), "function": "run"},
        "out_dir": str(tmp_path / "out"),
        "params": {},
        "inputs": {},
        "device": "cpu",
        **extra,
    }


def _run_child(
    tmp_path: Path, job: dict[str, Any], *, torch: bool = False, **env: str
) -> list[dict[str, Any]]:
    """child.py run as a runtime runs it, with the engine's Python; its events in order."""
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")
    extra = dict(env)
    if torch:
        site = tmp_path / "site"
        (site / "torch").mkdir(parents=True, exist_ok=True)
        (site / "torch" / "__init__.py").write_text("from . import cuda\n", encoding="utf-8")
        (site / "torch" / "cuda.py").write_text(FAKE_TORCH, encoding="utf-8")
        extra["PYTHONPATH"] = str(site)
        extra.setdefault("FAKE_TORCH_LOG", str(tmp_path / "torch.log"))
    done = subprocess.run(
        [sys.executable, "-u", str(CHILD), str(job_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=child_env(None, extra),
        timeout=60,
        check=False,
    )
    events = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
    assert events, done.stderr
    # First, the interpreter's own process id (on Windows not the launcher's the parent started).
    assert events[0]["event"] == "pid" and isinstance(events[0]["pid"], int), events[0]
    return events[1:]


def _torch_log(tmp_path: Path) -> list[dict[str, Any]]:
    path = tmp_path / "torch.log"
    return (
        [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []
    )


def _of(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [e for e in events if e["event"] == kind]


TELL = """
ctx.stage("told", json.dumps({
    "attempt": ctx.attempt,
    "budget": ctx.memory_budget_mb,
    "free": ctx.memory_free_mb(),
    "has_torch": "torch" in sys.modules,
}))
"""


def _told(events: list[dict[str, Any]]) -> dict[str, Any]:
    return json.loads(_of(events, "stage")[0]["message"])


def test_ac7_a_card_run_in_a_runtime_without_torch_runs_uncapped_and_says_so(tmp_path: Path) -> None:
    code = _write_node(tmp_path / "node", TELL)
    events = _run_child(
        tmp_path, _job(tmp_path, code, device="cuda", vram_cap_mb=5389, memory_budget_mb=5389)
    )
    ceiling = _of(events, "ceiling")
    assert ceiling == [
        {
            "event": "ceiling",
            "applied": False,
            "budget_mb": 5389,
            "why": "torch is not in this runtime, so nothing was capped",
        }
    ]
    assert events[-1]["event"] == "done"
    told = _told(events)
    assert told["free"] is None and told["budget"] == 5389 and not told["has_torch"]


def test_the_cap_is_the_smaller_of_the_budget_and_the_free_memory_less_outside_torch(tmp_path: Path) -> None:
    code = _write_node(
        tmp_path / "node", "import torch\ntorch.cuda.state['allocated'] = 1_000_000_000\n" + TELL
    )
    job = _job(
        tmp_path,
        code,
        device="cuda",
        vram_cap_mb=5389,
        memory_budget_mb=5389,
        outside_torch_mb=100,
        attempt=2,
    )
    events = _run_child(tmp_path, job, torch=True, FAKE_FREE="8000000000", FAKE_TOTAL="8590000000")
    (ceiling,) = _of(events, "ceiling")
    # min(5389, 8000 − 256) − 100
    assert ceiling == {
        "event": "ceiling",
        "applied": True,
        "budget_mb": 5389,
        "free_mb": 8000,
        "reserve_mb": 256,
        "outside_torch_mb": 100,
        "vram_cap_mb": 5289,
        "vram_total_mb": 8590,
    }
    calls = _torch_log(tmp_path)
    assert calls[0] == {"call": "init"}  # the context exists before free memory is read
    assert calls[1]["fraction"] == pytest.approx(5289 / 8590)
    told = _told(events)
    assert told == {"attempt": 2, "budget": 5389, "free": pytest.approx(4289), "has_torch": True}
    done = events[-1]
    assert done["event"] == "done" and done["peak_reserved_mb"] == 3000 and done["peak_vram_mb"] == 2500


def test_free_memory_lower_than_the_budget_sets_the_cap(tmp_path: Path) -> None:
    code = _write_node(tmp_path / "node", "pass\n")
    job = _job(tmp_path, code, device="cuda", vram_cap_mb=5389)
    events = _run_child(tmp_path, job, torch=True, FAKE_FREE="3000000000", FAKE_TOTAL="8590000000")
    assert _of(events, "ceiling")[0]["vram_cap_mb"] == 2744  # 3000 − 256, below the budget


def test_without_a_budget_only_the_free_memory_caps(tmp_path: Path) -> None:
    # "Tried anyway" and the fit turned off: capping at a budget the fit knows is too small would
    # make the run fail for certain.
    code = _write_node(tmp_path / "node", "pass\n")
    job = _job(tmp_path, code, device="cuda", vram_cap_mb=None, outside_torch_mb=50)
    events = _run_child(tmp_path, job, torch=True, FAKE_FREE="8000000000", FAKE_TOTAL="8590000000")
    (ceiling,) = _of(events, "ceiling")
    assert ceiling["budget_mb"] is None and ceiling["vram_cap_mb"] == 7694  # 8000 − 256 − 50


def test_torch_without_a_card_runs_uncapped_and_says_so(tmp_path: Path) -> None:
    code = _write_node(tmp_path / "node", "pass\n")
    job = _job(tmp_path, code, device="cuda", vram_cap_mb=5389)
    events = _run_child(tmp_path, job, torch=True, FAKE_CARD="0", FAKE_FREE="0", FAKE_TOTAL="0")
    (ceiling,) = _of(events, "ceiling")
    assert ceiling["applied"] is False and "no card" in ceiling["why"]
    assert _torch_log(tmp_path) == []


def test_a_processor_run_imports_no_torch_and_sets_no_cap(tmp_path: Path) -> None:
    code = _write_node(tmp_path / "node", TELL)
    events = _run_child(
        tmp_path, _job(tmp_path, code, memory_budget_mb=8000), torch=True, FAKE_FREE="1", FAKE_TOTAL="1"
    )
    assert not _of(events, "ceiling") and not _told(events)["has_torch"]


@pytest.mark.skipif(
    sys.platform not in ("linux", "win32"), reason="resident memory is read on Linux and Windows"
)
def test_a_processor_run_reports_its_growth_and_what_is_left_of_its_budget(tmp_path: Path) -> None:
    body = """
    before = ctx.memory_free_mb()
    block = bytearray(120_000_000)
    for i in range(0, len(block), 4096):
        block[i] = 1
    after = ctx.memory_free_mb()
    ctx.stage("told", json.dumps({"before": before, "after": after}))
    """
    code = _write_node(tmp_path / "node", body)
    events = _run_child(tmp_path, _job(tmp_path, code, memory_budget_mb=8000))
    told = _told(events)
    assert told["before"] <= 8000 and told["before"] - told["after"] >= 100
    done = events[-1]
    assert done["event"] == "done" and done["peak_ram_mb"] >= 100
    assert done["peak_vram_mb"] is None and done["peak_reserved_mb"] is None


def test_an_error_reports_its_kind_and_its_peaks(tmp_path: Path) -> None:
    body = """
    import torch
    class OutOfMemoryError(RuntimeError):
        pass
    raise OutOfMemoryError("CUDA out of memory. Tried to allocate 1.00 GiB")
    """
    code = _write_node(tmp_path / "node", body)
    job = _job(tmp_path, code, device="cuda", vram_cap_mb=5389)
    events = _run_child(tmp_path, job, torch=True, FAKE_FREE="8000000000", FAKE_TOTAL="8590000000")
    error = events[-1]
    assert error["event"] == "error" and error["kind"] == "oom"
    assert error["peak_reserved_mb"] == 3000 and error["peak_vram_mb"] == 2500
    assert "peak_ram_mb" in error


def test_the_engine_reads_its_own_resident_memory() -> None:
    now, peak = child.resident_mb()
    if sys.platform in ("linux", "win32"):
        assert now is not None and peak is not None and 0 < now <= peak + 1


# -- ctx.fallbacks -------------------------------------------------------------------------------


class FakeCuda:
    """torch.cuda as fallbacks uses it: a peak that a reset starts again, and a cache to empty."""

    def __init__(self) -> None:
        self.peak = 0
        self.calls: list[str] = []

    def is_available(self) -> bool:
        return True

    def empty_cache(self) -> None:
        self.calls.append("empty_cache")

    def reset_peak_memory_stats(self) -> None:
        self.calls.append("reset")
        self.peak = 0

    def max_memory_reserved(self) -> int:
        return self.peak

    def max_memory_allocated(self) -> int:
        return self.peak


def _ctx(tmp_path: Path, events: list[dict[str, Any]], device: str = "cuda") -> child.NodeContext:
    job = {"node": "t", "out_dir": str(tmp_path / "out"), "device": device}
    return child.NodeContext(job, events.append)


def test_a_way_that_runs_out_is_followed_by_the_next_in_the_same_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cuda = FakeCuda()
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda))
    events: list[dict[str, Any]] = []
    ctx = _ctx(tmp_path, events)

    def whole() -> str:
        cuda.peak = 4_000_000_000
        raise OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB")

    def tiled() -> str:
        cuda.peak = 1_500_000_000
        return "mesh"

    assert ctx.fallbacks("decode", [("whole", whole), ("tiled", tiled)]) == "mesh"
    assert ctx.ways == {"decode": "tiled"}
    assert events == [
        {"event": "step_oom", "stage": "decode", "way": "whole", "next": "tiled", "peak_reserved_mb": 4000.0}
    ]
    assert cuda.calls == ["empty_cache", "reset"]
    # The way that ran out is a lower bound on what this run needed.
    assert ctx.peaks()["peak_reserved_mb"] == 4000.0


def test_the_failed_way_is_released_before_the_next_starts(tmp_path: Path) -> None:
    class Tensor:
        pass

    held: list[weakref.ref[Tensor]] = []

    def whole() -> str:
        big = Tensor()
        held.append(weakref.ref(big))
        raise OutOfMemoryError("out of memory")  # the traceback's frame holds `big`

    def tiled() -> str:
        return "released" if held[0]() is None else "still held"

    assert _ctx(tmp_path, []).fallbacks("decode", [("whole", whole), ("tiled", tiled)]) == "released"


def test_only_torchs_out_of_memory_error_moves_to_the_next_way(tmp_path: Path) -> None:
    ran: list[str] = []

    def second() -> str:
        ran.append("second")
        return "ok"

    for error in (RuntimeError("CUDA error: out of memory"), MemoryError(), ValueError("bad shape")):

        def first(error: BaseException = error) -> str:
            raise error

        with pytest.raises(type(error)):
            _ctx(tmp_path, []).fallbacks("step", [("first", first), ("second", second)])
    assert ran == []


def test_torchs_error_as_the_cause_of_another_moves_on(tmp_path: Path) -> None:
    def first() -> str:
        try:
            raise OutOfMemoryError("out of memory")
        except OutOfMemoryError as exc:
            raise RuntimeError("the decoder failed") from exc

    assert _ctx(tmp_path, []).fallbacks("step", [("first", first), ("second", lambda: "ok")]) == "ok"


def test_when_every_way_runs_out_the_last_error_goes_up_as_oom(tmp_path: Path) -> None:
    def way(name: str) -> Any:
        def run() -> str:
            raise OutOfMemoryError(f"{name} ran out")

        return run

    events: list[dict[str, Any]] = []
    ctx = _ctx(tmp_path, events)
    with pytest.raises(OutOfMemoryError, match="c ran out") as info:
        ctx.fallbacks("step", [("a", way("a")), ("b", way("b")), ("c", way("c"))])
    assert classify(info.value) == "oom"
    assert [(e["way"], e["next"]) for e in events] == [("a", "b"), ("b", "c")]
    assert ctx.ways == {}
    with pytest.raises(ValueError, match="at least one way"):
        ctx.fallbacks("step", [])


def test_a_first_way_that_works_is_the_way_recorded(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path, [])
    assert ctx.fallbacks("load", [("on the card", lambda: 1), ("offloaded", lambda: 2)]) == 1
    assert ctx.ways == {"load": "on the card"}


def test_the_ways_reach_the_done_event(tmp_path: Path) -> None:
    body = """
    class OutOfMemoryError(RuntimeError):
        pass
    def whole():
        raise OutOfMemoryError("out of memory")
    ctx.fallbacks("decode", [("whole", whole), ("tiled", lambda: None)])
    """
    code = _write_node(tmp_path / "node", body)
    events = _run_child(tmp_path, _job(tmp_path, code))
    assert [e["event"] for e in events] == ["step_oom", "done"]
    assert events[-1]["ways"] == {"decode": "tiled"}
