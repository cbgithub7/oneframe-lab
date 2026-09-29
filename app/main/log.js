// @ts-check
// A small rotating log file: app.log, rolled to app.1.log .. app.3.log at 5 MB.

import { appendFileSync, existsSync, mkdirSync, renameSync, rmSync, statSync } from "node:fs";
import path from "node:path";

export class RotatingLog {
  /** @param {string} file @param {{ maxBytes?: number, keep?: number }} [options] */
  constructor(file, options = {}) {
    this.file = file;
    this.maxBytes = options.maxBytes ?? 5 * 1024 * 1024;
    this.keep = options.keep ?? 3;
    mkdirSync(path.dirname(file), { recursive: true });
  }

  /** @param {string} source @param {string} line */
  write(source, line) {
    this.#rotateIfNeeded();
    appendFileSync(this.file, `${new Date().toISOString()} [${source}] ${line}\n`, "utf8");
  }

  #rotateIfNeeded() {
    if (!existsSync(this.file) || statSync(this.file).size < this.maxBytes) return;
    const { dir, name, ext } = path.parse(this.file);
    const nth = (/** @type {number} */ n) => path.join(dir, `${name}.${n}${ext}`);
    rmSync(nth(this.keep), { force: true });
    for (let n = this.keep - 1; n >= 1; n--) {
      if (existsSync(nth(n))) renameSync(nth(n), nth(n + 1));
    }
    renameSync(this.file, nth(1));
  }
}
