// @ts-check
// The page's types, from contracts/protocol.json (spec 018): app/protocol.d.ts, committed.
// Run: node scripts/protocol-types.js [--write] [--contract <file>] [--types <file>]
//
// It writes two namespaces: `Wire`, each message as the engine or the app sends it, every field;
// and `Page`, as the page receives it, without the fields the contract marks "page": false, which
// the door strips (door.js). In each: a type per shape, one per event and their union `Event`,
// discriminated by `event`, and `Methods`, each method's params and answer. Without --write it
// compares the file with what the contract gives, and fails naming the lines that differ;
// `npm run check` runs it so, before tsc reads the file.

import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { CONTRACT_FILE, parseContract } from "../app/main/protocol.js";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const TYPES_FILE = path.join(ROOT, "app", "protocol.d.ts");
const WRITE = "node scripts/protocol-types.js --write";

/** @typedef {import("../app/main/protocol.js").Contract} Contract */
/** @typedef {import("../app/main/protocol.js").Declared} Declared */
/** @typedef {import("../app/main/protocol.js").Kind} Kind */

/** `graph_summary` -> `GraphSummary`, `node.step_oom` -> `NodeStepOom`. @param {string} name */
const pascal = (name) => name.split(/[._]/).map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join("");

/** @param {string} name */
const property = (name) => (/^[A-Za-z_$][\w$]*$/.test(name) ? name : JSON.stringify(name));

/** @param {string | undefined} text @param {string} indent */
const doc = (text, indent) => (text ? `${indent}/** ${text} */\n` : "");

/** @param {Kind} kind @returns {string} */
function typeText(kind) {
  const names = kind.names.map((name) => {
    if (name === "string" || name === "boolean") return name;
    if (name === "integer" || name === "number") return "number";
    if (name === "array") return kind.of ? `Array<${typeText(kind.of)}>` : "unknown[]";
    if (name === "object") return kind.of ? `{ [key: string]: ${typeText(kind.of)} }` : "{ [key: string]: unknown }";
    return pascal(name);
  });
  return [...names, ...(kind.null ? ["null"] : [])].join(" | ");
}

/**
 * A record's own fields, as the members of an object type.
 * @param {Declared} record @param {boolean} page @param {string} indent
 */
function members(record, page, indent) {
  const lines = [];
  for (const [name, field] of record.own) {
    if (page && !field.page) continue;
    const said = [field.doc, !field.page ? "Never reaches the page." : ""].filter(Boolean).join(" ");
    lines.push(`${doc(said, indent)}${indent}${property(name)}${field.optional ? "?" : ""}: ${typeText(field.type)};`);
  }
  if (record.open) lines.push(`${indent}/** Fields of its own, beside those declared. */\n${indent}[key: string]: unknown;`);
  return lines;
}

/** The shapes a record is `with`. @param {Declared} record */
const bases = (record) => record.with.map(({ shape, optional }) => (optional ? `Partial<${pascal(shape)}>` : pascal(shape)));

/**
 * A record as a type written inline: its shapes and its own fields.
 * @param {Declared} record @param {boolean} page @param {string} indent
 */
function inline(record, page, indent) {
  const own = members(record, page, `${indent}  `);
  const body = own.length ? `{\n${own.join("\n")}\n${indent}}` : "";
  const parts = [...bases(record), ...(body ? [body] : [])];
  return parts.length ? parts.join(" & ") : "{ [key: string]: never }";
}

/**
 * An interface for a record, extending its shapes.
 * @param {string} name @param {Declared} record @param {boolean} page @param {string[]} [first] members before its own
 */
function iface(name, record, page, first = []) {
  const from = bases(record);
  const head = `  export interface ${name}${from.length ? ` extends ${from.join(", ")}` : ""} {`;
  return `${doc(record.doc, "  ")}${head}\n${[...first, ...members(record, page, "    ")].join("\n")}\n  }`;
}

