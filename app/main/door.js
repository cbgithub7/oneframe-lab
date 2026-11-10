// @ts-check
// The one door into the page, as functions a test can call without Electron: main.js hands it
// each engine:request. It checks who asks and what, forwards the request, and answers with a value,
// never a throw: { ok: true, result } or { ok: false, error }. Electron passes only the message of a
// thrown error to the page, and the page needs a failure's kind, reason, next and retry to say what
// happened and what to do. Every failure's kind and reason is one oneframe/errors.py declares.
//
// The app's own methods (APP_METHODS) come through the same door and are answered here, in the
// main process: `photo.pick` shows the system's open dialog and has the engine import the file by
// its content, so the page learns the import's id and the file's name, never its path.
//
// No path reaches the page (`forPage`, on every answer and every event main.js sends): the fields
// contracts/protocol.json marks "page": false are removed wherever its declaration puts them (a
// value's file, a manifest's folder, the hello's data root), and the data root, the repo and the
// home folder are written <data>, <repo> and ~ in every other string, as `npm run diagnose` does
// for its file. The page asks for a value's file by its key instead (values.js). A message the
// contract does not declare as it is still goes, and is logged once per event or method name.

import { EngineError } from "./engine.js";
import { appFailure } from "./failures.js";
import { APP_METHODS, ENGINE_METHODS } from "./methods.js";
import { logOnce, problems, stripped } from "./protocol.js";

/** @typedef {import("./failures.js").Failure} Failure */
/** @typedef {import("../protocol.js").Wire.Methods} Methods */
/**
 * The door's answer: a result, or the failure as a value.
 * @template [T=unknown]
 * @typedef {{ ok: true, result: T } | { ok: false, error: Failure }} Answer
 */
/**
 * @typedef {{ ready: boolean, request: (method: string, params: Record<string, unknown>) => Promise<unknown> }} Engine
 * @typedef {{ data: string, repo: string, home: string }} Places the folders no answer may name
 * @typedef {{ pick?: () => Promise<string | null>, places?: Places, log?: (line: string) => void }} App
 *   what main.js gives the door: the open dialog (the file chosen, or null), the folders to keep
 *   from the page, and where a message the contract does not declare is logged
 */

const SEPARATORS = String.raw`[\\/]+`;
/** Not part of a longer name, before or after: /home/ana is not in /home/anabel. */
const BEFORE = String.raw`(?<![\w.~-])`;
const AFTER = String.raw`(?![\w.~-])`;

/** @param {string} text */
const escape = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/**
 * A folder as it may be spelled in text: either separator, repeated or not, in any case.
 * @param {string} folder
 */
function spelled(folder) {
  const parts = folder.split(/[\\/]+/).filter(Boolean);
  const lead = /^[\\/]/.test(folder) ? SEPARATORS : "";
  return new RegExp(BEFORE + lead + parts.map(escape).join(SEPARATORS) + AFTER, "giu");
}

/** @type {WeakMap<Places, Array<{ re: RegExp, label: string }>>} */
const RULES = new WeakMap();

/**
 * The scrubbing rules for `places`, built once for each: every event goes through them.
 * @param {Places} places
 */
function rulesFor(places) {
  const known = RULES.get(places);
  if (known) return known;
  const named = /** @type {Array<[string, string]>} */ ([
    [places.data, "<data>"],
    [places.repo, "<repo>"],
    [places.home, "~"],
  ]).filter(([folder]) => folder.split(/[\\/]+/).filter(Boolean).length > 0);
  // The longest first: the data root and the repo are often in the home folder.
  const rules = named.sort((a, b) => b[0].length - a[0].length).map(([folder, label]) => ({ re: spelled(folder), label }));
  RULES.set(places, rules);
  return rules;
}

/**
 * `payload` as the page may see it: an event, or the door's answer to `method`, without the fields
 * the contract marks "page": false, and with no data root, repo or home folder in any string or
 * key. What the contract does not declare is logged with `log`, once per event or method name.
 * The result is the page's view (app/protocol.d.ts's Page): a door's answer stays one.
 * @template T
 * @param {T} payload
 * @param {Places} places
 * @param {{ method?: string, log?: (line: string) => void }} [how]
 * @returns {T extends Answer ? Answer : unknown}
 */
export function forPage(payload, places, { method, log } = {}) {
  if (log) {
    const name = method ?? String(/** @type {{ event?: unknown }} */ (payload)?.event);
    logOnce(name, () => problems(payload, { method }), log);
  }
  const rules = rulesFor(places);
  /** @param {string} text */
  const scrub = (text) => rules.reduce((out, { re, label }) => out.replace(re, label), text);
  /** @param {unknown} value @returns {unknown} */
  const walk = (value) => {
    if (typeof value === "string") return scrub(value);
    if (Array.isArray(value)) return value.map(walk);
    if (value === null || typeof value !== "object") return value;
    /** @type {Record<string, unknown>} */
    const out = {};
    for (const [key, item] of Object.entries(value)) out[scrub(key)] = walk(item);
    return out;
  };
  return /** @type {T extends Answer ? Answer : unknown} */ (walk(stripped(payload, { method })));
}

/**
 * `photo.pick`: the open dialog, then the engine's import of the file chosen. Answers the import's
 * id and the file's name, or null when the person chose nothing.
 * @param {Engine} engine
 * @param {() => Promise<string | null>} pick
 */
async function pickPhoto(engine, pick) {
  const chosen = await pick();
  if (chosen === null) return null;
  const imported = /** @type {Methods["inputs.import"]["answer"]} */ (await engine.request("inputs.import", { path: chosen }));
  if ("stopped" in imported) return null; // the engine is closing
  return { id: imported.id, name: imported.name };
}

/**
 * @param {Engine | null} engine
 * @param {string | undefined} sender the URL of the frame that asked
 * @param {string} page the URL of the app's own page
 * @param {unknown} method
 * @param {unknown} params
 * @param {Failure | null} [down] why the engine is not running, when the app knows: a page that
 *   subscribed after the engine failed to start learns why from its first request
 * @param {App} [app]
 * @returns {Promise<Answer>}
 */
export async function answer(engine, sender, page, method, params, down = null, app = {}) {
  const seen = (/** @type {Answer} */ reply) =>
    (app.places ? forPage(reply, app.places, { method: String(method), log: app.log }) : reply);
  if (sender !== page) return { ok: false, error: appFailure("refused", "Refused: a request from an unknown page.") };
  const own = typeof method === "string" && APP_METHODS.has(method);
  if (typeof method !== "string" || !(own || ENGINE_METHODS.has(method))) {
    return { ok: false, error: appFailure("refused", `Refused: ${String(method)}`) };
  }
  if (params !== undefined && (params === null || typeof params !== "object" || Array.isArray(params))) {
    return { ok: false, error: appFailure("refused", "Refused: params must be an object.") };
  }
  if (own && !app.pick) return { ok: false, error: appFailure("refused", `Refused: ${method} is not offered here.`) };
  if (!engine || !engine.ready) {
    // Why it did not start may name a path (uv's), so it is scrubbed like any answer.
    return seen({ ok: false, error: down ?? appFailure("not_running", "The engine is not running.", "Wait for it to start.") });
  }
  try {
    const result = own
      ? await pickPhoto(engine, /** @type {() => Promise<string | null>} */ (app.pick))
      : await engine.request(method, /** @type {Record<string, unknown>} */ (params ?? {}));
    return seen({ ok: true, result });
  } catch (error) {
    if (error instanceof EngineError) return seen({ ok: false, error: error.failure });
    return seen({ ok: false, error: { kind: "engine", message: String(error), retry: false } });
  }
}
