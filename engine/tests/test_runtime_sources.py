"""Spec 011: a runtime's source is fetched by the model store's transport (requirement 3), against
spec 003's hosts over TLS and without uv: retried, resumed from its partial, Stop seen at once, and
only the pinned bytes kept. Each fault is used up by one request; the hosts' log shows what was
asked."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import TRANSFER
from fixtures.hosts import Drop, Hosts, NoLength, Stall

from oneframe.errors import Stopped
from oneframe.models.transport import Policy
from oneframe.runtime_install import InstallFailed, fetch_source
from oneframe.runtimes import Source

DATA = bytes(range(256)) * 4096  # 1 MiB
SHA = hashlib.sha256(DATA).hexdigest()
HALF = len(DATA) // 2
FAST = Policy(stall_s=0.5, attempts=3, first_wait_s=0.01, longest_wait_s=0.05, longest_asked_s=5)
SLOW = Policy(stall_s=60, attempts=3)  # a stall alone would hold an attempt for a minute


def _source(hosts: Hosts, data: bytes = DATA, sha: str | None = None) -> Source:
    return Source("upstream", hosts.add_file("/up.tar.gz", data), sha or hashlib.sha256(data).hexdigest())


def _fetch(
    source: Source,
    downloads: Path,
    stop: threading.Event | None = None,
    policy: Policy = FAST,
    progress: Callable[[int, int | None], None] | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    transfer = {**TRANSFER, "policy": policy}
    return fetch_source(source, downloads, stop or threading.Event(), progress, log, transfer)


def test_a_source_with_the_wrong_sha256_is_refused(hosts: Hosts, tmp_path: Path) -> None:
    downloads = tmp_path / "downloads"
    wrong = b"not the pinned file"
    with pytest.raises(InstallFailed) as caught:
        _fetch(_source(hosts, wrong, "0" * 64), downloads)
    got = hashlib.sha256(wrong).hexdigest()
    assert f"whose sha256 is {got}, not the pinned {'0' * 64}" in str(caught.value)
    assert (caught.value.kind, caught.value.reason) == ("runtime", "install_failed")
    assert list(downloads.iterdir()) == []  # neither the file nor a .part is kept


def test_a_pinned_download_is_kept_and_not_fetched_again(hosts: Hosts, tmp_path: Path) -> None:
    data = DATA * 3 + b"tail!"  # three of the transport's chunks and a few bytes more
    sha = hashlib.sha256(data).hexdigest()
    source = _source(hosts, data)
    seen: list[tuple[int, int | None]] = []
    archive = _fetch(source, tmp_path, progress=lambda done, total: seen.append((done, total)))
    assert archive == tmp_path / sha and archive.read_bytes() == data
    assert seen[-1] == (len(data), len(data)) and len(seen) >= 4  # a read takes a chunk at most
    assert _fetch(source, tmp_path) == archive
    assert len(hosts.served(data)) == 1 and [p.name for p in tmp_path.iterdir()] == [sha]


def test_a_file_on_disk_that_does_not_match_is_fetched_again(hosts: Hosts, tmp_path: Path) -> None:
    (tmp_path / SHA).write_bytes(b"stale")
    assert _fetch(_source(hosts), tmp_path).read_bytes() == DATA


def test_a_dropped_source_download_resumes(hosts: Hosts, tmp_path: Path) -> None:
    """AC4: the drop is healed in the same call, from where it broke."""
    source = _source(hosts)
    hosts.fault(source.url, Drop(at=HALF))
    lines: list[str] = []
    assert _fetch(source, tmp_path, log=lines.append).read_bytes() == DATA
    first, second = hosts.requests("files")
    assert "range" not in first.headers and second.headers["range"] == f"bytes={HALF}-"
    assert [line.split(" again")[0] for line in lines] == ["Fetching the source upstream"]


def test_stop_ends_a_stalled_source_download(hosts: Hosts, tmp_path: Path) -> None:
    """AC5: Stop is seen while no byte arrives, not when the next one does."""
    source = _source(hosts)
    stall = Stall(at=HALF)
    hosts.fault(source.url, stall)
    stop = threading.Event()
    ended: list[BaseException | Path] = []

    def work() -> None:
        try:
            ended.append(_fetch(source, tmp_path, stop, SLOW))
        except BaseException as exc:
            ended.append(exc)

    thread = threading.Thread(target=work)
    thread.start()
    assert stall.holding.wait(10)
    stop.set()
    thread.join(5)
    assert not thread.is_alive() and isinstance(ended[0], Stopped)
    assert stall.holding.is_set()  # the hosts still hold the stall: nothing but Stop ended it


def test_a_stopped_source_download_resumes(hosts: Hosts, tmp_path: Path) -> None:
    """AC6: what a Stop leaves in the partial is kept, and the next fetch asks only for the rest."""
    source = _source(hosts)
    hosts.fault(source.url, Stall(at=HALF))
    stop = threading.Event()

    def progress(done: int, _total: int | None) -> None:
        if done >= HALF:
            stop.set()

    with pytest.raises(Stopped):
        _fetch(source, tmp_path, stop, SLOW, progress)
    assert (tmp_path / f"{SHA}.part").stat().st_size == HALF
    assert _fetch(source, tmp_path).read_bytes() == DATA
    assert hosts.requests("files")[-1].headers["range"] == f"bytes={HALF}-"


def test_a_source_without_a_length_that_ignores_ranges_is_fetched_whole(hosts: Hosts, tmp_path: Path) -> None:
    """GitHub's archive host sends no length and ignores a range, and closes the connection after
    the body: the partial a Stop left is started again, and the file ends where the stream does."""
    source = _source(hosts)
    (tmp_path / f"{SHA}.part").write_bytes(DATA[:HALF])
    hosts.fault(source.url, NoLength())
    assert _fetch(source, tmp_path).read_bytes() == DATA
    [asked] = hosts.requests("files")
    assert asked.headers["range"] == f"bytes={HALF}-" and asked.status == 200
    assert not (tmp_path / f"{SHA}.part").exists()
