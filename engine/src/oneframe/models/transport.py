"""Fetching one file to its partial, on `http.client`, healing what goes wrong on the way (spec 003,
requirement 3; research.md's "Second round").

- **Every attempt starts from the source URL** and follows its redirects again, so a signed URL
  that expired, or was used up, is never asked twice. Redirects are https only, refused before
  connecting, and no header but Range follows one to another origin.
- **Retries.** A dropped, reset or stalled connection, a 5xx, a 408, a 429 and an unexpected status
  from a redirect target are retried, after a wait that grows, or the one the host asks for
  (Retry-After, RateLimit). The download ends as `stalled` only after ATTEMPTS attempts in a row
  that fetched no new byte, saying how many bytes it kept: a slow line that keeps delivering never
  runs out of attempts, and there is no limit on the total time.
- **Stop and stalls are seen by the reader.** Every read waits in slices of SLICE_S, looking at
  Stop between them, so a Stop ends a download stalled before its headers or in its body within a
  moment, and a stall is a window of STALL_S with no byte at all; a trickle is not a stall. Shutting
  the socket down from another thread, which research.md found unblocks a read on Linux, does not
  on Windows (CI, 2026-10-11), and closing the response unblocks nothing anywhere. The TCP connect
  and a proxy's CONNECT are polled the same way; only a TLS handshake waits whole, up to STALL_S.
- **Integrity.** A response whose total size differs from the pin fails before its body
  (`source_changed`), as do the hub's X-Linked-Size and X-Linked-Etag on its redirect. Only a 206
  that starts exactly at the partial's end is appended; a 200 starts the file again, and a 206 that
  starts anywhere else is dropped and the next attempt starts again from nothing. The sha256 is
  kept as the bytes are written, after reading back what a partial already held (`checking`), so the
  whole file is hashed before it is placed and a mismatch keeps nothing.
- **Space.** Free space is checked before the first byte; a full disk ends the download as
  `no_space` and keeps the partial.
- **TLS and proxies.** Certificates are checked against the operating system's trust store
  through truststore, imported only when a download starts; proxies come from the system
  (`urllib.request.getproxies`), through a CONNECT tunnel.
"""

from __future__ import annotations

import base64
import contextlib
import email.utils
import errno
import hashlib
import http.client
import io
import random
import re
import select
import socket
import ssl
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from oneframe import __version__
from oneframe.errors import Failure, Stopped

CHUNK = 1 << 20
SLICE_S = 0.1  # how long one read waits before it looks at Stop again
USER_AGENT = f"oneframe-lab/{__version__}"
REDIRECTS = (301, 302, 303, 307, 308)
# Hugging Face's X-Error-Code decides these, never the status alone (research.md).
HUB_ERRORS = {
    "GatedRepo": "gated",
    "RepoNotFound": "not_found",
    "EntryNotFound": "not_found",
    "RevisionNotFound": "revision_gone",
}
_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+|\*)$")
_UNSATISFIED = re.compile(r"^bytes \*/(\d+)$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DISK_FULL = {112, 39}  # Windows: ERROR_DISK_FULL, ERROR_HANDLE_DISK_FULL


@dataclass(frozen=True)
class Policy:
    """The plain values the transport works by. Each is set here with its reason, tuned to no
    machine or connection; tests pass smaller ones."""

    # No byte for this long ends an attempt: what uv and Ollama use. A trickle never trips it.
    stall_s: float = 30.0
    # Attempts in a row that fetch nothing before the download ends: Ollama's 6, one more than the
    # hub client's 5, since an attempt that fetches anything starts the count again.
    attempts: int = 6
    # The wait before a retry doubles from this, up to the next, with jitter so clients spread out.
    first_wait_s: float = 1.0
    longest_wait_s: float = 60.0
    # A host that asks for a longer wait than this (Retry-After, RateLimit) ends the download as
    # rate_limited at once: a download should not sit silent for longer.
    longest_asked_s: float = 600.0
    redirects: int = 10


class DownloadFailed(Failure):
    """A file could not be downloaded; `kept` is how many bytes of it the partial still holds."""

    kind = "download"

    def __init__(self, message: str, reason: str | None = None, kept: int = 0, next: str | None = None):
        super().__init__(message, reason=reason, next=next)
        self.kept = kept


class _Retry(Exception):
    """This attempt failed in a way the next one may not: wait (at least `wait_s`) and try again."""

    def __init__(self, why: str, wait_s: float = 0.0, limited: bool = False):
        super().__init__(why)
        self.wait_s = wait_s
        self.limited = limited  # the host said to slow down (429)


def system_context() -> ssl.SSLContext:
    """TLS checked against the operating system's trust store. truststore loads only now, when a
    download starts, never with the engine."""
    import truststore

    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def system_proxy(host: str) -> str | None:
    """The proxy the system names for https to `host`, or None (no proxy, or `host` bypasses it)."""
    proxy = urllib.request.getproxies().get("https")
    if not proxy or urllib.request.proxy_bypass(host):
        return None
    return proxy


def _origin(url: str) -> tuple[str, str, int]:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), parts.port or 443


