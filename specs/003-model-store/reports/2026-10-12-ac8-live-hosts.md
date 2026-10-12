# Spec 003's AC8 against the real hosts, on Windows: passed

2026-10-12 (all times UTC) · commit `2608782`: `main` at `cf92123`, plus the pins this run
wrote · no local code changes · the owner's Windows PC, no proxy

AC8 asks whether the test server matches the real hosts. The four live manifests in
[`live/`](../live/README.md) were pinned with `npm run models:pin`. Each was then fetched with
`npm run models:download -- --all` into a new, empty data root (`D:\OneframeLab-ac8`), stopped
midway with Ctrl+C, and run again until done. The machine-readable version of this report is
[2026-10-12-ac8-live-hosts.json](2026-10-12-ac8-live-hosts.json), which has every attempt with
its times.

## Verdict

| AC8 asks | Outcome | What was seen |
| --- | --- | --- |
| Pin the five files with `models:pin` | passed | sizes, sha256s, `format` (all `pickle`), `gated: false` and full commits written; committed in `2608782` |
| Fetch them with `models:download -- --all` | passed | 11,294,811,330 bytes in the new root's store, five blobs, read-only, no partials left |
| Each download stopped midway, resumed, completes and verifies | passed | 5 Stops (MoGe-3 twice, once in each checkpoint); every Stop ended in `model.stopped` with its partial kept; every resume started at the kept byte and ended `downloaded`; `sha256sum` of each blob matches its pin |
| The GitHub asset resumed more than 3600 s after its Stop | passed | stopped 00:58:22.213, resumed 02:02:37.686: **3,855 s**; the resume asked `github.com` again and followed a fresh redirect |
| Bytes, seconds and MB/s on one connection; hosts; longest path | below | |

## Each file, on one connection

The download into the empty root. A connection is timed from the file's `downloading` event to
its `verifying` event, which follows the last byte. A stopped one is timed to `model.stopped`, and
its bytes are the partial that the next run's `checking` event found. MB is 10⁶ bytes.

| File | Size (bytes) | Hosts | Before the Stop | After the resume |
| --- | ---: | --- | --- | --- |
| Depth Pro `depth_pro.pt` | 1,904,446,787 | `ml-site.cdn-apple.com` | 677,789,696 B, 17.5 s, 38.8 MB/s | from 677,789,696: 1,226,657,091 B, 25.7 s, **47.7 MB/s** |
| MoGe-2 `model.pt` | 1,323,815,904 | `huggingface.co` → `us.aws.cdn.hf.co` | 728,380,593 B, 10.4 s, 69.9 MB/s | from 728,380,593: 595,435,311 B, 8.5 s, **70.3 MB/s** |
| MoGe-3 `vitl` | 1,481,333,394 | `huggingface.co` → `us.aws.cdn.hf.co` | 740,357,244 B, 11.7 s, 63.5 MB/s | from 740,357,244: 740,976,150 B, 10.1 s, **73.3 MB/s** |
| MoGe-3 `vitg` | 5,003,249,242 | `huggingface.co` → `us.aws.cdn.hf.co` | 2,279,837,566 B, 39.3 s, 58.1 MB/s | from 2,279,837,566: 2,723,411,676 B, 35.6 s, **76.6 MB/s** |
| MiDaS `dpt_beit_large_512.pt` | 1,581,966,003 | `github.com` → `release-assets.githubusercontent.com` | 864,663,195 B, 9.9 s, 87.5 MB/s | from 864,663,195, 3,855 s later: 717,302,808 B, 8.2 s, **87.6 MB/s** |

One connection ran at 34 to 88 MB/s: Apple's server was the slowest (34 to 48 MB/s), Hugging
Face's CDN ran at 58 to 79, and GitHub's asset host at 58 to 88. This line's own capacity was not
measured, so these numbers do not show whether parallel ranges would gain anything here.

## The pins

Into the dev root's store, before the downloads.

| Node | Source pinned | sha256 | Seconds, MB/s |
| --- | --- | --- | --- |
| `live.depth_pro` | the URL | `3eb35ca6…85c0ce` | 41.4 s at 37.7 MB/s, then a retry (below), then 10.1 s at 34.0 MB/s |
| `live.moge2` | `Ruicheng/moge-2-vitl-normal` at `cb0e8bbd6b1e243589717c78e750b1ba4c093acf` | `280741fd…cc1a01` | 17.8 s, 74.4 MB/s |
| `live.moge3` `vitl` | `Ruicheng/moge-3-vitl` at `184008f877d7ad1ad4c2cd2182a9bd1f63d0e5be` | `9b41b7b9…fb7925` | 24.3 s, 60.9 MB/s |
| `live.moge3` `vitg` | `Ruicheng/moge-3-vitg` at `6ef26c5a4b4148dab5ccaacdb08b72dc66380475` | `ce7c1541…f0a00c` | 63.7 s, 78.6 MB/s |
| `live.midas` | the URL | `9e9e9007…b82328` | 27.4 s, 57.7 MB/s |

