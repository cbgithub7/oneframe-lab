# 003: Model store

Status: draft (revised 2026-10-02 after the research in [research.md](research.md); the owner
answered its questions the same day, below; a second review is under way before approval)
Owner approval: (date, once approved)

## Problem

A model node needs files that are not in its folder or its runtime: weights, configs, tokenizers
and the like. They are hosted on Hugging Face, on GitHub releases or on a vendor's own server, and
run from hundreds of megabytes to tens of gigabytes each. A runtime child runs with the network
closed (the "No network during a run" rule in [AGENTS.md](../../AGENTS.md)), so every file a node
reads has to be on disk before the run.

Today nothing declares these files, and nothing fetches, checks, places or removes them. The
scheduler passes each runtime child the bare `<data>/models` folder, which no node uses. The
child's docstring says hub libraries are told they are offline, but nothing tells them. Until this
exists, no model node can run (spec 005).

The best apps treat a file's hash as its identity and check every file before using it. They heal
a dropped download by themselves, let the store live on any drive, and accept a file the person
already has ([research.md](research.md)). The weakest follow `main`, check nothing, and make the
person start a 27 GB download again after every network drop. This spec brings the first set of
habits to the engine.

Spec 002 also left two things here: skipping a checkpoint that is not downloaded, and whether
weight sizes are read from the downloaded files.

## Requirements

1. **Files are declared in the manifest.** A node lists every file it reads from outside its
   folder and its runtime. Each entry gives:
    - a `name`, unique in the node, which the node's code uses to reach the file;
    - its source and path:
        - a Hugging Face repository at a full commit hash, never a branch or tag;
        - or an `https` URL;
    - its size in bytes and its sha256. Together these are the file's identity; the source is
      only where to fetch it;
    - optionally, the checkpoints it belongs to. These are values of the node's one checkpoint
      param, which is a choice param and, when the memory model has `weights_by`, that same
      param. `precision` may not key files. A file with no checkpoints is needed by every run;
    - its format, read from its content by the pin tool (requirement 11): safetensors, pickle,
      GGUF, ONNX, NumPy, text, code or other;
    - whether its source is gated, and if so the page where the person accepts its licence.

   The manifest check refuses a manifest with any of these, naming every problem at once:
    - a branch or tag in place of a commit;
    - a size or sha256 missing or malformed;
    - two commits of one repository in one node;
    - a URL that is not `https`. A plain-`http` URL is accepted only on a loopback address, which
      the tests use;
    - a path that is absolute or has a `..` step;
    - a path that is unsafe on Windows: a reserved name, a trailing dot or space, a `:`, or two
      paths that differ only in case;
    - a path longer than the budget the plan sets;
    - a checkpoint that is not a value of the checkpoint param;
    - files in a node that runs in the engine.

   Across manifests, two nodes conflict in either of these cases:
    - they pin different files at one source and path;
    - they share a runtime and pin different commits of one repository.

   A conflict is reported in the registry's problems and by `models.list`, naming both nodes.
   Their conflicting files cannot be downloaded or used until the pins agree.

   Nothing outside a node's folder names a repository or a file.
2. **One copy of each file, in a store that can live anywhere.**
    - **The store root** is `<data>/models`, unless `settings.json` names another folder. It takes
      effect when the engine starts.
        - If the root is missing (an unplugged drive), the store reports itself offline. Every
          model method and every run that needs a file says so.
        - The engine never falls back to another folder.
    - **Each file is kept once,** named by its sha256 and marked read-only, however many nodes,
      repositories or commits name it.
        - A file already in the store is never fetched again.
        - A new pin that leaves a file unchanged moves no bytes.
    - **Each runtime sees its nodes' present files through a view.** The view is a folder in the
      Hugging Face cache layout: `snapshots/<commit>/<path>`, with `refs/main` naming the commit
      its nodes pin. URL files appear under their own names.
        - A library inside a node that looks a repository up by itself finds the files, by
          `main` or by commit, with the network closed.
        - A view's files are hard links to the stored files where the volume allows, otherwise
          copies, and `models.list` says which.
    - **Partial downloads** live in the store, outside every view, keyed by the sha256 they will
      have. A library never sees a partial download, and two nodes that need one file share
      one partial.
