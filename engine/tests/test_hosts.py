"""The hosts spec 003's tests download from (fixtures/hosts.py) behave as the real ones do, as
research.md records them, and each fault breaks exactly one request. A plain http.client is the
client here, so nothing of the engine's own transport is assumed."""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import time
import urllib.parse
from pathlib import Path

import pytest
from fixtures.hosts import (
    CA,
    OTHER_CA,
    Drop,
    Hosts,
    IgnoreRange,
    RedirectTo,
    Replace,
    Stall,
    WrongOffset,
    client_context,
    error_code,
    git_oid,
    rate_limit,
    retry_after,
    sha256,
)

WEIGHTS = bytes(range(256)) * 64  # 16 KiB
CONFIG = b'{"hidden": 1024}\n'

Answer = tuple[int, dict[str, str], bytes]


def get(
    url: str,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    body: bytes | None = None,
    ca: Path = CA,
    timeout: float = 5,
    proxy: str | None = None,
) -> Answer:
    parts = urllib.parse.urlsplit(url)
    host, port = parts.hostname or "", parts.port
    if parts.scheme == "http":
        conn: http.client.HTTPConnection = http.client.HTTPConnection(host, port, timeout=timeout)
    elif proxy:
        tunnel = urllib.parse.urlsplit(proxy)
        conn = http.client.HTTPSConnection(
            tunnel.hostname or "", tunnel.port, timeout=timeout, context=client_context(ca)
        )
        conn.set_tunnel(host, port)
    else:
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=client_context(ca))
    try:
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = b"" if method == "HEAD" else response.read()
        return response.status, {k.lower(): v for k, v in response.getheaders()}, data
    finally:
        conn.close()


def follow(url: str, headers: dict[str, str] | None = None) -> Answer:
    """GET, following redirects as a client does (a relative Location is on the same host)."""
    for _ in range(5):
        status, got, data = get(url, headers)
        if status not in (301, 302, 307, 308):
            return status, got, data
        url = urllib.parse.urljoin(url, got["location"])
    raise AssertionError("too many redirects")


def _repo(hosts: Hosts) -> tuple[str, str]:
    repo = hosts.repo("Ruicheng/moge-2-vitl-normal")
    commit = repo.commit({"model.pt": WEIGHTS, "config.json": CONFIG, "sub/extra.bin": b"x" * 10})
    return repo.id, commit


def test_a_large_file_resolves_to_a_signed_url_that_works_once(hosts: Hosts) -> None:
    repo_id, commit = _repo(hosts)
    url = hosts.resolve_url(repo_id, commit, "model.pt")
    status, headers, _ = get(url, method="HEAD")
    assert status == 302
    assert headers["x-linked-etag"] == f'"{sha256(WEIGHTS)}"'
    assert headers["x-linked-size"] == str(len(WEIGHTS))
    assert headers["x-repo-commit"] == commit
    signed = headers["location"]
    assert signed.startswith(hosts.base("cdn"))
    assert get(signed)[::2] == (200, WEIGHTS)
    assert get(signed)[0] == 403  # used up, as an expired signed URL is
    assert follow(url)[::2] == (200, WEIGHTS)  # from the source again: a fresh URL
    assert len(hosts.served(WEIGHTS)) == 2


def test_a_small_file_resolves_on_the_same_host_and_honours_ranges(hosts: Hosts) -> None:
    repo_id, _commit = _repo(hosts)
    url = hosts.resolve_url(repo_id, "main", "config.json")
    status, headers, _ = get(url)
    assert status == 307
    assert headers["location"].startswith("/api/resolve-cache/models/Ruicheng/moge-2-vitl-normal/")
    assert headers["etag"] == f'"{git_oid(CONFIG)}"'
    status, headers, data = follow(url, {"Range": "bytes=4-"})
    assert (status, data) == (206, CONFIG[4:])
    assert headers["content-range"] == f"bytes 4-{len(CONFIG) - 1}/{len(CONFIG)}"
    status, headers, _ = follow(url, {"Range": f"bytes={len(CONFIG)}-"})
    assert (status, headers["content-range"]) == (416, f"bytes */{len(CONFIG)}")


