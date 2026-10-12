# Spec 003: the AC8 report's findings, run again on Windows: passed

2026-10-12 (all times UTC) · commit `e8b2986` (`main`, with the fixes from
[PR #21](https://github.com/cbgithub7/oneframe-lab/pull/21)) · no local code changes · the owner's
Windows PC

[AC8's report](2026-10-12-ac8-live-hosts.md) ended with three findings, which a cloud session then
fixed (the spec's Decisions taken). CI's Windows runner never reproduced the first two, so the
cloud session asked for them to be checked again on this PC, along with the model tools' new wait
for a held store. The machine-readable version of this report is
[2026-10-12-ac8-findings-local.json](2026-10-12-ac8-findings-local.json).

## Verdict

| Finding | Outcome | What was seen |
| --- | --- | --- |
| 1. The in-use test assumed Windows frees a killed process's lock at once | passed | `test_remove_is_refused_for_a_file_a_running_graph_reads_in_this_process_or_another`, which failed every time here before, passed 13 of 13: in two full runs and 11 runs alone |
| 2. A job's ending was sent before it let go of the store | passed | `test_verify_drops_a_changed_file_so_the_next_download_fetches_only_it` passed 13 of 13, both full runs included (it had failed under the full suite) |
| 3. `models:download` waits for a store another process holds | passed | printed the waiting line, carried on 62 ms after the holder let go, ended `model.done`; no network |
| 3. Ctrl+C on a live download, then the same download again at once | passed | MiDaS (1.58 GB, GitHub), two rounds: each second download resumed from the kept byte and its `sha256sum` matches the pin; in round 2 it met the store still held, printed the waiting line, and started 90 ms after the first's `model.stopped` |

## Finding 3: a held store

A throwaway script, run from the engine's environment, took the dev root's store lock
(`logs\locks\models.lock`) with `oneframe.files.FileLock`, as `test_model_pin.py`'s holder does. It
let go 3 s after a tool's numbered folder appeared under `cache\tmp\`, which a tool claims just
before it claims the store. While it held the lock, this ran in the Terminal panel (PowerShell):

```sh
npm run models:download -- --nodes specs/003-model-store/live live.moge2
```

| Time | What |
| --- | --- |
| 13:20:34.202 | the holder takes the lock |
| 13:21:44.344 | the tool starts (`cache\tmp\0` appears) and prints `Another process is using the model store; waiting up to 10 s for it.` |
| 13:21:47.378 | the holder lets go |
| 13:21:47.440 | the tool's journal opens |
| 13:21:47.447 | `model.start` |
| 13:21:47.448 | `model.done`: `weights: present` |

The tool waited about 3 s of its 10. The MoGe-2 file (1,323,815,904 bytes) was already in the dev
root's store from AC8's pins, which a read-only `Store.presence` check confirmed before the run, so
nothing was downloaded: the journal has no attempt and names no host. `cache\tmp\` was empty
afterwards.

Not run by hand: Ctrl+C while the tool waits, which
`test_ctrl_c_while_a_tool_waits_for_the_store_ends_it_with_nothing_started` covers and which passed
in both full runs here.

## Finding 3: Ctrl+C, then the same download again

The case finding 3 came from, asked for by the owner after the hold test: MiDaS v3.1's
`dpt_beit_large_512.pt` (1,581,966,003 bytes, a GitHub release asset), downloaded with the command
below into a new, empty root, in the Terminal panel (PowerShell, through npm), and stopped midway
with a Ctrl+C from the panel. The same command, with the same root, then started again from the
Bash tool, armed beforehand so that it started at once. Two rounds, each in its own root
(`D:\OneframeLab-retype`, `D:\OneframeLab-retype2`), 3.16 GB downloaded in all.

```sh
npm run models:download -- --data D:\OneframeLab-retype --nodes specs/003-model-store/live live.midas
```

| | Round 1 | Round 2 |
| --- | --- | --- |
| The second download started | after the panel saw the prompt back, as a person typing it again would | just before the Ctrl+C, to make it meet the store still held: round 1 had not |
| First: downloading from 0 | 13:58:25.964 | 14:11:41.634 |
| Second: started | 13:58:48.734 | 14:11:56.588 |
| First: `model.stopped` | 13:58:46.155 | 14:12:02.169 |
| Second: `model.start` | 13:58:50.291 | 14:12:02.259 |
| Waiting line | no: the store had been free for 2.6 s | yes: started 90 ms after the first's `model.stopped`, about 4 s into its 10 s wait |
| Bytes kept, and the resume's start | 870,921,799 | 1,113,307,213 |
| `checking` the partial | 3.2 s | 3.9 s |
| Second: `model.done` | 13:59:06.190 | 14:12:12.628 |
| One connection, first / second | 43.1 / 56.4 MB/s | 54.2 / 74.8 MB/s |
| Result | `downloaded`, exit 0, `sha256sum` matches the pin | the same |

In both rounds the resume asked `github.com` again and followed a fresh redirect, started at the
kept byte, and left no partial and an empty `cache\tmp\`. A job now sends `model.stopped` after it
lets go of the store (finding 2's fix), so round 2's waiting download started within 90 ms of it.

Not seen: the early prompt itself. In AC8, PowerShell's prompt came back before the tool ended in
2 of 5 Stops; here the tool's last lines (`stopped`, `model.stopped`) printed before the prompt in
both. Not measured: when the panel's Ctrl+C reached the download (the panel does not say), so how
long each Stop took is not known from this run.

## How it ran

The session ran in Claude desktop's Code tab. The checks, the repeated tests and the holder ran in
its Bash tool, and the download in its Terminal panel through npm, as the owner runs it.
`npm run diagnose` named `%LOCALAPPDATA%\OneframeLab-dev` as the dev root (no `\Packages\`), so the
lock the holder took and the one the tool claimed are the same real file.

The first try at running the holder in a Terminal panel tab was cancelled by the panel, which sends
Ctrl+C when it does not see a long command start within 2 s. The holder let go on that Ctrl+C at
once, before any tool ran, and was then run from the Bash tool instead. The second full pytest run
had ended before the holder that counted took the lock.

## Machine, tools and checks

- Windows 10 build 19045.6466. NVIDIA GeForce GTX 1070 (compute 6.1, 8,192 MiB, driver 582.66),
  not used. Other programs on the card: the desktop's usual graphics clients only (Explorer, the
  shell, Steam, Discord, Brave, Claude, Windows Terminal). Free disk on `C:` (the dev root): 341 GB;
  on `D:` before the MiDaS rounds: 143 GB.
- Node v24.21.0 (pinned 24.21.0), uv 0.12.20 (required ≥ 0.12.20), npm 12.0.1, git
  2.28.0.windows.1, Python 3.14.5 (engine).
- `npm run check` passed: eslint, tsc, 38 node tests, docs check ok.
- `npm run engine:check` passed: ruff, format and pyright clean; pytest 557 passed, 4 skipped in
  187.6 s. A second full run (`pytest engine -rs`): 557 passed, 4 skipped in 212.6 s. The skips
  are the same in both, and each is for Windows: two symlink tests (they need a privilege), the
  hardware reader's non-Windows path, and a SIGKILL test.

## Commands

```sh
npm install
uv sync --project engine --frozen
npm run diagnose
npm run check
npm run engine:check
uv run --project engine --frozen pytest <finding 1's test> <finding 2's test> -p no:cacheprovider   # 10 times, then once with -vv -rA
uv run --project engine --frozen pytest engine -rs -p no:cacheprovider
uv run --project engine --frozen python -I hold-store.py   # the holder, outside the repo
npm run models:download -- --nodes specs/003-model-store/live live.moge2
npm run models:download -- --data D:\OneframeLab-retype --nodes specs/003-model-store/live live.midas    # round 1: Ctrl+C midway, then again
npm run models:download -- --data D:\OneframeLab-retype2 --nodes specs/003-model-store/live live.midas   # round 2: again, started just before the Ctrl+C
sha256sum <each root>\models\blobs\9e\9e9e9007...
```