3. **Downloads are explicit, and heal themselves.**
    - **The request.** `models.download` takes one or more nodes, each with the checkpoints
      wanted. By default it fetches the files every run needs plus those of the default
      checkpoint; "all" is explicit.
        - It answers at once, with the request's place in the queue.
        - Requests run in order, one file at a time.
        - The queue is not kept across restarts: nothing downloads after a restart unless the
          person asks again.
    - **Free space.** Before the first byte, the bytes still to fetch are compared with the free
      space on the store's drive, less a reserve. The bytes to fetch are each missing file's size
      less what its partial already holds, and less what the store already has. If there is not
      enough room, the request is refused with both figures.
    - **Every attempt starts from the source URL** and follows its redirects again. A redirect
      URL is never stored or reused, because hosts sign it to expire. A redirect may lead only to
      `https`.
    - **The total size** in the first response is compared with the pinned size. A mismatch fails
      before any of the body is written ("the source changed").
    - **Resume.** Only a 206 whose range starts at the partial's size is appended. Any other
      answer starts that file again from zero. A 416 on a partial that is already full size goes
      straight to hashing.
        - A resume after a restart reads the partial again first, because the state of a hash
          cannot be saved. This phase is shown on its own as "checking".
    - **Retries.** These are retried by themselves, after a growing wait, resuming from the
      bytes on disk:
        - a dropped or reset connection, a timeout, a 5xx, a 408 or a 429. A 429's
          `Retry-After` or `RateLimit` wait is honoured;
        - a connection that delivers nothing for the stall window, which is dropped first.

      There is no total time limit. A download fails only after the plan's number of attempts
      in a row that brought no new bytes, and the failure says how many bytes are kept.
    - **Stop** ends a download within about a second, even on a stalled connection. The partial
      is kept.
    - **Checking and placing.**
        - Each file is hashed as its bytes arrive.
        - It is written to disk and moved into place only when its size and sha256 match the
          pin. A file that does not match is not kept.
        - On Windows, a move that another program refuses for a moment (antivirus, the indexer)
          is retried for a bounded time.
        - A disk that fills midway fails the download with that reason, and keeps the partial.
    - **One process at a time.** Download, verify, import and remove hold a lock on the store
      that the operating system releases if the process dies. A second engine process (a bench
      or pin tool) waits or is refused, with that reason.
    - **The network.**
        - Certificates are checked against the operating system's trust store.
        - Proxies come from the proxy variables. On Windows, with none set, the system proxy in
          the registry is used.
        - `HF_ENDPOINT` changes the Hugging Face host, for a mirror.
        - Compressed transfer is never requested.

   The plan sets the numbers this requirement names: the reserve, the stall window, the waits,
   the number of attempts and how long a refused move is retried. Each comes with its reason, and
   none is tuned to one machine.
4. **Present means checked.**
    - A file is present only once its sha256 has matched. Its record (size and modification time)
      is then written atomically.
    - Each run compares the size and modification time of the files it needs with their records.
      A file that differs is "changed", and is not used.
    - **`models.verify`** hashes again, for all files or for one node's, with progress events and
      Stop.
        - A file that fails is dropped from present, so the next download fetches only that file.
        - A file that matches gets a fresh record. A store copied from elsewhere is then checked,
          not downloaded again.
5. **Files the person already has are imported, not downloaded again.** `models.import` takes a
   file or a folder:
    - for example, a file downloaded in a browser, a store copied from another PC, or an existing
      Hugging Face cache;
    - it hashes each file whose size matches a pinned file, and copies the matches into the store;
    - it never moves, deletes or links the source, and never uses the network;
    - it reports what was adopted, what matched no pin, and what failed its hash.

   This is the offline path, the path for gated files until tokens arrive, and the answer when a
   source disappears upstream.
6. **Status, without the network.** `models.list` gives:
    - **the store:** its root, the free space on its drive, and whether it is offline;
    - **each node's files**, grouped into those every run needs and those of each checkpoint.
      Each group has:
        - its size, the bytes present, and the bytes to download once the store's own copies are
          counted;
        - what removing it would free, leaving out files that another node pins;
        - its status: present, missing, incomplete, changed or downloading;
    - **each file:**
        - its name and size;
        - the bytes on disk;
        - its status and format;
        - its source, whether it is gated, and the page where its licence is accepted;
    - **each node's weights licence;**
    - **everything else:**
        - the queue;
        - conflicts;
        - stored files and partials that no node pins, with their sizes;
        - the registry's problems.

   Planning a graph also reports, for each node, the bytes still to download at the checkpoint
   it would run. The page can then offer Download before Run.

   Listing, planning, fitting and running never use the network. Only `models.download` and the
   pin tool do, and only to a node's sources and wherever they redirect.
