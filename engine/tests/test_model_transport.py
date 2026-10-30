"""Spec 003: the transport heals what goes wrong on the way (AC3, AC4), against the test hosts over
TLS. Each fault is used up by one request, so a download that completes has met it and recovered,
without a second request from the person; the hosts' log shows what was asked."""

from __future__ import annotations

import errno
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
from conftest import wait_until
from fixtures.hosts import (
    FAST,
    OTHER_CA,
    TRANSFER,
    Drop,
    Hosts,
    IgnoreRange,
    RedirectTo,
    Replace,
    Stall,
    Status,
    WrongOffset,
    client_context,
    error_code,
    rate_limit,
    retry_after,
)
from fixtures.store import sha

from oneframe.errors import Stopped
from oneframe.models import transport
from oneframe.models.transport import DownloadFailed, Policy, Transfer

DATA = bytes(range(256)) * 4096  # 1 MiB
SHA = sha(DATA)


def _transfer(
    url: str, partial: Path, size: int | None = len(DATA), sha: str | None = SHA, **kw: Any
) -> Transfer:
    kw = {**TRANSFER, **kw}
    return Transfer(url, partial, size, sha, kw.pop("stop", threading.Event()), **kw)


def _hub_file(hosts: Hosts, data: bytes = DATA) -> str:
    repo = hosts.repo("Ruicheng/moge-2-vitl-normal")
    return hosts.resolve_url(repo.id, repo.commit({"model.pt": data}), "model.pt")


def _failed(reason: str | None) -> pytest.RaisesExc[DownloadFailed]:
    return pytest.raises(DownloadFailed, check=lambda e: e.reason == reason)


def test_a_hub_file_follows_its_signed_redirect_and_is_hashed(hosts: Hosts, tmp_path: Path) -> None:
    phases: list[str] = []
    partial = tmp_path / "partial" / SHA
    url = _hub_file(hosts)
    assert _transfer(url, partial, phase=lambda name, _d: phases.append(name)).run() == (SHA, len(DATA))
    assert partial.read_bytes() == DATA
    assert len(hosts.served(DATA)) == 1 and phases == ["downloading"]
    cdn = hosts.requests("cdn")[0]
    assert "user-agent" not in cdn.headers  # no header but Range follows a redirect to another host
    assert hosts.requests("hub")[0].headers["user-agent"].startswith("oneframe-lab/")
    assert hosts.requests("hub")[0].headers["accept-encoding"] == "identity"


def test_each_attempt_names_the_hosts_it_asked_without_a_path_or_a_query(
    hosts: Hosts, tmp_path: Path
) -> None:
    phases: list[tuple[str, dict[str, Any]]] = []
    url = _hub_file(hosts)
    hosts.fault(url, Status(503), Drop(at=400_000))
    assert _transfer(url, tmp_path / SHA, phase=lambda n, d: phases.append((n, d))).run() == (SHA, len(DATA))
    hub, cdn = (hosts.base(name).removeprefix("https://") for name in ("hub", "cdn"))
    assert [(n, d["hosts"]) for n, d in phases] == [
        ("retrying", [hub]),  # the 503 came from the hub, before any redirect
        ("downloading", [hub, cdn]),
        ("retrying", [hub, cdn]),  # dropped mid-body by the cdn
        ("downloading", [hub, cdn]),
    ]  # host and port alone: the cdn's single-use path is a credential


def test_a_host_is_recorded_by_its_name_and_its_port_when_not_443() -> None:
    def host(url: str) -> str:
        return transport._host(transport._origin(urllib.parse.urlsplit(url)))

    assert host("https://HuggingFace.co/x?signature=1") == "huggingface.co"
    assert host("https://cdn.example:443/x") == "cdn.example"
    assert host("https://user:secret@cdn.example:8443/x") == "cdn.example:8443"
    assert host("https://[2001:db8::1]:8443/x") == "[2001:db8::1]:8443"


