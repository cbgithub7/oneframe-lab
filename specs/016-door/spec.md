# 016: Photos and results through the door

Status: draft
Owner approval: (date, once approved)

The third of four specs for step 3, first light: [005](../005-first-models/spec.md),
[015](../015-graphs/spec.md), this one, and [017](../017-first-page/spec.md). From the
[review of 2026-10-12](../../docs/reviews/2026-10-12.md), section 2 ("the page chooses the photo's
path") and section 5, item 6, and three blockers the [handoff](../../docs/handoff.md) lists for
first light. It lands after 015, whose methods it opens to the page.

## Problem

- The door passes `graph.run`'s params through unchecked, so the page names the photo's path. Once
  the page can read results, a compromised page could read any image on disk.
- Every `node.done` carries absolute paths, and so do `nodes.list` (each node's `folder`) and
  `engine.hello` (the root). The page has no way to show a result but by its path.
- The engine answers requests on its input loop, one at a time, so a slow `runtimes.remove`, or
  `nodes.fit` waiting on nvidia-smi, holds up `run.stop` behind it.
- `models.*`, `nodes.fit` and 015's graph methods are not on the page's method list, so the page
  cannot offer Download, say what fits, or open a graph.

## Requirements

1. **The photo is picked in the main process.** The page asks for a photo; the app shows the
   system's open dialog and answers an opaque token and the file's name, never its path.
2. **A `file` param from the page is a token.** The door swaps each token for its path before the
   engine sees the graph, refuses any other value, and swaps paths in a graph the engine returns
   (a saved graph) for tokens. Tokens live while the app runs.
3. **Results are served by key.** The page reads a value's file through a protocol of the app's,
   by the value's key and port; the app serves only a value the engine says is in the cache.
4. **No path reaches the page**, in an answer or an event.
5. **A stop is never held up.** `run.stop`, `runtimes.stop` and `models.stop` are answered while
   any other request is still being worked on.
6. **The page's method list grows** by what first light needs: `nodes.fit`, `models.list`,
   `models.download`, `models.stop`, 015's graph methods, and the app's own `photo.pick`.

## Acceptance criteria

- [ ] AC1: The node test "a file param must be a token the app gave" (`door.test.js`): a graph
  whose `file` param holds a path is refused with `request`/`refused`; one holding a token reaches
  the engine with the token's path in its place. Fails today: the path passes through.
- [ ] AC2: The node test "a saved graph reaches the page with tokens" (`door.test.js`): a
  `graphs.get` answer whose `file` param is a path reaches the page with a token and the file's
  name, and that token, sent back in `graph.run`, reaches the engine as the same path.
- [ ] AC3: The node test "nothing the page receives holds a path" (`door.test.js`): every answer
  and event from a recorded run of the test engine, after the door, holds no string that contains
  the data root, the repo's folder or the home folder. Fails today: `node.done`, `nodes.list` and
  `engine.hello` do.
- [ ] AC4: `test_values_are_located_by_key_inside_the_cache` (`test_server_and_nodes.py`):
  `values.locate {key, port}` answers the file of a value in the cache, and refuses with
  `request`/`bad_params` a key not in the cache, a port the entry does not have, and a key that
  is not 64 hex.
- [ ] AC5: The node test "the value protocol serves what the engine locates, and nothing else"
  (`values.test.js`): the handler answers a located file's bytes and its carrier's type, and 404
  for a refused key, without reading any path the request names.
- [ ] AC6: `test_a_stop_is_answered_while_a_slow_request_works` (`test_server_and_nodes.py`):
  while a request is held (a test method that waits on an event), `run.stop` is answered; the held
  request's answer follows once released. Fails today: the stop waits behind it.
- [ ] AC7: The node test "the page may ask what first light needs, and not values.locate"
  (`door.test.js`): `ENGINE_METHODS` holds requirement 6's engine methods and not `values.locate`,
  `APP_METHODS` is `photo.pick` alone, the two share none, and the door answers `photo.pick`
  without the engine. Fails today: no `APP_METHODS`.

## Design

**`app/main/door.js`.** `answer()` gains the app's own methods (`APP_METHODS` in `methods.js`:
`photo.pick`), answered in the main process, so the page still has one channel, `engine:request`
(open question 1). It keeps a token table (`app/main/tokens.js`, new): `photo.pick` shows Electron's
`dialog.showOpenDialog` with image filters and answers `{token, name}`. Before a graph goes to the
engine (`graph.run`, `graph.validate`, `graph.fits`, `graphs.save`), each param whose manifest type
is `file` must hold a token, swapped for its path; the door knows the types from the `nodes.list`
answer it keeps, refreshed by `nodes.reload`. A graph coming back (`graphs.get`) has each such path
swapped for a token, and the answer gains `files: {token: name}`. A `nodes.fit` carries no file.

**`forPage(payload)`** in the door, applied to every answer and every event main sends: removes
each value's `path`, a manifest's `folder` and the hello's `data`, the fields that carry one.
Events go through it in `main.js`'s `send`. AC3 checks everything else.

**The value protocol.** `main.js` registers `oneframe-value://<key>/<port>` on the page's
partition with `protocol.handle`. The handler asks the engine `values.locate {key, port}` (an
engine method, not on the page's list), checks the answer lies under the layout's
`cache/objects`, and streams the file with its carrier's type (`image/png`, or
`application/octet-stream` for `npz`, `ply`, `glb`, `json`). `index.html`'s CSP gains the scheme in
`img-src` and `connect-src`. `values.locate` (`server.py`, `cache.py`) reads the entry's record, so
it answers only what a run committed.

**Requests off the input loop.** `server.py`'s loop answers `*.stop` and `engine.hello` itself,
and hands every other request to one worker thread, in order, so no two of them ever run at once,
as today. Replies already carry their request's id, and `engine.js` matches by id.

Risks: a token outlives the person's intent (it lives only while the app runs, and names one file);
a value's file is large (it is streamed, not read whole); a path the door does not know to remove
(AC3 scans everything).

## Tests

- Added: in `door.test.js`, "a file param must be a token the app gave", "a saved graph reaches the
  page with tokens", "nothing the page receives holds a path"; in `values.test.js` (new), "the value
  protocol serves what the engine locates, and nothing else"; in `test_server_and_nodes.py`,
  `test_values_are_located_by_key_inside_the_cache` and
  `test_a_stop_is_answered_while_a_slow_request_works`.
- Removed: none
- Renamed: none

## Verification

Every criterion is a test in `npm run check` or `npm run engine:check`, in CI on Windows and
Ubuntu. The dialog itself is checked by hand in 017's run on the owner's PC.

## Decisions taken

- The app's own methods go through the same door, so the page has one channel and one answer shape.
- A protocol rather than bytes over IPC: an `<img>` reads it directly, and a large file streams.
- One worker thread rather than a pool: the engine's methods were written to run one at a time,
  and only the stops need to pass them.
- Paths are removed by the door, not the engine: the journal and the tools keep them.

## Out of scope

- The page that uses all of this: 017.
- `models.remove`, `models.verify`, `nodes.forget` and settings on the page: when a page needs them.
- Tokens that survive a restart: a saved graph keeps the path, and a new token is issued on opening.

## Open questions

1. **The rule "one door into the page"** says the page reaches the engine only through
   `engine:request` with a method from `ENGINE_METHODS`. The photo dialog is the app's, not the
   engine's. Recommended: the rule reads "with a method from `ENGINE_METHODS`, or the app's own
   from `APP_METHODS`", through the same door, and `AGENTS.md` and `architecture.md` say so.
