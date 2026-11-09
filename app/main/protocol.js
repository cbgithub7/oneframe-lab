// @ts-check
// The protocol as the app reads it: contracts/protocol.json (spec 018), the declaration
// oneframe/protocol.py holds the engine to. The door checks each message it passes to the page
// against it and strips from it the fields marked "page": false (door.js's forPage);
// scripts/protocol-types.js writes the page's types from it (app/protocol.d.ts). The contract's
// _comment gives its grammar; the engine refuses a contract with problems, and its tests hold
// this file's to it, so here it is read, not judged.

import { readFileSync } from "node:fs";
import path from "node:path";

import { REPO_ROOT } from "./paths.js";

export const CONTRACT_FILE = path.join(REPO_ROOT, "contracts", "protocol.json");

/**
 * @typedef {{ names: string[], of: Kind | null, null: boolean }} Kind a field's type: one name or
 *   several (either may do), what each item or value is, and whether it may be null
 * @typedef {{ type: Kind, optional: boolean, page: boolean, doc?: string }} Field
 * @typedef {{ fields: Map<string, Field>, open: boolean, with: Array<{ shape: string, optional: boolean }>,
 *   own: Map<string, Field>, doc?: string }} Declared a record's fields, its shapes' merged in, and
 *   its own and its shapes apart, for the types
 * @typedef {{ params: Declared, answer: Declared, by: string, queued: boolean, null: boolean, doc?: string }} Method
 * @typedef {{ shapes: Map<string, Declared>, events: Map<string, Declared & { by: string[] }>,
 *   methods: Map<string, Method> }} Contract
 */

/** @param {any} raw @returns {Kind} */
function kindOf(raw) {
  const row = typeof raw === "string" ? { type: raw } : raw;
  const names = Array.isArray(row.type) ? row.type : [row.type];
  return { names, of: row.of === undefined ? null : kindOf(row.of), null: row.null === true };
}

/** @param {any} raw @returns {Field} */
function fieldOf(raw) {
  return { type: kindOf(raw), optional: raw.optional === true, page: raw.page !== false, doc: raw.doc };
}

/**
 * @param {any} raw
 * @param {Record<string, any>} shapes the contract's shapes, as written
 * @returns {Declared}
 */
function recordOf(raw, shapes) {
  /** @type {Map<string, Field>} */
  const fields = new Map();
  const withs = (raw.with ?? []).map((/** @type {any} */ entry) =>
    typeof entry === "string" ? { shape: entry, optional: false } : { shape: entry.shape, optional: entry.optional === true });
  for (const { shape, optional } of withs) {
    for (const [name, found] of recordOf(shapes[shape], shapes).fields) {
      fields.set(name, { ...found, optional: found.optional || optional });
    }
  }
  const own = new Map(Object.entries(raw.fields ?? {}).map(([name, row]) => [name, fieldOf(row)]));
  for (const [name, found] of own) fields.set(name, found);
  return { fields, open: raw.open === true, with: withs, own, doc: raw.doc };
}

/**
 * The contract in `data`, as protocol.json holds it.
 * @param {any} data
 * @returns {Contract}
 */
export function parseContract(data) {
  const shapes = data.shapes ?? {};
  return {
    shapes: new Map(Object.entries(shapes).map(([name, row]) => [name, recordOf(row, shapes)])),
    events: new Map(Object.entries(data.events ?? {}).map(([name, row]) =>
      [name, { ...recordOf(row, shapes), by: /** @type {any} */ (row).by ?? [] }])),
    methods: new Map(Object.entries(data.methods ?? {}).map(([name, /** @type {any} */ row]) => [name, {
      params: recordOf(row.params ?? {}, shapes),
      answer: recordOf(row.answer ?? {}, shapes),
      by: String(row.by),
      queued: row.queued === true,
      null: row.null === true,
      doc: row.doc,
    }])),
  };
}

/** @param {string} [file] */
export function loadContract(file = CONTRACT_FILE) {
  return parseContract(JSON.parse(readFileSync(file, "utf8")));
}

/** Why the contract could not be read when the app started, or null. */
export let CONTRACT_PROBLEM = /** @type {string | null} */ (null);

/**
 * The contract, read once when the app starts. One that cannot be read declares nothing, so every
 * message is logged as undeclared and still goes, its local paths taken out (`loose`).
 */