def test_a_failure_or_a_retry_names_the_hosts_asked_and_never_a_redirects_path(
    hosts: Hosts, tmp_path: Path
) -> None:
    files, cdn = (hosts.base(name).removeprefix("https://") for name in ("files", "cdn"))
    url = hosts.add_file("/depth_pro.pt", DATA)
    hosts.fault(url, RedirectTo(hosts.base("plain") + "/depth_pro.pt?signature=secret"))
    with _failed("tls") as failed:
        _transfer(url, tmp_path / SHA).run()
    assert "secret" not in str(failed.value) and failed.value.to_json()["hosts"] == [files]
    hosts.fault(url, RedirectTo("https://127.0.0.1:99999/depth_pro.pt"))  # no port: no engine bug
    with _failed(None) as failed:
        _transfer(url, tmp_path / SHA).run()
    assert failed.value.to_json()["hosts"] == [files]
    # http.client refuses a space in a path, naming the path: the record keeps only that it refused.
    phases: list[tuple[str, dict[str, Any]]] = []
    hosts.fault(url, RedirectTo(hosts.base("cdn") + "/depth_pro.pt?signature=se cret"))
    assert _transfer(url, tmp_path / SHA, phase=lambda n, d: phases.append((n, d))).run() == (SHA, len(DATA))
    name, detail = phases[0]
    assert (name, detail["why"], detail["hosts"]) == ("retrying", "InvalidURL", [files, cdn])


@pytest.mark.parametrize(
    "fault",
    [
        Drop(at=400_000),
        Stall(at=400_000),
        Stall(before_headers=True),
        WrongOffset(by=1000),
        IgnoreRange(),
        retry_after(1),
        rate_limit(1),
        Status(503),
    ],
    ids=[
        "drop",
        "stall mid-body",
        "stall before the headers",
        "206 at the wrong offset",
        "200 to a range",
        "429 Retry-After",
        "429 RateLimit",
        "503",
    ],
)
def test_each_fault_heals_without_a_second_request_from_the_person(
    hosts: Hosts, tmp_path: Path, fault: Any
) -> None:
    url = _hub_file(hosts)
    if isinstance(fault, WrongOffset | IgnoreRange):  # they break a resumed request: drop one first
        hosts.fault(url, Drop(at=300_000), fault)
    else:
        hosts.fault(url, fault)
    assert _transfer(url, tmp_path / SHA).run() == (SHA, len(DATA))
    assert hosts.faults_left() == 0
    assert (tmp_path / SHA).read_bytes() == DATA


def test_a_resumed_request_asks_for_the_rest_and_a_used_redirect_is_never_reused(
    hosts: Hosts, tmp_path: Path
) -> None:
    url = hosts.add_file("/releases/download/v3_1/dpt_beit_large_512.pt", DATA, redirect=True)
    hosts.fault(url, Drop(at=250_000))
    assert _transfer(url, tmp_path / SHA).run() == (SHA, len(DATA))
    cdn = hosts.requests("cdn")
    assert [r.status for r in cdn] == [200, 206]
    assert cdn[0].path != cdn[1].path  # a fresh signed URL from the source
    assert cdn[1].headers["range"] == "bytes=250000-"
    assert len(hosts.requests("files")) == 2


def test_a_partial_on_disk_is_read_back_then_resumed(hosts: Hosts, tmp_path: Path) -> None:
    partial = tmp_path / SHA
    partial.write_bytes(DATA[:123_456])
    phases: list[tuple[str, dict[str, Any]]] = []
    url = _hub_file(hosts)
    assert _transfer(url, partial, phase=lambda n, d: phases.append((n, d))).run() == (SHA, len(DATA))
    assert phases[0] == ("checking", {"bytes": 123_456})
    assert hosts.requests("cdn")[0].headers["range"] == "bytes=123456-"


def test_attempts_in_a_row_with_no_new_bytes_end_as_stalled_saying_what_was_kept(
    hosts: Hosts, tmp_path: Path
) -> None:
    url = _hub_file(hosts)
    hosts.fault(url, Drop(at=100_000), *[Status(503, on="body")] * 3)  # at the cdn, after the drop
    with _failed("stalled") as caught:
        _transfer(url, tmp_path / SHA).run()
    assert caught.value.kept == 100_000 and "100000 bytes were kept" in str(caught.value)
    assert (tmp_path / SHA).stat().st_size == 100_000


