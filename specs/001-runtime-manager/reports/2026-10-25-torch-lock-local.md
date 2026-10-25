# The torch runtime's lock on the GTX 1070: passed, outside Claude desktop

2026-10-25 (UTC) · commit `4315328` (`fix/torch-lock`) · no code changes

The handoff's item from the owner (2026-10-24): relock filelock and setuptools for Dependabot
alerts 46 to 49, then reinstall and probe. The machine-readable version of this file is
[2026-10-25-torch-lock-local.json](2026-10-25-torch-lock-local.json).

## Verdict

| Run | Where | Outcome | Numbers |
| --- | --- | --- | --- |
| [Attempt 1](2026-10-25-torch-lock-attempt-1.md): new lock over the 2026-10-11 install | inside Claude desktop | failed | os error 32 removing `filelock\_soft_rw\__pycache__` |
| [Attempt 2](2026-10-25-torch-lock-attempt-2.md): the same, again | inside | failed | os error 32 removing `setuptools\config\_validate_pyproject\__pycache__` |
| [Attempt 3](2026-10-25-torch-lock-attempt-3.md): new lock, fresh | inside | failed | os error 32 removing `sympy-1.14.0.data` |
| [Diagnostic](2026-10-25-torch-main-lock-uv-0.12.20.md): main's lock with uv 0.12.20, fresh | inside | failed | the same, on `sympy-1.14.0.data` |
| [Main's lock, fresh](2026-10-25-torch-main-lock-outside.md) | outside | passed | 55.1 s, 4,359 MB; probe on cuda, 0.179 s, peak 209.8 MB |
| [**New lock, over that install**](2026-10-25-torch-lock.md) | outside | **passed** | 8.0 s, 4,360 MB; probe on cuda, 0.183 s, peak 209.8 MB |

The item passes: the new lock installs over an existing install and the probe runs on the card.
The lock went past the advisories' fixes (4.2.1 and 84.1.0) to the latest releases, filelock
4.2.4 and setuptools 84.2.1; uv 0.13.5 also raised the lock's `revision` from 3 to 5. It
supersedes Dependabot's PRs #29 and #31.

## Finding: a runtime install from inside Claude desktop fails here

Every install this session started stopped at uv's step 3 with "The process cannot access the
file because it is being used by another process" (os error 32), on a folder uv had just emptied.
It did so with uv 0.13.5 and 0.12.20 and with either lock, upgrading or fresh, while the same
commands from outside the package passed. The machine, the card, Bitdefender (installed since
2022, Defender off) and the tools were the same in every run, and on 2026-10-11 the install was
also made from outside the package. So the likely cause is Claude desktop's MSIX package, whose
processes see AppData through Windows' virtualization, perhaps together with the antivirus; what
held each folder was not seen (no handle tool), and no process of ours was running afterwards.

The local-session skill sends only footprint checks outside the package. Whether runtime installs
go there too, and whether the engine's install should outlast such a refusal, is open for a cloud
session and the owner.

## How each ran

- **Inside the package:** the lock, attempts 1 to 3 and the diagnostic, from this Claude desktop
  Code-tab session. Before attempt 3, before the diagnostic and before the passing runs, the
  half-installed runtime was removed through the engine's `Runtimes.remove`.
- **Outside the package:** the two passing runs, by another agent app (Grok Bot) at the owner's
  request, in PowerShell. Its process chain was `pwsh.exe <- Grok Bot.exe <- Grok Bot.exe <-
  explorer.exe`; Grok Bot has no MSIX package. It ran exactly the commands given (plus a
  `Get-Location`), and both exited 0. The reports are the bench's own, read from the checkout.
- **The diagnostic** used uv 0.12.20 from astral-sh's GitHub release, its sha256 checked, run from
  the session's scratch folder through `--uv` and deleted after. It is not this item's evidence.

## What it cost, and what is left

- Attempt 3 downloaded torch again (2.4 GiB): uv 0.13.5 does not read the cache entries uv
  0.12.20 wrote. The root's uv cache is now 8.2 GB, about 4 GB of it 0.12.20's, which
  `uv cache prune` can take back.
- The data root holds one good torch install and nothing else from these runs: no half-installed
  runtime, no leftover `.data` folder, an empty `cache/tmp/`, nothing in Claude's package storage.
- What the fit learned for torch starts over, as the item said it would: the lock changed the
  runtime's hash.
- Paths in the attempts' reports have the home folder written `%USERPROFILE%`.

## Machine and tools

Windows 10 build 19045.6466; NVIDIA GeForce GTX 1070, compute 6.1, 8,192 MiB, driver 582.66;
about 339 GB free on the data root. Node v24.22.1, uv 0.13.5, git 2.28.0, Python 3.15.1 (engine)
and 3.14.7 (torch runtime), torch 2.14.0+cu126. The Oneframe Lab app was not running.

## Commands

```sh
uv lock --project runtimes/torch --upgrade-package filelock --upgrade-package setuptools
npm run bench:runtime -- torch --out specs/001-runtime-manager/reports/2026-10-25-torch-lock.md
# attempts 1 to 3 (inside), each report then renamed -attempt-<n>
npm run bench:runtime -- torch --uv <uv 0.12.20> --out specs/001-runtime-manager/reports/2026-10-25-torch-main-lock-uv-0.12.20.md
# on report/010-probes, inside: the diagnostic
npm run bench:runtime -- torch --out specs/001-runtime-manager/reports/2026-10-25-torch-main-lock-outside.md
# on report/010-probes, outside
npm run bench:runtime -- torch --out specs/001-runtime-manager/reports/2026-10-25-torch-lock.md
# on fix/torch-lock, outside
```
