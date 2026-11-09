// @ts-check
// The only bridge into the page. The preload is sandboxed, so it is CommonJS and can require
// nothing but Electron's renderer modules. The page gets two functions and nothing else.

const { contextBridge, ipcRenderer } = require("electron");

/**
 * The page's view of the protocol (app/protocol.d.ts, from contracts/protocol.json), and the door's
 * answer (app/main/door.js); types only, so nothing is required for them.
 * @typedef {import("../protocol.js").Page.MethodName} MethodName
 * @typedef {import("../protocol.js").Page.Event} Event
 * @typedef {import("../protocol.js").Page.Failure} Failure
 */
/**
 * @template {MethodName} M
 * @typedef {import("../protocol.js").Page.Params<M>} Params
 */
/**
 * @template {MethodName} M
 * @typedef {import("../protocol.js").Page.Answer<M>} Answer
 */

/** @type {Failure} */
const NO_ANSWER = { kind: "request", reason: "refused", message: "The app gave no answer.", retry: false };

contextBridge.exposeInMainWorld("oneframe", {
  /**
   * Resolves with the engine's result, or rejects with the failure itself, a plain object: kind,
   * reason, message, next and retry (app/main/door.js), so the page can say what to do.
   * @template {MethodName} M
   * @param {M} method
   * @param {Params<M>} [params]
   * @returns {Promise<Answer<M>>}
   */
  request: async (method, params) => {
    const reply = /** @type {import("../main/door.js").Answer<Answer<M>> | undefined} */ (
      await ipcRenderer.invoke("engine:request", String(method), params ?? {}));
    if (reply?.ok) return reply.result;
    throw reply?.error ?? NO_ANSWER;
  },
  /** @param {(event: Event) => void} callback */
  onEvent: (callback) => {
    /** @param {unknown} _sender @param {Event} payload */
    const listener = (_sender, payload) => callback(payload);
    ipcRenderer.on("engine:event", listener);
    return () => ipcRenderer.removeListener("engine:event", listener);
  },
});
