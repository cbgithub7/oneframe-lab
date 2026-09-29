# Versions

Every language, tool and library is on its latest stable release. A pin that is behind is a
decision someone made for a stated reason, not something that happened because nobody looked.

## The rule

1. **Exact pins.** `package.json` pins exact versions; `package-lock.json` and `engine/uv.lock`
   lock everything underneath. CI installs with `npm ci` and `uv sync --frozen`, so what runs is
   exactly what is committed.
2. **Latest stable, checked by a machine.** `npm run versions` compares every pin with its latest
   release: npm packages, the engine's Python packages, the engine's Python minor version, uv,
   the Node LTS line, and every GitHub Action. It runs on every push and every Monday.
3. **Fourteen days of grace.** A new release has 14 days for Dependabot's pull request to be merged.
   After that the check fails. Releases without a date (a new Python minor, an action tag) get no
   grace: the check fails at once and someone decides.
4. **Exceptions are written down.** To stay on an older version, add it to `versions.json` with the
   exact pin, a reason, and a `review_by` date. The check fails when the date passes, so the reason
   is looked at again rather than inherited.

```json
{ "name": "numpy", "pin": "2.5.3", "reason": "2.6.0 drops the wheels one runtime needs", "review_by": "2026-11-15" }
```

## What "latest" means for each part

| Part | Latest means | Why |
| --- | --- | --- |
| Engine Python | The newest stable CPython minor that uv can install | The engine imports no model libraries, so nothing holds it back |
| Model runtimes | The newest Python and torch each family's wheels support, per runtime | 3D research code often ships compiled wheels for older Pythons only; each runtime states its reason |
| Node | The current LTS line (`.node-version`, `engines.node`) | Electron's bundled Node follows the LTS line |
| `@types/node` | The newest release for the Node major the app runs on | Types must describe the Node the code actually runs on, not a newer one |
| Electron | The newest stable release | Electron supports only its three newest majors; staying current is the main security measure |
| GitHub Actions | The newest tag, pinned by commit hash with the version in a comment | A tag can be moved; a commit cannot |

## Floors that are never crossed

- **torch ≥ 2.6** in every runtime. Earlier versions let a crafted weight file run code even with
  `weights_only=True` (CVE-2025-32434).
- **No end-of-life Python** anywhere, runtimes included.
- **Electron within its supported majors.**

## When something is behind

- A Dependabot pull request: read the changelog, run `npm run check` and `npm run engine:check`,
  merge.
- A breaking release: fix the code, or write an exception with a reason and a review date. Both
  are fine; silence is not.
