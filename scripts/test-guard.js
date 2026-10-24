// @ts-check
// No test disappears without a written reason. Run: node scripts/test-guard.js [--base <ref>]
//
// Compares the tests that exist now (working tree, committed or not) with the tests at the merge
// base of HEAD and <ref> (default origin/main). A test that existed there and exists nowhere now
// fails the check -- unless a spec this branch changed names it under "- Removed", which is where
// the reason is written. Moving a test to another file is fine; its name still exists.
//
// Test names are read from the source, not by running the suites, so the check is fast and
// works with or without the engine environment: `def test_*` in Python test files and
// `test("...")` in JavaScript test files.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** @param {string} root */
const gitIn = (root) => (/** @type {string[]} */ ...args) =>
  execFileSync("git", args, { cwd: root, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
const git = gitIn(ROOT);

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
 * or after other code is not counted. Its flags: `@skip` or `@todo` for an option or a modifier,
 * `skip()` or `todo()` for a call on its context, and `empty` for no function, or a body with
 * nothing in it or that returns before anything else.
 * @param {string} file @param {string} text
 * @returns {Entry[]}
 */
export function jsEntries(file, text) {
  const starts = [...text.matchAll(/^[ \t]*test(?:\.(skip|todo|only))?\(\s*(["'`])((?:\\.|(?!\2).)*)\2/gm)];
  return starts.map((m, i) => {
    const flags = new Set(m[1] && m[1] !== "only" ? [`@${m[1]}`] : []);
    let rest = text.slice(/** @type {number} */ (m.index) + m[0].length, starts[i + 1]?.index ?? text.length);
    const options = /^\s*,\s*\{([^{}]*)\}/.exec(rest);
    if (options) {
      for (const o of options[1].matchAll(/(?:^|,)\s*(skip|todo)\s*(?=[:,]|$)/g)) flags.add(`@${o[1]}`);
      rest = rest.slice(options[0].length);
    }
    const fn = /^\s*,\s*(?:async\s+)?(?:function\s*\w*\s*\(([^)]*)\)\s*(\{)|\(([^)]*)\)\s*=>\s*(\{)?|(\w+)\s*=>\s*(\{)?)/.exec(rest);
    if (!fn) {
      if (/^\s*\)/.test(rest)) flags.add("empty");
    } else {
      const body = rest.slice(fn[0].length);
      const context = (fn[1] ?? fn[3] ?? fn[5] ?? "").split(",")[0].trim();
      if ((fn[2] ?? fn[4] ?? fn[6]) && /^(?:\s|\/\/[^\n]*|\/\*[\s\S]*?\*\/)*(?:\}|return\b)/.test(body)) flags.add("empty");
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
 * Names listed as removed in a spec: the backticked names on its "- Removed" lines.
 * @param {string} text
 * @returns {Set<string>}
 */
export function allowedRemovals(text) {
  return new Set(testsLines(text, "Removed").flatMap((line) => [...line.matchAll(/`([^`]+)`/g)].map((m) => m[1])));
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
  const left = new Map();
  for (const e of after) left.set(e.id, [...(left.get(e.id) ?? []), e]);
  const within = (/** @type {string[]} */ a, /** @type {string[]} */ b) => a.every((f) => b.includes(f));
  /** Take an entry of this ID with the same flags, or else with no flag these lack. @param {string} id @param {string[]} flags */
  const take = (id, flags) => {
    const list = left.get(id) ?? [];
    let i = list.findIndex((e) => within(e.flags, flags) && within(flags, e.flags));
    if (i < 0) i = list.findIndex((e) => within(e.flags, flags));
    return i >= 0 && list.splice(i, 1).length > 0;
  };
  const gained = (/** @type {string} */ id, /** @type {string[]} */ flags) =>
    (left.get(id)?.[0]?.flags ?? []).filter((f) => !flags.includes(f)).join(" ");
  const out = [];
  for (const b of before.filter((e) => !take(e.id, e.flags))) {
    if (allowed(b.id)) continue;
    const rename = allow.renamed.map(([o, n]) => [bare(o), bare(n)]).find(([o]) => covers(o, b.id));
    if (rename) {
      const target = rename[1] + b.id.slice(rename[0].length);
      if (take(target, b.flags)) continue;
      if (!left.get(target)?.length) out.push(`${b.where}: ${b.id} is renamed to ${target}, which does not exist`);
      else if (!allowed(target)) out.push(`${b.where}: ${b.id} is renamed to ${target}, which gained ${gained(target, b.flags)}`);
    } else if (left.get(b.id)?.length) {
      out.push(`${b.where}: ${b.id} gained ${gained(b.id, b.flags)}`);
    } else {
      out.push(`${b.where}: ${b.id} is gone`);
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

function main() {
  const at = process.argv.indexOf("--base");
  const baseRef = at > 0 ? process.argv[at + 1] : (process.env.TEST_GUARD_BASE ?? "origin/main");
  let base;
  try {
    base = git("merge-base", "HEAD", baseRef).trim();
  } catch {
    console.log(`test-guard: no ${baseRef} to compare with; skipped.`);
    return;
  }
  const before = new Map();
  for (const file of git("ls-tree", "-r", "--name-only", base).split("\n").filter(isTestFile)) {
    before.set(file, testNames(file, git("show", `${base}:${file}`)));
  }
  const after = new Map();
  const files = git("ls-files", "--cached", "--others", "--exclude-standard").split("\n").filter(isTestFile);
  for (const file of files) {
    try {
      after.set(file, testNames(file, readFileSync(path.join(ROOT, file), "utf8")));
    } catch {
      // listed but deleted in the working tree: it has no tests now
    }
  }
  const removed = removedTests(before, after, allowances(ROOT, base).removed);
  const count = (/** @type {Map<string, string[]>} */ m) => [...m.values()].flat().length;
  if (removed.length) {
    console.log(`test-guard: ${removed.length} test(s) that exist at ${baseRef} are gone, with no reason in a spec:`);
    for (const r of removed) console.log(`  ${r}`);
    console.log("Restore them, or list each under \"- Removed:\" in the spec's Tests section with the reason.");
    process.exitCode = 1;
    return;
  }
  console.log(`test-guard: ok (${count(before)} tests at ${baseRef}, ${count(after)} now).`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
