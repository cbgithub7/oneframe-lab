// @ts-check
// What a failure looks like on its way to the page, and the failures the app itself reports. Every
// kind and reason is one oneframe/errors.py declares, and docs/architecture.md lists; a test holds
// APP_FAILURES to that table.

/**
 * @typedef {{ kind: string, reason?: string, message: string, next?: string, retry: boolean,
 *   problems?: unknown[], detail?: string }} Failure
 */

/** The failures the app itself reports, each as errors.py declares it. */
export const APP_FAILURES = Object.freeze({
  refused: { kind: "request", reason: "refused", retry: false },
  not_running: { kind: "request", reason: "not_running", retry: true },
  timed_out: { kind: "request", reason: "timed_out", retry: true },
  no_uv: { kind: "app", reason: "no_uv", retry: false },
  start_failed: { kind: "app", reason: "start_failed", retry: false },
});

/**
 * @param {keyof typeof APP_FAILURES} which
 * @param {string} message
 * @param {string} [next]
 * @returns {Failure}
 */
export function appFailure(which, message, next) {
  return { ...APP_FAILURES[which], message, ...(next ? { next } : {}) };
}
