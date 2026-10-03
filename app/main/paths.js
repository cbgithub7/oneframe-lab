// @ts-check
// Where things live. Everything the app writes goes under one data root the person can see and
// remove: the node cache, model weights, runtimes, uv's own cache and Pythons, Electron's folders,
// and logs. This mirrors the engine's oneframe/layout.py, and contracts/layout.json holds the cases
// both test suites check, so the two never disagree about where a file is.

import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** The folder holding app/, engine/, contracts/ and nodes/ in development. */
export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

export const ROOT_FILE = "oneframe-root.json";
/** Per platform family: the dev root's folder name, then the packaged app's. */
const NAMES = { win32: ["OneframeLab-dev", "OneframeLab"], other: ["oneframe-lab-dev", "oneframe-lab"] };
const DRIVE = /^[A-Za-z]:\\/;
const UNC = /^\\\\[^\\]+\\[^\\]+/;

/**
 * `value` as an absolute path in the platform's spelling, with `.`, `..` and repeated or trailing
 * separators gone; null when it is not absolute (empty, relative, or on Windows a path without a
 * drive or share, such as `C:x` or `\\x`), since nothing here knows the working folder. Spelled out
 * rather than left to Node's path or Python's ntpath, which disagree at the edges.
 * @param {string | undefined} value
 * @param {string} platform
 * @returns {string | null}
 */
export function absolute(value, platform) {
  if (!value) return null;
  let sep, head, rest;
  if (platform === "win32") {
    sep = "\\";
    const text = value.replaceAll("/", sep);
    const anchor = DRIVE.exec(text) ?? UNC.exec(text);
    if (!anchor) return null;
    head = anchor[0].replace(/\\+$/, "");
    rest = text.slice(anchor[0].length);
  } else {
    sep = "/";
    if (!value.startsWith(sep)) return null;
    head = "";
    rest = value;
  }
  /** @type {string[]} */
  const parts = [];
  for (const part of rest.split(sep)) {
    if (part === "" || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  return head + sep + parts.join(sep);
}

/**
 * @param {string} base
 * @param {string} platform
 * @param {...string} names
 */
function join(base, platform, ...names) {
  const sep = platform === "win32" ? "\\" : "/";
  let trimmed = base;
  while (trimmed.endsWith(sep)) trimmed = trimmed.slice(0, -1);
  return trimmed + sep + names.join(sep);
}

/**
 * The data root for this environment, platform and home folder. ONEFRAME_DATA wins when it is
 * absolute. Otherwise, on Windows, a folder in LOCALAPPDATA (or `<home>\\AppData\\Local`); elsewhere,
 * in XDG_DATA_HOME (or `<home>/.local/share`). A variable that is empty or relative is ignored, as
 * the XDG spec says of its own. Only the app knows whether it is packaged; it passes the root it
 * chose to the engine with --data.
 * @param {NodeJS.ProcessEnv} env
 * @param {string} platform
 * @param {string} home
 * @param {boolean} packaged
 */
export function dataRoot(env, platform, home, packaged) {
  const chosen = absolute(env.ONEFRAME_DATA, platform);
  if (chosen !== null) return chosen;
  const name = NAMES[platform === "win32" ? "win32" : "other"][packaged ? 1 : 0];
  const homeDir = absolute(home, platform);
  if (homeDir === null) throw new Error(`The home folder is not an absolute path: ${JSON.stringify(home)}`);
  const base = platform === "win32"
    ? absolute(env.LOCALAPPDATA, platform) ?? join(homeDir, platform, "AppData", "Local")
    : absolute(env.XDG_DATA_HOME, platform) ?? join(homeDir, platform, ".local", "share");
  return join(base, platform, name);
}

/** @param {boolean} packaged */
export function defaultDataRoot(packaged) {
  return dataRoot(process.env, process.platform, homedir(), packaged);
}

/**
 * Every path under one data root, by name, as oneframe/layout.py's Layout names them.
 * @param {string} root
 */
export function layout(root) {
  return Object.freeze({
    root_file: path.join(root, ROOT_FILE),
    settings: path.join(root, "settings.json"),
    learned: path.join(root, "memory", "learned.json"),
    cache: path.join(root, "cache"),
    cachedir_tag: path.join(root, "cache", "CACHEDIR.TAG"),
    tmp: path.join(root, "cache", "tmp"),
    runtime_caches: path.join(root, "cache", "runtime"),
    pycache: path.join(root, "cache", "pycache"),
    electron_cache: path.join(root, "cache", "electron"),
    electron: path.join(root, "electron"),
    models: path.join(root, "models"),
    runtimes: path.join(root, "runtimes"),
    uv_cache: path.join(root, "uv", "cache"),
    uv_python: path.join(root, "uv", "python"),
    logs: path.join(root, "logs"),
    crashes: path.join(root, "logs", "crashes"),
    locks: path.join(root, "logs", "locks"),
    reports: path.join(root, "reports"),
  });
}

/**
 * Find uv: ONEFRAME_UV, then PATH. The packaged app will carry its own pinned uv; until then a
 * developer's uv is used and the engine project pins the minimum version it accepts.
 * @param {NodeJS.ProcessEnv} [env]
 * @param {NodeJS.Platform} [platform]
 * @returns {string | null}
 */
export function findUv(env = process.env, platform = process.platform) {
  if (env.ONEFRAME_UV) return env.ONEFRAME_UV;
  const exe = platform === "win32" ? "uv.exe" : "uv";
  const dirs = (env.PATH ?? env.Path ?? "").split(path.delimiter).filter(Boolean);
  const extra = [path.join(homedir(), ".local", "bin"), path.join(homedir(), ".cargo", "bin")];
  for (const dir of [...dirs, ...extra]) {
    const candidate = path.join(dir, exe);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

/**
 * The command that starts the engine in development: uv runs it from the locked project, so the
 * interpreter and every package are exactly what engine/uv.lock says.
 * @param {string} uv
 * @param {string} data
 */
export function engineCommand(uv, data) {
  const env = {
    ...process.env,
    ONEFRAME_ROOT: REPO_ROOT,
    // uv keeps its package cache and downloaded Pythons under the data root, so removing the data
    // root removes them too, and hard links between cache and runtimes stay on one volume. It writes
    // nothing outside it: no python link in the person's bin folder, no Windows registry entry.
    UV_CACHE_DIR: layout(data).uv_cache,
    UV_PYTHON_INSTALL_DIR: layout(data).uv_python,
    UV_PYTHON_INSTALL_BIN: "0",
    UV_PYTHON_INSTALL_REGISTRY: "0",
  };
  return {
    command: uv,
    args: ["run", "--project", path.join(REPO_ROOT, "engine"), "--frozen", "python", "-m", "oneframe.server",
      "--data", data],
    cwd: REPO_ROOT,
    env,
  };
}
