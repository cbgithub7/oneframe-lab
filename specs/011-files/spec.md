# 011: Files, hashes, downloads and paths

Status: done (merged 2026-10-26 in [PR #44](https://github.com/cbgithub7/oneframe-lab/pull/44))
Owner approval: 2026-10-18, every open question answered as recommended
Changed after approval (from `/code-review`, 2026-10-18), approved by the owner 2026-10-23: AC1
  checks that no recorded hash changes, rather than that its test file is untouched, which the new
  signatures would break. Changed in task 8, approved by the owner 2026-10-25: requirement 7 and AC8
  allow the fix that lets the transport read GitHub's archives (its socket closed too soon).

The fifth of eight clean-up specs (007 to 014), from section 3 of the
[review of 2026-10-12](../../docs/reviews/2026-10-12.md) and the [roadmap](../../docs/roadmap.md)'s
"One way each" and transport items. It lands after 010, with every stop a `threading.Event` (008),
and before 012.

## Problem

AGENTS.md asks for one way to write, hash and download a file, and to name a path under the root.
Today there are several of each:

- A file's sha256 is computed in five loops (the cache's, two in `archives.py`, the store's, the
  transport's resume), and a drive's free space in two walks (the store's, the machine profile's).
- A move into place waits on Windows in two ways (about 1 s in `write_text`, 10 s with backoff in
  the store), and not at all for a source archive, its unpacked folder or a cache entry.
- Runtime sources have a downloader of their own, on urllib, with no retry, no resume and a Stop
  that waits for the next bytes. Its `opener` runs through the runtime manager and the install,
  though only `test_archives.py` passes one. `transport.fetch` is never called.
- Paths under the root are built by hand (`runtimes/`, `uv/`, `cache/objects`, a `tmp` fallback),
  and Python never reads the layout's `runtimes`, `uv_cache` and `uv_python`.
- The transport passed spec 003's tripwire (urllib3 past about 600 lines; it is 754), and nothing
  says why it is kept. Installs never get the test TLS context model jobs get.

## Requirements

1. **One file hash.** `files.sha256_file(path, stop=None, progress=None, digest=None)` is the only
   loop that hashes a file; `text_sha256` moves into `files.py` unchanged. No recorded hash
   changes: cache keys, a marker's hashes and a pin's partial name keep their values.
2. **One move into place, one free space.** `files.replace(source, target)` is the store's rename,
   waiting on Windows while another program holds the target, but never for an existing folder: for
   `write_text`, the store's `place` (still `os_refused`, with today's messages), a source and its
   unpacking, and a cache entry. `files.free_bytes(path)` serves the store, the machine profile and
   sources.
3. **One downloader.** A runtime source is fetched by the model store's transport into
   `downloads/<sha256>.part`, then moved into place: retried, resumed, Stop seen at once (open
   question 2). An archive already there whose sha256 matches is kept, as today, and a failure is
   still kind `runtime`, reason `install_failed`. `archives.py` keeps only unpacking.
4. **Sources are https** (open question 1): `runtime.json` refuses any other scheme.
5. **Downloads take their options one way.** `Runtimes(..., transfer=)` takes what model jobs take
   (a TLS context, a policy, a proxy), the engine passes the same to both, and `server.main(argv,
   transfer=)` lets a test start the engine as a process that trusts the test CA.
6. **Paths only from the layout.** A runtime's folder and the delete guard's root come from
   `Layout.runtimes`; uv's folders are `Layout.uv_cache` and `Layout.uv_python`, or under the folder
   `--uv-home` names (open question 3); `Layout.objects` (`cache/objects`) joins `layout.py`,
   `app/main/paths.js` and `contracts/layout.json`, and `Cache(objects, work)` takes both.
7. **The transport is kept and grows no further.** Its docstring records that the tripwire fired and
   why. Its only edits: the resume hashes with the helper, "the store's drive" becomes "the drive",
   and its socket stays open until a response's file is closed. Needing more (http, auth, parallel
   ranges, a new phase or Policy field) is a new spec that reopens urllib3.
8. **Nothing else a person sees changes:** paths on disk, events, kinds and reasons are as today.

## Acceptance criteria

- [ ] AC1: `git diff <its first commit> HEAD -- engine/tests/test_recorded_hashes.py | rg '^[-+].*[0-9a-f]{64}'`
  prints nothing at the PR: the test's calls may follow the new signatures, but every recorded hash
  keeps the value `test_recorded_hashes_keep_their_values` took from today's code.
- [ ] AC2: `rg -ln 'def (sha256_of|file_digest|hash_file|_rename|_replace)\b|urlopen|\.read\((CHUNK|1 << 20)\)|\.replace\(\w+\)|disk_usage' engine/src/oneframe`
  prints nothing outside `engine/src/oneframe/files.py`. Today it prints six other files.