def test_a_host_that_keeps_asking_to_wait_ends_as_rate_limited(hosts: Hosts, tmp_path: Path) -> None:
    url = _hub_file(hosts)
    hosts.fault(url, Status(429), Status(429), Status(429))
    with _failed("rate_limited"):
        _transfer(url, tmp_path / SHA).run()
    hosts.fault(url, retry_after(3600))
    with _failed("rate_limited"):
        _transfer(url, tmp_path / SHA).run()


def test_a_changed_size_fails_before_the_body(hosts: Hosts, tmp_path: Path) -> None:
    url = hosts.add_file("/depth_pro.pt", DATA)
    hosts.fault(url, Replace(DATA + b"more"))
    with _failed("source_changed"):
        _transfer(url, tmp_path / SHA).run()
    assert not (tmp_path / SHA).exists()
    hub = _hub_file(hosts, DATA + b"more")  # the hub says so on its redirect: the cdn is never asked
    with _failed("source_changed"):
        _transfer(hub, tmp_path / SHA).run()
    assert hosts.requests("cdn") == []


def test_a_hash_mismatch_keeps_nothing(hosts: Hosts, tmp_path: Path) -> None:
    url = hosts.add_file("/depth_pro.pt", DATA)
    hosts.fault(url, Replace(b"x" * len(DATA)))
    with _failed("hash_mismatch") as caught:
        _transfer(url, tmp_path / SHA).run()
    assert caught.value.kept == 0 and not (tmp_path / SHA).exists()


def test_a_full_partial_answered_with_416_is_hashed(hosts: Hosts, tmp_path: Path) -> None:
    url = hosts.add_file("/depth_pro.pt", DATA)
    partial = tmp_path / "url-partial"
    partial.write_bytes(DATA)
    assert _transfer(url, partial, size=None, sha=None).run() == (SHA, len(DATA))  # pinning: size unknown
    assert [r.status for r in hosts.requests("files")] == [416]
    assert _transfer(url, tmp_path / "url-partial").run() == (SHA, len(DATA))  # pinned: no request at all
    assert len(hosts.requests("files")) == 1


def test_a_redirect_to_http_is_refused_before_connecting(hosts: Hosts, tmp_path: Path) -> None:
    url = hosts.add_file("/depth_pro.pt", DATA)
    hosts.fault(url, RedirectTo(hosts.base("plain") + "/depth_pro.pt"))
    with _failed("tls"):
        _transfer(url, tmp_path / SHA).run()
    assert hosts.requests("plain") == []


def test_too_little_space_is_refused_before_the_first_byte(hosts: Hosts, tmp_path: Path) -> None:
    url = hosts.add_file("/depth_pro.pt", DATA)
    with _failed("no_space"):
        _transfer(url, tmp_path / SHA, free_bytes=lambda: len(DATA) - 1).run()
    assert hosts.requests() == []


