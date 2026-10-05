// @ts-check
// The one door into the page, as a function a test can call without Electron: main.js hands it
// each engine:request. It checks who asks and what, forwards the request, and answers with a value,
// never a throw: { ok: true, result } or { ok: false, error }. Electron passes only the message of a
// thrown error to the page, and the page needs a failure's kind, reason, next and retry to say what
// happened and what to do. Every failure's kind and reason is one oneframe/errors.py declares.

import { EngineError } from "./engine.js";
import { appFailure } from "./failures.js";
import { ENGINE_METHODS } from "./methods.js";

/** @typedef {import("./failures.js").Failure} Failure */
/** @typedef {{ ok: true, result: unknown } | { ok: false, error: Failure }} Answer */

/**
 * @param {{ ready: boolean, request: (method: string, params: Record<string, unknown>) => Promise<unknown> } | null} engine
 * @param {string | undefined} sender the URL of the frame that asked
 * @param {string} page the URL of the app's own page
 * @param {unknown} method
 * @param {unknown} params
 * @param {Failure | null} [down] why the engine is not running, when the app knows: a page that
 *   subscribed after the engine failed to start learns why from its first request
 * @returns {Promise<Answer>}
 */
export async function answer(engine, sender, page, method, params, down = null) {
  if (sender !== page) return { ok: false, error: appFailure("refused", "Refused: a request from an unknown page.") };
  if (typeof method !== "string" || !ENGINE_METHODS.has(method)) {
    return { ok: false, error: appFailure("refused", `Refused: ${String(method)}`) };
  }
  if (params !== undefined && (params === null || typeof params !== "object" || Array.isArray(params))) {
    return { ok: false, error: appFailure("refused", "Refused: params must be an object.") };
  }
  if (!engine || !engine.ready) {
    return { ok: false, error: down ?? appFailure("not_running", "The engine is not running.", "Wait for it to start.") };
  }
  try {
    return { ok: true, result: await engine.request(method, /** @type {Record<string, unknown>} */ (params ?? {})) };
  } catch (error) {
    if (error instanceof EngineError) return { ok: false, error: error.failure };
    return { ok: false, error: { kind: "engine", message: String(error), retry: false } };
  }
}
