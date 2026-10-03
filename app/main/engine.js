// @ts-check
// The app's side of the engine: one long-lived Python process, NDJSON both ways.
//
// A request is written as one line with an id and answered by one line with the same id.
// Everything else the engine writes is an event (run progress, node output) and is emitted as
// `event`. Nothing here imports Electron, so it runs under plain Node in the tests.

import { spawn as nodeSpawn } from "node:child_process";
import { EventEmitter } from "node:events";

import { APP_FAILURES } from "./failures.js";

/** @typedef {import("./failures.js").Failure} Failure */

/**
 * @typedef {{ command: string, args: string[], cwd?: string, env?: NodeJS.ProcessEnv,
 *   spawn?: typeof nodeSpawn, requestTimeoutMs?: number }} EngineOptions
 * @typedef {{ resolve: (value: any) => void, reject: (reason: Error) => void,
 *   timer: NodeJS.Timeout | undefined, method: string }} Pending
 */

/**
 * A failure the engine reported, or the client found (a timeout, an engine that stopped), with its
 * declared kind and reason (oneframe/errors.py). `failure` is all of it, as the page receives it.
 */
export class EngineError extends Error {
  /**
   * @param {string} message
   * @param {Partial<Failure> & { problems?: Array<{node?: string, port?: string, message: string}> }} [detail]
   */
  constructor(message, detail = {}) {
    super(message);
    this.name = "EngineError";
    this.problems = detail.problems ?? [];
    this.kind = detail.kind ?? "engine";
    this.reason = detail.reason;
    /** @type {Failure} */
    this.failure = { ...detail, kind: this.kind, message, retry: detail.retry ?? false };
  }
}

/** Splits a byte stream into lines, however the chunks happen to be cut. */
export class LineSplitter {
  constructor() {
    this.buffer = "";
  }

  /** @param {string} chunk @returns {string[]} */
  push(chunk) {
    this.buffer += chunk;
    const lines = this.buffer.split("\n");
    this.buffer = lines.pop() ?? "";
    return lines.map((line) => line.replace(/\r$/, "")).filter((line) => line.length > 0);
  }
}

export class EngineClient extends EventEmitter {
  /** @param {EngineOptions} options */
  constructor(options) {
    super();
    this.options = options;
    /** @type {import("node:child_process").ChildProcessWithoutNullStreams | null} */
    this.child = null;
    /** @type {Map<number, Pending>} */
    this.pending = new Map();
    this.nextId = 1;
    this.ready = false;
  }

  /**
   * Start the process. Resolves with the engine's `engine.ready` event; rejects with an EngineError
   * when the engine says it cannot start (`engine.failed`, such as a data root laid out by a newer
   * version), or with an Error when it exits without a word.
   */
  start() {
    if (this.child) throw new Error("The engine is already running.");
    const spawn = this.options.spawn ?? nodeSpawn;
    const child = spawn(this.options.command, this.options.args, {
      cwd: this.options.cwd,
      env: { ...(this.options.env ?? process.env), PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    this.child = child;
    const out = new LineSplitter();
    const err = new LineSplitter();
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      for (const line of out.push(chunk)) this.#onLine(line);
    });
    child.stderr.on("data", (chunk) => {
      for (const line of err.push(chunk)) this.emit("log", line);
    });
    child.on("exit", (code, signal) => this.#onExit(code, signal));
    child.on("error", (error) => this.#onExit(null, null, error));
    return new Promise((resolve, reject) => {
      /** @type {EngineError | null} */
      let refused = null;
      /** @param {any} event */
      const onEvent = (event) => {
        if (event.event === "engine.failed") refused = new EngineError(String(event.message), event);
        if (event.event !== "engine.ready") return;
        this.off("event", onEvent);
        this.off("exit", onExit);
        this.ready = true;
        resolve(event);
      };
      /** @param {{ code: number | null, error?: Error }} info */
      const onExit = (info) => {
        this.off("event", onEvent);
        reject(info.error ?? refused ?? new Error(`The engine exited before it was ready (code ${info.code}).`));
      };
      this.on("event", onEvent);
      this.once("exit", onExit);
    });
  }

  /**
   * Ask the engine something. Resolves with its result or rejects with an EngineError.
   * @param {string} method
   * @param {Record<string, unknown>} [params]
   * @returns {Promise<any>}
   */
  request(method, params = {}) {
    const child = this.child;
    if (!child || !this.ready) return Promise.reject(new EngineError("The engine is not running.", APP_FAILURES.not_running));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timeoutMs = this.options.requestTimeoutMs ?? 30_000;
      const timer = timeoutMs > 0
        ? setTimeout(() => {
          this.pending.delete(id);
          reject(new EngineError(`The engine did not answer ${method} within ${timeoutMs} ms.`, APP_FAILURES.timed_out));
        }, timeoutMs)
        : undefined;
      this.pending.set(id, { resolve, reject, timer, method });
      child.stdin.write(`${JSON.stringify({ id, method, params })}\n`);
    });
  }

  /** Close stdin (the engine exits when it ends) and kill it if it has not gone in time. */
  async stop(graceMs = 3000) {
    const child = this.child;
    if (!child) return;
    const gone = new Promise((resolve) => child.once("exit", resolve));
    child.stdin.end();
    const timer = setTimeout(() => child.kill(), graceMs);
    await gone;
    clearTimeout(timer);
  }

  /** @param {string} line */
  #onLine(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      this.emit("log", `engine wrote a line that is not JSON: ${line.slice(0, 200)}`);
      return;
    }
    if (message === null || typeof message !== "object" || Array.isArray(message)) {
      this.emit("log", `engine wrote a line that is not an object: ${line.slice(0, 200)}`);
      return;
    }
    // A reply carries the id of its request, and an event never does. A reply to a request that
    // has already timed out is logged, never passed on to the page as an event.
    if (typeof message.id === "number" && !("event" in message)) {
      const pending = this.pending.get(message.id);
      if (!pending) {
        this.emit("log", `the engine answered request ${message.id} after it had timed out`);
        return;
      }
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) pending.reject(new EngineError(message.error.message, message.error));
      else pending.resolve(message.result);
      return;
    }
    this.emit("event", message);
  }

  /** @param {number | null} code @param {NodeJS.Signals | null} signal @param {Error} [error] */
  #onExit(code, signal, error) {
    if (!this.child) return;
    this.child = null;
    this.ready = false;
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new EngineError(`The engine stopped while answering ${pending.method}.`, APP_FAILURES.not_running));
    }
    this.pending.clear();
    this.emit("exit", { code, signal, error });
  }
}
