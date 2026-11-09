# 016: Photos and results through the door

Status: approved (2026-11-08)
Owner approval: 2026-11-08, with 015, 018 and 017 as one decision; every open question answered as recommended (2026-11-04)

The third of five specs for step 3, first light: [005](../005-first-models/spec.md),
[015](../015-graphs/spec.md), this one, [018](../018-protocol/spec.md) and
[017](../017-first-page/spec.md). From the
[review of 2026-10-12](../../docs/reviews/2026-10-12.md), section 2 ("the page chooses the photo's
path") and section 5, item 6, and three blockers the [handoff](../../docs/handoff.md) lists for
first light. It lands after 015, whose methods it opens to the page.

## Problem

- A `file` param holds a path, and the door passes `graph.run`'s params through unchecked, so the
  page names the photo's path. A compromised page could have the engine read any image on disk,
  and `nodes.fit` already answers whether any path exists (`check_param`'s "no file at").
- Paths reach the page today: every value in `node.done`, each node's `folder` in `nodes.list`, the
  root in `engine.hello`, problem lists keyed by file, and tracebacks. The page has no way to show a
  result but by its path.
- The engine answers requests on its input loop, one at a time, so a slow `runtimes.remove`, or
  `nodes.fit` waiting on nvidia-smi, holds up `run.stop` behind it.
- `models.*`, `nodes.fit` and 015's graph methods are not on the page's method list.

## Requirements

1. **A photo is imported, by content.** The page asks for a photo; the main process shows the
   system's open dialog and has the engine copy the file into the root's `inputs/`, named by its
   sha256. The page gets the import's id (the hash) and the file's name, never its path.
2. **A `file` param holds an import's id,** from any caller: the engine reads it from `inputs/`
   and refuses anything else, a path included. A command-line tool given a path imports it first,
   as the dialog does. A saved graph therefore holds hashes, never a path.
3. **Results are served by key.** The page reads a value's file through a protocol of the app's,
   by its key and port; the app serves only a value the engine says a run committed.
4. **No path reaches the page**, in any answer or event.
5. **A stop is never held up or overtaken.** `run.stop`, `runtimes.stop` and `models.stop` are
   answered while another request is being worked on, and cancel one of their kind still queued.
6. **The page's method list grows** by `nodes.fit`, `models.download`, `models.stop`, 015's graph
   methods, and the app's own `photo.pick`.

## Acceptance criteria

- [ ] AC1: `test_an_import_is_kept_once_by_content` (`test_inputs.py`): importing one photo from two
  paths gives one id, its sha256, and one file under `inputs/`. Fails today: no imports.
- [ ] AC2: `test_a_file_param_names_an_import` (`test_graph.py`): a graph whose `file` param holds
  an import's id plans and runs with that file; one holding a path, or an id with no import, is
  refused as a problem of that param, without touching the path. Fails today: a path is read.
- [ ] AC3: The node test "photo.pick answers an import and a name, never a path" (`door.test.js`):
  with the dialog given to `answer()` as a function, the page's answer holds the engine's id and the
  file's name, and no part of the path. Fails today: no `photo.pick`.
- [ ] AC4: The node test "nothing the page receives holds a path" (`door.test.js`): every method of
  `ENGINE_METHODS`, called against a test engine with a broken manifest and a node that fails, and
  every event of a run, reach the page with no string holding the data root, the repo's folder or
  the home folder. Fails today: `node.done`, `nodes.list` and `engine.hello` hold them.
- [ ] AC5: `test_values_are_located_only_when_committed` (`test_server_and_nodes.py`):
  `values.locate {key, port}` answers the file of a committed value, and refuses with
  `request`/`bad_params` every key or port no committed entry has.
- [ ] AC6: The node test "the value protocol serves what the engine locates, and nothing else"
  (`values.test.js`): the handler streams a located file with its carrier's type and answers 404
  for a refusal, never reading a path the request names.
- [ ] AC7: `test_a_stop_is_answered_while_a_request_works` (`test_server_and_nodes.py`): while a
  request is held (a test method waiting on an event), `run.stop` is answered. Fails today.
- [ ] AC8: `test_a_stop_cancels_a_queued_run` (`test_server_and_nodes.py`): a `graph.run` queued
  behind a held request, then `run.stop`, ends with the run never started and its request answered
  as stopped.

## Design

**Imports.** `oneframe/inputs.py` (new): `import_file(layout, path) -> (id, name)` hashes with
`files.sha256_file`, copies into `inputs/<sha256><suffix>` with `files.replace`, and keeps nothing
else. `layout.py`, `paths.js` and `contracts/layout.json` gain `inputs`. `manifest.check_param`'s
`file` check becomes "an import's id that exists under `inputs/`"; the scheduler hands the node the
imported file's path, so `source.image` keeps reading `ctx.params["path"]` as a file. `bench:fit
--graph` (005) imports the paths a bench graph names. `inputs.import {path}` is an engine method
that only the main process calls.

**The door.** `answer()` in `app/main/door.js` gains the app's own methods (`APP_METHODS` in
`methods.js`: `photo.pick`), answered in the main process through the same channel,
`engine:request` (open question 1). `photo.pick` calls `dialog.showOpenDialog` (images only), then
`inputs.import`, and answers `{id, name}`. The door no longer reads graphs: an id is not a path.
**`forPage(payload)`** in the door, applied to every answer and to every event `main.js` sends,
removes a value's `path`, a manifest's `folder` and the hello's `data`, and replaces the root, the
repo and the home folder in every other string with `<data>`, `<repo>` and `~`, as `diagnose` does
for its file (Python's for the engine's files, the door's for the page; the page is the app's).

**The value protocol.** `main.js` registers `oneframe-value` as a privileged scheme before the app
is ready, and handles `oneframe-value://<key>/<port>` on the page's partition: it asks the engine
`values.locate` (not on the page's list), checks the answer lies under `cache/objects`, and streams
the file with the type `port-types.json` gives its carrier (a `mime` per carrier, added there, so
nothing in main names a type). `index.html`'s CSP gains the scheme in `img-src` and `connect-src`.

**Requests off the input loop.** `server.py`'s loop answers `engine.hello`, `values.locate` and the
three stops itself, and queues every other request for one worker thread, in order, so no two of
those ever run at once, as today. A stop first takes queued requests of its kind off the queue and
answers each as stopped. `shutdown` drains the queue and joins the worker. Replies carry their
request's id, and `engine.js` matches by id.

**Docs:** architecture.md (imports, the protocol, `APP_METHODS`, the loop, the method list),
AGENTS.md's "one door" (open question 1), nodes.md (`file` params).

Risks: photos copied into the root take disk (open question 2); a value's file is large (streamed);
a string the door does not scrub (AC4 calls every method).

## Tests

- Added: `test_an_import_is_kept_once_by_content` (`test_inputs.py`, new),
  `test_a_file_param_names_an_import` (`test_graph.py`), `test_values_are_located_only_when_committed`,
  `test_a_stop_is_answered_while_a_request_works`, `test_a_stop_cancels_a_queued_run`
  (`test_server_and_nodes.py`); in `door.test.js`, "photo.pick answers an import and a name, never a
  path", "nothing the page receives holds a path"; in `values.test.js` (new), "the value protocol
  serves what the engine locates, and nothing else".
- Changed: tests that pass a path in a `file` param import it first.
- Removed: none
- Renamed: none

## Verification

Every criterion is a test in `npm run check` or `npm run engine:check`, in CI on Windows and Ubuntu.
Requirement 6 is held by `engine.test.js`'s check that every method the page may call exists, and by
017's end-to-end test, which calls them. The dialog itself is seen in 017's run on the owner's PC.

## Decisions taken

- The photo copied by content, not a token for its path: a saved graph then holds no path and opens
  after a restart, the door reads no graph, and the page can name only what was imported.
- The app's own methods through the same door: one channel and one answer shape for the page.
- A protocol rather than bytes over IPC: an `<img>` reads it directly, and a large file streams.
- One worker thread, not a pool: the methods were written to run one at a time; only the stops
  and the reads that answer at once need to pass them.
- Paths scrubbed by the door, not the engine: the journal and the tools keep them.
- A request a stop takes off the queue is answered `{"stopped": true}`, not a failure: Stop is not
  a failure (errors.py).
- The import's id is resolved when the graph is planned: the step holds the file's path, so cache
  keys, still the file's content, are unchanged, and a source's `meta.source` is the import's name.
- `forPage` drops a `path`, `folder` or `data` field only when it holds an absolute path: a file's
  path in its repository (a manifest's `files`) is no path on this machine, and stays.
- The protocol contract, which the review of 2026-11-04 put into this spec, is 018's: this one
  holds eight criteria, the most a spec may. 018 lands next, and `forPage` then strips the fields
  the contract marks instead of its own list.

## Out of scope

- The page that uses this: 017. `models.list`, `models.remove`, `models.verify` and settings on the
  page: when a page needs them. Removing imports nobody's graph names: with the cache's cap.

## Open questions (answered by the owner, 2026-11-04: every one as recommended)

1. **The rule "one door into the page"** names only `engine:request` with a method from
   `ENGINE_METHODS`. This adds the app's own methods (`APP_METHODS`) through that channel, and a
   second way in, the read-only `oneframe-value` protocol, which serves only committed results.
   Recommended: the rule says both, and that the protocol serves nothing but a value by its key.
2. **Photos copied into the root** (`inputs/`), so saved graphs hold hashes: a format a person
   keeps, and disk used once per photo. Recommended: yes; the uninstaller's second tier keeps
   `inputs/` with `graphs/`.