- [ ] AC3: `rg -n '\b(data|root) / "(runtimes|uv|objects|tmp)"' engine/src/oneframe --glob '!layout.py'`
  prints nothing. Today it prints five lines, in three files.
- [ ] AC4: `test_a_dropped_source_download_resumes`: a source whose connection drops halfway is
  fetched whole in the same call, its second request asking for `Range: bytes=<half>-`. Fails
  today: nothing is retried.
- [ ] AC5: `test_stop_ends_a_stalled_source_download`: a Stop while the source sends nothing ends
  the fetch with `Stopped` while the hosts still hold the stall, as they record. Fails today: Stop
  is seen only when bytes arrive, or the read fails after 60 s.
- [ ] AC6: `test_a_stopped_source_download_resumes`: after a Stop halfway through a source, the next
  fetch of it asks for `Range: bytes=<half>-`. Fails today: the partial is deleted.
- [ ] AC7: `test_a_source_that_is_not_https_is_refused`: a `runtime.json` whose source is `http://`
  reads with a problem naming that source. Fails today: it is accepted. Only if the owner says yes
  to question 1.
- [ ] AC8: The transport grows no further: `git diff $(git merge-base origin/main HEAD) --
  engine/src/oneframe/models/transport.py`, pasted in the PR, touches only its docstring, an import,
  the resume's hashing, the two messages that name the store's drive, and its socket's close.

## Design

- **`oneframe/files.py`** gains `CHUNK`, `sha256_file` (raising the one `Stopped`), `text_sha256`,
  `replace` and `free_bytes` (the drive of a path's nearest existing folder). Going:
  `files._replace`, `cache.file_digest`, the store's `hash_file`, `CHUNK`, `PLACE_WAIT_S` and
  `_rename`, `archives.py`'s two loops, and the walks in `Store.free_bytes` and
  `hardware.disk_free_mb`, which call it. The two tree digests (a node's code, a runtime's
  stand-ins) stay as they are, on the helpers.
- **Sources.** One function of `runtime_install` fetches a source: it keeps an archive whose sha256
  matches, else calls `transport.fetch` with the install's `stop`, progress as `runtime.progress`,
  `retrying` as a `runtime.log` line and `files.free_bytes` of the downloads folder, then moves the
  partial into place. `DownloadFailed` and `OSError` become `InstallFailed`. `archives.download`,
  `sha256_of`, urllib and `opener` go.
- **Paths.** `Runtimes.__init__` decides uv's two folders once. `environment.uv_environment` (008's
  module) takes the two folders, not their parent, and `bench_fit` gives its caches both folders.
- **Tests.** The plain-http `SourceServer` goes: sources come from spec 003's hosts, which gain a
  fault serving a body with no length, as GitHub's archive host does, and record a stall still
  held. The engine's process tests start through `engine/tests/fixtures/serve.py`, which calls
  `server.main` with the test CA.
- **Docs that change:** `docs/architecture.md` (the Files, Archives and Model store rows),
  `docs/runtimes.md` (sources are https, resumed from a `.part` under `downloads/`),
  `docs/roadmap.md` (the five hashes, the rename retry, the downloader, paths from the layout and
  the tripwire go), and with (a) to open question 3 the data root's exceptions (`AGENTS.md`, or
  014's `outside` if it has landed). Docstrings: the transport's (the tripwire, one line),
  `runtime_install`'s (resume) and `models/__init__.py`'s (the engine imports the transport at
  start; only truststore waits for a download).
- **Risks:**
    - A recorded hash that changed would make every runtime out of date and every cached result
      run again; AC1 fixes the values before any code moves.
    - Windows refuses a move onto an existing folder as `PermissionError`; without the folder rule,
      the cache's race would wait the whole deadline. A test runs on the Windows runner.
    - `write_text` may wait up to the deadline, not about 1 s, while another process holds the
      file: only under contention, where the alternative is a failure.
    - On Windows truststore reads only the system's store, so the driver passes the test CA in.
    - GitHub's archive host sends chunks with no length and ignores ranges: a resume there starts
      again, and free space is unchecked; a body without length or chunks cut short fails its hash.
    - The engine stays light, as the heavy-modules test holds: truststore loads only to download.

## Tests

