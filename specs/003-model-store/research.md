# 003: Research: how the best apps download, keep and serve model files

Checked on 2026-10-02. **The question:** how do the best applications get large model files onto a
person's machine, keep them, check them and hand them to the code that loads them, and what does
that mean for this spec? Six passes, each by reading code and docs at their current versions:

- the Hugging Face client (huggingface_hub 2.1.1, released 2026-10-01), its source and docs, with
  probes of its offline lookup against a store written by hand;
- the local AI apps: Ollama, LM Studio, ComfyUI and ComfyUI Desktop, InvokeAI, GPT4All, Jan,
  Stability Matrix, AUTOMATIC1111 and Foundry Local;
- desktop software that delivers large content after install: Steam, Visual Studio, Apple, Google,
  Windows ML, Blender, Topaz, Adobe and DaVinci Resolve;
- the engineering of a large, resumable download, with measurements in this container;
- content-addressed stores (Nix, pnpm, uv, OCI, git-lfs, DVC, Bazel, Homebrew), and the security
  of model files;
- a review of the draft against the engine code it changes.

huggingface.co, lmstudio.ai, learn.microsoft.com and several vendor sites are blocked from the
cloud container. Their docs were read from their GitHub sources where those exist. Anything that
could not be verified says so. AC11 settles what only the live hosts can show.

## Summary

No app does all of it well. The strongest share these habits:

1. **The content hash is the file's identity; a URL or a repository at a commit is only where to
   get it.** This holds for Nix's fixed outputs, Bazel's repository cache, Ollama's blobs, Windows
   ML's catalogue and Steam's chunks. A second consumer, or a new version that did not change a
   file, then never fetches its bytes again.
2. **Nothing is "present" until it has been checked.** Ollama, Jan and Stability Matrix hash
   every file before using it. Hugging Face's client checks only the size. ComfyUI Desktop, Foundry
   Local and ComfyUI-Manager check nothing, and their issue trackers show it.
3. **Downloads survive a bad network on their own.** The most common complaint across Ollama, LM
   Studio and ComfyUI is a large download that stops on a dropped connection and needs a person to
   start it again, sometimes from zero. The good ones retry with backoff, resume from the bytes on
   disk, detect a stall by lack of progress rather than a total time, and start each attempt from
   the original URL.
4. **The size is shown before anything is fetched,** with free space checked on the drive that
   will hold it (Steam, ChatRTX, Play Asset Delivery, Windows AI).
5. **The store can live on another drive,** and a file obtained elsewhere can be brought in by its
   hash (Steam, Visual Studio layouts, Blender's Install from Disk, LM Studio's `lms import`,
   Windows ML local catalogues).
6. **Cleanup is explicit and previewed.** Ollama deleting unused blobs at every start is the
   surprise to avoid. Bazel never cleans its cache by itself, because a file may no longer exist
   upstream.
