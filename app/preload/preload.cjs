// @ts-check
// The only bridge into the page. The preload is sandboxed, so it is CommonJS and can require
// nothing but Electron's renderer modules. The page gets two functions and nothing else.

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("oneframe", {
  /**
   * @param {string} method
   * @param {Record<string, unknown>} [params]
   */
  request: (method, params) => ipcRenderer.invoke("engine:request", String(method), params ?? {}),
  /** @param {(event: any) => void} callback */
  onEvent: (callback) => {
    /** @param {unknown} _sender @param {any} payload */
    const listener = (_sender, payload) => callback(payload);
    ipcRenderer.on("engine:event", listener);
    return () => ipcRenderer.removeListener("engine:event", listener);
  },
});
