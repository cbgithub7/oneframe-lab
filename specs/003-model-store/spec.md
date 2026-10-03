# 003: Model store (core)

Status: draft (rewritten 2026-10-03 to its core, in the one-document format, after the second
review; the owner's decisions of 2026-10-02 are below. The evidence is in
[research.md](research.md).)
Owner approval: (date, once approved)

Built after [spec 006](../006-foundations/spec.md), whose layout module, failure model, locks and
journal it uses.

## Problem

A model node reads weights, configs and tokenizers that are not in its folder or its runtime.
Those files are hosted on Hugging Face, GitHub releases or vendors' servers, and run from hundreds
of megabytes to tens of gigabytes. A run has no network, so every file must be on disk first. Today
nothing declares these files, and nothing fetches, checks, places or removes them. This core is
what the first real models (Depth Pro and MoGe-2) need. Import, a movable store, a queue and the
page's part follow in a later part of this spec.

## Requirements

1. **Files are declared in the manifest**, each with:
    - a `name`, which the node's code uses;
    - a source: a Hugging Face repository at a full commit hash, or an `https` URL;
    - its path, size and sha256. Size and sha256 are the file's identity; the source is only where
      to fetch it;
    - its checkpoints, if it belongs to some. The node names its checkpoint param once
      (`files_by`, a choice param), which must equal the memory model's `weights_by` when both
      exist. `precision` never keys files;
    - its format, read from its content (safetensors, pickle including torch's zip form, or other);
    - whether its source is gated, with the page where its licence is accepted.

   **The manifest check refuses**, naming every problem:
    - a branch for a commit;
    - a bad size or sha256;
    - two commits of one repository in a node;
    - a URL that is not `https` (plain `http` only on loopback);
    - an absolute or `..` path, or one unsafe on Windows: a reserved name, a trailing dot or
      space, `:`, or paths that differ only in case;
    - files in an engine node.

   Across manifests, two nodes conflict when they pin different bytes at one source and path, or
   share a runtime and pin different commits of one repository. The registry reports the conflict
   for both nodes.
2. **One copy of each file,** under `<data>/models/`, named by its sha256 and marked read-only.
    - A file already in the store is never fetched again, whatever pin names it.
    - A store on a volume that cannot make hard links is refused at start (reason
      `store_no_links`).
3. **Each runtime sees its nodes' files through a view.**
    - The view is a folder under the store in the Hugging Face cache layout: `snapshots/<commit>/<path>`,
      with `refs/main` naming the commit, plus URL files under their own names. Its files are
      hard links to the stored ones.
    - Views are derived. Before a runtime child starts, its view holds exactly the present files
      its runtime's nodes pin. Removing a stored file removes its links from every view.
    - Removing a runtime never touches the store.
4. **Download is explicit and heals itself.**
    - `models.download {node, checkpoints?}` fetches the files every run needs plus the default
      checkpoint's ("all" is explicit). It is a job: it answers at once with a request id, and
      then sends `model.start`, `model.file` (phase: checking, downloading, retrying, verifying,
      placing), `model.progress` (throttled), and one of `model.done`, `model.failed` or
      `model.stopped`.
    - One download runs at a time; a second is refused with `busy`. A file that fails does not stop
      the others, and the request ends listing each file's reason. `models.stop` ends it within
      2 s, even on a stalled connection, and keeps the partial.
    - **Each attempt starts from the source URL.** A redirect is never reused. An unexpected status
      from a redirect target means "ask the source again".
    - **Retries:** dropped, reset or stalled connections, 5xx, 408 and 429 are retried after a
      growing wait, honouring `Retry-After` and `RateLimit`. A download fails only after the plan's
      number of attempts in a row that brought no new bytes, saying how many bytes it kept.
    - **A source that changed** fails before any of its body is written: the size in the first
      response differs from the pin.
    - **The bytes on disk are never wrong.**
        - Only bytes that continue the partial are appended.
        - Every file is hashed in full before it is placed; a partial is read again when a new
          request resumes it.
        - A partial lives outside every view, keyed by its sha256.
    - **Space.** What is left to fetch is checked against the free space before the first byte,
      and a disk that fills keeps its partial.
    - **The network.** Redirects stay on `https`, and no header but `Range` follows a redirect to
      another host. Certificates are checked against the operating system's trust store, proxies
      come from the system, and `HF_ENDPOINT` names a mirror.
5. **Present means checked.**
    - A file counts as present once its sha256 has matched; its record holds its size and
      modification time.
    - Each run compares both.
    - `models.verify` (a job like download) hashes again, and drops a file that fails, so the next
      download fetches only it.
6. **Runs use only what is present.**
    - **Order.** A cached result is served without files. Otherwise these run before `node.start`,
      in order: the fit, the runtime check, then the files check.
    - **The fit's inputs gain the set of present checkpoints** (amending spec 002 requirement 2).
      A change to a checkpoint that is not present is skipped, and `node.fit` says so. If only such
      a checkpoint would fit, the node fails with kind `memory`, naming it and its size.
    - **A node whose files are not all present fails with kind `files`.** Its reason is one of
      `not_downloaded`, `downloading`, `incomplete`, `changed` or `conflict`. The failure names the
      files and bytes left, and a present checkpoint if there is one. The engine never switches
      checkpoint because one is on disk.
    - **Nodes reach files only through `ctx`:** `ctx.file(name)`, `ctx.snapshot(repo)` and
      `ctx.revision(repo)`. `ctx.models` goes.
    - **The child's hub cache points at its runtime's view,** and is offline (the rest of the run's
      environment is already set by the engine).
    - **The cache key includes the sha256 of every file the run uses.** A node without files keeps
      its key.
7. **Remove is safe.**
    - `models.remove {node, checkpoint?}` deletes those files, their links and their partials,
      keeping and naming files another node pins.
    - `models.remove {unpinned: [sha256…]}` deletes exactly the files `models.list` showed as
      pinned by no node. It is refused while the registry has problems or a node root is missing.
    - It is refused for files a running graph uses, or a download is writing.
    - Nothing is deleted automatically. A remove never waits on a download.
8. **Status without the network.** `models.list` gives each node's file groups (shared files, and
   each checkpoint), each with:
    - size, bytes present and bytes to fetch;
    - what removing would free;
    - status, format, gated and licence.

   It also gives the store's free space, conflicts, and the files and partials no node pins.
   Listing, fitting and running never use the network.
