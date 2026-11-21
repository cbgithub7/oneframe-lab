# The checks' times on the owner's PC, with the engine's fast tier

Received 2026-11-21. Commit `98635db` (`fix/review-2026-11-07-decisions`, PR #72), on the owner's
PC (Windows; its card, a GTX 1070, plays no part in these checks). From the handoff's "Waiting on a
local session" item; the review of 2026-11-07, decision 5.

There is no pass or fail. The item asked for each check's time, so that the next change to the
fast tier rests on numbers. Every check ended with exit code 0.

## Times

| Check | Seconds | Exit |
| --- | --- | --- |
| `npm run check` | 45.6 | 0 |
| `npm run engine:check` (full) | 276.5 | 0 |
| `npm run engine:check:fast` | 128.5 | 0 |
| `node scripts/test-guard.js` | 3.9 | 0 |
| `uv run --project engine --frozen pytest engine -m "not real_uv" --durations=30` | 109.0 | 0 |

## What they say

- **The fast tier halves the engine's check here:** 128.5 s against 276.5 s in the same session,
  148 s (54 %) saved by leaving out the 35 `real_uv` tests.
- **A turn that changed something now costs about 3 minutes, not 5 and a half:** the stop hook
  runs `npm run check`, the fast tier and the test guard, 178 s in all here, against 326 s with
  the full engine check. The review of 2026-11-07 measured 440 s (32 + 396 + 12) for the old
  hook on this PC, on another day and commit.
- **Where the rest goes, by subtraction** (two runs, so approximate): the fast tier's pytest took
  109.0 s of its 128.5, leaving about 20 s for ruff, pyright and `runtime:check`. The full check's
  pytest is then about 256 s, of which about 147 s are the 35 `real_uv` tests: about 4 s each,
  against about 0.17 s each for the other 645.
- **The fast tier is still slower here than in the cloud,** where it took 60 s in all (2026-11-12).
  The 30 slowest tests left in it are what would show why.

## How it ran

- Grok Bot started a PowerShell 7 window and ran one script there, which checked that the checkout
  had no local changes, switched to the PR's branch, ran the five commands in order with a stopwatch
  around each, wrote every line to `check-times.log` (ignored by git), and switched back.
- **Outside Claude desktop's package:** the log's process chain reads `pwsh.exe < pwsh.exe` and
  ends there, because the process that started the window had already exited. It names no
  `claude.exe`. The checkout is `D:\GitHub_Repositories\Applications\oneframe-lab`, which is not
  under `\Packages\`. The full check's footprint test, which watches the real profile, passed.

## Not in this report

- **The list of the 30 slowest tests** in the fast tier is in the log on the owner's PC, not here:
  only its summary was passed on. Until it is added, the fast tier's next change has a total to go
  on, not a list.
- **The log's header:** the run's date and time, Node's and uv's versions and the processor's name,
  for the same reason. The commit and the branch are above.