def test_the_apis_give_the_canonical_id_the_commit_and_each_sha256() -> None:
    hosts = Hosts(page_size=2)
    try:
        repo_id, commit = _repo(hosts)
        hosts.repo(repo_id, gated="manual")
        status, _, data = get(f"{hosts.hub}/api/models/ruicheng/MOGE-2-vitl-normal/revision/main")
        info = json.loads(data)
        assert status == 200
        assert (info["id"], info["sha"], info["gated"]) == (repo_id, commit, "manual")
        rows: list[dict[str, object]] = []
        url: str | None = f"{hosts.hub}/api/models/{repo_id}/tree/{commit}?recursive=true"
        while url:
            status, headers, data = get(url)
            rows += json.loads(data)
            link = headers.get("link")
            url = link.split(";")[0].strip("<>") if link else None
        assert sorted(str(r["path"]) for r in rows) == ["config.json", "model.pt", "sub", "sub/extra.bin"]
        lfs = {str(r["path"]): r.get("lfs") for r in rows}
        assert lfs["model.pt"] == {"oid": sha256(WEIGHTS), "size": len(WEIGHTS), "pointerSize": 130}
        assert lfs["config.json"] is None  # a small git file has no published sha256
        form = urllib.parse.urlencode([("paths", "model.pt"), ("paths", "gone.pt")]).encode()
        status, _, data = get(
            f"{hosts.hub}/api/models/{repo_id}/paths-info/{commit}",
            {"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
            body=form,
        )
        assert [r["path"] for r in json.loads(data)] == ["model.pt"]
    finally:
        hosts.close()


def test_errors_carry_the_hubs_codes(hosts: Hosts) -> None:
    repo_id, commit = _repo(hosts)
    cases = [
        (hosts.resolve_url("nobody/nothing", commit, "model.pt"), 401, "RepoNotFound"),
        (hosts.resolve_url(repo_id, "f" * 40, "model.pt"), 404, "RevisionNotFound"),
        (hosts.resolve_url(repo_id, commit, "gone.pt"), 404, "EntryNotFound"),
    ]
    for url, status, code in cases:
        got, headers, _ = get(url)
        assert (got, headers["x-error-code"]) == (status, code)
    hosts.repo(repo_id, gated="auto")
    got, headers, _ = get(hosts.resolve_url(repo_id, commit, "model.pt"))
    assert (got, headers["x-error-code"]) == (401, "GatedRepo")
    hosts.repo(repo_id).squash(commit)
    got, headers, _ = get(f"{hosts.hub}/api/models/{repo_id}/revision/{commit}")
    assert (got, headers["x-error-code"]) == (404, "RevisionNotFound")


def test_each_fault_is_used_up_by_one_request(hosts: Hosts) -> None:
    url = hosts.add_file("/releases/v3_1/dpt.pt", WEIGHTS, redirect=True)
    hosts.fault(
        url,
        retry_after(1),
        rate_limit(2),
        error_code(403, "GatedRepo"),
        RedirectTo(hosts.base("plain") + "/releases/v3_1/dpt.pt"),
        Drop(at=100),
        WrongOffset(by=7),
        IgnoreRange(),
        Replace(b"y" * len(WEIGHTS)),
    )
    status, headers, _ = get(url)
    assert (status, headers["retry-after"]) == (429, "1")
    status, headers, _ = get(url)
    assert (status, headers["ratelimit"]) == (429, '"resolvers";r=0;t=2')
    assert get(url)[1]["x-error-code"] == "GatedRepo"
    assert get(url)[1]["location"].startswith("http://")
    with pytest.raises(http.client.IncompleteRead):
        follow(url)  # dropped after 100 bytes
    status, headers, data = follow(url, {"Range": "bytes=100-"})
    assert (status, headers["content-range"]) == (206, f"bytes 107-{len(WEIGHTS) - 1}/{len(WEIGHTS)}")
    assert data == WEIGHTS[107:]
    assert follow(url, {"Range": "bytes=100-"})[::2] == (200, WEIGHTS)
    assert follow(url)[2] == b"y" * len(WEIGHTS)
    assert follow(url)[::2] == (200, WEIGHTS)
    assert hosts.faults_left() == 0
    assert hosts.requests("plain") == []  # the redirect to http was only handed out
    _, headers, _ = get(url)
    get(headers["location"])
    status, _, data = get(headers["location"])
    assert (status, data) == (618, b"jwt:expired")  # GitHub's answer to a used signed URL


def test_a_stall_holds_the_connection_until_released(hosts: Hosts) -> None:
    url = hosts.add_file("/depth_pro.pt", WEIGHTS)
    first, second = Stall(at=1000), Stall(before_headers=True)
    hosts.fault(url, first, second)
    started = time.monotonic()
    with pytest.raises((TimeoutError, socket.timeout, ssl.SSLError)):
        get(url, timeout=0.5)
    with pytest.raises((TimeoutError, socket.timeout, ssl.SSLError)):
        get(url, timeout=0.5)
    assert time.monotonic() - started < 5
    assert [r.sent for r in hosts.requests("files")] == [1000, 0]
    first.release.set()
    second.release.set()
    assert get(url)[::2] == (200, WEIGHTS)


def test_the_proxy_records_each_tunnel(hosts: Hosts) -> None:
    url = hosts.add_file("/depth_pro.pt", WEIGHTS)
    assert get(url, proxy=hosts.proxy)[::2] == (200, WEIGHTS)
    port = urllib.parse.urlsplit(url).port
    assert hosts.tunnels() == [f"127.0.0.1:{port}"]


def test_a_client_that_trusts_another_ca_refuses_the_hosts(hosts: Hosts) -> None:
    url = hosts.add_file("/depth_pro.pt", WEIGHTS)
    with pytest.raises(ssl.SSLCertVerificationError):
        get(url, ca=OTHER_CA)
    assert get(url)[::2] == (200, WEIGHTS)