9. **Tools.**
    - `npm run models:download -- <node> [--checkpoint c | --all]` does from the command line what
      the method does.
    - `npm run models:pin -- <node>` makes pins:
        - it resolves the commit and the canonical id, and records `gated`;
        - it expands a folder into entries;
        - it downloads and hashes every file through the store, and cross-checks Hugging Face's own
          hashes;
        - it reads formats from content, and reports `.py` files and `auto_map`;
        - re-pinning to a new commit rewrites that repository's entries and reports each changed
          sha256;
        - a value that changed under an unchanged pin is reported and left as it was.

## Acceptance criteria

Every criterion except AC8 runs in CI on Windows and Linux. The test server imitates Hugging
Face's resolve, revision, tree and paths-info endpoints, a URL host, and a redirect host with
single-use or expiring URLs. It can:

- drop, stall or corrupt a response;
- ignore ranges;
- answer 401, 403, 404 or 429 with Hugging Face's error codes;
- serve TLS from a test CA.

It logs every request. The test nodes are:

- one node with a Hugging Face source (a file every run needs, and checkpoints `small` and
  `large`) plus a URL source;
- a second node, in the same runtime, sharing one of those files;
- a third node, in another runtime, pinning another commit of the same repository.

- [ ] AC1: The manifest check refuses each case of requirement 1, and both nodes of a conflict
  report it (unit tests).
