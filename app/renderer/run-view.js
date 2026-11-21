// @ts-check
// What a run shows, as data: each card's state from the run's events, the offer each failure
// gets, and how far each install and download has gone. Events are the page's view of the
// protocol (app/protocol.d.ts's Page, generated from contracts/protocol.json); nothing here names
// a node or a runtime, and the page asks what an offer needs from the failed step's manifest.

/** @typedef {import("../protocol.js").Page.Event} Event */
/** @typedef {import("../protocol.js").Page.Failure} Failure */
/** @typedef {import("../protocol.js").Page.Fit} Fit */
/** @typedef {import("../protocol.js").Page.Value} Value */
/** @typedef {import("../protocol.js").Page.Collection} Collection */
/**
 * A step run for each (spec 021): how many of its items are done, of how many, and whether any ran.
 * @typedef {{ done: number, of: number, ran: boolean }} Items
 */
/**
 * @typedef {"install" | "download" | "wait" | "retry"} Action
 * @typedef {{ action: Action, for?: "runtime" | "files" }} Offer
 * `waiting`: not reached yet; `queued`: reached, and waiting its turn for a device (spec 023).
 * @typedef {"waiting" | "queued" | "fitting" | "running" | "cached" | "done" | "failed" | "skipped" | "stopped"} Phase
 * @typedef {{ device?: string, card?: number, behind: import("../protocol.js").Page.Place }} Queued
 * @typedef {{ phase: Phase, fit?: Fit, stage?: string, progress?: { done: number | null, total: number | null, what: string },
 *   seconds?: number, peak_vram_mb?: number | null, failure?: Failure, offer?: Offer | null, note?: string,
 *   outputs?: Record<string, Value | Collection>, items?: Items, earlier?: boolean, queued?: Queued }} Card a card's state;
 *   `earlier`: from an earlier run, which a run up to other cards left as it was (spec 022); `queued`:
 *   what its step waits for, and behind which run's step
 * @typedef {"done" | "failed" | "stopped" | null} Ended
 * @typedef {{ ended: Ended, failure?: Failure }} RunEnd
 * @typedef {{ run: string | null, pending: number, expected: string[], runs: Record<string, RunEnd>,
 *   owner: Record<string, string>, cards: Record<string, Card>, ended: Ended, failure?: Failure }} RunState
 *   Every run this page started (spec 023): `run` the newest, whose ending `ended` and `failure`
 *   say; `pending` the presses whose run has not started; `expected` the runs the engine answered
 *   whose run.start has not come; `owner` the run each card shows, the newest that named its step.
 */

/**
 * Every failure kind architecture.md's table declares (oneframe/errors.py, and the app's own in
 * app/main/failures.js); a test holds the two equal. A kind not here is one the page does not know.
 */
export const KINDS = new Set([
  "oom", "memory", "fetch", "missing", "node", "error", "died", "contract", "edge", "runtime", "files",
  "download", "store", "graph", "root", "request", "app", "engine",
]);

/** The failures one press can mend, by kind and reason (spec 017, requirement 5). */
const MENDS = /** @type {Record<string, Record<string, Offer>>} */ ({
  runtime: {
    not_installed: { action: "install" },
    out_of_date: { action: "install" },
    installing: { action: "wait", for: "runtime" },
  },
  files: {
    not_downloaded: { action: "download" },
    incomplete: { action: "download" },
    downloading: { action: "wait", for: "files" },
  },
});

/**
 * What the page offers for a failure: Install, Download, a wait for the job that is mending it,
 * Run again when the same run may succeed later, or nothing (its message and `next` say the rest).
 * @param {Pick<Failure, "kind" | "reason" | "retry">} failure
 * @returns {Offer | null}
 */
export function offerFor(failure) {
  if (!KINDS.has(failure.kind)) return null;
  const mend = MENDS[failure.kind]?.[failure.reason ?? ""];
  if (mend) return { ...mend };
  return failure.retry ? { action: "retry" } : null;
}

/** No run yet. @returns {RunState} */
export function idle() {
  return { run: null, pending: 0, expected: [], runs: {}, owner: {}, cards: {}, ended: null };
}

