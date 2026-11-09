# 016: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [x] 1. **Imports, by content.** `oneframe/inputs.py`: `import_file` copies a file into the root's
  `inputs/` as `<sha256><suffix>`, once, and `find` gives an import's file by its id; `inputs` in
  `layout.py`, `paths.js` and `contracts/layout.json`; `inputs.import {path}`, for the main
  process. AC1.
- [x] 2. **A `file` param names an import.** `check_param` refuses anything but an import's id,
  without touching a path; `plan()` and `nodes.fit` resolve the id under `inputs/`, so the node
  still reads a file; `bench:fit --graph` and CI's model check import the photos they name. AC2.
- [x] 3. **Requests off the input loop.** The loop answers `engine.hello`, `values.locate` and the
  three stops itself, and hands every other request to one worker, in order; a stop first answers
  each queued request of its kind as stopped. AC7, AC8.
- [x] 4. **`values.locate {key, port}`**, the file of a committed value, and nothing else. AC5.
- [ ] 5. **The door.** `APP_METHODS` (`photo.pick`) answered in the main process through
  `engine:request`; `forPage` on every answer and every event: a local path in a value's `path`, a
  manifest's `folder` or the hello's `data` removed, and the root, the repo and the home folder
  written `<data>`, `<repo>` and `~` everywhere else. AC3, AC4.
- [ ] 6. **The value protocol.** `oneframe-value://<key>/<port>` on the page's partition, served by
  `app/main/values.js` from what `values.locate` answers, under `cache/objects` only, with the
  `mime` `port-types.json` gives its carrier; the CSP allows it. AC6.
- [ ] 7. **The page's list, the docs and the end.** `ENGINE_METHODS` gains `nodes.fit`,
  `models.download`, `models.stop` and 015's graph methods; architecture.md, AGENTS.md's one door,
  nodes.md; `/code-review`; the spec marked done in the PR.
