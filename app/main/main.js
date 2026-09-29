// @ts-check
// Electron's main process: one window, one engine, and a single narrow door between them.
//
// The renderer is sandboxed and isolated, and can do exactly two things: ask the engine one of the
// methods listed in ENGINE_METHODS, and listen to the engine's events. It cannot read files, start
// processes or open windows. Every argument it sends is checked here before the engine sees it.

import { app, BrowserWindow, ipcMain, session, shell } from "electron";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { EngineClient } from "./engine.js";
import { RotatingLog } from "./log.js";
import { REPO_ROOT, dataRoot, engineCommand, findUv } from "./paths.js";

/** What the renderer may ask the engine. Anything else is refused here. */
export const ENGINE_METHODS = new Set([
  "engine.hello",
  "nodes.list",
  "nodes.reload",
  "ports.list",
  "graph.validate",
  "graph.run",
  "run.stop",
  "runtimes.list",
  "runtimes.plan",
  "runtimes.install",
  "runtimes.stop",
  "runtimes.remove",
]);

const RENDERER = path.join(REPO_ROOT, "app", "renderer", "index.html");
const RENDERER_URL = pathToFileURL(RENDERER).href;
const EXTERNAL_HOSTS = new Set(["github.com"]);

// ONEFRAME_NO_SANDBOX=1 is for automated tests in containers that run as root, where Chromium's
// OS-level sandbox cannot start. It never applies otherwise; the page stays isolated either way.
if (process.env.ONEFRAME_NO_SANDBOX === "1") app.commandLine.appendSwitch("no-sandbox");
else app.enableSandbox();
if (!app.requestSingleInstanceLock()) app.quit();

const data = dataRoot();
const log = new RotatingLog(path.join(data, "logs", "app.log"));
/** @type {EngineClient | null} */
let engine = null;
/** @type {BrowserWindow | null} */
let win = null;

process.on("uncaughtException", (error) => log.write("main", `uncaught: ${error.stack ?? error}`));
process.on("unhandledRejection", (reason) => log.write("main", `unhandled rejection: ${String(reason)}`));

/** @param {Record<string, unknown>} payload */
function send(payload) {
  if (win && !win.isDestroyed()) win.webContents.send("engine:event", payload);
}

async function startEngine() {
  const uv = findUv();
  if (!uv) {
    send({ event: "engine.failed", message: "uv was not found. Install uv, or set ONEFRAME_UV to its path." });
    return;
  }
  const spec = engineCommand(uv, data);
  const client = new EngineClient({ ...spec, requestTimeoutMs: 60_000 });
  engine = client;
  client.on("log", (line) => log.write("engine", line));
  client.on("event", (event) => send(event));
  client.on("exit", (info) => {
    log.write("main", `engine exited: ${JSON.stringify(info)}`);
    send({ event: "engine.exit", code: info.code });
  });
  try {
    const ready = await client.start();
    log.write("main", `engine ready: ${JSON.stringify(ready)}`);
    send(ready);
  } catch (error) {
    log.write("main", `engine failed to start: ${String(error)}`);
    send({ event: "engine.failed", message: String(error) });
  }
}

function createWindow() {
  win = new BrowserWindow({
    width: 1400,
    height: 900,
    title: "Oneframe Lab",
    backgroundColor: "#16181c",
    webPreferences: {
      preload: path.join(REPO_ROOT, "app", "preload", "preload.cjs"),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
      spellcheck: false,
    },
  });
  win.removeMenu();
  win.loadFile(RENDERER);
  win.on("closed", () => {
    win = null;
  });
}

app.on("web-contents-created", (_event, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    try {
      if (EXTERNAL_HOSTS.has(new URL(url).hostname)) shell.openExternal(url);
    } catch {
      // not a URL: nothing to open
    }
    return { action: "deny" };
  });
  contents.on("will-navigate", (event, url) => {
    if (url !== RENDERER_URL) event.preventDefault();
  });
});

ipcMain.handle("engine:request", async (event, method, params) => {
  if (event.senderFrame?.url !== RENDERER_URL) throw new Error("Refused: request from an unknown page.");
  if (typeof method !== "string" || !ENGINE_METHODS.has(method)) throw new Error(`Refused: ${String(method)}`);
  if (params !== undefined && (params === null || typeof params !== "object" || Array.isArray(params))) {
    throw new Error("Refused: params must be an object.");
  }
  if (!engine || !engine.ready) throw new Error("The engine is not running.");
  return engine.request(method, params ?? {});
});

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  createWindow();
  startEngine();
});

app.on("second-instance", () => {
  if (win) {
    if (win.isMinimized()) win.restore();
    win.focus();
  }
});

app.on("window-all-closed", () => app.quit());

app.on("will-quit", (event) => {
  if (!engine || !engine.ready) return;
  event.preventDefault();
  const closing = engine;
  engine = null;
  closing.stop().finally(() => app.quit());
});
