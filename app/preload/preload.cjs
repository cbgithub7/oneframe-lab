// @ts-check
// The only bridge into the page. The preload is sandboxed, so it is CommonJS and can require
// nothing but Electron's renderer modules. The page gets two functions and nothing else.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("oneframe", {
  /**
   * Resolves with the engine's result, or rejects with the failure itself, a plain object: kind,
   * reason, message, next and retry (app/main/door.js), so the page can say what to do.
   * @param {string} method
   * @param {Record<string, unknown>} [params]
   */
  request: async (method, params) => {
    const reply = await ipcRenderer.invoke("engine:request", String(method), params ?? {});
    if (reply?.ok) return reply.result;
    throw reply?.error ?? { kind: "request", reason: "refused", message: "The app gave no answer.", retry: false };
  },
  /** @param {(event: any) => void} callback */
  onEvent: (callback) => {
    /** @param {unknown} _sender @param {any} payload */
    const listener = (_sender, payload) => callback(payload);
    ipcRenderer.on("engine:event", listener);
    return () => ipcRenderer.removeListener("engine:event", listener);
  },
});
