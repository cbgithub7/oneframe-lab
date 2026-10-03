# 003: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. Drafted with the
spec; the order may change when spec 006 lands.

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
- [ ] 2. The files schema in manifests and its checks (AC1).
- [ ] 3. The store: blobs, records, partials, the store's format file (AC2's first half).
- [ ] 4. The transport on `http.client`: redirects, retries, stall, the stop watchdog, the TLS
  factory, proxies (AC3, AC4).
- [ ] 5. The download and verify jobs, their events and the journal; `models.download`,
  `models.verify`, `models.stop` (AC2, AC3, AC4).
- [ ] 6. Runs: the fit's present checkpoints, the `files` failure, `ctx.file`, `ctx.models` gone
  (AC5).
- [ ] 7. `models.list` and `models.remove` (AC6).
- [ ] 8. The pin tool and both command-line tools (AC7).
- [ ] 9. The live manifests under `specs/003-model-store/live/` for AC8 (Depth Pro, MoGe-2, and
  MoGe-3 with checkpoints `vitl` and `vitg`), and its local-session commands. MoGe-2 and MoGe-3 load
  the same way: one `model.pt` per repository, read with `torch.load(..., weights_only=True)`
  (microsoft/MoGe, `moge/model/v2.py`).
