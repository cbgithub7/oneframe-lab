# 003: Model store (core)

Status: draft (core, 2026-10-03; evidence in [research.md](research.md), the owner's decisions in
[the review](../../docs/reviews/2026-10-02.md))
Owner approval: (date, once approved)

Built on [spec 006](../006-foundations/spec.md): its layout module, failure model, locks and journal.

## Problem

A model node reads files that are not in its folder or its runtime: weights, configs and the like,
hosted on Hugging Face or a vendor's server, often gigabytes each. A run has no network, so they
must be on disk first, and today nothing declares, fetches, checks or removes them. This core is
what the first models need: Depth Pro (one `.pt` from Apple's server), and MoGe-2 and MoGe-3 (one
`model.pt` per variant, on Hugging Face).

## Requirements

1. **Files are declared in the manifest.** Each file has:
    - a `name`, unique in the node;
    - a source: a Hugging Face repository at a full commit hash, or an `https` URL;
    - a size and a sha256, which are its identity (the source is only where to fetch it);
    - optionally, checkpoints: values of the node's `files_by` choice param, which must equal the
      memory model's `weights_by` when both exist. `precision` never keys files;
    - its format, read from its content (safetensors, pickle including torch's zip form, or other);
    - whether its source is gated, with the page where its licence is accepted.

   The manifest check refuses each of these, naming all of them at once:
    - a branch in place of a commit;
    - a bad size or sha256;
    - a URL that is not `https`;
    - an unsafe path: absolute, `..`, a Windows reserved name, a trailing dot or space, `:`, or
      paths that differ only in case;
    - a checkpoint that is not a choice of `files_by`, or checkpoints without `files_by`;
    - two files with one `name`;
    - files in a node that runs in the engine.
2. **One copy of each file,** under `<data>/models/`, named by its sha256 and read-only. A file
   already stored is never fetched again, whatever pin names it.
3. **Download is a job, and heals itself.**
    - `models.download {node, checkpoints?}` fetches the files every run needs, plus the default
      checkpoint's, or every checkpoint's when asked. It answers at once with a request id, then
      sends:
        - `model.start`;
        - `model.file`, with its phase: checking, downloading, retrying, verifying or placing;
        - `model.progress`, throttled;
        - then `model.done`, `model.failed` or `model.stopped`.

      Its events go to spec 006's journal.
    - **One job at a time.** One job (download or verify) runs at a time across processes; another
      is refused with `busy` or `locked`. A file that fails does not stop the others.
    - **Stop.** `models.stop` sends `model.stopped` within 2 s, even on a stalled connection, and
      keeps the partial.
    - **Each attempt starts from the source URL;** redirects are never reused. Dropped, reset or
      stalled connections, 5xx, 408 and 429 are retried with a growing wait, honouring
      `Retry-After` and `RateLimit`. The download fails only after a number of attempts in a row
      with no new bytes (set in code, with its reason), saying how many bytes it kept.
    - **Integrity.**
        - A source whose first response gives another size fails before its body
          (`source_changed`).
        - Only bytes that continue the partial are appended.
        - Every file is hashed in full before it is placed.
        - Partials live apart, keyed by sha256; while a URL file's sha256 is still unknown (during
          pinning), by its URL.
    - **Space and network.**
        - Free space is checked before the first byte, and a full disk keeps the partial.
        - Every request, to a source or a redirect target, is `https`.
        - No header but `Range` follows a redirect to another host.
        - Certificates are checked against the operating system's trust store (truststore, an
          engine dependency), proxies come from the system, and `HF_ENDPOINT` names a mirror.
    - **Failures** carry kind `download` and a reason:
        - `source_changed`, `hash_mismatch`, `no_space`, `stalled`, `tls`, `rate_limited`;
        - `gated`, `not_found`, `revision_gone`;
        - `busy`, `locked`, `stopped`.

      Hugging Face's `X-Error-Code` decides `gated`, `not_found` (`RepoNotFound`, `EntryNotFound`)
      and `revision_gone`, never the status alone.
4. **Present means checked.**
    - A file is present once its sha256 matched, and its record holds its size and modification
      time. Each run compares both.
    - `models.verify {node?}`, a job like download, hashes again and drops a file that fails, so the
      next download fetches only it.