/**
 * Whether the card's step is held by a run still going.
 * @param {RunState} state @param {string} step
 */
export function held(state, step) {
  const run = state.owner[step];
  return run !== undefined && state.runs[run]?.ended === null;
}

/** Whether any run this page started goes, or is about to. @param {RunState} state */
export function going(state) {
  return state.pending > 0 || state.expected.length > 0 || Object.values(state.runs).some((r) => r.ended === null);
}

/**
 * The cards no run still going holds, kept as from an earlier run.
 * @param {RunState} state @returns {Record<string, Card>}
 */
function keptEarlier(state) {
  /** @type {Record<string, Card>} */
  const cards = {};
  for (const [step, card] of Object.entries(state.cards)) cards[step] = held(state, step) ? card : { ...card, earlier: true };
  return cards;
}

/**
 * Run or Run to here was pressed: the next run.start this page did not expect is this one's,
 * whatever its id. The cards no run still going holds are kept, as from an earlier run, until it
 * says which it resets; another run's cards go on as they are.
 * @param {RunState} [state] @returns {RunState}
 */
export function starting(state = idle()) {
  return { ...state, cards: keptEarlier(state), pending: state.pending + 1 };
}

/**
 * The engine answered a press with its run's id: its run.start is this page's, should it come
 * after the answer.
 * @param {RunState} state @param {string} run @returns {RunState}
 */
export function started(state, run) {
  if (run in state.runs || state.pending === 0) return state; // its run.start came first
  return { ...state, pending: state.pending - 1, expected: [...state.expected, run] };
}

/**
 * A press the engine refused: no run comes of it, and the cards it marked as from an earlier run
 * show again what they showed, unless another press or run has marked them since.
 * @param {RunState} state @param {RunState} before the state the press began from
 * @returns {RunState}
 */
export function refused(state, before) {
  const pending = Math.max(state.pending - 1, 0);
  const cards = { ...state.cards };
  if (pending === 0) {
    for (const [step, card] of Object.entries(cards)) {
      const was = before.cards[step];
      if (card.earlier && was && !was.earlier && card.phase === was.phase && !held(state, step)) cards[step] = was;
    }
  }
  return { ...state, pending, cards };
}

/**
 * A step's outputs once one item of a for each is done: each port's collection, that item's value
 * put at its place.
 * @param {Record<string, Value | Collection> | undefined} outputs @param {Record<string, Value | Collection>} made
 * @param {number} item
 * @returns {Record<string, Value | Collection>}
 */
function placed(outputs, made, item) {
  /** @type {Record<string, Value | Collection>} */
  const out = { ...outputs };
  for (const [port, value] of Object.entries(made)) {
    if ("items" in value) continue; // a step run for each gives one value an item
    const held = out[port];
    const items = held && "items" in held ? [...held.items] : [];
    items[item] = value;
    out[port] = { type: value.type, items };
  }
  return out;
}

/** @param {Failure} failure @returns {Failure} */
const failureOf = (failure) => ({
  kind: failure.kind,
  ...(failure.reason === undefined ? {} : { reason: failure.reason }),
  message: failure.message,
  ...(failure.next === undefined ? {} : { next: failure.next }),
  retry: failure.retry,
  ...(failure.problems === undefined ? {} : { problems: failure.problems }),
});

/** A card no longer waiting its turn. @type {Partial<Card>} */
const UNQUEUED = { queued: undefined };

/**
 * The runs' state after one event. A run's events change only the cards it holds, so an older
 * run's late events never overwrite a newer one's (spec 023); events of a run this page did not
 * start are ignored.
 * @param {RunState} state @param {Event} event
 * @returns {RunState}
 */
