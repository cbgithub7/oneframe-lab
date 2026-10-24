// @ts-check
// No test disappears, or stops running, without a written reason (spec 007).
// Run: node scripts/test-guard.js [--base <ref>] [--write]
//
// Compares the tests that exist now (working tree, committed or not) with the tests at the merge
// base of HEAD and <ref> (default origin/main). A test, or a parametrized case, that existed there
// fails the check when it is gone, or when it gained a skip or an empty body -- unless a spec this
// branch changed lists it under "- Removed", which is where the reason is written. A line
// "- Renamed: `old` -> `new`" lets the old one go once the new one exists, with no more skips. A
// test may move to another file; tests match by name and case.
//
// The engine's tests are collected by pytest, which prints each ID with its flags
// (engine/tests/fixtures/listing.py), so the guard needs the engine environment: without it, or
// when collection fails, it fails. The base's IDs come from its committed list,
// engine/tests/collected.txt, read with `git show`, so the base is never collected; a base without
// the list is compared by the names in its source (`def test_*`). The list in the checkout must
// equal the collection; --write rewrites it, and cannot hide a removal, since the base's list comes
// from git. JavaScript tests are read from the source at both ends (jsEntries).
//
// What it cannot see: a skipif, or a JavaScript skip option, whose condition is widened; a skip
// inside a fixture or a helper the test calls, or called through pytest imported under another
// name; a gutted assertion such as `assert True`, which is not an empty body. Reviewers still read
// a rewritten test beside the old one.