7. **Runs use only what is present.**
    - **The order of checks.** A cached result is served without any file present. Otherwise the
      checks run in this order, before `node.start`:
        - the fit (`node.fit`);
        - the runtime check;
        - the files check.
    - **The fit knows which checkpoints are downloaded** (this amends spec 002 requirement 2).
      A change to a checkpoint that is not downloaded is skipped, and `node.fit` says
      "not downloaded".
    - **If the run's checkpoint is not fully present,** the node fails with kind `files`. The
      failure gives:
        - its reason: not downloaded, downloading, incomplete or changed;
        - the checkpoint, the files and the bytes left;
        - a downloaded checkpoint, if there is one.

      The engine never switches to another checkpoint because that one is on disk. It switches
      only for memory, as spec 002 allows.
    - **A node reaches its files through `ctx`** and never builds a path:
        - `ctx.file(name)` gives the file's path in its runtime's view;
        - `ctx.snapshot(repo)` gives that repository's folder at its pinned commit;
        - `ctx.revision(repo)` gives that commit, for libraries that take one.

      `ctx.models` goes.
    - **The engine sets the run's environment last,** over the person's environment and the
      runtime's definition:
        - the hub's cache points at the runtime's view, and is marked offline;
        - the hub's home folder is under the data root;
        - every hub token and endpoint the person set is removed;
        - torch's `weights_only` loading is forced on, and the variable that turns it off is
          removed.

      A runtime definition that sets any of these is refused.
    - **The cache key.** A node's cache key includes the sha256 of every file its run uses. A
      node that declares no files keeps the key it has today.
8. **Failures say what happened and what to do.** A failed download, verify or import gives:
    - a stable reason, one of:
        - `no_space`, `disk_full`, `store_offline`, `locked`, `stopped`;
        - `network` (with the host and any proxy), `tls`, `http`, `rate_limited`;
        - `gated`, `not_found`, `revision_gone`;
        - `size_mismatch`, `hash_mismatch`;
    - whether trying again could help;
    - the next step, such as "free 3.2 GB on D: or move the model store", "accept the licence at
      <page>, or import a file you downloaded", or "the pinned commit is gone upstream: import
      the file or update the node".

   Hugging Face's `X-Error-Code` tells a gated repository from a missing or private one. A 401
   alone does not mean gated.
9. **Remove is safe and explicit.**
    - **`models.remove` with a node,** or one of its checkpoints, deletes those files and their
      partials. Files that another node pins are kept, and the answer names that node.
    - **`models.remove` with a list of files that no node pins** deletes exactly that list, as
      `models.list` showed it.
        - It is refused while the registry has problems, because a node that failed to load
          still owns its files.
        - It is refused if any listed file is pinned again by then.
    - **What is never deleted:**
        - a file a running graph is using, or one being downloaded: the answer names the node.
          A file the system will not let go is reported, not skipped silently;
        - anything automatically;
        - anything outside the store root.
10. **Command line.** These print progress and write the same events as the engine methods. They
    let a local session fetch weights without the app, as spec 005's `bench:fit` needs:
    - `npm run models:download -- <node> [--checkpoint <c> | --all]`;
    - `npm run models:import -- <path>`.
11. **Pins are made by a tool, not by hand.** `npm run models:pin -- <node>` works from a
    repository and commit (or a branch it resolves to a commit) and from URLs:
    - it records the repository's canonical id, and whether it is gated;
    - it expands a folder at a commit into file entries;
    - it **downloads every file through the store's own downloader and hashes what arrived**;
    - it cross-checks the hosts' own hashes: Hugging Face's sha256 for large files and git SHA-1
      for small ones, and GitHub's asset digest;
    - it reads each file's format from its content, never from its extension;
    - it reports files that carry code: `.py` files, `auto_map` (above all one naming another
      repository), and templates;
    - a value that differs from an existing pin is reported and left as it was.

    The tool runs where the hosts are reachable: on the owner's PC, not in a cloud session.

## Acceptance criteria

Every criterion but AC13 runs on the processor in CI, on Windows and Linux, against a local test
server.