5. **Runs use only what is present.**
    - **Order.** A cached result needs no files. Otherwise these run before `node.start`, in
      order: the fit, the runtime check, then the files check.
    - **The fit's inputs gain the present checkpoints** (amending spec 002 requirement 2).
        - A change to a checkpoint that is not present is skipped, and `node.fit` says so.
        - If only such a checkpoint would fit, the node fails with kind `memory`, naming it and its
          size.
        - The engine never switches checkpoint just because one is on disk.
    - **A node missing a file fails with kind `files`.** Its reason is `not_downloaded`,
      `downloading`, `incomplete` or `changed`, and the failure names the files, the bytes left, and
      a present checkpoint if there is one.
    - **A node reaches its files only through `ctx.file(name)`.** `ctx.models` goes.
6. **Remove is safe.**
    - `models.remove {node, checkpoint?}` deletes those files and their partials, keeping and
      naming any that another node pins.
    - `models.remove {unpinned: [sha256…]}` deletes exactly the files `models.list` showed as
      pinned by no node. It is refused while the registry has problems or a node root is missing.
    - Remove is refused while a job runs in any process, and for files a running graph uses.
    - Nothing is deleted automatically.
7. **Status without the network.**
    - `models.list` gives, for each node's shared files and each checkpoint:
        - size, bytes present and bytes to fetch;
        - what removing would free;
        - status, format, gated and licence.

      It also gives the store's free space, and the files and partials no node pins.
    - Listing, fitting and running never use the network.
8. **Tools.**
    - `npm run models:download -- [--nodes <folder>] <node> [--checkpoint c | --all]` does what
      the method does.
    - `npm run models:pin -- [--nodes <folder>] <node>`:
        - resolves the commit and the canonical id, and records `gated`;
        - downloads and hashes every file through the store, and reads each file's format from its
          content;
        - re-pinning to a new commit rewrites that source's entries and reports each sha256 that
          changed;
        - a value that changed under an unchanged pin is reported and left as it was.

## Acceptance criteria

AC1 to AC7 run in CI on Windows and Linux, against a test server. It imitates Hugging Face's
endpoints, a URL host and a redirect host with single-use URLs, all over TLS from a committed test
CA. It can inject faults, and it logs every request (tasks.md lists each fault).

- [ ] AC1: The manifest check refuses each case of requirement 1 at once (unit tests).
- [ ] AC2: Store (integration tests):
    - each distinct file is stored and fetched once, as the server's log shows, even when two nodes
      pin it;
    - re-pinning to a commit with one changed file fetches only that file.
- [ ] AC3: Healing (integration tests). With each fault in tasks.md (a drop, a stall, a 206 at the
  wrong offset, a 200 to a range request, a 429, a single-use redirect, a killed engine), the
  download completes without a second request from the person. Repeated failures with no progress
  end it as `stalled`, saying how many bytes it kept. A Stop sends `model.stopped` within 2 s,
  before the headers and mid-body, over TLS, on both operating systems.
