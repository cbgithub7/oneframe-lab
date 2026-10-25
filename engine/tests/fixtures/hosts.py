"""The hosts spec 003's tests download from, all on 127.0.0.1, over TLS from the committed test CA
(tls/README.md):

    hub     an imitation of Hugging Face: resolve, and the revision, tree and paths-info APIs
    files   a plain URL host, like Apple's server; a URL on it may redirect, as GitHub's assets do
    cdn     the redirect host: each URL it hands out works once, as signed URLs that expire do
    plain   the files host's files over plain HTTP, which a client must never reach
    proxy   a CONNECT proxy that records each tunnel

Every request is logged. A fault is queued against a source URL (a resolve URL, or a URL on the
files host) and used up by one request: at the source, or where the bytes are served, which for a
source that redirects is the cdn. So a test breaks exactly the request it means to, however the
client reaches it, and each attempt that starts again from the source meets the next fault.

The imitation follows what spec 003's research.md records of the real hub: a large file's resolve
answers 302 to a signed URL with X-Linked-Etag (its sha256), X-Linked-Size and X-Repo-Commit; a
small file's answers 307 to /api/resolve-cache/ on the same host; an error carries X-Error-Code;
a repository that does not exist answers 401 RepoNotFound, as a private one does; a 429 carries
Retry-After or RateLimit. A used or expired signed URL answers 403 from the hub's cdn and GitHub's
`618 jwt:expired` from the files host's, both statuses a client does not expect from a redirect.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.server
import json
import secrets
import select
import socket
import socketserver
import ssl
import sys
import threading
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NoReturn, cast

TLS = Path(__file__).parent / "tls"
CA = TLS / "ca.pem"
# A CA that signed nothing here: a client that trusts only it refuses every host.
OTHER_CA = TLS / "other-ca.pem"
# What git-lfs takes on the hub: weights by their suffix, anything else by its size.
LFS_SUFFIXES = (".pt", ".pth", ".bin", ".ckpt", ".safetensors", ".onnx", ".npz", ".gguf")
LFS_MIN_BYTES = 10 * 2**20
STALL_S = 60  # the longest a stall holds a connection; close() releases every stall at once
IDLE_S = 10  # a kept-alive connection with no request for this long is closed


def client_context(ca: Path = CA) -> ssl.SSLContext:
    """What a client that trusts `ca` uses: Python's defaults, strict checks included."""
    return ssl.create_default_context(cafile=str(ca))


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git_oid(data: bytes) -> str:
    """The id git gives a blob, which the hub's tree gives as `oid`."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def lfs_pointer(data: bytes) -> bytes:
    return (
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{sha256(data)}\nsize {len(data)}\n".encode()
    )


# -- faults: each is used up by one request -----------------------------------------------------


@dataclass
class Drop:
    """Close the connection, without a TLS goodbye, after `at` bytes of the body."""

    at: int
    on: str = "body"


@dataclass
class Stall:
    """Hold the connection, before the headers or after `at` bytes of the body, until `release`
    is set or the hosts close; then carry on as if nothing happened. `holding` is set while the
    hosts hold it, so a test can tell a client that gave up from a stall that ended."""

    at: int = 0
    before_headers: bool = False
    release: threading.Event = field(default_factory=threading.Event)
    holding: threading.Event = field(default_factory=threading.Event)
    on: str = "body"


@dataclass
class WrongOffset:
    """Answer a range request with a 206 that starts `by` bytes from where it was asked."""

    by: int
    on: str = "body"


@dataclass
class IgnoreRange:
    """Answer a range request with a 200 and the whole file; with `at`, dropped after that many
    bytes, as a proxy that ignores ranges on a flaky line would."""

    at: int | None = None
    on: str = "body"


@dataclass
class NoLength:
    """Answer with a 200 and the whole file, a range ignored, and no Content-Length, so the body
    ends when the connection closes: what GitHub's archive host is said to send."""

    on: str = "body"


@dataclass
class Replace:
    """Serve these bytes in place of the file's: another size, or the same size altered."""

    data: bytes
    on: str = "body"