- Added:
    - `test_recorded_hashes.py`: `test_recorded_hashes_keep_their_values`, a case each for a node's
      code digest, a cache key with a file parameter, a runtime's hashes with CRLF files and a
      stand-in, and a pin's partial name;
    - `test_runtime_sources.py`: `test_a_dropped_source_download_resumes`,
      `test_stop_ends_a_stalled_source_download`, `test_a_stopped_source_download_resumes`,
      `test_a_source_without_a_length_that_ignores_ranges_is_fetched_whole`;
    - `test_files.py`: `test_a_move_onto_an_existing_folder_is_never_tried_again`,
      `test_free_space_is_read_from_the_nearest_folder_that_exists`;
    - `test_runtime_plan.py`: `test_a_source_that_is_not_https_is_refused`;
    - `test_runtime_install.py`: `test_uv_folders_are_the_layouts_unless_named`.
- Moved, names kept: `test_hashing_stops_between_chunks` to `test_files.py`; the three download
  tests of `test_archives.py` to `test_runtime_sources.py`, against the hosts.
- Changed, names kept:
    - tests calling or patching `_replace`, `_rename` or `hash_file` use `files.replace` or
      `jobs.sha256_file`; the store's placing test reads `os_refused` from `Store.place`, and
      `test_there_is_one_stopped` names the transport for `archives`;
    - the three install and three server tests with a source use the hosts, and a Stop in them no
      longer waits for the stall to be released;
    - every `Cache(...)`, `uv_environment(...)` and direct `install(...)` call passes uv's or the
      cache's two folders;
    - `test_every_problem_in_a_definition_is_listed_at_once` expects "https" in its source problem.
- Renamed: none.
- Removed:
    - `test_stop_during_a_download_keeps_nothing`: a stopped source now keeps its partial for the
      next install, the opposite claim; the resume test under Added replaces it.

## Verification

AC1 to AC3 and AC8 are checked in the PR; AC4 to AC7 run in CI on ubuntu-latest and
windows-latest, with every test above. No step needs a GPU or a local session. No shipped runtime
has a source, so the spec that adds one makes the first download from GitHub's real archive host.

## Decisions taken

- `sha256_file` carries on a caller's digest, so the transport's resume keeps one running hash.
- The two tree digests stay apart: one would change their order and bases, and so recorded hashes.
- One deadline for every move, `write_text`'s too: the store's 10 s, about what uv waits.
- A move onto an existing folder is never tried again: Windows can never replace a folder.
- `files.replace` reads `MOVE_WAIT_S` when called, so a test through `Store.place` can shorten it.
- Free space joins `files.py`: a source's check would otherwise be a third walk to the same answer.
- Sources reuse `transport.fetch`, and the transport stays in `oneframe/models/`: less churn.
- The transport is kept: it passed the real hosts (spec 003's AC8), and urllib3 saves ~15 lines.
- `get_json` stays in the transport: moving it would only chase a line count.
- Archives stay in `runtimes/<id>/downloads/`: in the store they would outlive their runtime.
- A source's partial is `downloads/<sha256>.part`, so removing the runtime removes it.
- Of the transport's phases only `retrying` reaches a person, as a log line; progress is as today.
- Fetching a source is one function, tested against the hosts without uv: fast, no Python fetched.
- The layout names folders by kind; what is inside a runtime's folder stays the install's.
- `Cache(objects, work)` has no fallback, which wrote among the processes' folders in `cache/tmp`.
- `Cache` keeps its attribute names, `objects` and `tmp`: fewer test edits.
- The process tests' driver passes `transfer` in, as `Engine` takes it: no new variable or flag.

## Out of scope

- The test guard, which lets a test move to another file under its name (007).
- Stop as one `threading.Event`, and `environment.py` (008); `Stopped`'s home (009).
- An install's ending sent only after its slot is let go (010).
- Splitting `runtimes.py` and `runtime_install.py`, and the install's long function (012).
- The tests' wall-clock bounds, and the killed-install test's second reply (014).
- urllib3, or any new capability of the transport (a later spec, if one ever needs it).
- The app's and the scripts' own writes, in JavaScript: unchanged.

## Open questions (answered by the owner, 2026-10-18)

1. **Must runtime sources be https?** No shipped runtime has a source; `runtime.json` accepts
   `http://`, which the transport refuses. *Answered: yes:* every example is https, and http
   would grow the transport.
2. **May sources behave as model downloads do?** Retried; resumed from a `.part` kept under
   `downloads/` after a Stop or a kill; Stop seen at once; TLS from the system's trust store; a 30 s
   stall window for a 60 s read timeout; a failure's text changes, not its kind or reason (a full
   disk says "the drive" for model files too). *Answered: yes:* these are the fixes the review
   asked for, and model files already work so.
3. **`--uv-home`** (the engine's and both benches') lets uv write outside the root without being
   one of AGENTS.md's exceptions: list it as a folder the person names, beside `--out` (a), or keep
   it to tests, passed in by the driver as `transfer` is (b)? *Answered: (a):* it is never a
   default, the app never passes it, and the tests share one uv cache through it.