Each canonical id equals the repository named, and no source is gated. **Apple's server dropped a
connection on its own:** it closed the Depth Pro download after 1,560,281,088 of 1,904,446,787
bytes. The transport logged `retrying` ("the connection closed after 1560281088 of 1904446787
bytes"), waited 0.67 s, resumed from that byte, and the hash matched. That is AC3's "drop" fault,
met for real.

## Hosts, proxy and the longest path

- **Hosts reached:** `ml-site.cdn-apple.com` (no redirect); `huggingface.co`, then
  `us.aws.cdn.hf.co`; `github.com`, then `release-assets.githubusercontent.com`. Every resume, in
  the pins and the downloads, named the source host first, so no redirect was reused.
- **No proxy:** WinINet has `ProxyEnable` 0 with no server or PAC file, WinHTTP is direct, and no
  `*_proxy` variable is set.
- **Longest path:** `D:\OneframeLab-ac8\models\records\<sha256>.json`, 103 characters in full and 84
  under the root. With Windows' 260-character limit, that leaves room for a root of up to
  174 characters. Blobs (`models\blobs\<2>\<sha256>`, 80) and partials
  (`models\partial\<sha256>`, 79) are shorter. Only what remained after the run was measured.

## Findings for a cloud session

`npm run engine:check` failed before the hardware step. The owner chose to run AC8 anyway and
record the failures. None of the three findings touches the transport that AC8 tests.

1. **`test_remove_is_refused_for_a_file_a_running_graph_reads_in_this_process_or_another`**
   (`engine/tests/test_model_catalog.py`) fails every time on this PC. After `holder.kill()` and
   `holder.wait()`, Windows has not yet released the killed process's `msvcrt` lock, so the next
   `models.remove` still answers `in_use` (`retry: true`). In a copy run outside the repo, the
   remove succeeded 0.27 s later. The product is right; the test assumes the release is immediate,
   which held on CI's Windows runner and does not hold here.
2. **`test_verify_drops_a_changed_file_so_the_next_download_fetches_only_it`**
   (`engine/tests/test_model_jobs.py`) failed once under the full suite (`busy` right after
   verify's `model.done`) and passed 10 times out of 10 alone. `oneframe/models/jobs.py` emits the
   job's ending event in `_work`, before `_run`'s `finally` releases the store lock and clears
   `_job`. A client that starts the next job when it hears `model.done` can be refused `busy`, and
   the page will be such a client once it gets the `models.*` methods.
3. **Ctrl+C under `npm run` returns before the tool ends.** In 2 of the 5 Stops (MoGe-2, and
   MoGe-3 in `vitg`), PowerShell's prompt came back after "Stopping…", and the tool's last lines
   (`stopped`, `model.stopped`) printed after the prompt. The journals ended in `model.stopped`
   and no process was left. `scripts/tool.js` waits for the tool, but npm above it does not, so a
   download typed again at once could be refused `locked`.

Also seen, not a finding: the MiDaS resume's job began about 145 s after its command was typed, with
nothing logged before `model.start`. Its `checking` phase took 12.3 s for 864.7 MB (2.5 to 7 s
earlier), as the partial had been out of the disk cache for an hour.

## How it ran

The session ran in Claude desktop's Code tab. Checks and pins ran in its shell. Each download ran
in its Terminal panel (PowerShell), and each Stop was a Ctrl+C that the panel sent to that console.
Claude desktop is an MSIX package, but the download root `D:\OneframeLab-ac8` is outside AppData,
so the package's folder redirect does not apply. `npm run diagnose` named
`%LOCALAPPDATA%\OneframeLab-dev` as the dev root (no `\Packages\`), and the pins were stored there.
The pins and the downloads ran one at a time, so no two shared the line.

## Machine, tools and checks

- Windows 10 build 19045.6466. NVIDIA GeForce GTX 1070 (compute 6.1, 8,192 MiB, driver 582.66),
  not used: AC8 needs no GPU. Free disk on `D:` before: 152.9 GB.
- Node v24.21.0 (pinned 24.21.0), uv 0.12.20 (required ≥ 0.12.20), npm 12.0.1, git
  2.28.0.windows.1, Python 3.14.5 (engine).
- `npm run check` passed: eslint, tsc, 38 node tests, docs check ok.
- `npm run engine:check` failed: ruff, format and pyright clean; pytest 546 passed, 4 skipped and 2
  failed in 188.3 s (findings 1 and 2).

## Commands

```sh
npm install
uv sync --project engine --frozen
npm run diagnose
npm run check
npm run engine:check
npm run models:pin -- --nodes specs/003-model-store/live live.midas
npm run models:pin -- --nodes specs/003-model-store/live live.depth_pro
npm run models:pin -- --nodes specs/003-model-store/live live.moge2
npm run models:pin -- --nodes specs/003-model-store/live live.moge3
# in PowerShell, for each node: run, Ctrl+C midway, run again until done
# (live.moge3 stopped twice; live.midas run again 3,855 s after its Stop)
$env:ONEFRAME_DATA = 'D:\OneframeLab-ac8'; npm run models:download -- --nodes specs/003-model-store/live <node> --all
# each stored blob hashed again
sha256sum D:/OneframeLab-ac8/models/blobs/*/*
```