@dataclass
class Status:
    """Answer with this status and these headers, and a short body."""

    code: int
    headers: dict[str, str] = field(default_factory=dict)
    on: str = "source"


@dataclass
class RedirectTo:
    """Redirect to this URL instead (an `http` one, say)."""

    url: str
    code: int = 302
    on: str = "source"


Fault = Drop | Stall | WrongOffset | IgnoreRange | NoLength | Replace | Status | RedirectTo


def error_code(status: int, code: str) -> Status:
    """The hub's error for `code`: GatedRepo, RepoNotFound, RevisionNotFound, EntryNotFound."""
    return Status(status, {"X-Error-Code": code, "X-Error-Message": code})


def retry_after(seconds: int) -> Status:
    return Status(429, {"Retry-After": str(seconds)})


def rate_limit(seconds: int) -> Status:
    """A 429 as the hub sends it (research.md: rate-limits.md), with the wait in RateLimit."""
    return Status(
        429,
        {
            "RateLimit": f'"resolvers";r=0;t={seconds}',
            "RateLimit-Policy": '"fixed window";"resolvers";q=3000;w=300',
        },
    )


# -- the hub's repositories -----------------------------------------------------------------------


@dataclass
class Repo:
    """A model repository: commits, each a full snapshot of its files, and `main`."""

    id: str  # as the revision API spells it: the canonical id
    gated: bool | str = False  # False, "auto" or "manual"
    commits: dict[str, dict[str, bytes]] = field(default_factory=dict)
    lfs: dict[str, set[str]] = field(default_factory=dict)
    main: str | None = None

    def commit(self, files: dict[str, bytes], lfs: Iterable[str] | None = None) -> str:
        """A new commit holding exactly `files`, which `main` then names. `lfs` names the files
        kept in git-lfs; by default, those LFS_SUFFIXES or LFS_MIN_BYTES pick."""
        seed = json.dumps([self.id, len(self.commits), sorted((p, sha256(d)) for p, d in files.items())])
        commit = hashlib.sha1(seed.encode()).hexdigest()
        self.commits[commit] = dict(files)
        if lfs is None:
            lfs = [p for p, d in files.items() if p.endswith(LFS_SUFFIXES) or len(d) >= LFS_MIN_BYTES]
        self.lfs[commit] = set(lfs)
        self.main = commit
        return commit

    def squash(self, commit: str) -> None:
        """The commit is gone, as `super_squash_history` leaves an old one."""
        del self.commits[commit]
        if self.main == commit:
            self.main = next(reversed(self.commits), None)

    def revision(self, rev: str) -> str | None:
        if rev == "main":
            return self.main
        return rev if rev in self.commits else None

    def entry(self, commit: str, path: str) -> dict[str, Any]:
        data = self.commits[commit][path]
        if path not in self.lfs[commit]:
            return {"type": "file", "oid": git_oid(data), "size": len(data), "path": path}
        pointer = lfs_pointer(data)
        return {
            "type": "file",
            "oid": git_oid(pointer),
            "size": len(data),
            "path": path,
            "lfs": {"oid": sha256(data), "size": len(data), "pointerSize": len(pointer)},
        }

    def folders(self, commit: str) -> set[str]:
        found: set[str] = set()
        for path in self.commits[commit]:
            parts = path.split("/")[:-1]
            found.update("/".join(parts[: i + 1]) for i in range(len(parts)))
        return found

    def folder_entry(self, path: str) -> dict[str, Any]:
        return {"type": "directory", "oid": hashlib.sha1(path.encode()).hexdigest(), "size": 0, "path": path}


# -- the log ------------------------------------------------------------------------------------


@dataclass
class Request:
    host: str  # hub, files, cdn, plain or proxy
    method: str
    path: str
    headers: dict[str, str]  # names in lower case
    status: int | None = None
    file: str | None = None  # the sha256 of the bytes this response served, if it served any
    sent: int = 0  # bytes of the body written