import { execFileSync, spawnSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LIST = "engine/tests/collected.txt";
const PREFIX = "listed\t"; // engine/tests/fixtures/listing.py
const PYTEST = ["uv", "run", "--project", "engine", "--frozen", "pytest", "engine", "--collect-only", "-q", "--guard-listing"];

/** @param {string} root */
const gitIn = (root) => (/** @type {string[]} */ ...args) =>
  execFileSync("git", args, { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });

/** @param {string} file */
export function isTestFile(file) {
  return /(^|\/)test_[^/]*\.py$/.test(file) || /\.test\.(c|m)?js$/.test(file);
}

/** @param {string} file @param {string} text @returns {string[]} */
export function testNames(file, text) {
  if (file.endsWith(".py")) return [...text.matchAll(/^\s*(?:async\s+)?def\s+(test_\w+)\s*\(/gm)].map((m) => m[1]);
  return jsEntries(file, text).map((e) => e.id);
}

/**
 * JavaScript tests from source: a `test(` call where a line starts, so that one inside a string
 * or after other code is not counted. Its flags: `@skip` or `@todo` for an option (unless it is
 * `false`) or a modifier, `@options` for options passed as a variable the guard cannot read,
 * `skip()` or `todo()` for a call on its context, and `empty` for no function, or a body with
 * nothing in it or a bare `return` before anything else.
 * @param {string} file @param {string} text
 * @returns {Entry[]}
 */
export function jsEntries(file, text) {
  const starts = [...text.matchAll(/^[ \t]*test(?:\.(skip|todo|only))?\(\s*(["'`])((?:\\.|(?!\2).)*)\2/gm)];
  return starts.map((m, i) => {
    const flags = new Set(m[1] && m[1] !== "only" ? [`@${m[1]}`] : []);
    let rest = text.slice(/** @type {number} */ (m.index) + m[0].length, starts[i + 1]?.index ?? text.length);
    const comma = /^\s*,\s*/.exec(rest)?.[0].length ?? -1;
    if (comma >= 0 && rest[comma] === "{") {
      let end = comma;
      for (let depth = 0; end < rest.length; end++) {
        if (rest[end] === "{") depth++;
        else if (rest[end] === "}" && --depth === 0) break;
      }
      for (const o of rest.slice(comma + 1, end).matchAll(/(?:^|,)\s*(skip|todo)\s*(?::\s*([^,}]*))?(?=[,}]|$)/g)) {
        if (!/^(?:false|null|undefined)\s*$/.test(o[2] ?? "")) flags.add(`@${o[1]}`);
      }
      rest = rest.slice(end + 1);
    } else if (/^\s*,\s*[A-Za-z_$][\w$.]*\s*,/.test(rest)) {
      flags.add("@options");
      rest = rest.replace(/^\s*,\s*[A-Za-z_$][\w$.]*/, "");
    }
    const fn = /^\s*,\s*(?:async\s+)?(?:function\s*\w*\s*\(([^)]*)\)\s*(\{)|\(([^)]*)\)\s*=>\s*(\{)?|(\w+)\s*=>\s*(\{)?)/.exec(rest);
    if (!fn) {
      if (/^\s*\)/.test(rest)) flags.add("empty");
    } else {
      const body = rest.slice(fn[0].length);
      const context = (fn[1] ?? fn[3] ?? fn[5] ?? "").split(",")[0].trim();
      if ((fn[2] ?? fn[4] ?? fn[6]) && /^(?:\s|\/\/[^\n]*|\/\*[\s\S]*?\*\/)*(?:\}|return[ \t]*(?:;|\}|\r?\n))/.test(body)) flags.add("empty");
      if (/^\w+$/.test(context)) {
        for (const c of body.matchAll(new RegExp(`\\b${context}\\.(skip|todo)\\(`, "g"))) flags.add(`${c[1]}()`);
      }
    }
    return { id: m[3], flags: [...flags].sort(), where: file };
  });
}

/**
 * The lines of one kind ("Removed", "Renamed") in a spec: a line starting "- <kind>" and the
 * indented list items under it, inside the "## Tests" section only, so that a "- Removed" bullet
 * elsewhere in a spec (a removed feature, a decision) never allows a test to go.
 * @param {string} text @param {string} kind
 */
function testsLines(text, kind) {
  const lines = [];
  let inside = false;
  let tests = false;
  for (const line of text.split(/\r?\n/)) {
    if (/^##\s/.test(line)) {
      tests = /^##\s+Tests\b/.test(line);
      inside = false;
      continue;
    }
    if (!tests) continue;
    if (new RegExp(`^\\s*-\\s*${kind}\\b`, "i").test(line)) inside = true;
    else if (inside && !/^\s{2,}\S/.test(line)) inside = false;
    if (inside) lines.push(line);
  }
  return lines;
}

/**
 * Names listed as removed in a spec: the backticked names a "- Removed" line or an item under it
 * starts with (`` `a`, `b` and `c`: why ``), and not those in the reason after them.
 * @param {string} text
 * @returns {Set<string>}
 */
export function allowedRemovals(text) {
  const names = new Set();
  for (const line of testsLines(text, "Removed")) {
    const item = /^\s*-\s*(?:Removed\b:?\s*)?(.*)$/i.exec(line)?.[1] ?? "";
    const lead = /^`[^`]+`(?:\s*(?:,|and)?\s*`[^`]+`)*/.exec(item)?.[0] ?? "";
    for (const m of lead.matchAll(/`([^`]+)`/g)) names.add(m[1]);
  }
  return names;
}

/**
 * Renames listed in a spec: ``- Renamed: `old` -> `new` `` lines.
 * @param {string} text
 * @returns {[string, string][]}
 */
export function renamedTests(text) {
  return testsLines(text, "Renamed").flatMap((line) =>
    [...line.matchAll(/`([^`]+)`\s*->\s*`([^`]+)`/g)].map((m) => /** @type {[string, string]} */ ([m[1], m[2]])));
}

/** @typedef {{ removed: Set<string>, renamed: [string, string][] }} Allowances */

/**
 * The Removed and Renamed lines that count: those of the specs whose folder changed since the merge
 * base, in a commit on the branch, in the working tree or not yet tracked. An approved spec not yet
 * started allows nothing, nor does a done one; the template is skipped.
 * @param {string} root @param {string} base
 * @returns {Allowances}
 */
export function allowances(root, base) {
  const git = gitIn(root);
  const changed = [
    ...git("diff", "--name-only", base, "--", "specs").split("\n"),
    ...git("ls-files", "--others", "--exclude-standard", "--", "specs").split("\n"),
  ];
  const folders = new Set(changed.map((file) => /^specs\/([^/]+)\//.exec(file)?.[1] ?? "").filter((f) => f && f !== "_template"));
  /** @type {Allowances} */
  const allow = { removed: new Set(), renamed: [] };
  for (const folder of [...folders].sort()) {
    let text;
    try {
      text = readFileSync(path.join(root, "specs", folder, "spec.md"), "utf8");
    } catch {
      continue; // a folder without a spec, or one deleted
    }
    for (const name of allowedRemovals(text)) allow.removed.add(name);
    allow.renamed.push(...renamedTests(text));
  }
  return allow;
}

/**
 * A test as the guard compares it: its ID (a name, and for a parametrized engine test its case),
 * its flags (a skip or an empty body), and where it is, for messages.
 * @typedef {{ id: string, flags: string[], where: string }} Entry
 */

/** A name in a spec, without the file an older spec may have put before it. @param {string} name */
const bare = (name) => name.replace(/^[\w./-]+\.(py|c?js|mjs)::/, "");

/** Whether a name in a spec covers an ID: the ID itself, or a function and all its cases. @param {string} name @param {string} id */
const covers = (name, id) => id === name || id.startsWith(`${name}[`);

/**
 * What the guard fails on: a test that is gone, or gained a flag, with no "- Removed" line; and a
 * "- Renamed" line whose old test is gone while the new one does not exist, or gained a flag the
 * old one did not have. Tests match by ID, not by file, and an ID listed twice counts twice.
 * @param {Entry[]} before @param {Entry[]} after @param {Allowances} allow
 * @returns {string[]}
 */
export function problems(before, after, allow) {
  const removed = [...allow.removed].map(bare);
  const allowed = (/** @type {string} */ id) => removed.some((name) => covers(name, id));
  /** @type {Map<string, Entry[]>} */
  const all = new Map();
  for (const e of after) all.set(e.id, [...(all.get(e.id) ?? []), e]);
  const left = new Map([...all].map(([id, list]) => [id, [...list]]));
  const within = (/** @type {string[]} */ a, /** @type {string[]} */ b) => a.every((f) => b.includes(f));
  /** Take a test of this ID with the same flags, or with no flag these lack. @param {Entry} b @param {boolean} same */
  const take = (b, same) => {
    const list = left.get(b.id) ?? [];
    const i = list.findIndex((e) => within(e.flags, b.flags) && (!same || within(b.flags, e.flags)));
    return i >= 0 && list.splice(i, 1).length > 0;
  };
  const gained = (/** @type {Entry} */ e, /** @type {string[]} */ flags) => e.flags.filter((f) => !flags.includes(f)).join(" ");
  const out = [];
  for (const b of before.filter((e) => !take(e, true)).filter((e) => !take(e, false))) {
    if (allowed(b.id)) continue;
    const rename = allow.renamed.map(([o, n]) => [bare(o), bare(n)]).find(([o]) => covers(o, b.id));
    if (rename) {
      const target = rename[1] + b.id.slice(rename[0].length);
      const now = all.get(target) ?? [];
      if (now.some((e) => within(e.flags, b.flags))) continue;
      if (!now.length) out.push(`${b.where}: ${b.id} is renamed to ${target}, which does not exist`);
      else if (!allowed(target)) out.push(`${b.where}: ${b.id} is renamed to ${target}, which gained ${gained(now[0], b.flags)}`);
    } else {
      const flagged = left.get(b.id)?.shift();
      out.push(flagged ? `${b.where}: ${b.id} gained ${gained(flagged, b.flags)}` : `${b.where}: ${b.id} is gone`);
    }
  }
  return out;
}

/**
 * @param {Map<string, string[]>} before  file -> names
 * @param {Map<string, string[]>} after
 * @param {Set<string>} allowed
 */
export function removedTests(before, after, allowed) {
  const now = new Set([...after.values()].flat());
  const removed = [];
  for (const [file, names] of before) {
    for (const name of names) {
      if (!now.has(name) && !allowed.has(name) && !allowed.has(`${file}::${name}`)) removed.push(`${file}::${name}`);
    }
  }
  return removed;
}

/**
 * The engine's tests, collected: one line per ID with its flags, sorted. Throws, saying why, when
 * the command cannot run or collection fails.
 * @param {string} root @param {string[]} command
 * @returns {string[]}
 */
export function collectPython(root, command = PYTEST) {
  const r = spawnSync(command[0], command.slice(1), { cwd: root, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 });
  if (r.error) throw new Error(`cannot collect the engine's tests: ${r.error.message}. It needs the engine environment (npm run engine:sync).`);
  if (r.status !== 0) throw new Error(`collecting the engine's tests failed:\n${`${r.stdout}${r.stderr}`.trim()}`);
  const lines = r.stdout.split(/\r?\n/).filter((l) => l.startsWith(PREFIX)).map((l) => l.slice(PREFIX.length));
  if (!lines.length) throw new Error(`collecting the engine's tests listed none; ${command.join(" ")} printed:\n${r.stdout.trim()}`);
  return lines;
}

/** @param {string} line @returns {Entry} */
function collected(line) {
  const [id, flags = ""] = line.split("\t");
  return { id, flags: flags ? flags.split(" ") : [], where: "engine" };
}

/**
 * The guard in a checkout, with its messages and exit code. `write` rewrites the committed list
 * first; `collect` stands in for pytest in tests.
 * @param {{ root: string, baseRef: string, write?: boolean, collect?: (root: string) => string[] }} options
 * @returns {{ code: number, out: string[] }}
 */
export function guard({ root, baseRef, write = false, collect = collectPython }) {
  const git = gitIn(root);
  let base = "";
  try {
    base = git("merge-base", "HEAD", baseRef).trim();
  } catch {
    if (!write) return { code: 0, out: [`test-guard: no ${baseRef} to compare with; skipped.`] };
  }
  let live;
  try {
    live = collect(root);
  } catch (e) {
    return { code: 1, out: [`test-guard: ${e instanceof Error ? e.message : e}`] };
  }
  /** @param {string} file */
  const readNow = (file) => {
    try {
      return readFileSync(path.join(root, file), "utf8");
    } catch {
      return null; // listed but deleted in the working tree: it has no tests now
    }
  };
  const text = live.map((line) => `${line}\n`).join("");
  if (write) {
    mkdirSync(path.dirname(path.join(root, LIST)), { recursive: true });
    writeFileSync(path.join(root, LIST), text);
  }
  if (!base) return { code: 0, out: [`test-guard: wrote ${LIST}; no ${baseRef} to compare with, so nothing else is checked.`] };
  const stale = readNow(LIST) !== text;
  const allow = allowances(root, base);
  const isJs = (/** @type {string} */ f) => !f.endsWith(".py");
  const thenFiles = git("ls-tree", "-r", "--name-only", base).split("\n").filter(isTestFile);
  const nowFiles = git("ls-files", "--cached", "--others", "--exclude-standard").split("\n").filter(isTestFile);
  const jsBefore = thenFiles.filter(isJs).flatMap((f) => jsEntries(f, git("show", `${base}:${f}`)));
  const jsAfter = nowFiles.filter(isJs).flatMap((f) => jsEntries(f, readNow(f) ?? ""));
  const found = problems(jsBefore, jsAfter, allow);
  const out = [];
  let list = null;
  try {
    list = git("show", `${base}:${LIST}`);
  } catch {
    // a base from before spec 007
  }
  let counts;
  if (list !== null) {
    const before = list.split("\n").filter(Boolean).map(collected);
    found.push(...problems(before, live.map(collected), allow));
    counts = [before.length, live.length];
  } else {
    out.push(`test-guard: ${baseRef} has no ${LIST}; its engine tests are compared by the names in their source.`);
    const names = (/** @type {string[]} */ files, /** @type {(f: string) => string} */ read) =>
      new Map(files.filter((f) => !isJs(f)).map((f) => [f, testNames(f, read(f))]));
    const before = names(thenFiles, (f) => git("show", `${base}:${f}`));
    const after = names(nowFiles, (f) => readNow(f) ?? "");
    found.push(...removedTests(before, after, allow.removed).map((r) => `${r} is gone`));
    counts = [before, after].map((m) => [...m.values()].flat().length);
  }
  if (found.length) {
    out.push(`test-guard: ${found.length} test(s) at ${baseRef} are gone, skipped or emptied, with no line in a spec this branch changed:`);
    out.push(...found.map((f) => `  ${f}`));
    out.push("Restore them, or list each in the Tests section of the spec being worked on: under \"- Removed:\" with the reason, or as \"- Renamed: `old` -> `new`\".");
  }
  if (stale) out.push(`test-guard: ${LIST} differs from the tests collected now. Run node scripts/test-guard.js --write, and commit it.`);
  if (found.length || stale) return { code: 1, out };
  out.push(`test-guard: ok (${jsBefore.length + counts[0]} tests at ${baseRef}, ${jsAfter.length + counts[1]} now).`);
  return { code: 0, out };
}

function main() {
  const at = process.argv.indexOf("--base");
  const baseRef = at > 0 ? process.argv[at + 1] : (process.env.TEST_GUARD_BASE ?? "origin/main");
  const { code, out } = guard({ root: ROOT, baseRef, write: process.argv.includes("--write") });
  for (const line of out) console.log(line);
  process.exitCode = code;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