/** @param {Contract} contract @param {boolean} page */
function namespace(contract, page) {
  const out = [];
  for (const [name, record] of contract.shapes) out.push(iface(pascal(name), record, page));
  const events = [...contract.events.keys()];
  for (const [name, record] of contract.events) {
    const by = record.by.length ? ` Sent by the ${record.by.join(" or the ")}.` : "";
    out.push(iface(`${pascal(name)}Event`, { ...record, doc: `${record.doc ?? ""}${by}`.trim() }, page,
      [`    event: ${JSON.stringify(name)};`]));
  }
  out.push(`  /** Every event, told apart by \`event\`. */\n  export type Event =\n${events.map((e) => `    | ${pascal(e)}Event`).join("\n")};`);
  out.push("  export type EventName = Event[\"event\"];");
  const methods = [...contract.methods].map(([name, method]) => {
    const answer = [inline(method.answer, page, "      ")];
    if (method.queued) answer.push("Stopped");
    if (method.null) answer.push("null");
    const how = [method.doc, method.by === "app" ? "Answered by the app." : "", method.queued ? "Queued, so a stop may answer it {stopped: true}." : ""];
    return `${doc(how.filter(Boolean).join(" "), "    ")}    ${JSON.stringify(name)}: {\n` +
      `      params: ${inline(method.params, page, "      ")};\n      answer: ${answer.join(" | ")};\n    };`;
  });
  out.push(`  /** Every method, its params and its answer. */\n  export interface Methods {\n${methods.join("\n")}\n  }`);
  out.push("  export type MethodName = keyof Methods;");
  out.push("  export type Params<M extends MethodName> = Methods[M][\"params\"];");
  out.push("  export type Answer<M extends MethodName> = Methods[M][\"answer\"];");
  return out.join("\n\n");
}

/**
 * The text of app/protocol.d.ts for the contract in `data`.
 * @param {unknown} data
 */
export function generate(data) {
  const contract = parseContract(data);
  return [
    "// Generated from contracts/protocol.json by scripts/protocol-types.js: do not edit it by hand.",
    `// After a change to the contract, \`${WRITE}\` writes it again; \`npm run check\``,
    "// fails while it is not current.",
    "",
    "/** Each message as the engine or the app sends it, every field: what the main process reads. */",
    `export declare namespace Wire {\n${namespace(contract, false)}\n}`,
    "",
    "/** Each message as the page receives it, without the fields marked \"page\": false (door.js). */",
    `export declare namespace Page {\n${namespace(contract, true)}\n}`,
    "",
  ].join("\n");
}

/**
 * The lines where `actual` differs from `expected`, between what they share at the start and at
 * the end: what the contract gives (+) and what the file has (-), at most `most` of each.
 * @param {string} expected @param {string} actual @param {number} [most]
 * @returns {string[]}
 */
export function differences(expected, actual, most = 12) {
  if (expected === actual) return [];
  const want = expected.split("\n");
  const have = actual.split("\n");
  let start = 0;
  while (start < want.length && start < have.length && want[start] === have[start]) start++;
  let end = 0;
  while (end < want.length - start && end < have.length - start && want[want.length - 1 - end] === have[have.length - 1 - end]) end++;
  const lines = (/** @type {string[]} */ rows, /** @type {string} */ mark) => {
    const middle = rows.slice(start, rows.length - end);
    const shown = middle.slice(0, most).map((line, i) => `${mark} ${start + i + 1}: ${line.trim()}`);
    return middle.length > most ? [...shown, `${mark} ... and ${middle.length - most} more`] : shown;
  };
  return [...lines(want, "+"), ...lines(have, "-")];
}

/** @param {string[]} argv */
function main(argv) {
  const option = (/** @type {string} */ name, /** @type {string} */ fallback) => {
    const at = argv.indexOf(name);
    return at >= 0 && argv[at + 1] ? path.resolve(argv[at + 1]) : fallback;
  };
  const contractFile = option("--contract", CONTRACT_FILE);
  const typesFile = option("--types", TYPES_FILE);
  const text = generate(JSON.parse(readFileSync(contractFile, "utf8")));
  const shown = path.relative(ROOT, typesFile) || typesFile;
  if (argv.includes("--write")) {
    writeFileSync(typesFile, text);
    console.log(`protocol-types: wrote ${shown}.`);
    return 0;
  }
  let current = "";
  try {
    current = readFileSync(typesFile, "utf8");
  } catch {
    // no file: every line differs
  }
  const found = differences(text, current.replace(/\r\n/g, "\n"));
  if (found.length === 0) {
    console.log(`protocol-types: ok (${shown} is what the contract gives).`);
    return 0;
  }
  console.error(`protocol-types: ${shown} is not what ${path.relative(ROOT, contractFile) || contractFile} gives ` +
    `(+ the contract, - the file); run \`${WRITE}\`:\n${found.join("\n")}`);
  return 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2));
}