@dataclass
class _Token:
    key: tuple[str, str]  # the source it was handed out for
    data: bytes
    expired: tuple[int, str]  # what it answers once used: a status and a body
    used: bool = False


class Hosts:
    def __init__(self, page_size: int = 1000):
        """`page_size`: how many entries a page of the tree API holds before a `Link` to the next."""
        self.page_size = page_size
        self.log: list[Request] = []
        self.repos: dict[str, Repo] = {}  # by id in lower case: the hub ignores case
        self.files: dict[str, bytes] = {}  # by path on the files host (and the plain one)
        self.redirect: set[str] = set()  # files-host paths that redirect to the cdn
        self._tokens: dict[str, _Token] = {}
        self._origins: dict[str, tuple[str, str]] = {}  # resolve-cache path -> its source
        self._faults: dict[tuple[str, str, str], list[Fault]] = {}  # (host, path, stage) -> queue
        self._stalls: list[threading.Event] = []
        self._lock = threading.Lock()
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(TLS / "leaf.pem", TLS / "leaf.key")
        self._servers = {name: _Server(self, name, None if name == "plain" else tls) for name in _HOSTS}
        self._proxy = _Proxy(self)
        for server in [*self._servers.values(), self._proxy]:
            threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()

    # where things are

    def base(self, host: str) -> str:
        scheme = "http" if host in ("plain", "proxy") else "https"
        port = (self._proxy if host == "proxy" else self._servers[host]).server_address[1]
        return f"{scheme}://127.0.0.1:{port}"

    @property
    def hub(self) -> str:
        """What HF_ENDPOINT names."""
        return self.base("hub")

    @property
    def proxy(self) -> str:
        """What HTTPS_PROXY names."""
        return self.base("proxy")

    def repo(self, repo_id: str, gated: bool | str = False) -> Repo:
        found = self.repos.setdefault(repo_id.lower(), Repo(repo_id, gated))
        found.gated = gated
        return found

    def resolve_url(self, repo_id: str, revision: str, path: str) -> str:
        rev = urllib.parse.quote(revision, safe="")
        return f"{self.hub}/{repo_id}/resolve/{rev}/{urllib.parse.quote(path)}"

    def add_file(self, path: str, data: bytes, redirect: bool = False) -> str:
        """Serve `data` at `path` on the files host (and the plain one); its https URL. A file that
        redirects is served from a single-use URL on the cdn."""
        self.files[path] = data
        if redirect:
            self.redirect.add(path)
        else:
            self.redirect.discard(path)
        return self.base("files") + urllib.parse.quote(path)

    def fault(self, url: str, *faults: Fault) -> None:
        """Queue faults against a source URL, each used up by one request, in order."""
        key = self._key(url)
        with self._lock:
            for one in faults:
                self._faults.setdefault((*key, one.on), []).append(one)
                if isinstance(one, Stall):
                    self._stalls.append(one.release)

    def faults_left(self) -> int:
        with self._lock:
            return sum(len(queue) for queue in self._faults.values())

    # what happened

    def requests(self, host: str | None = None) -> list[Request]:
        with self._lock:
            return [r for r in self.log if host is None or r.host == host]

    def served(self, data: bytes | str) -> list[Request]:
        """The responses that began sending this file's bytes (a sha256, or the bytes)."""
        sha = data if isinstance(data, str) else sha256(data)
        return [r for r in self.requests() if r.file == sha and r.method == "GET" and r.status in (200, 206)]

    def tunnels(self) -> list[str]:
        return [r.path for r in self.requests("proxy") if r.method == "CONNECT"]

    def close(self) -> None:
        with self._lock:
            for release in self._stalls:
                release.set()
        for server in [*self._servers.values(), self._proxy]:
            server.shutdown()
            server.server_close()

    # used by the handlers

    def _key(self, url: str) -> tuple[str, str]:
        parts = urllib.parse.urlsplit(url)
        for name, server in self._servers.items():
            if parts.port == server.server_address[1]:
                return name, urllib.parse.unquote(parts.path)
        raise ValueError(f"{url} is not on these hosts")

    def _take(self, key: tuple[str, str], stage: str) -> Fault | None:
        with self._lock:
            queue = self._faults.get((*key, stage))
            return queue.pop(0) if queue else None

    def _mint(self, key: tuple[str, str], data: bytes, expired: tuple[int, str]) -> str:
        token = secrets.token_hex(16)
        with self._lock:
            self._tokens[token] = _Token(key, data, expired)
        return f"{self.base('cdn')}/{token}"

    def _use(self, token: str) -> tuple[_Token | None, bool]:
        """The token (None if it was never handed out), and whether it was still unused."""
        with self._lock:
            found = self._tokens.get(token)
            fresh = found is not None and not found.used
            if found is not None:
                found.used = True
            return found, fresh


