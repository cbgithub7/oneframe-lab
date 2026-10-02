# 003: Model store

Status: draft
Owner approval: (date, once approved)

## Problem

A model node needs files that are not in its folder or its runtime: weights, configs, tokenizers
and the like. They are hosted on Hugging Face, on GitHub releases or on a vendor's own server. A
runtime child runs with the network closed (the "No network during a run" rule in
[AGENTS.md](../../AGENTS.md)), so every file a node reads has to be on disk before the run. Today
nothing declares these files, and nothing fetches, checks, places or removes them. The scheduler
passes each runtime child the bare `<data>/models` folder, and no node uses it yet. Until this
exists, no model node can run (spec 005).

Spec 002 also left two things to this spec: skipping a checkpoint that is not downloaded, and
whether weight sizes are read from the downloaded files.

## Requirements

1. **Files are declared in the manifest.** A node lists every file it reads from outside its
   folder and its runtime. For each file it gives:
    - its source:
        - a Hugging Face repository at a full commit hash, never a branch or tag;
        - or an `https` URL, such as a GitHub release asset or a vendor's server;
    - its path in that source, its size in bytes and its sha256;
    - optionally, the checkpoint it belongs to: a value of one of the node's choice params, as
      `weights_by` does in a memory model. A file with no checkpoint is needed by every run;
    - whether its source is gated, meaning the host requires the person to accept a licence on
      its site first. A gated file also gives the page where that is done.

   A manifest that gets any of this wrong is refused with every problem named, as today. Nothing
   outside a node's folder names a file, a repository or a host.
2. **One copy of each file, kept under the data root.**
    - All files live under `<data>/models/`, and two nodes that pin the same file share one copy.
    - Files from Hugging Face are kept in the Hugging Face cache layout, as real files rather
      than links. A library inside a node that looks in the Hugging Face cache by itself (a
      `from_pretrained(repo_id)` call, for example) then finds them with the network closed.

   The exact layout is open question 1.
3. **Download is explicit, resumable and checked.** `models.download` fetches a node's missing
   files, either every checkpoint or only the ones named:
    - it answers at once. Progress arrives as `model.*` events, in bytes, per file and in total;
    - Stop ends it. A partial file is kept and the next download resumes it with an HTTP range
      request. If the server ignores the range, the file starts again from zero;
    - each file is written beside its final name, and moved into place only when its size and
      sha256 match the pin. A file that does not match is not kept. The download then fails,
      naming the file, its source and both hashes;
    - before the first byte, the free disk on the data root is compared with what is left to
      fetch. If there is not enough room, the download is refused with both figures;
    - one download runs at a time, as runtime installs do.
4. **A file is present only once it has been checked.** A file is recorded as present after its
   sha256 matched, and only then. Each run checks that its files are still in place at their
   pinned sizes, without hashing them again. `models.verify` hashes them again when asked. A file
   that went missing, or whose size changed, is reported as such and is not used.
5. **Status, without the network.** `models.list` gives each node's files, grouped into those
   every run needs and those of each checkpoint. Each group has:
    - its total size, how much is present, and its status: not downloaded, partial, present or
      changed;
    - whether its source is gated;
    - the node's weights licence;
    - which of its files are pickles (`.pt`, `.pth`, `.ckpt`, `.bin`, `.pkl`), because loading a
      pickle can run code.

   It also lists files on disk that no node pins any more (a node moved to a new revision), with
   their sizes. Listing, planning, fitting and running never touch the network; only
   `models.download` does, and only the hosts of the node's own sources and their redirects.
6. **Runs use only files that are present.**
    - A runtime node whose needed files are not all present fails before its process starts.
      Its needed files are those every run needs plus those of the checkpoint it runs with. The
      failure has kind `model` and a reason: not downloaded, partial, or changed. It also gives
      the size left to download.
    - The fit never changes a node to a checkpoint that is not downloaded (left over from spec
      002). If the checkpoint the graph asks for, or the default, is not downloaded, the node
      fails with kind `model`. The engine never picks a different checkpoint just because it is
      the one on disk.
    - A node gets its files by name through `ctx`, and never builds a path itself.
    - Inside the runtime child, the Hugging Face cache points at the store and is marked offline.
      A library then reads from disk and never tries the network first.
    - A node's cache key includes the sha256 of every file its run reads. Moving a file to a new
      pin runs the node again; the same pins give the same key.
7. **Gated files fail clearly.** A gated source that refuses the download (401 or 403) fails with
   a message naming the page where the person accepts its licence. Tokens are open question 4.