- [ ] AC4: Integrity, space and the network (tests). Each reason in requirement 3 is produced by a
  test:
    - a changed size fails before the body;
    - a hash mismatch keeps nothing;
    - a full partial answered with 416 is hashed;
    - a redirect to `http` is refused before connecting;
    - too little space is refused before the first byte;
    - a full disk (a seam raising each platform's real error) keeps the partial;
    - a rejected certificate fails as `tls`;
    - a CONNECT proxy is used;
    - Hugging Face's error codes map as requirement 3 says.
- [ ] AC5: Runs (integration tests in the tiny runtime, and unit tests of the fit):
    - before any download, kind `files` with the bytes to fetch, and no process starts;
    - a cached result needs no files;
    - with the runtime missing too, the failure is kind `runtime`;
    - after the download, the node runs and reads through `ctx.file`;
    - a truncated file, or one with a new modification time, reads as `changed`, and
      `models.verify` catches a change that keeps both the size and the time;
    - with only `small` present, a graph that sets `large`, or a default of `large` with room,
      fails and names `small`;
    - with only `large` present and no room for it, the change to `small` is skipped (said in
      `node.fit`), and the node fails with kind `memory`, naming `small` and its size.
- [ ] AC6: Remove (tests, Windows included):
    - a file shared with another node is kept and the other node named;
    - a checkpoint's removal deletes only its files and partials;
    - an unpinned list deletes exactly that list;
    - remove is refused while the registry has problems, while a node root is missing, during a job
      in another process, and for a file a running graph uses;
    - nothing outside the store is touched.
- [ ] AC7: The pin tool, against the test server:
    - it fills in sizes, sha256s, formats (a torch zip archive named `.bin` reads as a pickle),
      `gated` and the canonical id;
    - a re-pin reports the changed sha256s;
    - a changed value under the same pin is reported and left.
- [ ] AC8 (local, real hosts, no GPU): on the owner's Windows PC, with manifests that pin only
  files, kept in `specs/003-model-store/live/`:
    - these are pinned with `npm run models:pin` and fetched with
      `npm run models:download -- --all`:
        - Depth Pro's `depth_pro.pt` from Apple's server;
        - MoGe-2's `model.pt` from `Ruicheng/moge-2-vitl-normal`;
        - MoGe-3's `model.pt` from `Ruicheng/moge-3-vitl` and `Ruicheng/moge-3-vitg`, as two
          checkpoints of one manifest. The 1.25B-parameter `vitg` is the large file this test
          needs;
    - each download is stopped midway, resumed, and completes and verifies;
    - one GitHub release asset is resumed more than 300 s later, past its redirect token's life;
    - the report gives each file's bytes, seconds and MB/s on one connection, the hosts reached,
      and the longest path. It goes under `specs/003-model-store/reports/`.

  This proves the test server matches the real hosts; its speeds decide whether to add parallel
  ranges. Spec 005 starts from these pins.

## Design

- **On disk** (a format; hard to change):
    - `models/oneframe-store.json`, the store's format;
    - `blobs/<2>/<sha256>`;
    - `partial/<sha256>`, or `partial/url-<hash of the URL>` while pinning;
    - `records/<sha256>.json`.

  Paths come from spec 006's layout module.
- **Code** in `oneframe/models/`: the schema, the store, the transport, the download and verify
  jobs, the pin tool and the methods. The transport follows research.md's "Second round":
    - `http.client`;
    - Stop shuts the socket down from a watchdog;
    - the stall rule is the socket timeout;
    - proxies come from `urllib.request.getproxies()`, with a CONNECT tunnel;
    - truststore is imported only when a download starts.
- **Fit.** The scheduler passes the present checkpoints to the fit.
- **Risks.**
    - Windows' handling of a TLS read shut down mid-read is unverified (AC3).
    - Windows cannot delete a memory-mapped file: remove refuses files a run uses, and spec 004's
      workers inherit that rule.

## Tests

- Added: AC1 to AC7.
- Removed: none.

## Decisions taken

- **The Hugging Face–layout view per runtime moves to a short part before step 4 of the
  handoff's order.** This keeps the owner's decision 1 and changes only when it lands. The view
  includes `refs/main`, hard links, `ctx.snapshot` and `ctx.revision`, the per-runtime commit
  rule, the hub lookup tests, and the pin tool's folder expansion and code report. TripoSR is
  the first model that needs it; Depth Pro, MoGe-2 and MoGe-3 load one local file each. Until then,
  `ctx.file` gives the stored file's own path.
- **No file hashes in the cache key.** Spec 006 hashes the node's folder, so a changed pin in
  `node.json` already changes it.
- **A second job is refused, not queued,** until the UI's part adds a queue.
- **The test CA is a committed fixture:** a CA and a leaf for `127.0.0.1` and `localhost`, valid
  for a century and built to pass Python's strict checks. The standard library cannot make
  certificates.

## Out of scope

- **Before step 4:** the view part above.
- **With the UI:**
    - import of files the person already has;
    - a store on another drive;
    - a queue;
    - the page's allowlist;
    - sizes at planning time.
- **Later specs:** tokens; parallel ranges; a bandwidth limit.
- **Not attempted:**
    - scanning pickles;
    - signatures;
    - a sandbox stronger than the socket guard;
    - archives as weights;
    - torch hub's own folder;
    - weight sizes read from files (left here by spec 002), because a file's size is a poor
      stand-in for the memory it takes.