_HOSTS = ("hub", "files", "cdn", "plain")


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, hosts: Hosts, name: str, tls: ssl.SSLContext | None):
        self.hosts = hosts
        self.name = name
        self.tls = tls
        super().__init__(("127.0.0.1", 0), _Handler)

    def handle_error(self, request: Any, client_address: Any) -> None:
        """A client that went away mid-answer (a stopped download) is expected; anything else is a
        bug in the hosts, printed as usual."""
        if not isinstance(sys.exception(), OSError):
            super().handle_error(request, client_address)

    def finish_request(self, request: Any, client_address: Any) -> None:
        """The TLS handshake happens here, in the connection's own thread, so a client that
        refuses the certificate, or never finishes the handshake, holds up no one else."""
        if self.tls is None:
            self.RequestHandlerClass(request, client_address, self)
            return
        request.settimeout(IDLE_S)
        try:
            secured = self.tls.wrap_socket(request, server_side=True)
        except OSError:  # ssl.SSLError is one: the client refused the certificate, or left
            return
        try:
            self.RequestHandlerClass(secured, client_address, self)
        finally:
            with contextlib.suppress(OSError):
                secured.close()


class _Dropped(Exception):
    """The response ended the connection on purpose."""


class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = IDLE_S

    def log_message(self, format: str, *args: Any) -> None:
        pass

    @property
    def hosts(self) -> Hosts:
        return cast(_Server, self.server).hosts

    @property
    def host(self) -> str:
        return cast(_Server, self.server).name

    def send_response(self, code: int, message: str | None = None) -> None:
        record = getattr(self, "record", None)  # None for a request line too bad to log
        if record is not None:
            record.status = code
        super().send_response(code, message)

    def do_GET(self) -> None:
        self._handle(body=True)

    def do_HEAD(self) -> None:
        self._handle(body=False)

    def do_POST(self) -> None:
        self._handle(body=True)

    def _handle(self, body: bool) -> None:
        self.record = Request(
            self.host, self.command, self.path, {k.lower(): v for k, v in self.headers.items()}
        )
        with self.hosts._lock:
            self.hosts.log.append(self.record)
        length = int(self.headers.get("Content-Length") or 0)
        self.payload = self.rfile.read(length) if length else b""
        self.body = body
        try:
            route = {"hub": self._hub, "files": self._files, "plain": self._files, "cdn": self._cdn}
            route[self.host]()
        except _Dropped:
            self.close_connection = True
        except OSError:  # the client went away mid-response, as a stopped download does
            self.close_connection = True

    # the hosts

    def _files(self) -> None:
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        key = (self.host, path)
        if self._source_fault(key):
            return
        data = self.hosts.files.get(path)
        if data is None:
            self._send(404, {}, b"Not Found")
        elif path in self.hosts.redirect and self.host == "files":
            location = self.hosts._mint(key, data, (618, "jwt:expired"))
            self._send(302, {"Location": location}, b"")
        else:
            self._serve(key, data)

    def _cdn(self) -> None:
        token = urllib.parse.urlsplit(self.path).path.strip("/")
        found, fresh = self.hosts._use(token)
        if found is None:
            self._send(404, {}, b"Not Found")
        elif not fresh:
            self._send(found.expired[0], {}, found.expired[1].encode())
        else:
            self._serve(found.key, found.data)

    def _hub(self) -> None:
        parts = urllib.parse.urlsplit(self.path)
        segments = parts.path.strip("/").split("/")
        query = urllib.parse.parse_qs(parts.query)
        if segments[:3] == ["api", "resolve-cache", "models"] and len(segments) >= 7:
            self._resolve_cache(segments[3:])
        elif segments[:2] == ["api", "models"] and len(segments) >= 6:
            repo_id = "/".join(segments[2:4])
            what, rev = segments[4], urllib.parse.unquote(segments[5])
            rest = "/".join(urllib.parse.unquote(s) for s in segments[6:])
            if what == "revision" and self.command != "POST":
                self._api_revision(repo_id, rev)
            elif what == "tree" and self.command != "POST":
                self._api_tree(repo_id, rev, rest, query)
            elif what == "paths-info" and self.command == "POST":
                self._api_paths_info(repo_id, rev)
            else:
                self._send(404, {}, b"Not Found")
        elif len(segments) >= 5 and segments[2] == "resolve" and self.command != "POST":
            repo_id = "/".join(segments[:2])
            rev = urllib.parse.unquote(segments[3])
            self._resolve(repo_id, rev, "/".join(urllib.parse.unquote(s) for s in segments[4:]))
        else:
            self._send(404, {}, b"Not Found")

    def _find(self, repo_id: str) -> Repo | None:
        found = self.hosts.repos.get(repo_id.lower())
        if found is None:
            self._error(401, "RepoNotFound", f"Repository not found: {repo_id}")
        return found

    def _commit(self, repo: Repo, rev: str) -> str | None:
        commit = repo.revision(rev)
        if commit is None:
            self._error(404, "RevisionNotFound", f"Invalid rev id: {rev}")
        return commit

    def _resolve(self, repo_id: str, rev: str, path: str) -> None:
        key = ("hub", urllib.parse.unquote(urllib.parse.urlsplit(self.path).path))
        if self._source_fault(key):
            return
        repo = self._find(repo_id)
        if repo is None:
            return
        if repo.gated:  # no token is ever sent
            self._error(401, "GatedRepo", f"Access to model {repo.id} is restricted.")
            return
        commit = self._commit(repo, rev)
        if commit is None:
            return
        if path not in repo.commits[commit]:
            self._error(404, "EntryNotFound", f"{path} does not exist on {rev}")
            return
        data = repo.commits[commit][path]
        headers = {"X-Repo-Commit": commit}
        if path in repo.lfs[commit]:
            headers["X-Linked-Etag"] = f'"{sha256(data)}"'
            headers["X-Linked-Size"] = str(len(data))
            headers["ETag"] = f'"{git_oid(lfs_pointer(data))}"'
            headers["Location"] = self.hosts._mint(key, data, (403, "Request has expired"))
            self._send(302, headers, b"")
            return
        cached = f"/api/resolve-cache/models/{repo.id}/{commit}/{urllib.parse.quote(path)}"
        with self.hosts._lock:
            self.hosts._origins[urllib.parse.unquote(cached)] = key
        headers["ETag"] = f'"{git_oid(data)}"'
        headers["Location"] = cached
        self._send(307, headers, b"")

    def _resolve_cache(self, segments: list[str]) -> None:
        repo_id, commit = "/".join(segments[:2]), segments[2]
        path = "/".join(urllib.parse.unquote(s) for s in segments[3:])
        repo = self.hosts.repos.get(repo_id.lower())
        if repo is None or commit not in repo.commits or path not in repo.commits[commit]:
            self._send(404, {}, b"Not Found")
            return
        here = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        with self.hosts._lock:
            key = self.hosts._origins.get(here, ("hub", here))
        self._serve(key, repo.commits[commit][path])

    def _api_revision(self, repo_id: str, rev: str) -> None:
        key = ("hub", urllib.parse.unquote(urllib.parse.urlsplit(self.path).path))
        if self._source_fault(key):
            return
        repo = self._find(repo_id)
        commit = repo and self._commit(repo, rev)
        if repo is None or commit is None:
            return
        self._json(
            {
                "_id": hashlib.sha1(repo.id.encode()).hexdigest()[:24],
                "id": repo.id,
                "modelId": repo.id,
                "sha": commit,
                "gated": repo.gated,
                "private": False,
                "disabled": False,
                "lastModified": "2026-01-01T00:00:00.000Z",
                "siblings": [{"rfilename": p} for p in sorted(repo.commits[commit])],
            },
            {"X-Repo-Commit": commit},
        )

    def _api_tree(self, repo_id: str, rev: str, under: str, query: dict[str, list[str]]) -> None:
        key = ("hub", urllib.parse.unquote(urllib.parse.urlsplit(self.path).path))
        if self._source_fault(key):
            return
        repo = self._find(repo_id)
        commit = repo and self._commit(repo, rev)
        if repo is None or commit is None:
            return
        recursive = query.get("recursive", ["false"])[0].lower() in ("true", "1")
        prefix = f"{under}/" if under else ""
        depth = prefix.count("/")

        def wanted(path: str) -> bool:
            return path.startswith(prefix) and (recursive or path.count("/") == depth)

        rows = [repo.folder_entry(p) for p in sorted(repo.folders(commit)) if wanted(p)]
        rows += [repo.entry(commit, p) for p in sorted(repo.commits[commit]) if wanted(p)]
        start = int(query.get("cursor", ["0"])[0])
        page = rows[start : start + self.hosts.page_size]
        headers: dict[str, str] = {}
        if start + self.hosts.page_size < len(rows):
            following = {**{k: v[0] for k, v in query.items()}, "cursor": str(start + self.hosts.page_size)}
            path = urllib.parse.urlsplit(self.path).path
            headers["Link"] = f'<{self.hosts.hub}{path}?{urllib.parse.urlencode(following)}>; rel="next"'
        self._json(page, headers)

    def _api_paths_info(self, repo_id: str, rev: str) -> None:
        key = ("hub", urllib.parse.unquote(urllib.parse.urlsplit(self.path).path))
        if self._source_fault(key):
            return
        repo = self._find(repo_id)
        commit = repo and self._commit(repo, rev)
        if repo is None or commit is None:
            return
        if self.headers.get("Content-Type", "").startswith("application/json"):
            asked = list(json.loads(self.payload or b"{}").get("paths", []))
        else:
            asked = urllib.parse.parse_qs(self.payload.decode()).get("paths", [])
        folders = repo.folders(commit)
        rows: list[dict[str, Any]] = []
        for path in asked:
            if path in repo.commits[commit]:
                rows.append(repo.entry(commit, path))
            elif path in folders:
                rows.append(repo.folder_entry(path))
        self._json(rows, {})

    # answers

    def _source_fault(self, key: tuple[str, str]) -> bool:
        """Apply the next fault queued at this source, if any; whether it answered."""
        fault = self.hosts._take(key, "source")
        return fault is not None and self._answer(fault)

    def _answer(self, fault: Fault) -> bool:
        """Answer with a fault that replaces the whole response; False for one that breaks a body."""
        if isinstance(fault, Status):
            self._send(fault.code, fault.headers, b"fault")
        elif isinstance(fault, RedirectTo):
            self._send(fault.code, {"Location": fault.url}, b"")
        elif isinstance(fault, Stall) and fault.before_headers:
            self._stall(fault)
            raise _Dropped()
        else:
            return False
        return True

    def _serve(self, key: tuple[str, str], data: bytes) -> None:
        fault = self.hosts._take(key, "body")
        if fault is not None and self._answer(fault):
            return
        if isinstance(fault, Replace):
            data = fault.data
        size = len(data)
        start = _range_start(self.headers.get("Range"))
        headers = {"Accept-Ranges": "bytes", "Content-Type": "application/octet-stream"}
        if start is None or isinstance(fault, IgnoreRange | NoLength):
            status, start = 200, 0
        elif start >= size:
            self._send(416, {"Content-Range": f"bytes */{size}"}, b"")
            return
        else:
            status = 206
            if isinstance(fault, WrongOffset):
                start = min(max(start + fault.by, 0), size - 1)
            headers["Content-Range"] = f"bytes {start}-{size - 1}/{size}"
        if isinstance(fault, NoLength):
            headers["Connection"] = "close"
            self.close_connection = True
        else:
            headers["Content-Length"] = str(size - start)
        self.record.file = sha256(data)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if not self.body:
            return
        rest = data[start:]
        if isinstance(fault, Drop) or (isinstance(fault, IgnoreRange) and fault.at is not None):
            self._write(rest[: fault.at])
            self._drop()
        if isinstance(fault, Stall):
            self._write(rest[: fault.at])
            self._stall(fault)
            rest = rest[fault.at :]
        self._write(rest)

    def _write(self, data: bytes) -> None:
        self.wfile.write(data)
        self.wfile.flush()
        self.record.sent += len(data)

    def _drop(self) -> NoReturn:
        """Shut the connection down at the socket, with no TLS close_notify: a dropped connection."""
        with contextlib.suppress(OSError):
            self.connection.shutdown(socket.SHUT_RDWR)
        raise _Dropped()

    def _stall(self, fault: Stall) -> None:
        fault.holding.set()
        try:
            fault.release.wait(STALL_S)
        finally:
            fault.holding.clear()

    def _send(self, status: int, headers: dict[str, str], body: bytes) -> None:
        reason = "jwt:expired" if status == 618 else None
        self.send_response(status, reason)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.body and body:
            self.wfile.write(body)

    def _json(self, value: Any, headers: dict[str, str]) -> None:
        self._send(200, {"Content-Type": "application/json", **headers}, json.dumps(value).encode())

    def _error(self, status: int, code: str, message: str) -> None:
        headers = {"X-Error-Code": code, "X-Error-Message": message, "Content-Type": "application/json"}
        self._send(status, headers, json.dumps({"error": message}).encode())