def _wait_asked(headers: http.client.HTTPMessage) -> float:
    """The wait a host asks for, in seconds: Retry-After (seconds or a date), or RateLimit's `t`."""
    found = 0.0
    value = headers.get("Retry-After")
    if value:
        if value.strip().isdigit():
            found = float(value.strip())
        else:
            with contextlib.suppress(TypeError, ValueError):
                when = email.utils.parsedate_to_datetime(value)
                found = max(when.timestamp() - time.time(), 0.0)
    limit = headers.get("RateLimit")
    if limit:
        match = re.search(r"(?:^|;)\s*t=(\d+)", limit)
        if match:
            found = max(found, float(match.group(1)))
    return found


def _write(handle: BinaryIO, data: memoryview) -> int:
    """One write to the partial; the seam a test replaces to fill the disk."""
    return handle.write(data) or 0


def _disk_full(exc: OSError) -> bool:
    return exc.errno == errno.ENOSPC or getattr(exc, "winerror", None) in _DISK_FULL


class _Reader(io.RawIOBase):
    """A response's bytes, read in slices so Stop is seen between them and a stall is a window
    with no byte at all, however the system ends a blocked read."""

    def __init__(self, sock: socket.socket, stop: threading.Event, stall_s: float):
        self._sock = sock
        self._stop = stop
        self._stall_s = stall_s

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        return _recv_into(self._sock, buffer, self._stop, self._stall_s)


def _recv_into(sock: socket.socket, buffer: Any, stop: threading.Event, stall_s: float) -> int:
    """One read, waiting at most `stall_s` for its first byte, in slices; Stopped when Stop is set.
    A TLS read that times out leaves its connection as it was, so the next slice reads on."""
    quiet_since = time.monotonic()
    while True:
        if stop.is_set():
            raise Stopped()
        try:
            return sock.recv_into(buffer)
        except TimeoutError:
            if time.monotonic() - quiet_since >= stall_s:
                raise TimeoutError(f"no byte arrived for {stall_s:g} s") from None


class _Socket:
    """What http.client asks of a connection's socket (sendall, makefile, close), over one whose
    reads go in slices (_Reader)."""

    def __init__(self, sock: socket.socket, stop: threading.Event, stall_s: float):
        self._sock = sock
        self._stop = stop
        self._stall_s = stall_s
        sock.settimeout(SLICE_S)

    def sendall(self, data: Any) -> None:
        # A request is a few hundred bytes, which never waits on the host; sent whole.
        self._sock.settimeout(self._stall_s)
        try:
            self._sock.sendall(data)
        finally:
            self._sock.settimeout(SLICE_S)

    def makefile(self, _mode: str = "rb", *_args: Any, **_kw: Any) -> io.BufferedReader:
        return io.BufferedReader(_Reader(self._sock, self._stop, self._stall_s), CHUNK)

    def close(self) -> None:
        self._sock.close()


