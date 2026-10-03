# 006: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [x] 1. The shared root table (JSON) and `layout.py` beside `app/main/paths.js`; the engine's and
  the app's root functions take environment, platform, home and `packaged`; the dev root becomes
  `OneframeLab-dev` (AC1).
- [ ] 2. One JSON writer (unique temporary name, flush, replace, refuses a newer format) and the
  operating-system locks (`msvcrt.locking` / `fcntl.flock`, files under `logs/locks/`, never
  deleted); move learned memory, settings and the runtime marker onto them (part of AC4, AC5).
- [ ] 3. Formats: `oneframe-root.json`, settings format 1, learned memory and markers left alone
  when newer, the marker's `format` and environment path, `newer_format` and `moved` (AC4).
- [ ] 4. Per-process temporary folders under `cache/tmp/`, locked while the process lives; job and
  bench scratch folders inside them; the start-up sweep removes only unlocked ones; runtime
  install and remove take that runtime's lock (AC5).
- [ ] 5. `child_env` puts every library cache, temp and `PYTHONDONTWRITEBYTECODE` under the root;
  the engine's `sys.pycache_prefix`; `cache/CACHEDIR.TAG` (AC2's environment part).
- [ ] 6. `app/main/boot.js`: Electron's user data, session data, crash dumps and log path under
  the root, before the single-instance lock (AC3).
- [ ] 7. The footprint test, in CI on Windows and Linux (AC2).
- [ ] 8. `errors.py` declares every kind and reason; failures carry kind, reason, message, next
  and retry; one `Stopped`; runtime reasons in snake_case; `main.js` returns errors as values; the
  architecture doc's table, held equal by a test (AC6). A facet mismatch found at run time gets its
  own kind, reported against the edge rather than blamed on the node that receives it.
- [ ] 9. The journal around `Scheduler.run` and `Runtimes.run_install`, pruning, and
  `npm run diagnose` with its redaction (AC7).
- [ ] 10. Cache keys from the node's folder and the runtime's build and marker hashes;
  `KEY_VERSION` 2 (AC8).
- [ ] 11. Docs: AGENTS.md drops "today's code still breaks this"; architecture and runtimes docs;
  the local-session skill's old root; the handoff.