def _range_start(header: str | None) -> int | None:
    """The first byte of `Range: bytes=N-`, the only form a resuming client sends."""
    if not header or not header.startswith("bytes="):
        return None
    first = header[len("bytes=") :].split(",")[0].split("-")[0].strip()
    return int(first) if first.isdigit() else None


# -- the proxy ----------------------------------------------------------------------------------


class _Proxy(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True
    handle_error = _Server.handle_error

    def __init__(self, hosts: Hosts):
        self.hosts = hosts
        super().__init__(("127.0.0.1", 0), _Tunnel)


class _Tunnel(socketserver.StreamRequestHandler):
    """CONNECT host:port, then bytes both ways until either side closes."""

    def handle(self) -> None:
        line = self.rfile.readline(65536).decode("latin-1").strip()
        headers: dict[str, str] = {}
        while True:
            header = self.rfile.readline(65536).decode("latin-1").strip()
            if not header:
                break
            name, _, value = header.partition(":")
            headers[name.strip().lower()] = value.strip()
        method, _, rest = line.partition(" ")
        target = rest.rpartition(" ")[0]
        hosts = cast(_Proxy, self.server).hosts
        with hosts._lock:
            hosts.log.append(Request("proxy", method, target, headers))
        if method != "CONNECT":
            self.wfile.write(b"HTTP/1.1 405 Method Not Allowed\r\nContent-Length: 0\r\n\r\n")
            return
        host, _, port = target.rpartition(":")
        try:
            upstream = socket.create_connection((host, int(port)), timeout=IDLE_S)
        except OSError:
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
            return
        self.wfile.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        self.wfile.flush()
        with upstream:
            ends = [self.connection, upstream]
            while True:
                ready, _, _ = select.select(ends, [], [], STALL_S)
                if not ready:
                    return
                for end in ready:
                    try:
                        chunk = end.recv(65536)
                    except OSError:
                        return
                    if not chunk:
                        return
                    other = upstream if end is self.connection else self.connection
                    try:
                        other.sendall(chunk)
                    except OSError:
                        return