export const CONTRACT = (() => {
  try {
    return loadContract();
  } catch (error) {
    CONTRACT_PROBLEM = `${CONTRACT_FILE} cannot be read: ${String(error)}`;
    return parseContract({});
  }
})();

/** @param {unknown} value */
function kindName(value) {
  if (value === null) return "null";
  if (Array.isArray(value)) return "array";
  if (typeof value === "number") return Number.isInteger(value) ? "integer" : "number";
  return typeof value === "string" || typeof value === "boolean" ? typeof value : "object";
}

/** @param {string} name @param {unknown} value */
function isA(name, value) {
  if (name === "string" || name === "boolean") return typeof value === name;
  if (name === "integer") return Number.isInteger(value);
  if (name === "number") return typeof value === "number" && Number.isFinite(value);
  if (name === "array") return Array.isArray(value);
  return value !== null && typeof value === "object" && !Array.isArray(value); // object, or a shape
}

/** @param {unknown} value @returns {value is { [key: string]: unknown }} */
const isObject = (value) => isA("object", value);

/**
 * @typedef {{ contract: Contract, name: string, page: boolean, out: string[] }} Checking `page`:
 *   the message as the page receives it, so a field marked "page": false must be absent
 */

/** @param {Checking} c @param {Kind} kind @param {unknown} value @param {string} where */
function checkValue(c, kind, value, where) {
  if (value === null || value === undefined) {
    if (!kind.null) c.out.push(`${c.name}: ${where} should be ${kind.names.join(" or ")}, not null`);
    return;
  }
  /** @type {string[][]} */
  const tried = [];
  for (const one of kind.names) {
    const found = { ...c, out: /** @type {string[]} */ ([]) };
    const shape = c.contract.shapes.get(one);
    if (!isA(one, value)) found.out.push(`${c.name}: ${where} should be ${kind.names.join(" or ")}, not ${kindName(value)}`);
    else if (shape && isObject(value)) checkFields(found, shape, value, where);
    else if (kind.of && Array.isArray(value)) value.forEach((item, i) => checkValue(found, /** @type {Kind} */ (kind.of), item, `${where}[${i}]`));
    else if (kind.of && isObject(value)) {
      for (const [key, item] of Object.entries(value)) checkValue(found, kind.of, item, `${where}.${key}`);
    }
    if (found.out.length === 0) return;
    tried.push(found.out);
  }
  c.out.push(...tried.reduce((a, b) => (b.length < a.length ? b : a))); // the type it came closest to
}

/** @param {Checking} c @param {Declared} record @param {{ [key: string]: unknown }} value @param {string} where */
function checkFields(c, record, value, where) {
  const at = (/** @type {string} */ key) => (where ? `${where}.${key}` : key);
  for (const [key, field] of record.fields) {
    if (!(key in value)) {
      if (!field.optional && !(c.page && !field.page)) c.out.push(`${c.name}: no field ${at(key)}`);
    } else if (c.page && !field.page) {
      c.out.push(`${c.name}: ${at(key)} never reaches the page`);
    } else {
      checkValue(c, field.type, value[key], at(key));
    }
  }
  if (!record.open) {
    for (const key of Object.keys(value)) if (!record.fields.has(key)) c.out.push(`${c.name}: undeclared field ${at(key)}`);
  }
}

/** An error reply's type: a failure. */
const FAILURE = /** @type {Kind} */ ({ names: ["failure"], of: null, null: false });

/**
 * What `method`'s result is declared as: null, the `stopped` shape (a queued request a stop took
 * off the queue) or its answer.
 * @param {Contract} contract @param {Method} method @param {unknown} result
 * @returns {Declared | null}
 */
function answerRecord(contract, method, result) {
  if (result === null) return null;
  const stopped = contract.shapes.get("stopped");
  if (method.queued && stopped && isObject(result) && Object.keys(result).join() === "stopped") return stopped;
  return method.answer;
}

/**
 * What is wrong with a message, against the contract: an event (no `method`), or the door's
 * answer to `method` ({ ok: true, result } or { ok: false, error }). Each problem names the event
 * or method and the field; none when it is as declared.
 * @param {unknown} message
 * @param {{ method?: string, page?: boolean, contract?: Contract }} [how] `page`: as the page
 *   receives it, after forPage
 * @returns {string[]}
 */