8. **Network settings.** The engine honours the proxy environment variables
   (`HTTPS_PROXY`, `NO_PROXY`). `HF_ENDPOINT` changes the Hugging Face host, for a mirror. A
   failed download names the host and what it answered.
9. **Remove.** `models.remove` deletes a node's files, all of them or one checkpoint's. A file
   that another node also pins is kept, and the answer names that node. `models.remove` with no
   node deletes the files no node pins any more. Nothing outside `<data>/models/` is touched.
10. **Pins are written by a tool, not by hand.** `npm run models:pin -- <node>` reads each
    source at its pinned commit or URL and writes every file's size and sha256 into the node's
    manifest. A pinned value that differs from the source is reported, not silently replaced.
    Besides `models.download`, this tool is the only thing that touches the network for models.
    It runs where the hosts are reachable: on the owner's PC, not in a cloud session.

## Acceptance criteria

Every criterion but AC11 runs on the processor in CI against a local test server. The server
imitates the Hugging Face resolve and tree endpoints and a plain URL host, and can redirect to
a second host, honour or ignore range requests, serve altered bytes, and refuse with 401. Its
test node has a Hugging Face source with a file every run needs, two checkpoints (`small` and
`large`), and a URL source.

- [ ] AC1: The manifest check refuses each of these, naming every problem at once (unit tests):
    - a branch or tag where a commit hash belongs;
    - a missing or malformed size or sha256;
    - a URL that is not `https`;
    - a file path that is absolute or has a `..` step;
    - a checkpoint that is not a value of the named choice param, or names an unknown param;
    - two files that would land at the same place with different pins;
    - a gated source with no page to accept its licence.
- [ ] AC2: Downloading the test node puts every file in the layout of requirement 2, and
  `models.list` reports it present. Inside the tiny runtime, with the network closed, the node
  reads each file by name through `ctx`. `huggingface_hub`'s own offline lookup of the
  repository's file returns the stored file (integration test, CI on Windows and Linux).
- [ ] AC3: Resume (integration tests):
    - stopping a download midway keeps the partial file and records nothing as present. The next
      download sends a range request starting at the partial size (the server logs it) and
      completes;
    - with the engine killed midway, the same holds;
    - with a server that ignores the range, the file restarts from zero and completes.
- [ ] AC4: Integrity (tests):
    - a file whose bytes or size differ from its pin is not kept, and nothing is recorded as
      present. The failure names the file, its source and both hashes;
    - with too little free disk (a recorded figure), the download is refused before the first
      byte, naming what it needs and what is free.
- [ ] AC5: Runs check their files (integration tests in the tiny runtime):
    - before any download, the node fails with kind `model`, reason not downloaded, and the size
      to download, and no process starts;
    - after the download, it runs;
    - after a file is truncated, `models.list` reports it changed, and the run fails with kind
      `model`, reason changed;
    - `models.verify` catches a file altered at the same size.
- [ ] AC6: Checkpoints, with only `small` downloaded (unit tests of the fit, and an integration
  test):
    - a graph that sets `large` fails with kind `model`, naming `large` and its size;
    - with `large` as the default and not downloaded, the node fails the same way, and the
      engine does not switch to `small` because it is on disk;
    - a fit that would save memory by moving to a checkpoint that is not downloaded skips that
      change.
- [ ] AC7: Changing a file's pinned sha256 changes the node's cache key; the same pins give the
  same key (unit test).
- [ ] AC8: The network (tests with sockets blocked or logged):
    - `models.list`, `nodes.fit`, graph planning and a run make no network connection;
    - `models.download` connects only to the test node's source hosts and the host they redirect
      to;
    - the token-free gated source answered 401 fails with the page to accept its licence;
    - with `HF_ENDPOINT` set, requests go to that host.
- [ ] AC9: Remove (tests):
    - removing one of two nodes that pin the same file keeps that file and names the other node;
    - removing a checkpoint deletes only that checkpoint's files;
    - removing what no node pins deletes exactly those files;
    - nothing outside `<data>/models/` is touched.
- [ ] AC10: `npm run models:pin` against the test server fills in a node's sizes and sha256 from
  the tree endpoint and the URL host. A pinned value that differs is reported and left as it was
  (test).
- [ ] AC11 (local, needs the real hosts): On the owner's PC, with the network:
    - a test node pinned to a small public Hugging Face repository at a commit is pinned with
      `npm run models:pin`, and then downloaded;
    - it includes at least one file stored with Xet, and one URL source;
    - the download is stopped midway, then resumed to completion and verified;
    - the report gives each file's bytes, seconds and the hosts reached, and is committed under
      `specs/003-model-store/reports/`.

  This proves the test server matches the real hosts: redirects to a CDN, range requests on the
  redirected URL, and Xet-backed files served over plain HTTP. It needs no GPU.