**The test server** imitates Hugging Face's resolve, revision, tree and paths-info endpoints, a
plain URL host, and a second host that redirects lead to. It can:

- sign redirect URLs to be used once or to expire after a set time;
- honour or ignore ranges;
- drop the connection or stall partway;
- send a wrong `Content-Range`, a wrong size or altered bytes;
- answer with 401, 403, 404 and 429 and Hugging Face's error codes.

It logs every request.

**The test nodes:**

- A node with a Hugging Face source, holding one file every run needs and two checkpoints
  (`small` and `large`), plus a URL source.
- A second node in the same runtime, sharing one of those files.
- A third node in another runtime, pinning another commit of the same repository.

- [ ] AC1: The manifest check refuses each case in requirement 1, naming every problem at once.
  The cross-manifest conflicts are reported for both nodes (unit tests).
- [ ] AC2: The store and its views (integration tests):
    - after downloading the first two nodes, each distinct file is stored once and was fetched
      once (the server's log);
    - each runtime's view holds its nodes' files in the Hugging Face layout, and its `refs/main`
      names its own commit while the third node's runtime names another;
    - in a test runtime of its own holding `huggingface_hub` (not the tiny runtime), with the
      network closed, each of these finds the file in the view:
        - `hf_hub_download` by `main` and by commit;
        - `try_to_load_from_cache`;
        - `snapshot_download` by `main`;
        - `snapshot_download` by commit with `local_files_only`;
    - re-pinning the first node to a commit where one file changed fetches only that file.
- [ ] AC3: Resume and retries (integration tests). In each case the download completes unless the
  case says otherwise:
    - a connection dropped at 40% completes without a second request from the person, with a
      logged range from the partial's size;
    - a connection that stalls is dropped after the stall window (set small for the test), and
      the download goes on;
    - after Stop, and after the engine is killed, the partial is kept. The next download shows
      "checking", then resumes from the partial's size;
    - with single-use redirect URLs, every attempt asks the source URL again;
    - a 206 at the wrong offset, or a 200 to a range request, starts the file again from zero;
    - a 429 with a wait is waited out;
    - Stop during a stall returns within about a second;
    - repeated failures with no new bytes end the download after the plan's number of attempts,
      saying how many bytes are kept.
- [ ] AC4: Integrity and space (tests):
    - a first response whose size differs from the pin fails before the body is written;
    - altered bytes are not kept, and the failure names the file, its source and both hashes;
    - a 416 on a full-size partial goes to hashing;
    - a partial larger than its pin is discarded;
    - a redirect to `http` is refused;
    - too little free space (a recorded figure) refuses the request before the first byte, naming
      both figures;
    - a disk that fills midway (simulated) fails with `disk_full` and keeps the partial.
- [ ] AC5: Runs (integration tests in the tiny runtime):
    - before any download, the node fails with kind `files`, reason not downloaded and the bytes
      to fetch, and no process starts;
    - a cached result is served with no files on disk;
    - with both the runtime and the files missing, the failure is kind `runtime`;
    - after the download, the node runs and reads each file through `ctx.file`;
    - a truncated file, and a file changed at the same size with a new modification time, are
      reported changed, and the run fails with that reason;
    - `models.verify` catches a file changed at the same size and modification time, and the
      next download fetches only that file;
    - in the child, the hub variables point at the view and say offline, `weights_only` is
      forced, and none of the person's hub tokens, endpoints or the `weights_only` override
      remain;
    - the server logs no request during a run;
    - a runtime definition that sets one of these variables is refused.
- [ ] AC6: Checkpoints, with only `small` downloaded (unit tests of the fit, and an integration
  test):
    - a graph that sets `large` fails with kind `files`, naming `large`, its size, and `small` as
      downloaded;
    - with `large` as the default and plenty of memory, the node fails the same way. It does not
      switch to `small`;
    - with `large` as the default and too little memory for it, the fit moves to `small` and the
      run goes ahead, labelled;
    - a fit that would save memory by moving to a checkpoint that is not downloaded skips that
      change, and `node.fit` says why.
- [ ] AC7: Changing a file's pinned sha256 changes the node's cache key. The same pins give the
  same key, and a node without files keeps its key (unit tests).
- [ ] AC8: The network and errors (tests):
    - listing, planning, fitting and a run make no connection;
    - a download connects only to the test node's source hosts and where they redirected;
    - a `GatedRepo` answer fails as `gated`, naming the page;
    - a 401 with `RepoNotFound` fails as `not_found`, not gated;
    - `RevisionNotFound` fails as `revision_gone`;
    - with `HF_ENDPOINT` set, requests go to that host;
    - with a proxy variable set, requests go through a recording proxy, except for hosts in
      `NO_PROXY`;
    - a certificate the trust store rejects fails as `tls`, naming the host.
- [ ] AC9: Remove and concurrency (tests, including Windows CI):
    - removing one of two nodes that share a file keeps it and names the other node;
    - removing a checkpoint deletes only its files and partials;
    - removing a list of unpinned files deletes exactly that list;
    - it is refused while the registry has problems, and while a listed file is pinned again;
    - removing a file that a running graph uses is refused, naming the node;
    - a second engine process downloading the same file waits or is refused, and the file is
      written once;
    - nothing outside the store root is touched.
- [ ] AC10: Store location and import (integration tests):
    - with the root moved to another folder, download, list, run, verify and remove all work, and
      `<data>/models` is untouched;
    - with the root missing, everything reports the store offline, and nothing is written
      elsewhere;
    - importing a folder holding one matching file, one altered file and one unrelated file
      adopts only the first, and leaves the source as it was;
    - importing a folder in the Hugging Face cache layout adopts its matching files.
- [ ] AC11: The pin tool, against the test server (tests). It:
    - fills in sizes and sha256s;
    - downloads and hashes the small git files, and cross-checks their git SHA-1;
    - expands a folder;
    - records gated and the canonical id;
    - reads a pickle named `.bin` as a pickle;
    - reports a `.py` file and a cross-repository `auto_map`;
    - reports a differing pin and leaves it as it was.
- [ ] AC12: What the page sees (tests):
    - `models.list` returns every field of requirement 6 (checked against a recorded answer);
    - a download emits the phases of requirement 3 in order, and progress at no more than the
      rate the plan sets;
    - a second request is queued with its position, and one file transfers at a time;
    - planning a graph reports the bytes to download per node;
    - each failure reason of requirement 8 is produced by at least one test above;
    - the methods the owner allows (decision 7) are in `ENGINE_METHODS`, and no other model
      method is.
- [ ] AC13 (local, needs the real hosts, no GPU): on the owner's Windows PC, with the network:
    - a test node is pinned with `npm run models:pin` and downloaded with
      `npm run models:download`. It covers a small public Hugging Face repository at a commit,
      with at least one Xet-backed file and one small git file, and a GitHub release asset;
    - the download is stopped midway, resumed after a pause longer than the release asset's
      signed URL lives, and completes and verifies;
    - the report gives each file's bytes, seconds and MB/s on one connection, the hosts reached,
      the size headers each host sent, whether each host honoured `If-Range`, and the data root's
      longest path;
    - the report is committed under `specs/003-model-store/reports/`.

  This proves the test server matches the real hosts. Its speeds decide decision 3.

## Out of scope

- **Later specs:**
    - Keeping models loaded between runs (spec 004). Its workers must keep requirement 9's rule:
      a file in use is not removed.
    - Real model nodes and their pins (spec 005).
    - The Download button, the confirmation with size, licence and free space, and the Storage
      page (workspace UI spec). This spec gives them the data.
    - Moving the store with a button. The setting in requirement 2 is enough here; a move (copy,
      verify, switch) comes with the UI.
    - Tokens for gated repositories (decision 5).
    - Passing Electron's system proxy (PAC, proxy authentication) to the engine (packaging spec).
- **Left out of the downloader for now:**
    - several connections per file (decision 3);
    - a bandwidth limit;
    - resuming the queue after a restart.
- **Not attempted:**
    - Scanning pickles. Scanners are bypassed again and again
      ([research.md](research.md#the-security-of-model-files)). Pickles are flagged by content
      and loaded with `weights_only` forced.
    - Model signatures. Hugging Face publishes none.
    - A sandbox stronger than the run's socket guard. That guard stops accidental downloads, not
      a malicious payload, and the docs will say so.
- **Not covered:**
    - Weights shipped as zip or tar archives. Every file here is stored as downloaded, so its
      download size is its size on disk.
    - Files placed where torch hub or a library's own store looks (`TORCH_HOME`,
      `HY3DGEN_MODELS`, `U2NET_HOME`). A node passes its file's path from `ctx` instead.
    - Hugging Face datasets and Spaces, other hubs, and Xet's own client.
- **Weight sizes read from files** (left here by spec 002): no. A file's size is a poor stand-in
  for the memory it takes, because of precision casts, tied weights and extra tensors kept in
  checkpoints. The memory model keeps its declared and measured figures.

## Decisions

Answered by the owner on 2026-10-02, who accepted each recommendation. Decision 2 stands
subject to a second check. The evidence is in [research.md](research.md).

1. **The store's shape.**
   *Decided:* one copy per sha256, with a view in the Hugging Face layout per runtime, made
   of hard links (requirement 2).
    - It shares files across nodes, repositories and commits, so a new pin moves only the files
      that changed.
    - It keeps `refs/main` right in each view.
    - It makes cleanup a matter of comparing sets of hashes.
    - A view is per runtime, not per node, because spec 004 keeps one worker per runtime and the
      hub's cache path is fixed per process.
    - Its cost: hard links need the same NTFS or ext4 volume, which holds within the store root.
      Elsewhere (exFAT, network drives) files are copied, and the list says so.

   *The alternatives:*
    - **One shared Hugging Face cache of real files** (the first draft). Simpler, but a new pin
      copies everything again. One `refs/main` serves every node, so one node's pin can break
      another node's library lookup, or feed it the wrong bytes.
    - **A folder per node** (the handoff's first idea). The simplest, but every shared file is
      stored twice.
2. **Our own downloader, or `huggingface_hub` in the engine?** *Decided:* our own, in the
   engine, on urllib.
    - The research settled it: Hugging Face's own client can no longer resume after a Stop or a
      crash, never checks a sha256, and only warns about disk space.
    - Electron's downloader was also considered. It discards bytes on resume unless the server
      sends both an ETag and a Last-Modified, it reuses expired URLs, and the engine would still
      have to verify every file.
3. **One connection per file, or several?** *Decided:* one, with the retries above, keeping
   the partial's form open to several later.
    - Many of Hugging Face's CDN edges cap one connection at 8.7 MB/s (xet-core #821): about 19
      minutes for 10 GB instead of 2.5.
    - Several connections need sparse files on NTFS, state per part, and a second hashing pass.
    - AC13 measures the speed on the owner's line. If it shows the cap, a follow-up adds parallel
      ranges.
4. **The store's location and import: now, or with the UI?** *Decided:* now, both.
    - Every app surveyed lets the models live on another drive, while `<data>` is on C:.
    - Import is cheap.
    - Import is the only path for an offline machine, a gated file, or a source that has gone.
    - A path for import reaches the engine from the command line, or later from a file dialog
      in the main process, never typed into the page.
5. **Tokens for gated repositories: now or later?** *Decided:* later, in the spec that adds
   the first gated node. Import covers gated files meanwhile.
    - Of the first models planned, SAM 2.1 and TripoSR are confirmed not gated. Spec 005 checks
      the rest when pinning them.
    - When tokens arrive:
        - use a fine-grained, read-only token;
        - keep it with Electron's `safeStorage` in the main process;
        - send it only to the Hub's own host, never with a redirect;
        - never write it to a file or an environment variable.
6. **Pickles and torch.** *Decided:*
    - Flag pickles by content in `models.list`.
    - Force `weights_only` in every run (requirement 7).
    - **Raise the runtime manager's torch floor from 2.6 to 2.10 now, as a small fix of its own**
      (CVE-2026-24747, which lets a crafted file corrupt memory through `weights_only` before
      2.10). The torch runtime already pins 2.14.0, so only the floor, its test and the docs
      change.
7. **The page's allowlist.** *Decided:* add `models.list`, `models.download`, `models.stop`,
   `models.remove` and `models.verify` to `ENGINE_METHODS`, as spec 001 added `runtimes.*`.
   `models.import` stays off it until the UI spec gives it a file dialog in the main process. No
   secret passes through the page.
8. **Certificates.** *Decided:* take truststore (0.10.4, pure Python, no dependencies, used
   by pip by default) as an engine dependency. Import it lazily, so the light-engine test still
   holds.
    - As a follow-up, have runtime installs use the system's certificates too, so runtimes and
      models trust the same roots.
