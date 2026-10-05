// @ts-check
// The first thing the app does: put every folder Electron writes under the data root, before
// anything has written to the defaults. Electron would otherwise keep its user data, its session
// data (HTTP and GPU caches), its crash dumps, its logs and its temporary files in the person's
// Roaming or ~/.config, where removing the data root would leave them behind. The single-instance
// lock lives in the user data folder, so it is asked for only after, and is then one per root: a
// dev checkout and a packaged app never stop each other from starting.
//
// Nothing here imports Electron, so it runs under plain Node in the tests with a fake `app`.

import { mkdirSync } from "node:fs";
import { homedir } from "node:os";

import { dataRoot, layout } from "./paths.js";

/** The page's session: in memory, so the page keeps nothing on disk between starts. */
export const PAGE_PARTITION = "oneframe-page";

// On Linux, Chromium makes the single-instance socket at $TMPDIR/scoped_dirXXXXXX/SingletonSocket
// (Electron's process_singleton patch), reading TMPDIR when the lock is asked for; setPath("temp")
// does not move it. A socket path of 108 bytes or more aborts the app at start, with nothing
// JavaScript can catch, so a TMPDIR too long for it is set aside for the lock and /tmp used.
// The socket stays in the system temporary folder on purpose: an exception to the storage rule
// (AGENTS.md), since a root may be too long for a socket path, or on a file system that cannot
// hold one. SOCKET_MARGIN leaves room for a longer folder name in a later Electron.
const SOCKET_SUFFIX = Buffer.byteLength("/scoped_dirXXXXXX/SingletonSocket");
const SOCKET_MAX = 107;
const SOCKET_MARGIN = 16;

/**
 * The folder Chromium may make its single-instance socket in: the inherited TMPDIR when the
 * socket's path fits in it (counted in bytes), otherwise /tmp.
 * @param {string | undefined} inherited
 */
export function socketFolder(inherited) {
  const fits = Boolean(inherited) && String(inherited).startsWith("/")
    && Buffer.byteLength(String(inherited)) + SOCKET_SUFFIX + SOCKET_MARGIN <= SOCKET_MAX;
  return fits ? String(inherited) : "/tmp";
}

/**
 * Ask for the single-instance lock. On Linux, a TMPDIR too long for Chromium's socket is set aside
 * (to /tmp) while it is asked for, and given back after.
 * @param {Pick<import("electron").App, "requestSingleInstanceLock">} app
 * @param {NodeJS.ProcessEnv} env process.env in the app: its setter changes the environment Chromium reads
 * @param {string} platform
 */
export function requestLock(app, env, platform) {
  const inherited = env.TMPDIR;
  const setAside = platform === "linux" && inherited !== undefined && socketFolder(inherited) !== inherited;
  if (setAside) env.TMPDIR = "/tmp";
  try {
    return app.requestSingleInstanceLock();
  } finally {
    if (setAside) env.TMPDIR = inherited;
  }
}

/**
 * @typedef {Pick<import("electron").App, "isPackaged" | "setPath" | "setAppLogsPath"
 *   | "requestSingleInstanceLock">} BootApp
 */

/**
 * @param {BootApp} app
 * @param {{ env?: NodeJS.ProcessEnv, platform?: string, home?: string }} [machine]
 * @returns {{ root: string, first: boolean }} the data root, and whether this is the only instance
 *   on it
 */
export function boot(app, { env = process.env, platform = process.platform, home = homedir() } = {}) {
  const root = dataRoot(env, platform, home, app.isPackaged);
  const where = layout(root);
  /** @type {Array<[Parameters<BootApp["setPath"]>[0], string]>} */
  const folders = [
    ["userData", where.electron],
    ["sessionData", where.electron_cache],
    ["crashDumps", where.crashes],
    ["temp", where.app_tmp],
  ];
  for (const [name, folder] of folders) {
    mkdirSync(folder, { recursive: true }); // setPath refuses a folder that does not exist
    app.setPath(name, folder);
  }
  mkdirSync(where.logs, { recursive: true });
  app.setAppLogsPath(where.logs);
  return { root, first: requestLock(app, env, platform) };
}