def test_a_full_disk_keeps_the_partial(hosts: Hosts, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = hosts.add_file("/depth_pro.pt", DATA)
    written: list[int] = []
    real = transport._write

    def fill(handle: Any, data: memoryview) -> int:
        room = 300_000 - sum(written)
        if room <= 0:
            # The error each system really raises: errno ENOSPC, or Windows' ERROR_DISK_FULL.
            raise OSError(errno.ENOSPC, "No space left on device", None, 112)
        written.append(real(handle, data[:room]))
        return written[-1]

    monkeypatch.setattr(transport, "_write", fill)
    with _failed("no_space") as caught:
        _transfer(url, tmp_path / SHA).run()
    assert caught.value.kept == 300_000 and (tmp_path / SHA).read_bytes() == DATA[:300_000]


def test_a_rejected_certificate_fails_as_tls(hosts: Hosts, tmp_path: Path) -> None:
    url = hosts.add_file("/depth_pro.pt", DATA)
    with _failed("tls"):
        _transfer(url, tmp_path / SHA, context=lambda: client_context(OTHER_CA)).run()


def test_certificates_are_checked_against_the_systems_trust_store() -> None:
    import truststore

    assert isinstance(transport.system_context(), truststore.SSLContext)


def test_the_systems_connect_proxy_is_used(
    hosts: Hosts, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("NO_PROXY", "no_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("https_proxy", hosts.proxy)
    url = _hub_file(hosts)
    assert transport.system_proxy("127.0.0.1") == hosts.proxy
    assert _transfer(url, tmp_path / SHA, proxy=transport.system_proxy).run() == (SHA, len(DATA))
    hub, cdn = hosts.base("hub").split("//")[1], hosts.base("cdn").split("//")[1]
    assert hosts.tunnels() == [hub, cdn]
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    assert transport.system_proxy("127.0.0.1") is None


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (401, "GatedRepo", "gated"),
        (403, "GatedRepo", "gated"),
        (401, "RepoNotFound", "not_found"),
        (404, "EntryNotFound", "not_found"),
        (404, "RevisionNotFound", "revision_gone"),
    ],
)
def test_the_hubs_error_codes_decide_the_reason(
    hosts: Hosts, tmp_path: Path, status: int, code: str, reason: str
) -> None:
    url = _hub_file(hosts)
    hosts.fault(url, error_code(status, code))
    with _failed(reason):
        _transfer(url, tmp_path / SHA).run()


def test_a_401_without_a_code_is_not_called_gated(hosts: Hosts, tmp_path: Path) -> None:
    url = _hub_file(hosts)
    hosts.fault(url, Status(401))
    with _failed(None) as caught:
        _transfer(url, tmp_path / SHA).run()
    assert "401" in str(caught.value)


@pytest.mark.parametrize("before_headers", [True, False], ids=["before the headers", "mid-body"])
def test_stop_ends_a_stalled_download_within_two_seconds_and_keeps_the_partial(
    hosts: Hosts, tmp_path: Path, before_headers: bool
) -> None:
    url = _hub_file(hosts)
    hosts.fault(url, Stall(at=200_000, before_headers=before_headers))
    stop = threading.Event()
    slow = Policy(stall_s=60, attempts=3)  # the stall alone would hold it for a minute
    done: list[BaseException | None] = []

    def work() -> None:
        try:
            _transfer(url, tmp_path / SHA, stop=stop, policy=slow).run()
            done.append(None)
        except BaseException as exc:
            done.append(exc)

    thread = threading.Thread(target=work)
    thread.start()
    wait_until(
        lambda: hosts.requests("cdn") and (before_headers or (tmp_path / SHA).stat().st_size >= 200_000),
        "the download to reach the stall",
    )
    time.sleep(0.2)  # well into the stall
    pressed = time.monotonic()
    stop.set()
    thread.join(5)
    assert not thread.is_alive() and time.monotonic() - pressed < 2
    assert isinstance(done[0], Stopped)
    partial = tmp_path / SHA
    assert (partial.stat().st_size if partial.exists() else 0) == (0 if before_headers else 200_000)


def test_a_host_that_ignores_ranges_on_a_flaky_line_never_runs_out_of_attempts(
    hosts: Hosts, tmp_path: Path
) -> None:
    """Each attempt starts the file again and breaks before the last one's offset; bytes still
    flow, so no attempt counts as fetching nothing."""
    url = _hub_file(hosts)
    hosts.fault(url, Drop(at=600_000), *[IgnoreRange(at=300_000) for _ in range(FAST.attempts + 1)])
    assert _transfer(url, tmp_path / SHA).run() == (SHA, len(DATA))
    assert hosts.faults_left() == 0


def test_an_https_proxy_is_refused_with_its_reason_before_any_request(hosts: Hosts, tmp_path: Path) -> None:
    url = hosts.add_file("/depth_pro.pt", DATA)
    with _failed(None) as caught:
        _transfer(url, tmp_path / SHA, proxy=lambda _host: "https://proxy.example:8443").run()
    assert "only an http:// proxy" in str(caught.value) and hosts.requests() == []