## Out of scope

- Keeping models loaded between runs (spec 004).
- Real model nodes and their pins (spec 005).
- The Download button and a Storage page in the app (workspace UI spec), beyond the allowlist in
  open question 6.
- Placing files where torch hub looks (`TORCH_HOME`). A node whose library fetches with torch hub
  passes its file's path instead, until a chosen node cannot.
- Scanning pickles. The sha256 pin means the bytes are exactly the ones reviewed when pinning,
  and every runtime's torch is at least 2.6, whose `torch.load` defaults to `weights_only=True`.
  Pickles are flagged, not scanned (open question 5).
- Hugging Face datasets and Spaces, other hubs (ModelScope), and Xet's own accelerated client.
- Downloading several nodes at once.
- Passing the system proxy from Electron to the engine (packaging spec). The engine honours the
  proxy environment variables.

## Open questions

Answered by the owner before approval. Each has a recommendation.

1. **Layout: per node, or one shared store?** The handoff says `<data>/models/<node>`.
   *Recommended:* one store keyed by source, in the Hugging Face cache layout:
    - Hugging Face files at
      `<data>/models/hf/models--<org>--<name>/snapshots/<commit>/<path>`, with `refs/main`
      naming the commit;
    - URL files at `<data>/models/url/<sha256>/<file name>`;
    - a record of what each node pins, so `models.list` and `models.remove` can answer per node.

   Nodes that share a backbone (SAM 2.1, DINOv2) then share one copy, and libraries find their
   files offline with no code from us. The cost: if two nodes pin different commits of one
   repository, `refs/main` can name only one of them, so a library that looks up `main` by
   itself finds that one. A node that pins a commit of its own passes that commit explicitly.
   Per node is simpler to remove, but keeps duplicate copies and does not give libraries that
   offline lookup.
2. **Our own downloader, or `huggingface_hub` in the engine?** *Recommended:* our own, on
   `urllib` like `archives.download`:
    - the engine stays light, with no new dependency;
    - the pins are ours to check;
    - the Hub is reached through only two URLs: resolve, for the bytes, and tree, for the pin
      tool.

   `huggingface_hub` (2.1.1 on PyPI, checked 2026-10-02) would add httpx2, click, filelock,
   fsspec, pyyaml, tqdm and `hf-xet` to the engine, for a faster Xet path we do not need. AC2
   still uses it inside the tiny runtime, to prove its offline lookup finds our files.
3. **The pin tool in this spec?** *Recommended:* yes. Spec 005 needs it on its first day, and
   sha256s typed by hand are the most likely mistake in a manifest. It is tested against the
   test server here; against the real Hub it is part of AC11.
4. **Tokens for gated repositories: now or later?** *Recommended:* later, in the spec that adds
   the first gated node. None of the first models planned (Depth Pro, MoGe-2, SAM 2.1, TripoSR,
   Hunyuan3D-2mini) is believed to be gated; spec 005 confirms each when pinning it. Tokens need
   safe storage in the app's main process (Electron `safeStorage`), plus a way to set one that
   never passes through the page. That is a separate piece of design. Until then, a gated
   source fails clearly (requirement 7).
5. **Pickles: flag, or scan?** *Recommended:* flag in `models.list`, and add a rule to
   [nodes.md](../../docs/nodes.md): prefer `.safetensors`, and never pass `weights_only=False`
   for a downloaded file. A scanner (picklescan) blocks only what it already knows, and would
   add a dependency, while the sha256 pin already fixes the bytes.
6. **The page's allowlist.** *Recommended:* add `models.list`, `models.download`, `models.stop`
   and `models.remove` to `ENGINE_METHODS`, as spec 001 added `runtimes.*`, so the first page
   can offer Download. No secret passes through the page, given open question 4.
   `models.verify` stays off the list until there is a UI for it.
7. **Weight sizes from files.** Spec 002 left open whether the fit should read weight sizes from
   the downloaded files. *Recommended:* no. A file's size is a poor stand-in for memory, because
   of precision casts, tied weights and extra tensors kept in checkpoints. The memory model keeps
   its declared and measured figures. File sizes are used for disk and download only.
8. **Checking at each run.** *Recommended:* compare the size of each needed file at every run,
   and hash again only on `models.verify`. Hashing a 2 GB file takes seconds on every run;
   requirement 4 already hashes each file once, before it is kept.
