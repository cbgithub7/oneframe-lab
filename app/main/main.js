// @ts-check
// Electron's main process: one window, one engine, and a single narrow door between them.
//
// The renderer is sandboxed and isolated, and can do exactly two things: ask the engine one of the
// methods listed in ENGINE_METHODS (methods.js), and listen to the engine's events. It cannot read
// files, start processes or open windows. The method and the shape of its arguments are checked
// in door.js; the engine checks their values. Every answer is a value, a failure included.

import { app, BrowserWindow, ipcMain, session, shell } from "electron";
import path from "node:path";
import { pathToFileURL } from "node:url";

import { PAGE_PARTITION, boot } from "./boot.js";
import { answer } from "./door.js";
import { EngineClient, EngineError } from "./engine.js";
import { appFailure } from "./failures.js";
import { RotatingLog } from "./log.js";
import { REPO_ROOT, engineCommand, findUv } from "./paths.js";

const RENDERER = path.join(REPO_ROOT, "app", "renderer", "index.html");
const RENDERER_URL = pathToFileURL(RENDERER).href;
const EXTERNAL_HOSTS = new Set(["github.com"]);

// ONEFRAME_NO_SANDBOX=1 is for automated tests in containers that run as root, where Chromium's
// OS-level sandbox cannot start. It never applies otherwise; the page stays isolated either way.
if (process.env.ONEFRAME_NO_SANDBOX === "1") app.commandLine.appendSwitch("no-sandbox");
else app.enableSandbox();

// Electron's folders under the data root first (the dev root in a checkout, the app's own root once
// packaged; ONEFRAME_DATA overrides either), then the single-instance lock, which is one per root.
const { root: data, first } = boot(app);
if (!first) app.quit();
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
    send({ event: "engine.failed", ...appFailure("no_uv", "uv was not found.", "Install uv, or set ONEFRAME_UV to its path.") });
    return;
  }
  const spec = engineCommand(uv, data, app.isPackaged);
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
    // An engine that said why it refused has already sent its own engine.failed to the page.
    if (!(error instanceof EngineError)) send({ event: "engine.failed", ...appFailure("start_failed", String(error)) });
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
      partition: PAGE_PARTITION,
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

// Answered with a value, a failure included, so its kind, reason and next reach the page (door.js).
ipcMain.handle("engine:request", (event, method, params) =>
  answer(engine, event.senderFrame?.url, RENDERER_URL, method, params));

app.whenReady().then(() => {
  for (const each of [session.defaultSession, session.fromPartition(PAGE_PARTITION)]) {
    each.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  }
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
