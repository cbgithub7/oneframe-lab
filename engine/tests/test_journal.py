"""Failures leave evidence (AC7 of spec 006): every event of a run or an install in its journal,
in order; journals and step logs pruned to their bound; and `npm run diagnose`, which holds no
environment variable, no path that names the user, and no hub token."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import NO_GPU, Events

from oneframe import diagnose, journal, runtime_install
from oneframe.cache import Cache
from oneframe.graph import Graph
from oneframe.journal import Journal, prune
from oneframe.layout import Layout
from oneframe.runtimes import Runtimes
from oneframe.scheduler import Scheduler


def _journal_events(folder: Path) -> list[dict[str, Any]]:
    [path] = list(folder.glob("*.ndjson"))
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert all("at" in row for row in rows)
    assert (rows[0]["event"], rows[0]["format"]) == ("journal", journal.JOURNAL_FORMAT)  # it says its format
    return [{k: v for k, v in row.items() if k != "at"} for row in rows[1:]]


@pytest.mark.usefixtures("basic_nodes")
def test_a_runs_events_are_in_its_journal_in_order(
    tmp_path: Path, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    folder = tmp_path / "journal"
    graph = Graph.from_json(
        {
            "version": 1,
            "nodes": {"i": {"node": "test.image"}, "d": {"node": "test.const_depth"}},
            "edges": [{"from": "i.image", "to": "d.image"}],
        }
    )
    result = scheduler_for(journal_dir=folder).run(graph, events.append)
    assert result.status == "done", result.error
    assert len(events) > 4
    assert _journal_events(folder) == json.loads(json.dumps(events, default=str))


def test_an_installs_events_are_in_its_journal_failure_included(
    tmp_path: Path, tiny: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing(
        _runtime: Any, _build: str, _data: Path, _uv: str, emit: Callable[..., None], *_a: Any, **_k: Any
    ):
        emit({"runtime": "tiny", "build": "cpu", "event": "runtime.step", "step": "marker"})
        raise runtime_install.InstallFailed("uv sync failed (exit 2).", "the tail")

    monkeypatch.setattr(runtime_install, "install", failing)
    data = tmp_path / "data"
    manager = Runtimes(data, [tiny.parent], uv=os.devnull, profile=lambda: NO_GPU)
    manager.uv = __file__  # any file will do: install is replaced
    seen: list[dict[str, Any]] = []
    assert manager.install("tiny", emit=seen.append) is None
    kept = _journal_events(Layout(data).journals)
    assert kept == seen
    assert [e["event"] for e in kept] == ["runtime.start", "runtime.step", "runtime.failed"]
    assert (kept[-1]["kind"], kept[-1]["reason"], kept[-1]["retry"]) == ("runtime", "install_failed", True)


def test_pruning_keeps_its_bound(tmp_path: Path) -> None:
    for i in range(30):
        path = tmp_path / f"{i:02}.ndjson"
        path.write_bytes(b"x" * 100)
        os.utime(path, ns=(10**9 * i, 10**9 * i))
    gone = prune(tmp_path, keep=10, max_bytes=550, pattern="*.ndjson")
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == [f"{i:02}.ndjson" for i in range(25, 30)]  # the newest, within the size
    assert len(gone) == 25
    prune(tmp_path, keep=2, max_bytes=10**6, pattern="*.ndjson")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["28.ndjson", "29.ndjson"]
    big = tmp_path / "99.ndjson"
    big.write_bytes(b"x" * 5000)
    os.utime(big, ns=(10**12, 10**12))
    prune(tmp_path, keep=10, max_bytes=100, pattern="*.ndjson")
    assert [p.name for p in tmp_path.iterdir()] == ["99.ndjson"]  # the newest stays, whatever its size


def test_a_new_journal_prunes_the_old_and_a_full_one_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(journal, "JOURNALS_KEEP", 3)
    monkeypatch.setattr(journal, "JOURNAL_MAX_BYTES", 300)
    for i in range(5):
        one = Journal(tmp_path, f"run-{i}")
        one.write({"event": "run.start", "i": i})
        one.close()
    assert len(list(tmp_path.glob("*.ndjson"))) == 3
    full = Journal(tmp_path, "talkative")
    for i in range(50):
        full.write({"event": "runtime.log", "line": f"line {i}"})
    full.close()
    lines = full.path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["event"] == "journal.full"
    assert full.path.stat().st_size < 400


WINDOWS_HOME = "C:\\Users\\data"  # a user whose name is also an ordinary word
WINDOWS_SHORT = "C:\\Users\\DATA~1"
POSIX_HOME = "/home/data"
# Built here, so the source holds nothing a secret scanner takes for a real token.
TOKEN = "hf_" + "abcdefghij" * 3 + "ABCD"
SPELLINGS = [
    "C:\\Users\\data\\AppData\\Local\\OneframeLab",  # backslashes
    "C:/Users/data/AppData/Local/OneframeLab",  # forward slashes
    "c:\\USERS\\Data\\x",  # another case
    "C:\\Users\\DATA~1\\AppData",  # the 8.3 short name
    "\\Users\\data\\Desktop",  # no drive
    "/home/data/.local/share/oneframe-lab-dev",
    "/HOME/DATA/x",
]


def _planted_root(tmp_path: Path) -> Path:
    data = tmp_path / "root"
    layout = Layout(data)
    layout.journals.mkdir(parents=True)
    # Written as JSON, so each Windows spelling also lands JSON-escaped (C:\\Users\\data).
    rows = [{"event": "node.failed", "message": f"failed at {s}", "token": TOKEN} for s in SPELLINGS]
    rows.append({"event": "stage", "message": "the data root is fine; data is kept"})
    layout.journals.joinpath("20261003T000000Z-run-x.ndjson").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8"
    )
    layout.settings.write_text(
        json.dumps({"format": 1, "note": SPELLINGS[0], "hub": TOKEN, "where": "C:\\Users\\data"}),
        encoding="utf-8",
    )
    layout.step_logs.mkdir(parents=True)
    layout.step_logs.joinpath("r-n.log").write_text(f"{SPELLINGS[1]}\n{TOKEN}\n", encoding="utf-8")
    return data


def test_diagnose_removes_the_user_from_every_spelling_and_scrubs_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ONEFRAME_DIAGNOSE_PROBE", "probe-value-8c1f")
    data = _planted_root(tmp_path)
    out = diagnose.write(data, tmp_path / "out.json", [WINDOWS_HOME, WINDOWS_SHORT, POSIX_HOME], [])
    text = out.read_text(encoding="utf-8")
    report = json.loads(text)  # still JSON after redaction
    assert set(report) >= {"versions", "machine", "runtimes", "settings", "learned", "journals", "logs"}
    lowered = text.lower()
    for needle in ("users\\\\data", "users/data", "users\\\\\\\\data", "data~1", "/home/data"):
        assert needle not in lowered, needle
    assert TOKEN not in text and "hf_<redacted>" in text
    assert "probe-value-8c1f" not in text  # no environment variable
    assert "the data root is fine; data is kept" in text  # the word is kept where it is only a word
    assert "<user>" in text


def test_redaction_leaves_other_users_and_longer_names_alone() -> None:
    text = "C:\\Users\\database\\x /home/dataset/y /srv/home/data/z C:\\Users\\data"
    got = diagnose.redact(text, [WINDOWS_HOME, POSIX_HOME])
    assert got == "C:\\Users\\database\\x /home/dataset/y /srv/home/data/z C:\\Users\\<user>"


def test_npm_run_diagnose_writes_one_file_to_reports(tmp_path: Path) -> None:
    data = _planted_root(tmp_path)
    home = str(Path.home())
    data.joinpath("logs", "app.log").write_text(f"started in {home}\n", encoding="utf-8")
    assert diagnose.main(["--data", str(data)]) == 0
    [written] = list(Layout(data).reports.iterdir())
    text = written.read_text(encoding="utf-8")
    assert json.loads(text)["format"] == diagnose.REPORT_FORMAT
    if Path(home).name not in ("", "root"):  # a container's home may be /root, a word everywhere
        assert home not in text


def test_a_journal_that_cannot_be_written_never_fails_the_run(tmp_path: Path) -> None:
    blocked = tmp_path / "journal"
    blocked.write_text("a file where the folder should be", encoding="utf-8")
    seen: list[dict[str, Any]] = []
    with journal.journalled(seen.append, blocked, "run-x") as emit:
        emit({"event": "run.start"})
    assert seen == [{"event": "run.start"}]


def test_diagnose_reports_on_a_root_the_engine_refuses_and_changes_nothing(tmp_path: Path) -> None:
    data = tmp_path / "root"
    data.mkdir()
    (data / "oneframe-root.json").write_text('{"format": 99}', encoding="utf-8")
    before = sorted(p.relative_to(data) for p in data.rglob("*"))
    assert diagnose.main(["--data", str(data)]) == 0
    [written] = list(Layout(data).reports.iterdir())
    assert json.loads(written.read_text(encoding="utf-8"))["root_file"] == {"format": 99}
    reports = Layout(data).reports
    after = sorted(p.relative_to(data) for p in data.rglob("*") if reports not in (p, *p.parents))
    assert after == before  # only reports/ was written


@pytest.mark.usefixtures("basic_nodes")
def test_an_engine_crash_is_the_last_line_of_the_runs_journal(
    tmp_path: Path, scheduler_for: Callable[..., Scheduler], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*_args: object, **_kwargs: object) -> str:
        raise ValueError("a bug in the engine")

    monkeypatch.setattr(Cache, "key", broken)
    folder = tmp_path / "journal"
    graph = Graph.from_json({"version": 1, "nodes": {"i": {"node": "test.image"}}, "edges": []})
    with pytest.raises(ValueError, match="a bug in the engine"):  # the server reports it to the page
        scheduler_for(journal_dir=folder).run(graph, lambda _e: None, run_id="r1")
    last = _journal_events(folder)[-1]
    assert (last["event"], last["run"], last["kind"]) == ("run.failed", "r1", "engine")
    assert last["message"] == "ValueError: a bug in the engine" and "Traceback" in last["detail"]


def test_a_full_journal_still_keeps_how_the_run_ended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(journal, "JOURNAL_MAX_BYTES", 400)
    monkeypatch.setattr(journal, "ENDINGS_MAX_BYTES", 400)
    kept = Journal(tmp_path, "run-x")
    for i in range(50):
        kept.write({"event": "progress", "done": i})  # a fit that reports every iteration
    kept.write({"event": "node.failed", "kind": "oom", "message": "out of memory"})
    kept.write({"event": "progress", "done": 51})
    kept.write({"event": "run.failed", "kind": "oom"})
    for _ in range(50):
        kept.write({"event": "run.failed", "kind": "oom", "message": "x" * 40})  # bounded even so
    kept.close()
    events = [json.loads(line)["event"] for line in kept.path.read_text(encoding="utf-8").splitlines()]
    assert events.count("journal.full") == 1
    assert events[events.index("journal.full") + 1 :][:2] == ["node.failed", "run.failed"]
    assert "progress" not in events[events.index("journal.full") :]
    assert kept.path.stat().st_size <= 400 + 400 + 200


def test_redaction_catches_urls_and_wsl_spellings() -> None:
    home = "C:\\Users\\John Smith"
    text = (
        "at file:///C:/Users/John%20Smith/oneframe/app/main/main.js:39 "
        "and /mnt/c/Users/John Smith/.cache and /MNT/C/USERS/JOHN SMITH/x "
        "and C:\\Users\\John Smith\\AppData; John Smith himself is kept"
    )
    got = diagnose.redact(text, [home])
    assert "John%20Smith" not in got and "Users/John Smith" not in got and "USERS/JOHN SMITH" not in got
    assert "Users\\John Smith" not in got
    assert got.count("<user>") == 4 and got.endswith("John Smith himself is kept")