export function problems(message, { method, page = false, contract = CONTRACT } = {}) {
  if (!isObject(message)) return [`${method ?? "an event"}: should be an object, not ${kindName(message)}`];
  if (method === undefined) {
    const name = String(message.event);
    const record = contract.events.get(name);
    if (!record) return [`${JSON.stringify(message.event)} is not a declared event`];
    const c = { contract, name, page, out: /** @type {string[]} */ ([]) };
    const { event: _event, ...fields } = message;
    checkFields(c, record, fields, "");
    return c.out;
  }
  const c = { contract, name: method, page, out: /** @type {string[]} */ ([]) };
  if (message.ok === false) {
    checkValue(c, FAILURE, message.error, "error");
    return c.out;
  }
  const declared = contract.methods.get(method);
  if (!declared) return [`${JSON.stringify(method)} is not a declared method`];
  const record = answerRecord(contract, declared, message.result);
  if (record === null) {
    if (!declared.null) c.out.push(`${method}: the answer should be an object, not null`);
  } else if (!isObject(message.result)) {
    c.out.push(`${method}: the answer should be an object, not ${kindName(message.result)}`);
  } else {
    checkFields(c, record, message.result, "");
  }
  return c.out;
}

/** @param {Contract} contract @param {Kind} kind @param {unknown} value @returns {unknown} */
function stripValue(contract, kind, value) {
  if (Array.isArray(value)) return value.map((item) => (kind.of ? stripValue(contract, kind.of, item) : loose(item)));
  if (!isObject(value)) return value;
  const shape = kind.names.map((name) => contract.shapes.get(name)).find(Boolean);
  if (shape) return stripFields(contract, shape, value);
  if (!kind.of) return loose(value);
  const of = kind.of;
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, stripValue(contract, of, item)]));
}

/** @param {Contract} contract @param {Declared} record @param {{ [key: string]: unknown }} value */
function stripFields(contract, record, value) {
  /** @type {{ [key: string]: unknown }} */
  const out = {};
  for (const [key, item] of Object.entries(value)) {
    const field = record.fields.get(key);
    if (field && !field.page) continue;
    out[key] = field ? stripValue(contract, field.type, item) : loose(item);
  }
  return out;
}

/** @param {string} text */
const isLocalPath = (text) => path.win32.isAbsolute(text) || path.posix.isAbsolute(text);

/**
 * What the contract leaves free-form (a value's meta) or does not declare, without a field that
 * holds a local path, whatever its name: nothing says such a field may reach the page.
 * @param {unknown} value @returns {unknown}
 */
function loose(value) {
  if (Array.isArray(value)) return value.map(loose);
  if (!isObject(value)) return value;
  return Object.fromEntries(Object.entries(value)
    .filter(([, item]) => typeof item !== "string" || !isLocalPath(item))
    .map(([key, item]) => [key, loose(item)]));
}

/**
 * `message` without the fields the contract marks "page": false, wherever its declaration puts
 * them: an event (no `method`), or the door's answer to `method`. In what it leaves free-form or
 * does not declare, a field that holds a local path goes; the rest is left for the door to scrub.
 * @param {unknown} message
 * @param {{ method?: string, contract?: Contract }} [how]
 * @returns {unknown}
 */
export function stripped(message, { method, contract = CONTRACT } = {}) {
  if (!isObject(message)) return message;
  if (method === undefined) {
    const record = contract.events.get(String(message.event));
    return record ? stripFields(contract, record, message) : loose(message);
  }
  if (message.ok === false) return { ...message, error: stripValue(contract, FAILURE, message.error) };
  const declared = contract.methods.get(method);
  const record = declared ? answerRecord(contract, declared, message.result) : null;
  if (!isObject(message.result)) return message;
  return { ...message, result: record ? stripFields(contract, record, message.result) : loose(message.result) };
}

/** The names a problem was logged for: once each, for as long as the app runs. */
const LOGGED = new Set();

/**
 * Log the problems of a message the door passed on, the first time its event or method has one;
 * once it has, they are not looked for again.
 * @param {string} name @param {() => string[]} find @param {(line: string) => void} log
 */
export function logOnce(name, find, log) {
  if (LOGGED.has(name)) return;
  const found = find();
  if (found.length === 0) return;
  LOGGED.add(name);
  const why = CONTRACT_PROBLEM ? ` (${CONTRACT_PROBLEM})` : "";
  log(`not as contracts/protocol.json declares it, sent all the same${why}: ${found.join("; ")}`);
}
