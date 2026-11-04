// @ts-check
// The stop hook's view of the working tree: one way to run a command from a hook, and a
// fingerprint that changes whenever anything the checks read could have changed.

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { closeSync, openSync, readSync } from "node:fs";
import path from "node:path";

/**
 * Run a command in `root`. On Windows only npm and .cmd shims need a shell; everything else runs
 * directly, so a project path with spaces in it works.
 * @param {string} root @param {NodeJS.ProcessEnv} env @param {string} command @param {string[]} args
 */
export function run(root, env, command, args) {
  const shell = process.platform === "win32" && (command === "npm" || command.endsWith(".cmd"));
  const quote = (/** @type {string} */ s) => (shell && /\s/.test(s) ? `"${s}"` : s);
  const r = spawnSync(quote(command), args.map(quote), { cwd: root, env, encoding: "utf8", shell, maxBuffer: 64 * 1024 * 1024 });
  return { ok: r.status === 0, out: `${r.stdout ?? ""}${r.stderr ?? ""}` };
}

/**
 * A hash of the commit, the status, the diff against HEAD, and the bytes of every untracked file
 * git does not ignore. The first three hold an untracked file's name but not its contents, so
 * without the last an edit to a new file would look like no change at all.
 * @param {string} root @param {NodeJS.ProcessEnv} env
 */
export function treeFingerprint(root, env) {
  const git = (/** @type {string[]} */ ...args) => run(root, env, "git", args).out;
  const hash = createHash("sha256").update(git("rev-parse", "HEAD")).update(git("status", "--porcelain")).update(git("diff", "HEAD"));
  const untracked = git("ls-files", "-z", "--others", "--exclude-standard").split("\0").filter(Boolean).sort();
  const chunk = Buffer.alloc(1024 * 1024); // read in chunks, so a large file costs no memory
  for (const name of untracked) {
    const file = createHash("sha256");
    let fd;
    try {
      fd = openSync(path.join(root, name), "r");
      for (let n; (n = readSync(fd, chunk)) > 0;) file.update(chunk.subarray(0, n));
    } catch {
      file.update("unreadable"); // gone since git listed it, or not a regular file
    } finally {
      if (fd !== undefined) closeSync(fd);
    }
    hash.update(`\0${name}\0${file.digest("hex")}`);
  }
  return hash.digest("hex");
}