7. **Failures carry a code and a next step** (Play's `AssetPackErrorCode`, Windows ML's
   `ExtendedError`, Hugging Face's `X-Error-Code`).

The draft got the pins right; they are stricter than any app surveyed. It got wrong where shared
files live (`refs/main`), and it left out retries, the store's location, import, and the
environment a run gets.

## Hugging Face's own client (huggingface_hub 2.1.1)

- **Cache layout.** `models--<org>--<name>/{refs, blobs, snapshots, trees, .no_exist}`, with
  `.locks/` and `CACHEDIR.TAG` at the root.
  - A blob is named by its etag: the sha256 for LFS and Xet files, but the git SHA-1 for small
    files kept in git (configs, tokenizers).
  - Without symlinks (Windows without Developer Mode), real files go into `snapshots/` and
    `blobs/` goes unused. The Hub's language-neutral cache spec says a snapshot entry counts "if
    it exists (as a symlink or file)"
    ([local-cache.md](https://github.com/huggingface/hub-docs/blob/main/docs/hub/local-cache.md)).
    So real files are a supported form.
- **Offline lookup, probed against a store of real files with `HF_HUB_OFFLINE=1`:**

  | Call | Result |
  | --- | --- |
  | `hf_hub_download(repo, f)` with no revision (reads `refs/main`), or with `revision=<commit>` | found |
  | `try_to_load_from_cache`, by `main` or by commit | found |
  | `snapshot_download(repo)` | found |
  | `snapshot_download(repo, revision=<commit>)` | fails (`OfflineModeIsEnabled`): a commit skips the offline fallback and asks the tree API |
  | the same with `local_files_only=True` | found |
  | a file not in the snapshot | `LocalEntryNotFoundError`, at once |

  Without the offline flag and with sockets blocked, each missing optional file costs about 23 s
  of retries before it fails. The offline flag is needed for speed, not only to be honest.
- **`refs/main` is shared by every user of the cache.** It names one commit per repository. A
  library that looks a repository up by name, with no revision, reads whichever commit `refs/main`
  names.
- **Resume and integrity.** Since 1.18.0 ([PR #4306](https://github.com/huggingface/huggingface_hub/pull/4306))
  each download writes a temporary file unique to its process and deletes it on failure. So the
  reference client **cannot resume after a Stop or a crash**
  ([#4196](https://github.com/huggingface/huggingface_hub/issues/4196), open).
  - Within one call it retries up to 5 times with a range request, with a 10 s read timeout.
  - It compares only the size; no sha256 is computed in cache mode.
  - Low disk space only prints a warning.
- **Resolve and Xet.** HEAD on `/resolve/<commit>/<path>` gives `X-Repo-Commit`, `X-Linked-Etag`
  (the sha256 of a large file), `X-Linked-Size` and `X-Xet-Hash`.
  - Small files answer with a 307 on the same host; large files with a 302 to a signed CDN URL.
  - Xet files still download over plain HTTP through the Hub's bridge
    ([legacy-git-lfs.md](https://github.com/huggingface/hub-docs/blob/main/docs/hub/xet/legacy-git-lfs.md)),
    and range requests work there.
  - How long a signed URL lives is unverified. The Xet spec calls such URLs "short-lived; do not
    cache".
  - Text files are sent gzip-chunked unless the client asks for `Accept-Encoding: identity`. With
    gzip accepted, CloudFront ignores range requests.
- **Pinning API.**
  - `/api/models/{repo}/revision/{rev}` gives the canonical id, the commit, and `gated`
    (`auto`, `manual` or false).
  - `/tree/{rev}?recursive=true` and `/paths-info/{rev}` give each file's size, git `oid` and,
    for large files only, `lfs.oid` (the sha256).
  - **Small git files have no published sha256.** A pin tool must download them and hash them.
  - `expand=true` adds `securityFileStatus`, the Hub's own antivirus and pickle scan.
  - `super_squash_history` makes old commits unreachable, so a pinned commit can disappear.
- **Errors.** The client tells cases apart by `X-Error-Code`, not by status:

  | Case | Status and code |
  | --- | --- |
  | Gated repository, no token | 401, `GatedRepo` |
  | Gated repository, token not approved | 403, `GatedRepo` |
  | Private or missing repository, no token | 401, `RepoNotFound` |
  | The pinned commit is gone | `RevisionNotFound` |
  | The file is not in that commit | `EntryNotFound` |

  A 401 alone does not mean "gated".
- **Rate limits** ([rate-limits.md](https://github.com/huggingface/hub-docs/blob/main/docs/hub/rate-limits.md),
  table dated September 2025). An anonymous IP gets 3,000 resolver requests per 5 minutes. A 429
  carries `RateLimit: "resolvers";r=0;t=55`, and the client waits `t` seconds.
- **Mirrors.** `HF_ENDPOINT` is read at import. hf-mirror.com sends the bytes back to Hugging
  Face's CDN. The Xet client ignores `HF_ENDPOINT`.

## The local AI apps

| App | Layout and sharing | Pin | Check | Resume and concurrency | Store location and import | Removal |
| --- | --- | --- | --- | --- | --- | --- |
| Ollama | `blobs/sha256-<hex>` plus manifests; shared | registry digest | full hash; newer path hashes while downloading | 16 parts of 100 MB–1 GB, state per part; 6 retries; 30 s stall | `OLLAMA_MODELS` | `rm` keeps shared blobs; **prunes unused blobs at every start** (1 h grace) |
| Hugging Face client | per repository, as above | commit | size only | retries within one call; **no resume across processes** | `HF_HUB_CACHE` | `hf cache rm`, `prune` |
| LM Studio | `<publisher>/<model>/<file>` | none | checksum at the end | pause and resume | folder setting; `lms import --copy/--hard-link` | from the app |
| ComfyUI Desktop | folders by type | URL | **none** | Electron pause, this session only | `extra_model_paths.yaml` | deletes |
| InvokeAI | `models/<random key>/`, SQLite | none | BLAKE3 as an id, never against a source | 5 at once; `Range` with `If-Range`; survives restart | folder setting | records and files |
| GPT4All | one folder | md5 and URL on `main` | md5, **with TLS checking off** | range, 10 retries | path setting | deletes |
| Jan | per model | optional sha256 | sha256, second pass | resumes only if the URL is unchanged | — | — |
| Stability Matrix | one shared folder; junctions or config per app | sha256 if given | sha256 after | state saved as JSON | shared folder | — |
| Foundry Local | `<publisher>/<model>` | catalogue entry | none found | 64 chunks per file; lock file across processes | `ModelCacheDir` | `cache remove` |

Sources: [Ollama download.go](https://github.com/ollama/ollama/blob/main/server/download.go) and
[images.go](https://github.com/ollama/ollama/blob/main/server/images.go);
[lms get.ts](https://github.com/lmstudio-ai/lms/blob/main/src/subcommands/get.ts);
[ComfyUI Desktop DownloadManager.ts](https://github.com/Comfy-Org/desktop/blob/main/src/models/DownloadManager.ts);
[InvokeAI download_default.py](https://github.com/invoke-ai/InvokeAI/blob/main/invokeai/app/services/download/download_default.py);
[GPT4All download.cpp](https://github.com/nomic-ai/gpt4all/blob/main/gpt4all-chat/src/download.cpp);
[Jan helpers.rs](https://github.com/janhq/jan/blob/main/src-tauri/src/core/downloads/helpers.rs);
[Stability Matrix SharedFolders.cs](https://github.com/LykosAI/StabilityMatrix/blob/main/StabilityMatrix.Core/Helper/SharedFolders.cs);
[Foundry Local download_manager.cc](https://github.com/microsoft/Foundry-Local/blob/main/sdk_v2/cpp/src/download/download_manager.cc).

What the issue trackers say:

- **Stopped downloads.**
  - LM Studio [#1031](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1031): a large
    download pauses after a timeout and waits for someone to resume it.
  - ComfyUI [#14419](https://github.com/Comfy-Org/ComfyUI/issues/14419): a 27 GB download
    restarts from 0% after every network drop.
  - Ollama [#8167](https://github.com/ollama/ollama/issues/8167): "max retries exceeded".
- **Files that fail at the end.** LM Studio
  [#1222](https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1222): a checksum error after
  a resume.
- **Pins that drift.**
  - Of ComfyUI-Manager's 564 model entries, 513 point at `resolve/main` and none has a hash.
  - GPT4All's md5 fails whenever upstream changes.
- **Hashing at the wrong time.** AUTOMATIC1111 hashes when a model is first used: 100–150 s per
  model switch ([#6738](https://github.com/AUTOMATIC1111/stable-diffusion-webui/issues/6738)).
- **Windows.** WinError 32 right after a download, most likely antivirus holding the file (InvokeAI
  [#8627](https://github.com/invoke-ai/InvokeAI/issues/8627)).
- **Moved folders.** A moved models folder is not found, and the app falls back to the default and
  downloads again (Ollama [#9597](https://github.com/ollama/ollama/issues/9597)).
- **Secrets.** InvokeAI saves the access token in plain text beside its resume state.

ComfyUI's frontend is worth copying in one detail: a workflow embeds
`models: [{name, url, directory, hash}]`, and the "missing models" dialog offers them before the
run. It knows a repository is gated only from `x-error-code: GatedRepo` together with
401/403/451.

## Desktop software that delivers content after install

- **Steam.**
  - Manifests list chunks of about 1 MB by hash, so an update fetches only the chunks that
    changed.
  - "Verify integrity" fetches again only what failed.
  - Space is allocated before the download.
  - Downloads queue one at a time; the queue can be reordered, and it survives a restart.
  - There is a bandwidth limit, and libraries can move to any drive.
- **Visual Studio Installer.**
  - `--layout` builds an offline copy, and `--noWeb` refuses anything that copy lacks.
  - `--verify` and `--fix` repair; `--clean` removes old versions.
  - The install location can be chosen only at first install, which is a known pain point
    ([locations](https://raw.githubusercontent.com/MicrosoftDocs/visualstudio-docs/main/docs/install/change-installation-locations.md)).
- **Apple Background Assets** ([AssetPackManager](https://developer.apple.com/documentation/backgroundassets/assetpackmanager)).
  - Download state and freshness are separate axes: downloading and downloaded, against up to
    date, update available and obsolete.
  - "An asset pack is available locally only after a finished status update is posted."
  - Apps reach files by relative path through the framework, never by building paths themselves.
  - Size on disk is reported apart from the download size.
- **Google Play Asset Delivery and ML Kit.**
  - Sizes are known before any download.
  - The states include `WAITING_FOR_WIFI` and `REQUIRES_USER_CONFIRMATION`.
  - `download()` returns at once when the content is present.
- **Windows ML model catalogue** ([schema](https://raw.githubusercontent.com/MicrosoftDocs/windows-ai-docs/docs/docs/new-windows-ml/model-catalog/model-catalog-source.md)).
  - Each file carries a sha256 "for integrity verification and de-duping of identical models".
  - A licence and its link are required.
  - A catalogue can be a local file, which allows offline installs.
- **Blender.**
  - Online access is off by default.
  - "Install from Disk" works offline.
  - The extension index pins `archive_hash: "sha256:…"`.
  - Asset libraries judge freshness by size plus mtime between full hashes.
- **Topaz** shows what goes wrong without a store.
  - Models download at processing time, sometimes again on every export.
  - Moving the model folder in Photo AI means editing the registry.
  - Offline use needs scripts run from a terminal.
- **Patterns.**
  - Size before consent.
  - Reuse by hash.
  - A store that can move.
  - Offline import checked by hash.
  - Verify that repairs only what failed.
  - States for queued, waiting, checking and transferring.
  - Explicit cleanup.
  - No accounts.

## Downloading large files reliably

Measured in this container, unless a source is named.

- **Resume.**
  - **Accept a resumed answer only if it is a 206 whose `Content-Range` starts at the partial
    size.** Restart from zero on anything else. pip 26.2 raises on a wrong start ("the server is
    misbehaving",
    [download.py](https://github.com/pypa/pip/blob/main/src/pip/_internal/network/download.py)).
  - A 416 on a partial that is already full size means "now hash it".
- **`If-Range` cannot be relied on.** GitHub's release-asset host (Azure Blob) answered 206 to a
  range request with a wrong ETag in `If-Range`. Only our own pins catch a changed source. Every
  206 and 200 carries the total size, so **comparing it with the pinned size on the first
  response** fails a changed source before 10 GB arrive.
- **Signed redirects expire.** A GitHub release asset redirects to a signed URL. The first check
  read its token as living 1800 s. The second round (below) found it now carries a token that lives
  **300 s**: reused after that, the URL answered `618 jwt:expired` with a short HTML body. A 10 GB
  file at 5 MB/s takes 33 minutes.
  - Hugging Face's resolve URL redirects to signed CDN URLs too (lifetime unverified).
  - Hugging Face's client, Ollama and Chromium all reuse the signed URL across retries.
  - The robust rule costs nothing: **every attempt starts from the source URL** and follows its
    redirects again. 3,000 resolver requests per 5 minutes leave plenty of room.
- **urllib** (CPython 3.14):
  - sends `Accept-Encoding: identity`, which keeps range requests honest;
  - forwards `Range` across redirects, which resume needs;
  - **also forwards `Authorization`** unless it was added with `add_unredirected_header`;
  - **follows https→http redirects.**
- **Retries and stalls.**

  | Tool | Retries | Backoff | Stall or read timeout |
  | --- | --- | --- | --- |
  | uv | 3 | exponential, 1–30 s with jitter | 30 s |
  | Hugging Face client | 5 | — | 10 s read timeout |
  | Ollama | 6 | 2^n s | 30 s stall, which does not use up a retry |

  - The Hugging Face client resets its retry count whenever bytes arrive.
  - `urlopen(timeout=60)` is an idle timeout per read, so a trickle never trips it. A stall needs a
    no-progress window.
  - A total time limit on a 20 GB file is wrong.
- **Stop.**
  - On a stalled connection, closing the response from another thread did **not** unblock the
    read; it hung until the server closed 30 s later.
  - Shutting the socket down unblocked it at once.
  - `archives.download` has this weakness today.
- **Hashing** (Xeon 2.1 GHz, Python 3.14.7, OpenSSL 3.5.8):

  | Hash | Throughput |
  | --- | --- |
  | sha256 | 1.37 GB/s |
  | sha256 without the CPU's SHA instructions (older Intel desktops lack them) | 0.38 GB/s |
  | blake2b | 0.74 GB/s |
  | BLAKE3 | 5.8 GB/s on one thread |

  - Verifying 10 GB is about 7–26 s of CPU, or about 20 s from a SATA SSD and about 70 s from a
    hard disk.
  - `hashlib` releases the GIL, so hashing while downloading is free.
  - **A hash object cannot be saved** (`TypeError: cannot pickle '_hashlib.HASH'`). A resume after
    a restart must read the partial file again.
  - Both hosts publish sha256: Hugging Face's `lfs.oid`, and GitHub's asset `digest` since
    [2025-06-03](https://github.blog/changelog/2025-06-03-releases-now-expose-digests-for-release-assets/).
    BLAKE3 would buy nothing.
- **Parallel ranges.**
  - [xet-core #821](https://github.com/huggingface/xet-core/issues/821) (April 2026) measured
    65–75% of Hugging Face's CloudFront edges capping one connection at **8.7 MB/s**; the rest
    gave 67–69 MB/s. For 10 GB that is about 19 minutes against 2.5.
  - Ollama uses 16 parts in a sparse file with per-part state; Foundry Local up to 64 chunks; aria2
    one connection per server by default.
  - The costs:
    - sparse files on NTFS, which Python reaches only through ctypes;
    - a second hashing pass, because parts arrive out of order;
    - per-part state;
    - more 429s.
- **Windows file system.**
  - Renames that fail because antivirus or the indexer holds the new file are retried: uv for
    about 10 s, pip for 1 s, npm's graceful-fs for up to 60 s.
  - A file that is open or memory-mapped (safetensors maps) cannot be deleted or replaced.
  - Paths: `%LOCALAPPDATA%\…\models\hf\models--tencent--Hunyuan3D-2mini\snapshots\<40 hex>\…` is
    about 190–212 characters. Past 259, the library in the runtime child fails unless
    `LongPathsEnabled` is set; it does not add `\\?\` itself.
- **TLS and proxies.**
  - Python's `ssl` on Windows does read the Windows root stores. It misses roots Windows has not
    yet fetched, does not fetch missing intermediates, and does not check revocation.
  - **truststore** (0.10.4, pure Python, no dependencies) verifies through the operating system,
    and pip uses it by default.
  - uv uses bundled roots unless told to use the system's, so runtime installs and model
    downloads could trust different roots.
  - urllib reads the proxy variables, and on Windows the static proxy in the registry. It does not
    support PAC, WPAD or NTLM proxy authentication.
- **Electron's downloader**, considered and rejected.
  - Its strengths are PAC and proxy authentication.
  - `DownloadItem.resume()` discards bytes unless the server sends both an ETag and a
    Last-Modified, and it resumes against the expired final URL.
  - The engine would then verify a file the shell wrote.
  - The command-line tools and tests run without Electron.
  - The packaging spec can pass `session.resolveProxy`'s answer to the engine instead.

## Content-addressed stores

| Tool | Entry | What keeps an entry | Lesson |
| --- | --- | --- | --- |
| Nix | `<hash>-name`; a fixed output is named by its declared hash | GC roots | a new URL with the same hash is not fetched |
| Bazel | `content_addressable/sha256/<h>` | **never cleaned by itself**: the file may be gone upstream | a changed definition that needs the same file downloads nothing |
| OCI, containerd | `blobs/sha256/<hex>` | reachability from refs; leases protect what is being written | never collect what is in use |
| Ollama | `blobs/sha256-<hex>` | mark and sweep at start, **skipped if any manifest is corrupt** | refuse to collect on unreadable references |
| git-lfs, DVC | objects by hash | refs; both warn that a shared cache can lose another project's files | GC needs every consumer's roots |
| pnpm, uv | content store, hard links on Windows | prune | a hard-linked file *is* the stored file: protect it from writes |

On Windows, hard links need NTFS and the same volume but no privilege. Symlinks need Developer
Mode or elevation. Python 3.14's `shutil.copy2` uses `CopyFile2`, which clones blocks on ReFS and
Dev Drive.

## The security of model files

- **Pickles.**
  - [CVE-2025-32434](https://github.com/advisories/GHSA-53q9-r3pm-6pq6) ran code through
    `torch.load(weights_only=True)` up to torch 2.5.1.
  - **[CVE-2026-24747](https://github.com/advisories/GHSA-63cw-57p8-fm3p)** (High, 2026-01-26):
    the `weights_only` unpickler could corrupt memory and possibly run code **before torch
    2.10.0**. This repo's floor is still 2.6 (`TORCH_FLOOR` in `runtimes.py`). The torch runtime
    pins 2.14.0, so nothing is exposed today, but the floor is out of date.
  - `TORCH_FORCE_WEIGHTS_ONLY_LOAD=1` makes `weights_only=True` hold even when the caller passes
    False.
- **Scanners block only what they know, and are bypassed repeatedly.**
  - picklescan: CVE-2025-1716 and CVE-2025-10155/6/7. The last is a pickle named `.bin`, so
    flagging by extension is exactly what it exploits.
  - nullifAI got past Hugging Face's own scan.
  - [arXiv 2508.19774](https://arxiv.org/abs/2508.19774) finds 19 of 22 pickle loading paths
    missed.
  - fickling was bypassed in 2025–2026.
- **"Safe" formats can still run code:**
  - Keras configs ([CVE-2025-1550](https://towerofhanoi.it/writeups/cve-2025-1550/));
  - GGUF chat templates;
  - Hydra `_target_` in metadata, including safetensors metadata (Unit 42, January 2026);
  - `trust_remote_code`, whose `auto_map` can name another repository at `main`, unpinned.
- **Model namespace reuse** (Unit 42, September 2025). A pinned commit plus a sha256 defeats it
  if it happens after we pin. It does not help if it happened before.
- **Signing.** OpenSSF model-signing v1.0 shipped in April 2025, and NVIDIA's NGC signs models.
  **Hugging Face publishes no signatures.**
- **This repo's run guard is not a sandbox.**
  - `forbid_network` patches `socket.connect`, which code in a malicious pickle can undo.
  - It lets loopback through.
  - `child_env` passes the person's whole environment, including any `HF_TOKEN`, `HF_HOME` or
    `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD`.
  - The child's docstring says hub libraries "are told they are offline", but nothing sets
    `HF_HUB_OFFLINE`.
- **Tokens.**
  - Hugging Face's token file is plain text.
  - Electron's `safeStorage` uses DPAPI on Windows. On Linux it can fall back to a hard-coded
    password. Its sync API goes away in Electron 46.
  - keytar has been unmaintained since 2022.

What a commit plus a sha256 protects against:

- upstream changes after pinning: new commits, force-pushes, replaced assets, stolen accounts,
  namespace reuse;
- tampering by a CDN, mirror or proxy;
- corruption in transit;
- drift between machines.

What it does not protect against:

- harmful content that was there when we pinned;
- bugs in loaders;
- code reached through configs;
- a source that disappears;
- tampering on the person's own disk;
- a payload that does run.

## The draft against the engine

Six findings from the review would have bitten the plan.

1. **Pins are read from the live registry.** A node whose manifest fails to load goes into
   `registry.problems` (`registry.py`), so its files would look unpinned. "Delete what no node
   pins" would then offer its weights for deletion after one typo.
2. **"Checkpoint" was undefined.**
   - A file could belong to only one checkpoint.
   - Nothing tied the checkpoint param to `weights_by` in `memory.py`.
   - "Fail if the default is not downloaded" did not say what happens when the fit would move to
     the downloaded checkpoint anyway.
3. **The new failure had no place.** The scheduler catches only `NodeError`, `RuntimeMissing` and
   `ValueError`; a cached result is served before any check; and the kind name `model` sits next
   to `missing`.
4. **AC8 could not fail.**
   - Nothing sets `HF_HUB_OFFLINE`.
   - The child lets loopback connections through, so a library could download from the local
     test server during a run and still pass.
   - `child_env` passes `HF_ENDPOINT` and `HF_TOKEN` on.
5. **The criteria contradicted themselves.** AC1 refused URLs that are not `https`, while the test
   server is plain HTTP on loopback.
6. **`refs/main` breaks the cache key.** With two commits of one repository pinned, a node whose
   library looks up `main` reads the other node's bytes. Its result is then cached under its own
   pins: a wrong result under a correct-looking key.

Also found:

- The node API was unspecified, and `ctx.models` invites code to build its own paths.
- Nothing said what happens when a run and a remove or download touch the same file.
- Requirements had no criterion: the events, the proxy, the `models.list` fields and the
  allowlist.
- Adding `huggingface_hub` to the tiny runtime would add tqdm, which turns on the child's tqdm
  patch, and a compiled dependency, to every runtime test.
- There was no way to download without the app, yet spec 005 needs weights on disk before
  `bench:fit`.

How spec 005's first models load their weights:

- **Depth Pro, MoGe-2 and SAM 2.1** take a local checkpoint path.
- **Hunyuan3D-2mini** has `from_single_file` and its own `HY3DGEN_MODELS` layout.
- **TripoSR** takes a local folder, but fetches `facebook/dino-vitb16/config.json` itself, with no
  revision. That is exactly the `refs/main` case.

Diffusers-based view synthesisers, later, load by repository id. The Hugging Face layout earns
its place, but only if `refs/main` cannot be wrong.

## What follows for this project

The spec is revised from these points. The ones that are the owner's choice are its open
questions.

1. **One copy of each file, named by its sha256.** A file's identity is its size and sha256; the
   repository at a commit, or the URL, is only where to fetch it.
   - Each runtime sees its nodes' files through a view in the Hugging Face layout, made of hard
     links. The view is per runtime rather than per node, because spec 004 keeps one long-lived
     worker per runtime, and the hub's cache path is fixed per process. Within one runtime, every
     node pins the same commit of a repository, so that view's `refs/main` is always right.
   - A file used by several nodes or commits is stored once. A new pin that did not change a file
     fetches nothing.
   - Where the volume cannot link, a copy is made, and the list says so.
2. **Partials live apart, keyed by sha256.** A partial download is kept by its sha256 outside every
   view, so a library never sees it and two nodes share it.
3. **Downloads heal themselves.** Requirement 3 now covers:
   - automatic retries with backoff and a stall rule, with no total time limit;
   - a fresh redirect on every attempt;
   - the pinned size checked on the first response;
   - strict 206 handling;
   - https only, for the source and every redirect;
   - Stop within a second;
   - a lock across processes;
   - rename retries on Windows;
   - the free-space check on the store's drive.

   It names the plain values (a stall window, a retry count) as the plan's to set, with reasons,
   tuned to no machine.
4. **The store's location is a setting.** If it is missing (an unplugged drive), the store
   reports itself offline and never falls back to the default.
5. **Import by hash** (`models.import`). A file or folder the person already has, such as one
   downloaded in a browser, copied from another PC, or in an existing Hugging Face cache, is
   hashed and copied in when it matches a pin. This is the offline path, the gated path until
   tokens arrive, and the answer when a source disappears.
6. **Verify repairs.** A file that fails is dropped from present, so the next download fetches
   only it. Each run compares size and mtime, which costs a stat.
7. **A queue instead of a refusal.** One transfer at a time. The queue is not saved: nothing
   downloads after a restart unless the person asks.
8. **Phases and reasons.**
   - Events carry phases: queued, checking (re-reading a partial), downloading, retrying,
     verifying and placing.
   - Failures carry a stable reason, whether a retry could help, and the next step.
   - Gated is told apart from not found by `X-Error-Code`.
9. **Removal is safe.**
   - It is refused for files a run is using.
   - It deletes partials.
   - Files no node pins are deleted only from the exact list the person confirmed, never while
     the registry has problems, and never automatically.
10. **The run's environment is set by the engine, last.**
    - The hub's cache points at the runtime's view, offline.
    - `HF_HOME` is under the data root.
    - Hub tokens and endpoints are removed.
    - `TORCH_FORCE_WEIGHTS_ONLY_LOAD=1` is set.
    - A runtime definition cannot override any of these.
11. **The pin tool hashes what it fetched,** reads each file's format from its content, records
    whether the source is gated and its canonical id, and reports files that carry code.
12. **Not in this spec:**
    - parallel ranges (decided from AC11's measured speeds);
    - a bandwidth limit;
    - moving the store with a button;
    - tokens;
    - scanning;
    - signatures;
    - a sandbox stronger than the socket guard;
    - archives as weights.
13. **The torch floor goes to 2.10** (CVE-2026-24747). It is a one-line change to the runtime
    manager, best made as its own small fix.

## Second round (2026-10-02)

The owner accepted every recommendation, asking that the downloader decision be checked again.
The second round also re-reviewed the spec; the project-wide findings are in
[the review of 2026-10-02](../../docs/reviews/2026-10-02.md).

**The downloader, checked again: the decision holds, built on `http.client`.** Each option was
weighed against requirements 3, 4 and 8, from the libraries' source and from spikes against a
local server over HTTP and TLS. All spikes ran on Linux.

| Option | Why not, or why |
| --- | --- |
| Standard library, our own | **Chosen.** A 157-line prototype on `http.client` passed every case, as listed below. No dependency. |
| urllib3 2.8.0 | Saves about 15 lines. It still follows https→http redirects and reads no proxy variables. **The fallback** if our transport passes about 600 lines or keeps finding protocol bugs. |
| httpx 0.28.1, httpx2 2.13.1 | Ask for gzip by default, which can make CloudFront ignore ranges. Closing does not unblock a read. httpx has had no stable release since 2024-12. |
| niquests 3.21.2 | Installs a fork of urllib3 under urllib3's own import name. |
| huggingface_hub 2.1.1 | No resume across processes. No sha256. It reuses the signed URL across retries, and appends a 206 that starts at the wrong offset. |
| hf_xet 1.6.0 | No resume after a stop: the next download rewrites from byte 0, with the chunk cache off. No sha256. It does open 4 to 64 connections, so it is the candidate if AC13 shows the per-connection cap. |
| aria2 1.37.0 | GPL; last release 2023-11; no https-only rule. |
| curl 8.22.0 | A binary per OS, with no pinned-size or sha256 check. |
| Electron net | Splits the store across two processes and two languages; the CLI tools and tests would need Electron. |

What the prototype passed:

- a drop at 40%;
- a stall, with a 2 s window;
- Stop during a stall, in 1.01 s;
- a 206 at the wrong offset, and a 200 to a range request;
- a half-full partial, and a full partial answered with 416;
- a size mismatch failing before the body, and a hash mismatch;
- a refused http redirect.

Findings that change the spec:

- **Stop needs a watchdog,** whichever library is used. Closing a stalled response unblocks
  nothing in urllib, urllib3 or httpx, while shutting the socket down unblocks all of them.
    - The read then ends differently by library: an empty read over HTTP, `BrokenPipeError` over
      TLS. So a stop is recognised by its own flag, and a dropped connection by counting bytes
      against the total.
    - A read timeout is final, so it cannot be used to poll for Stop.
    - **This is the one behaviour not yet verified on Windows**, so AC3's Stop case must pass on
      the Windows runner.
- **An unusual status from a redirect target** (GitHub's `618 jwt:expired`) means ask the source
  URL again. Nothing is written before the status is checked.
- **Speed is not the client's problem.** One stream ran at 1.1 GB/s over plain HTTP and about
  530 MB/s over TLS on loopback, far above any CDN's speed per connection.

What the second review found in the spec: four blockers, all fixed by rewording, and none
reversing a decision.

- the views had no location, lifecycle or rule for removal;
- "redirects only to https" contradicted the loopback test server;
- nothing names the checkpoint param without `weights_by`;
- the methods and events were undefined, and slow work blocked the engine.

The review lists them, with the should-fix items and what to cut. The owner then cut the spec to
its core (the review's decision 2), and it was rewritten on 2026-10-03 with the blockers fixed.

