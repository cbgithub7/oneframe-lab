# 017: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [x] 1. **Types per environment.** `tsconfig.json` splits into the page's (`app/renderer/`, DOM, no
  Node), the preload's (no DOM) and the rest's (Node, no DOM), all run by `npm run typecheck`;
  ESLint refuses `node:` and `electron` imports under `app/renderer/`. (Design: types)
- [x] 2. **The graph and its settings.** `app/renderer/graph-model.js` (open graph, params set and
  reset, nodes and edges added and removed, what feeds an input, shown panes in `page`) and
  `form.js` (a control per param type); `tests/app/page.test.js` with "a settings form is drawn
  from a manifest". (AC1; requirements 1 to 3)
- [x] 3. **The npz reader.** `app/renderer/npz.js` (stored and deflated, zip64 as numpy writes
  it); fixtures written by numpy under `tests/app/fixtures/npz/`; "the npz reader reads what numpy
  writes". (AC2)
- [x] 4. **What a run shows.** `app/renderer/run-view.js`: a card's state from events typed from
  `Page`, and the offer each failure gets; "a failure offers what it needs", read against
  architecture.md's table. (AC3; requirements 4 and 5)
- [x] 5. **Viewers by port type.** `app/renderer/viewers/` (Image, Depth, PointMap with
  `POINT_BUDGET`, Camera) and the fallback for any other type; three.js and `@types/three` pinned;
  "every viewer is for a port type that exists". (AC7; requirement 6)
- [x] 6. **One bundle.** esbuild pinned; `npm run page:build` writes `dist/renderer/` (ignored);
  `main.js` loads the page from there; `npm start` builds first. (Design: one bundle)
- [x] 7. **The page.** `app/renderer/main.js` and `index.html`: graphs opened and saved, cards with
  their forms and wiring, the catalogue filtered by `graph.fits`, Run and Stop, offers that install
  and download with progress, panes side by side with linked cameras; keyboard, names and contrast
  per the page rules. (Requirements 1 to 6)
- [ ] 8. **The page names nothing.** `test_the_page_names_nothing` in `test_rules.py`; the
  collected list written. (AC6; requirement 7)
- [x] 9. **Folders of the person's own.** `engineCommand` passes `ONEFRAME_NODES` and
  `ONEFRAME_GRAPHS` as `--nodes` and `--graphs` after the repo's. (Design)
- [ ] 10. **The e2e test.** `@playwright/test` pinned; test nodes, a test graph and a photo under
  `tests/app/fixtures/`; `tests/e2e/first-page.test.js` with "the first page runs a graph and shows
  two results" and "Stop ends a run and the page says so"; `npm run e2e`; CI runs it on Windows and
  Ubuntu. (AC4, AC5)
- [ ] 11. **Docs and the shipped graph.** README's commands, architecture.md (the page's modules,
  the bundle, the viewers), nodes.md (folders of one's own); the shipped graph's `page` fields as
  the page writes them.
- [ ] 12. **Close.** `/code-review` (high) and its fixes; the spec's status done with the PR; the
  handoff's step-3 row, State table and AC8 under "Waiting on a local session"; the PR, AC8 pending
  hardware.
