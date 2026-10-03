// @ts-check
// No test disappears without a written reason. Run: node scripts/test-guard.js [--base <ref>]
//
// Compares the tests that exist now (working tree, committed or not) with the tests at the merge
// base of HEAD and <ref> (default origin/main). A test that existed there and exists nowhere now
// fails the check -- unless a spec names it under "Removed", which is where the reason is written:
// in spec.md, or in plan.md for the specs written before spec and plan became one document. Moving a test to another file is fine; its name still exists.
//
// Test names are read from the source, not by running the suites, so the check is fast and
// works with or without the engine environment: `def test_*` in Python test files and
// `test("...")` in JavaScript test files.

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

/** @param {string} file */
export function isTestFile(file) {
  return /(^|\/)test_[^/]*\.py$/.test(file) || /\.test\.(c|m)?js$/.test(file);
}

/** @param {string} file @param {string} text @returns {string[]} */
export function testNames(file, text) {
  if (file.endsWith(".py")) return [...text.matchAll(/^\s*(?:async\s+)?def\s+(test_\w+)\s*\(/gm)].map((m) => m[1]);
  return [...text.matchAll(/\btest\(\s*(["'`])((?:\\.|(?!\1).)*)\1/g)].map((m) => m[2]);
}

/**
 * Names listed as removed in a spec: backticked names on a line starting "- Removed", and on the
 * indented list items under it.
 * @param {string} text
 * @returns {Set<string>}
 */
export function allowedRemovals(text) {
  const allowed = new Set();
  let inside = false;
  for (const line of text.split(/\r?\n/)) {
    if (/^\s*-\s*Removed\b/i.test(line)) inside = true;
    else if (inside && !/^\s{2,}\S/.test(line)) inside = false;
    if (inside) for (const m of line.matchAll(/`([^`]+)`/g)) allowed.add(m[1]);
  }
  return allowed;
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

/** @param {string[]} args */
const git = (...args) => execFileSync("git", args, { cwd: ROOT, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });

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
  const allowed = new Set();
  for (const doc of git("ls-files", "--cached", "--others", "--exclude-standard", "specs").split("\n")) {
    if (!/(^|\/)(spec|plan)\.md$/.test(doc)) continue;
    try {
      for (const name of allowedRemovals(readFileSync(path.join(ROOT, doc), "utf8"))) allowed.add(name);
    } catch {
      // deleted
    }
  }
  const removed = removedTests(before, after, allowed);
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
