// @ts-check
// What a run shows, as data: each card's state from the run's events, the offer each failure
// gets, and how far each install and download has gone. Events are the page's view of the
// protocol (app/protocol.d.ts's Page, generated from contracts/protocol.json); nothing here names
// a node or a runtime, and the page asks what an offer needs from the failed step's manifest.

/** @typedef {import("../protocol.js").Page.Event} Event */
/** @typedef {import("../protocol.js").Page.Failure} Failure */
/** @typedef {import("../protocol.js").Page.Fit} Fit */
/** @typedef {import("../protocol.js").Page.Value} Value */
/**
 * @typedef {"install" | "download" | "wait" | "retry"} Action
 * @typedef {{ action: Action, for?: "runtime" | "files" }} Offer
 * @typedef {"waiting" | "fitting" | "running" | "cached" | "done" | "failed" | "skipped" | "stopped"} Phase
 * @typedef {{ phase: Phase, fit?: Fit, stage?: string, progress?: { done: number | null, total: number | null, what: string },
 *   seconds?: number, peak_vram_mb?: number | null, failure?: Failure, offer?: Offer | null, note?: string,
 *   outputs?: Record<string, Value> }} Card
 * @typedef {{ run: string | null, pending: boolean, cards: Record<string, Card>,
 *   ended: "done" | "failed" | "stopped" | null, failure?: Failure }} RunState
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
  return { run: null, pending: false, cards: {}, ended: null };
}

/** Run was pressed: the next run.start is this page's, whatever its id. @returns {RunState} */
export function starting() {
  return { ...idle(), pending: true };
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

/**
 * The run's state after one event. Events of another run are ignored.
 * @param {RunState} state @param {Event} event
 * @returns {RunState}
 */
export function runView(state, event) {
  if (!("run" in event)) return state;
  if (event.event === "run.start") {
    if (!(state.pending || state.run === event.run)) return state;
    /** @type {Record<string, Card>} */
    const cards = {};
    for (const step of event.order) cards[step] = { phase: "waiting" };
    return { run: event.run, pending: false, cards, ended: null };
  }
  if (state.run !== event.run) return state;
  const cards = { ...state.cards };
  /** @param {string} step @param {Partial<Card>} change */
  const card = (step, change) => {
    cards[step] = { ...(cards[step] ?? { phase: "waiting" }), ...change };
  };
  switch (event.event) {
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
      card(event.step, { phase: "cached", outputs: event.outputs, ...(event.fit ? { fit: event.fit } : {}) });
      break;
    case "node.done":
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
        if (cards[step] && !cards[step].outputs) card(step, { outputs });
      }
      return { ...state, cards, ended: "done" };
    case "run.failed": {
      const failure = failureOf(event);
      // A failure no node.failed told: an edge's, or the engine's own. A step's card shows it.
      if (event.step && cards[event.step]?.phase !== "failed") {
        card(event.step, { phase: "failed", failure, offer: offerFor(failure) });
      }
      return { ...state, cards, ended: "failed", failure };
    }
    case "run.stopped":
      for (const [step, row] of Object.entries(cards)) {
        if (step === event.step || row.phase === "running" || row.phase === "fitting") card(step, { phase: "stopped" });
      }
      return { ...state, cards, ended: "stopped" };
    default:
      return state;
  }
  return { ...state, cards };
}

/**
 * @typedef {{ kind: "runtime" | "files", id: string, state: "running" | "done" | "failed" | "stopped",
 *   text: string, done: number | null, total: number | null, failure?: Failure }} Job
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
      return set(`files:${event.job}`, { kind: "files", id: event.job, state: "running", text: `Downloading ${mb(event.bytes)}`, done: 0, total: event.bytes });
    case "model.progress": {
      const job = jobs[`files:${event.job}`];
      if (!job) return jobs;
      return set(`files:${event.job}`, { kind: "files", id: event.job, text: `${event.file ?? "a file"}: ${mb(event.bytes)}${event.total ? ` of ${mb(event.total)}` : ""}` });
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
