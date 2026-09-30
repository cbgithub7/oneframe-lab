---
name: local-session
description: Start of an Oneframe Lab session on the owner's own PC, used to run what a cloud session cannot -- GPU runs, Windows-only behaviour, real model weights -- and to record the evidence as a report. Checks the machine and the checkout, finds the hardware steps a spec's plan asks for, runs exactly those, and commits a report. Use at the start of any local session, and when asked to verify a spec or PR on real hardware.
---

# Local session (real hardware)

A local session proves what only real hardware can: that a runtime builds on this Windows
machine, that a node runs on this GPU, and in how many seconds and how much VRAM. Its product is
evidence. Other sessions and the owner rely on its reports, so a report must say exactly what
happened, failures included.

The rules in `AGENTS.md` apply here too. Two differ from a cloud session:

- **Verify, don't develop.** Run the steps the spec's plan lists. If something fails, record it
  and report it. Change code only when the owner asks, on the PR's branch, with the checks green,
  and name the change in the report.
- **Network is real here.** Downloads (runtimes, weights) happen only when a step calls for them,
  after telling the owner what and how big, and never during a run.

Do these steps in order.

## 1. Rules and target

Read `AGENTS.md`. Ask the owner which spec or PR to verify if the message does not say. Read that
spec's `spec.md` (the acceptance criteria marked hardware) and its `plan.md` (the Verification
section: the exact commands and the report each writes).

If the plan gives no command for a hardware criterion, stop and say so. Do not invent a
procedure: the cloud session that wrote the plan owes one.

## 2. The machine

Record, for the report:

- **OS:** `cmd /c ver` (Windows build), or `uname -a` elsewhere.
- **GPU:** `nvidia-smi --query-gpu=name,compute_cap,memory.total,memory.used,driver_version --format=csv`.
  If there is no `nvidia-smi`, record "no NVIDIA GPU".
- **Other GPU users:** `nvidia-smi` process list. Ask the owner to close anything heavy, and the
  Oneframe Lab app itself, which holds the card. Note what was still running.
- **Free disk** on the data root (`%LOCALAPPDATA%\OneframeLab` unless `ONEFRAME_DATA` says
  otherwise).
- **Tools:** `node --version` against `.node-version`, `uv --version` against `engine/pyproject.toml`
  `required-version`, `git --version`. If Node or uv is older than required, stop and tell the owner
  what to install. The versions are part of the evidence, so no substitutes.

## 3. The checkout

- `git status`: the tree must be clean. Stash nothing silently; ask the owner.
- `git fetch origin`, then check out the PR's branch (or the spec's branch) and `git pull`.
- `npm install`, then `uv sync --project engine --frozen`.
- Run `npm run check` and `npm run engine:check` here too. Windows catches what Linux CI can
  miss. Record the result, and stop if they fail before any hardware step.

## 4. Run the hardware steps

For each hardware acceptance criterion, in the plan's order:

1. Tell the owner what is about to run, and what it will download (with size) if anything.
2. Run the plan's command exactly. Keep its full output.
3. Watch for the failures a GPU run can have: out of memory (the allocator ceiling should turn it
   into an error, not a frozen desktop, and if the desktop froze, that is a finding), driver
   errors, a download attempted during a run.
4. Note the outcome: passed, failed or blocked, and why.

Never retry quietly until something passes. A second attempt is fine when the first failed for a
reason you name (the app was still holding the card); the report lists both.

## 5. The report

Write `specs/<id>/reports/<YYYY-MM-DD>-<short-step-name>.json`, plus a `.md` of the same name for
people, unless the plan's command already wrote the JSON (then add only the `.md`). The JSON
carries:

```json
{
  "spec": "001-runtime-manager",
  "criterion": "AC9",
  "outcome": "passed | failed | blocked",
  "date": "YYYY-MM-DD",
  "commit": "<git rev-parse HEAD>",
  "machine": {
    "os": "...", "gpu": "...", "compute_capability": "6.1", "vram_mb": 8192,
    "driver": "...", "free_disk_gb": 0, "other_gpu_processes": []
  },
  "tools": { "node": "...", "uv": "...", "python": "..." },
  "commands": ["exactly what ran"],
  "results": { "seconds": 0, "peak_vram_mb": 0, "arrangement": "...", "notes": "..." },
  "local_changes": "none, or what was changed and why"
}
```

Leave out anything personal: no user name, home path, host name or serial numbers (write
`%USERPROFILE%` for the home folder). The GPU's model name is fine in a report. Decisions in the
code still never key on it.

## 6. Hand back

- Commit the reports on the PR's branch ("Add hardware report for NNN ACx: passed"), and push.
- If you have GitHub access, comment on the PR with one line per criterion: outcome, headline
  numbers, report path. Otherwise tell the owner the same, ready to paste.
- A failed or blocked criterion goes back to a cloud session as a finding. Add it to the spec's
  open questions, or describe it in the PR comment. Do not fix it here unless the owner asks.
