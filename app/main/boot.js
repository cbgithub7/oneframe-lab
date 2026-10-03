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
  return { root, first: app.requestSingleInstanceLock() };
}