- [ ] AC2: Store and views (integration tests):
    - each distinct file is stored and fetched once (the server's log);
    - each view holds its runtime's files, its `refs/main` names its own commit, and the third
      node's view names another;
    - with the network closed, `huggingface_hub` (in the engine's dev group) finds a file through
      `hf_hub_download` by `main` and by commit, `try_to_load_from_cache`, and `snapshot_download`
      by `main` and by commit with `local_files_only`, and the server logs no request;
    - re-pinning to a commit with one changed file fetches only that file;
    - after a remove, the links are gone and the file's link count is 1;
    - a store that cannot hard-link (through a seam) is refused.
- [ ] AC3: Healing (integration tests). In each case the download completes unless the case says
  otherwise:
    - a drop at 40%, with a logged range from the partial's size and no second request from the
      person;
    - a stall, dropped after the test-sized window;
    - a Stop, and a killed engine, keep the partial; the next request shows `checking`, then
      resumes;
    - single-use redirects are asked for again on each attempt;
    - a 206 at the wrong offset, or a 200 to a range request, restarts the file;
    - a 429 is waited out;
    - a Stop during a stall returns within 2 s, over HTTP and TLS, on both operating systems;
    - repeated failures with no progress end the request, saying how many bytes were kept.
- [ ] AC4: Integrity, space and the network (tests):
    - a size mismatch fails before the body;
    - a hash mismatch keeps nothing and names both hashes;
    - a 416 on a full partial goes straight to hashing;
    - a partial larger than its pin is discarded;
    - a redirect to `http` is refused;
    - too little space refuses the request before the first byte;
    - a disk-full error from a write seam, with each platform's real error, keeps the partial;
    - the downloader's TLS context comes from one factory, truststore's in production, and a
      rejected certificate fails as `tls`;
    - a request through a recording CONNECT proxy;
    - `GatedRepo` fails as `gated` with the page, `RepoNotFound` as `not_found`, and
      `RevisionNotFound` as `revision_gone`.
- [ ] AC5: Runs (integration tests in the tiny runtime, and unit tests of the fit):
    - before any download, kind `files` with the bytes to fetch, and no process starts;
    - a cached result needs no files;
    - with the runtime also missing, the failure is kind `runtime`;
    - after the download, the node runs and reads through `ctx.file`;
    - a truncated file, or one with a new modification time, reads as `changed`, and
      `models.verify` catches a same-size, same-time change;
    - with only `small` present:
        - a graph that sets `large` fails, naming `small` as present;
        - a default of `large` with room fails the same way;
        - a default of `large` without room runs at `small`, labelled;
        - a fit change to `large` is skipped, and says why;
    - changing a file's pin changes the cache key, and a node without files keeps its key.
- [ ] AC6: Remove and concurrency (tests, Windows CI included):
    - removing one of two nodes that share a file keeps it and names the other node;
    - removing a checkpoint deletes only its files and partials;
    - removing unpinned files deletes exactly the list given;
    - remove is refused while the registry has problems or a node root is missing, and for a file
      a running graph uses;
    - a second download, or one from a second engine process, is refused with `busy` or `locked`;
    - nothing outside the store is touched.
- [ ] AC7: The pin tool against the test server (tests):
    - it fills in sizes and sha256s, and hashes small git files and cross-checks their SHA-1;
    - it expands a folder;
    - it records `gated` and the canonical id;
    - it reads a torch zip archive named `.bin` as a pickle;
    - it reports `.py` files and a cross-repository `auto_map`;
    - a re-pin rewrites the entries and reports the changed sha256s;
    - a changed value under the same pin is reported and left.
- [ ] AC8 (local, needs the real hosts, no GPU): on the owner's Windows PC, `npm run models:pin`
  and `npm run models:download` fetch the weights of Depth Pro and MoGe-2, at least one of them
  Xet-backed, plus a GitHub release asset.
    - The download is stopped midway, resumed more than five minutes later, and completes and
      verifies.
    - The report gives each file's bytes, seconds and MB/s on one connection, the hosts reached,
      and the longest path written. It is committed under `specs/003-model-store/reports/`.

  This proves the test server matches the real hosts, and its speeds decide whether to add
  parallel ranges later.

## Design

- **Where things live.** `oneframe/models/` holds:
    - `manifest.py`: the files schema and its checks;
    - `store.py`: blobs, records, views and partials;
    - `transport.py`: `http.client`, redirects, retries, the stop watchdog and the TLS factory;
    - `download.py`, `pin.py` and `methods.py`.

  Paths come from spec 006's layout module. Under `models/`: `oneframe-store.json` (the format),
  `blobs/<2>/<sha256>`, `partial/<sha256>`, `records/<sha256>.json` and
  `views/<runtime>/{hub,url}/`.
- **The transport** follows research.md's second round. Stop shuts the socket down from a watchdog,
  because closing it does not unblock a read. The stall rule is the socket timeout, since a read
  is one `recv`. Proxies come from `urllib.request.getproxies()` and a tunnel. truststore is
  imported only when a download starts.
- **Views** are rebuilt before a runtime child starts: links are added and dropped to match its
  pins, which costs a few stats. A blob's read-only attribute is cleared just before its last link
  goes.
- **Fit and cache.** The scheduler passes the present checkpoints to the fit, and file sha256s to
  `Cache.key` as part of the node's identity.
- **Risks.**
    - Windows' handling of a socket shut down mid-read over TLS is unverified (AC3 covers it).
    - A memory-mapped file cannot be deleted on Windows: remove refuses files a run uses, and spec
      004's workers inherit that rule.
    - Long paths: AC8 records the longest path.

## Tests

- Added: the cases of AC1 to AC7.
- Removed: none.

## Verification

AC1 to AC7 run in CI. AC8 runs in a local session:

```
npm run models:pin -- <depth node>
npm run models:download -- <depth node>
```

run with a Stop midway, then again after five minutes. Its report goes under
`specs/003-model-store/reports/`.

## Decisions

The owner's, 2026-10-02:

1. one copy per sha256, with a Hugging Face–layout view per runtime;
2. our own downloader, checked again and confirmed on `http.client`;
3. one connection per file, with parallel ranges only if AC8 shows the per-connection cap;
4. and 7. import, the store's location setting, and the page's allowlist, deferred with the rest of
   the UI work (review decision 2);
5. tokens later;
6. pickles flagged by content, `weights_only` forced, and the torch floor at 2.10 (done);
8. truststore for certificates.

Decisions taken (by the agent; reversible):

- **A second download is refused** rather than queued, until the UI's part adds a queue.
- **`huggingface_hub` joins the engine's dev group** for AC2, rather than a third test runtime, so
  the version check covers it.
- **A store that cannot hard-link is refused.** Copied views would need space accounting and a way
  to keep copies in step, and CI could not test them.
- **The pin tool's GitHub digest cross-check and template report** wait for a node that needs
  them.

## Out of scope

- **The later part of this spec, with the UI:**
    - import of files the person already has;
    - a store on another drive, with its pointer and location checks;
    - a queue;
    - the page's allowlist;
    - sizes at planning time.
- **Later specs:** tokens for gated repositories; parallel ranges; a bandwidth limit.
- **Not attempted:** scanning pickles, model signatures, and a sandbox stronger than the run's
  socket guard.
- **Not covered:** weights shipped as archives, files placed where torch hub looks, Hugging Face
  datasets and Spaces, and other hubs.
- **Weight sizes read from files** (left here by spec 002): no. File size is a poor stand-in for
  memory.