export function runView(state, event) {
  if (!("run" in event)) return state;
  if (event.event === "run.start") {
    const expected = state.expected.includes(event.run);
    if (!(expected || event.run in state.runs || state.pending > 0)) return state;
    const pending = expected || event.run in state.runs ? state.pending : state.pending - 1;
    const runs = { ...state.runs, [event.run]: { ended: /** @type {Ended} */ (null) } };
    const owner = { ...state.owner };
    for (const step of event.order) owner[step] = event.run;
    // Only the cards the run holds start again (spec 022); the others keep what they showed, as
    // from an earlier run unless a run still going holds them.
    const cards = keptEarlier({ ...state, runs, owner });
    for (const step of event.order) cards[step] = { phase: "waiting" };
    const left = state.expected.filter((run) => run !== event.run);
    return { ...state, run: event.run, pending, expected: left, runs, owner, cards, ended: null, failure: undefined };
  }
  if (!(event.run in state.runs)) return state;
  const cards = { ...state.cards };
  /** Whether this run holds the step's card. @param {string} step */
  const holds = (step) => state.owner[step] === event.run;
  /** @param {string} step @param {Partial<Card>} change */
  const card = (step, change) => {
    cards[step] = { ...(cards[step] ?? { phase: "waiting" }), ...UNQUEUED, ...change };
  };
  // A step's own event, of a card a newer run now holds: that run's state stands.
  if (!event.event.startsWith("run.") && "step" in event && !holds(String(event.step))) return state;
  // One item of a step run for each: the card counts it, and is done only at the last.
  /** @param {{ step: string, item?: number, of?: number, outputs: Record<string, Value | Collection> }} ev @param {boolean} ran @param {Partial<Card>} change */
  const itemDone = (ev, ran, change) => {
    const was = cards[ev.step]?.items;
    const of = ev.of ?? 0;
    const items = { done: (was?.done ?? 0) + 1, of, ran: (was?.ran ?? false) || ran };
    const outputs = placed(cards[ev.step]?.outputs, ev.outputs, ev.item ?? 0);
    const last = items.done >= of;
    card(ev.step, { ...change, items, outputs, phase: last ? (items.ran ? "done" : "cached") : "running" });
  };
  /** The run ended: its own state, and the bar's when it is the newest. @param {Ended} ended @param {Failure} [failure] */
  const end = (ended, failure) => {
    const runs = { ...state.runs, [event.run]: { ended, ...(failure ? { failure } : {}) } };
    const newest = event.run === state.run ? { ended, failure } : {};
    return { ...state, runs, cards, ...newest };
  };
  switch (event.event) {
    case "node.queued": {
      const queued = { ...(event.device === undefined ? {} : { device: event.device }),
        ...(event.card === undefined ? {} : { card: event.card }), behind: event.behind };
      card(event.step, { phase: "queued", queued });
      break;
    }
    case "node.fit":
      card(event.step, { phase: "fitting", fit: event });
      break;
    case "node.start":
      card(event.step, { phase: "running", stage: undefined, progress: undefined });
      break;
    case "stage":
      card(event.step, { stage: event.message || event.stage });
      break;
    case "progress":
      card(event.step, { progress: { done: event.done, total: event.total, what: event.what } });
      break;
    case "node.oom":
      card(event.step, { note: event.message });
      break;
    case "node.step_oom":
      card(event.step, { note: event.message });
      break;
    case "node.cached":
      if (event.of !== undefined) itemDone(event, false, event.fit ? { fit: event.fit } : {});
      else card(event.step, { phase: "cached", outputs: event.outputs, ...(event.fit ? { fit: event.fit } : {}) });
      break;
    case "node.done":
      if (event.of === 0) {
        card(event.step, { phase: "done", seconds: 0, items: { done: 0, of: 0, ran: false }, outputs: event.outputs });
        break;
      }
      if (event.of !== undefined) {
        const was = cards[event.step]?.seconds ?? 0;
        itemDone(event, true, { seconds: was + event.seconds, peak_vram_mb: event.peak_vram_mb, ...(event.fit ? { fit: event.fit } : {}) });
        break;
      }
      card(event.step, {
        phase: "done", seconds: event.seconds, peak_vram_mb: event.peak_vram_mb, outputs: event.outputs,
        ...(event.fit ? { fit: event.fit } : {}),
      });
      break;
    case "node.failed": {
      const failure = failureOf(event);
      card(event.step, { phase: "failed", failure, offer: offerFor(failure) });
      break;
    }
    case "node.skipped": {
      const why = event.waited_on ? `Not run: ${event.waited_on} did not finish.` : (event.message ?? "Not run.");
      card(event.step, { phase: "skipped", note: why });
      break;
    }
    case "run.done":
      for (const [step, outputs] of Object.entries(event.outputs)) {
        if (holds(step) && cards[step] && !cards[step].outputs) card(step, { outputs });
      }
      return end("done");
    case "run.failed": {
      const failure = failureOf(event);
      // A failure no node.failed told: an edge's, or the engine's own. A step's card shows it.
      if (event.step && holds(event.step) && cards[event.step]?.phase !== "failed") {
        card(event.step, { phase: "failed", failure, offer: offerFor(failure) });
      }
      return end("failed", failure);
    }
    case "run.stopped":
      for (const [step, row] of Object.entries(cards)) {
        if (row.earlier || !holds(step)) continue;
        if (step === event.step || ["running", "fitting", "queued"].includes(row.phase)) card(step, { phase: "stopped" });
        else if (row.phase === "waiting") card(step, { phase: "skipped", note: "Not run: the run was stopped." });
      }
      return end("stopped");
    default:
      return state;
  }
  return { ...state, cards };
}

