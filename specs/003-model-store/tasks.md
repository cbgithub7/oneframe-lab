# 003: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. Revised on
2026-10-05 for spec 006 as built. A new kind or reason goes into `oneframe/errors.py`, with its
retry, and into the table in `docs/architecture.md` in the task that first raises it; a doc that a
task makes stale is updated in that task.

- [ ] 1. Test server and test CA: Hugging Face's resolve, revision, tree and paths-info endpoints,
  a URL host, a redirect host with single-use URLs, all over TLS from a committed CA. Faults it can
  inject, each used by AC3 or AC4:
    - a connection dropped at a given byte, and a stall before the headers and mid-body;
    - a 206 at the wrong offset, a 200 to a range request, a 416 on a full partial;
    - a first response whose size differs from the pin, and altered bytes;
    - 429 with `Retry-After` and with `RateLimit`;
    - 401, 403 and 404 with `X-Error-Code` (`GatedRepo`, `RepoNotFound`, `RevisionNotFound`,
      `EntryNotFound`);
    - a redirect to `http`;
    - a recording CONNECT proxy (the test clears `NO_PROXY`).
- [ ] 2. The files schema in manifests and its checks, a checkpoint param that does not affect
  output included (AC1); files in the manifest documented in `docs/nodes.md`.
- [ ] 3. The store (AC2's first half, AC6's newer store):
    - its paths named in `oneframe/layout.py`, `contracts/layout.json` and `app/main/paths.js`;
    - blobs, partials, the format file and records, written through `files.write_json`; a newer or
      unreadable store or record handled as requirement 2 says;
    - on Windows, placing a blob retries its rename for longer than `files._replace` does (an
      antivirus holds a new file for seconds; research.md), and every delete clears read-only first;
    - kind `store`; a rename or delete the system still refuses is `os_refused`, never kind
      `engine`.
- [ ] 4. The transport on `http.client`: redirects, retries, stall, the stop watchdog, the TLS
  factory, proxies (AC3, AC4).
- [ ] 5. The download and verify jobs (AC2, AC3, AC4):
    - `models.download`, `models.verify`, `models.stop`; a job id on every `model.*` event; each
      file's outcome in the ending event; a second job refused, `busy` here and `locked` from
      another process;
    - their journal, opened where the job starts, with its crash line;
    - `Engine.shutdown` stops a running job and waits for it with the run and the install, so its
      journal ends with `model.stopped`.
- [ ] 6. Runs (AC5):
    - the fit's present checkpoints, the `files` failure (a `Failure` the scheduler catches, its
      `next` naming the command when a download would help), `ctx.file`, `ctx.models` gone, and
      `docs/nodes.md` to match;
    - one scheduler factory that the engine and `bench_fit.py` both use, so `bench:fit` reads files
      too;
    - the cache key's `node.json` part taken from the bytes the manifest was parsed from;
    - each process lists the files its run reads in its own folder under `cache/tmp/`.
- [ ] 7. `models.list` and `models.remove`: remove refused for a file any process's run lists,
  verify's drop of such a file only its record (AC6, a graph in another process included).
- [ ] 8. The pin tool and both command-line tools (AC7): the root opened and its journal kept as
  006's tools do, Ctrl+C as a Stop. One launcher starts uv for every tool that opens a data root
  (`bench:runtime`, `bench:fit`, `diagnose` and the two new ones), with its temporary folder under
  the root but outside `cache/tmp/`, whose sweep deletes what it does not know. `AGENTS.md`'s
  Commands gains the two tools.
- [ ] 9. The live manifests under `specs/003-model-store/live/` for AC8: stub runtime nodes for
  Depth Pro, MoGe-2, MoGe-3 (checkpoints `vitl` and `vitg`) and MiDaS v3.1's
  `dpt_beit_large_512.pt`. MoGe-2 and MoGe-3 load the same way: one `model.pt` per repository,
  read with `torch.load(..., weights_only=True)` (microsoft/MoGe, `moge/model/v2.py`; v3's model
  inherits it). MoGe-3's code also imports FlexGEMM. Only the files matter here.
- [ ] 10. Docs: `docs/architecture.md` (the store, `models.*` not yet on the page's method list)
  and the handoff.