class Transfer:
    """One file, from its source to `partial`, resuming what the partial holds.

    `size` and `sha256` are the pin; while pinning, both are None and the file is whatever the
    source gives. `run` returns the sha256 and size of the whole file, which the partial then holds,
    or raises DownloadFailed, or Stopped. A file whose sha256 does not match the pin is deleted."""

    def __init__(
        self,
        source: str,
        partial: Path,
        size: int | None,
        sha256: str | None,
        stop: threading.Event,
        phase: Callable[[str, dict[str, Any]], None] | None = None,
        progress: Callable[[int, int | None], None] | None = None,
        policy: Policy | None = None,
        context: Callable[[], ssl.SSLContext] = system_context,
        proxy: Callable[[str], str | None] = system_proxy,
        free_bytes: Callable[[], int] | None = None,
    ):
        self.source = source
        self.partial = partial
        self.size = size
        self.sha256 = sha256
        self.stop = stop
        self._phase = phase or (lambda _name, _detail: None)
        self._progress = progress or (lambda _have, _total: None)
        self.policy = policy or Policy()
        self._context_factory = context
        self._context: ssl.SSLContext | None = None
        self._proxy = proxy
        self._free = free_bytes
        self.have = 0
        self._digest = hashlib.sha256()
        self.total: int | None = size

    # the whole download

    def run(self) -> tuple[str, int]:
        self.partial.parent.mkdir(parents=True, exist_ok=True)
        self._resume()
        self._check_space()
        misses = 0
        limited = False
        while not self._complete():
            before = self.have
            try:
                self._attempt()
                continue
            except _Retry as retry:
                if self.stop.is_set():
                    raise Stopped() from None
                misses = 0 if self.have > before else misses + 1
                limited = retry.limited
                if retry.wait_s > self.policy.longest_asked_s:
                    raise DownloadFailed(
                        f"{self._where()} asked to wait {retry.wait_s:.0f} s before trying again; "
                        f"{self.have} bytes were kept.",
                        "rate_limited",
                        self.have,
                        "Download again later; it resumes where it stopped.",
                    ) from None
                if misses >= self.policy.attempts:
                    raise DownloadFailed(
                        f"{self.policy.attempts} attempts in a row fetched nothing from {self._where()} "
                        f"(the last: {retry}); {self.have} bytes were kept.",
                        "rate_limited" if limited else "stalled",
                        self.have,
                        "Check the connection, then download again; it resumes where it stopped.",
                    ) from None
                wait = max(retry.wait_s, self._backoff(misses))
                self._phase("retrying", {"attempt": misses + 1, "wait_s": round(wait, 2), "why": str(retry)})
                if self.stop.wait(wait):
                    raise Stopped() from None
        digest = self._digest.hexdigest()
        if self.sha256 is not None and digest != self.sha256:
            self.partial.unlink(missing_ok=True)
            raise DownloadFailed(
                f"{self._where()} sent {self.have} bytes whose sha256 is {digest}, not the pinned "
                f"{self.sha256}; nothing was kept.",
                "hash_mismatch",
                0,
                "Download again; if it fails the same way, the source changed and the node needs a new pin.",
            )
        return digest, self.have

    def _where(self) -> str:
        parts = urllib.parse.urlsplit(self.source)
        return f"{parts.hostname}{parts.path}"

    def _backoff(self, misses: int) -> float:
        wait = min(self.policy.first_wait_s * 2 ** max(misses - 1, 0), self.policy.longest_wait_s)
        return wait * random.uniform(0.5, 1.0)

    def _complete(self) -> bool:
        return self.total is not None and self.have >= self.total

    def _resume(self) -> None:
        """Read back what the partial already holds, so the sha256 covers it (`checking`)."""
        try:
            held = self.partial.stat().st_size
        except FileNotFoundError:
            return
        if self.size is not None and held > self.size:
            self._restart()
            return
        if held:
            self._phase("checking", {"bytes": held})
            digest = hashlib.sha256()
            with self.partial.open("rb") as handle:
                while chunk := handle.read(CHUNK):
                    if self.stop.is_set():
                        raise Stopped()
                    digest.update(chunk)
            self._digest = digest
            self.have = held
            self._progress(self.have, self.total)

    def _restart(self) -> None:
        self.partial.write_bytes(b"")
        self.have = 0
        self._digest = hashlib.sha256()

    def _check_space(self) -> None:
        if self._free is None or self.total is None:
            return
        need = self.total - self.have
        free = self._free()
        if free < need:
            raise DownloadFailed(
                f"The store's drive has {free} bytes free and {self._where()} needs {need} more; "
                f"{self.have} bytes were kept.",
                "no_space",
                self.have,
                "Free some space on the drive, then download again.",
            )

    # one attempt: from the source, through its redirects, to the bytes

    def _attempt(self) -> None:
        url = self.source
        source_origin = _origin(url)
        for hop in range(self.policy.redirects + 1):
            parts = urllib.parse.urlsplit(url)
            if parts.scheme != "https" or not parts.hostname:
                raise DownloadFailed(
                    f"{self._where()} redirected to {url}, which is not https, and a download never "
                    f"leaves https; {self.have} bytes were kept.",
                    "tls",
                    self.have,
                )
            headers: dict[str, str] = {}
            if _origin(url) == source_origin:
                headers["User-Agent"] = USER_AGENT
            if self.have:
                headers["Range"] = f"bytes={self.have}-"
            conn: http.client.HTTPSConnection | None = None
            try:
                conn = self._connect(parts.hostname, parts.port or 443)
                path = parts.path or "/"
                conn.request("GET", f"{path}?{parts.query}" if parts.query else path, headers=headers)
                response = conn.getresponse()
                status = response.status
                if status in REDIRECTS and response.getheader("Location"):
                    self._check_announced(response.headers)
                    url = urllib.parse.urljoin(url, response.getheader("Location", ""))
                    continue
                if status in (200, 206, 416):
                    self._body(response, status)
                    return
                self._refused(response, from_source=hop == 0)
            except (OSError, http.client.HTTPException) as exc:
                if self.stop.is_set():
                    raise Stopped() from None
                if isinstance(exc, ssl.SSLCertVerificationError):
                    raise DownloadFailed(
                        f"The certificate of {parts.hostname} was refused ({exc.verify_message}); "
                        f"{self.have} bytes were kept.",
                        "tls",
                        self.have,
                        "A proxy or antivirus that inspects https needs its certificate trusted.",
                    ) from exc
                raise _Retry(f"{type(exc).__name__}: {exc}") from exc
            finally:
                if conn is not None:
                    conn.close()
        raise _Retry(f"more than {self.policy.redirects} redirects")

    def _connect(self, host: str, port: int) -> http.client.HTTPSConnection:
        """A TLS connection to host:port, through the system's proxy if it names one, whose reads
        look at Stop between slices."""
        proxy = self._proxy(host)
        tunnel = urllib.parse.urlsplit(proxy if "://" in proxy else f"http://{proxy}") if proxy else None
        sock = self._tcp(tunnel.hostname or "", tunnel.port or 80) if tunnel else self._tcp(host, port)
        try:
            if tunnel is not None:
                self._tunnel(sock, host, port, tunnel)
            if self._context is None:
                self._context = self._context_factory()
            sock.settimeout(self.policy.stall_s)  # a handshake waits whole (truststore checks after it)
            secured = self._context.wrap_socket(sock, server_hostname=host)
        except BaseException:
            sock.close()
            raise
        conn = http.client.HTTPSConnection(host, port, timeout=self.policy.stall_s, context=self._context)
        conn.sock = _Socket(secured, self.stop, self.policy.stall_s)  # type: ignore[assignment]
        return conn

    def _tcp(self, host: str, port: int) -> socket.socket:
        """A TCP connection, made without blocking so a Stop during it is seen within a moment."""
        deadline = time.monotonic() + self.policy.stall_s
        last: OSError | None = None
        for family, kind, proto, _name, address in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
            sock = socket.socket(family, kind, proto)
            try:
                sock.setblocking(False)
                sock.connect_ex(address)
                while True:
                    if self.stop.is_set():
                        raise Stopped()
                    if time.monotonic() > deadline:
                        raise TimeoutError(f"connecting to {host}:{port} timed out")
                    # Windows reports a failed connect as an exception, not as writable.
                    _r, writable, failed = select.select([], [sock], [sock], 0.05)
                    if writable or failed:
                        break
                error = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if error:
                    raise OSError(error, f"connecting to {host}:{port}: {errno.errorcode.get(error, error)}")
                sock.setblocking(True)
                sock.settimeout(SLICE_S)
                return sock
            except OSError as exc:
                sock.close()
                last = exc
            except BaseException:
                sock.close()
                raise
        raise last or OSError(f"{host} has no address")

    def _tunnel(self, sock: socket.socket, host: str, port: int, proxy: urllib.parse.SplitResult) -> None:
        lines = [f"CONNECT {host}:{port} HTTP/1.1", f"Host: {host}:{port}"]
        if proxy.username is not None:
            secret = f"{urllib.parse.unquote(proxy.username)}:{urllib.parse.unquote(proxy.password or '')}"
            lines.append(f"Proxy-Authorization: Basic {base64.b64encode(secret.encode()).decode()}")
        sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("latin-1"))
        reply = b""
        sock.settimeout(SLICE_S)
        buffer = bytearray(4096)
        while b"\r\n\r\n" not in reply:
            got = _recv_into(sock, buffer, self.stop, self.policy.stall_s)
            chunk = bytes(buffer[:got])
            if not chunk:
                raise ConnectionError("the proxy closed the connection")
            reply += chunk
            if len(reply) > 65536:
                raise ConnectionError("the proxy's answer is too long")
        status = reply.split(b"\r\n", 1)[0].decode("latin-1")
        if len(status.split()) < 2 or status.split()[1] != "200":
            raise ConnectionError(f"the proxy refused the tunnel: {status}")

    # what came back

    def _check_announced(self, headers: http.client.HTTPMessage) -> None:
        """The hub names the file's size and sha256 on its redirect: a change fails before the body."""
        size = headers.get("X-Linked-Size")
        if self.size is not None and size and size.strip().isdigit() and int(size) != self.size:
            self._changed(int(size))
        etag = (headers.get("X-Linked-Etag") or "").strip().removeprefix("W/").strip('"')
        if self.sha256 is not None and _SHA256.match(etag) and etag != self.sha256:
            raise DownloadFailed(
                f"{self._where()} now names a file whose sha256 is {etag}, not the pinned {self.sha256}; "
                f"{self.have} bytes were kept.",
                "source_changed",
                self.have,
                "The node needs a new pin (npm run models:pin).",
            )

    def _changed(self, total: int) -> None:
        raise DownloadFailed(
            f"{self._where()} now gives {total} bytes, not the pinned {self.size}, so it changed since it "
            f"was pinned; {self.have} bytes were kept.",
            "source_changed",
            self.have,
            "The node needs a new pin (npm run models:pin).",
        )

    def _refused(self, response: http.client.HTTPResponse, from_source: bool) -> None:
        status = response.status
        code = response.getheader("X-Error-Code")
        if code in HUB_ERRORS:
            reason = HUB_ERRORS[code]
            nexts = {
                "gated": "Accept its licence on its page; gated files are not downloaded yet.",
                "not_found": "The node's pin names something its source does not have.",
                "revision_gone": "The node needs a new pin (npm run models:pin).",
            }
            raise DownloadFailed(
                f"{self._where()} answered {status} {code}; {self.have} bytes were kept.",
                reason,
                self.have,
                nexts[reason],
            )
        with contextlib.suppress(OSError, http.client.HTTPException):
            response.read(65536)  # a short body, read so the connection closes cleanly
        if status == 429:
            raise _Retry(f"{status}: asked to slow down", _wait_asked(response.headers), limited=True)
        if status == 408 or status >= 500 or not from_source:
            # A redirect target that answers oddly (an expired signed URL: 403, GitHub's 618) is
            # asked again from the source.
            raise _Retry(f"{status} {response.reason}", _wait_asked(response.headers))
        if status in (404, 410):
            raise DownloadFailed(
                f"{self._where()} answered {status}; {self.have} bytes were kept.", "not_found", self.have
            )
        raise DownloadFailed(f"{self._where()} answered {status} {response.reason}.", None, self.have)

    def _body(self, response: http.client.HTTPResponse, status: int) -> None:
        if status == 416:
            match = _UNSATISFIED.match(response.getheader("Content-Range", "").strip())
            total = int(match.group(1)) if match else None
            if total is not None and self.size is not None and total != self.size:
                self._changed(total)
            if total is not None and total == self.have:
                self.total = total  # the partial was already whole: now hash it
                return
            self._restart()
            raise _Retry(f"416 for a partial of {self.have} bytes; starting again")
        if status == 206:
            match = _RANGE.match(response.getheader("Content-Range", "").strip())
            if match is None:
                self._restart()
                raise _Retry("a 206 without a Content-Range; starting again")
            start, total = int(match.group(1)), match.group(3)
            if total != "*" and self.size is not None and int(total) != self.size:
                self._changed(int(total))
            if start != self.have:
                self._restart()
                raise _Retry(f"a 206 from byte {start}, not {self.have + 0}; starting again")
            if total != "*":
                self.total = int(total)
        else:
            length = response.getheader("Content-Length")
            total_200 = int(length) if length and length.strip().isdigit() else None
            if total_200 is not None and self.size is not None and total_200 != self.size:
                self._changed(total_200)
            if self.have:
                self._restart()  # a 200 to a range request is the whole file again
            if total_200 is not None:
                self.total = total_200
        self._check_space()
        self._phase("downloading", {"from": self.have, "total": self.total})
        self._copy(response)

    def _copy(self, response: http.client.HTTPResponse) -> None:
        with self.partial.open("ab", buffering=0) as handle:
            while True:
                try:
                    chunk = response.read1(CHUNK)
                except (OSError, http.client.HTTPException) as exc:
                    if self.stop.is_set():
                        raise Stopped() from None
                    raise _Retry(f"the connection broke after {self.have} bytes: {exc}") from exc
                if not chunk:
                    break
                view = memoryview(chunk)
                while view:
                    try:
                        wrote = _write(handle, view)
                    except OSError as exc:
                        if not _disk_full(exc):
                            raise
                        raise DownloadFailed(
                            f"The store's drive is full after {self.have} bytes of {self._where()}; they "
                            "were kept.",
                            "no_space",
                            self.have,
                            "Free some space on the drive, then download again; it resumes where it stopped.",
                        ) from exc
                    self._digest.update(view[:wrote])
                    self.have += wrote
                    view = view[wrote:]
                self._progress(self.have, self.total)
                if self.stop.is_set():
                    raise Stopped()
        if self.stop.is_set():
            raise Stopped()
        if self.total is None:
            self.total = self.have  # no length given: the file ends where the stream did
        elif self.have < self.total:
            raise _Retry(f"the connection closed after {self.have} of {self.total} bytes")


def fetch(
    source: str, partial: Path, size: int | None, sha256: str | None, stop: threading.Event, **kw: Any
) -> tuple[str, int]:
    """Download `source` into `partial` (see Transfer); the file's sha256 and size."""
    return Transfer(source, partial, size, sha256, stop, **kw).run()