/**
 * @typedef {{ kind: "runtime" | "files", id: string, state: "running" | "done" | "failed" | "stopped",
 *   text: string, done: number | null, total: number | null, failure?: Failure, node?: string | null,
 *   bytes?: Record<string, number> }} Job a download's `bytes` are each file's so far, by sha256
 */

/** @param {number} bytes */
const mb = (bytes) => `${(bytes / 1e6).toFixed(bytes < 1e7 ? 1 : 0)} MB`;

/**
 * The installs and downloads the page started, after one event: an install by its runtime, a
 * download by its job id.
 * @param {Record<string, Job>} jobs @param {Event} event
 * @returns {Record<string, Job>}
 */
export function jobView(jobs, event) {
  /** @param {string} key @param {Partial<Job> & Pick<Job, "kind" | "id">} change */
  const set = (key, change) => ({
    ...jobs,
    [key]: { ...(jobs[key] ?? { state: "running", text: "", done: null, total: null }), ...change },
  });
  switch (event.event) {
    case "runtime.start":
      return set(`runtime:${event.runtime}`, { kind: "runtime", id: event.runtime, state: "running", text: `Installing (${event.build})` });
    case "runtime.step":
      return set(`runtime:${event.runtime}`, {
        kind: "runtime", id: event.runtime, text: `Step ${event.index} of ${event.total}: ${event.message || event.step}`,
        done: event.index, total: event.total,
      });
    case "runtime.done":
      return set(`runtime:${event.runtime}`, { kind: "runtime", id: event.runtime, state: "done", text: `Installed in ${Math.round(event.seconds)} s` });
    case "runtime.failed":
      return set(`runtime:${event.runtime}`, {
        kind: "runtime", id: event.runtime, state: "failed", text: event.message, failure: failureOf(event),
      });
    case "runtime.stopped":
      return set(`runtime:${event.runtime}`, { kind: "runtime", id: event.runtime, state: "stopped", text: event.message });
    case "model.start":
      return set(`files:${event.job}`, {
        kind: "files", id: event.job, node: event.node, state: "running", text: `Downloading ${mb(event.bytes)}`,
        done: 0, total: event.bytes, bytes: {},
      });
    case "model.progress": {
      const job = jobs[`files:${event.job}`];
      if (!job) return jobs;
      const bytes = { ...job.bytes, [event.sha256]: event.bytes };
      return set(`files:${event.job}`, {
        kind: "files", id: event.job, bytes, done: Object.values(bytes).reduce((a, b) => a + b, 0),
        text: `${event.file ?? "a file"}: ${mb(event.bytes)}${event.total ? ` of ${mb(event.total)}` : ""}`,
      });
    }
    case "model.done":
      return set(`files:${event.job}`, { kind: "files", id: event.job, state: "done", text: "Downloaded" });
    case "model.failed":
      return set(`files:${event.job}`, { kind: "files", id: event.job, state: "failed", text: event.message, failure: failureOf(event) });
    case "model.stopped":
      return set(`files:${event.job}`, { kind: "files", id: event.job, state: "stopped", text: event.message });
    default:
      return jobs;
  }
}
