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
in both full runs here. Nor was the case finding 3 came from: Ctrl+C on a live download, then the
same download typed again at once. That needs a real download.

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
  shell, Steam, Discord, Brave, Claude, Windows Terminal). Free disk on `C:`: 341 GB.
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
```
