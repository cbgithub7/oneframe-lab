// @ts-check
// Every repo path the rules and docs name must exist. Run: node scripts/check-docs.js
//
// A stale rules file is worse than none: an agent follows it confidently. This catches the most
// common kind of staleness -- a file or folder renamed or removed while the docs still point at
// it. It checks relative markdown links in every doc, and, in the documents that describe the
// current state (not specs, which describe what is to be built), paths written in backticks.

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const TOP = ["app", "engine", "nodes", "contracts", "scripts", "docs", "specs", "tests", "runtimes", ".claude", ".github"];
const ROOT_FILES = new Set(["package.json", "package-lock.json", "versions.json", "AGENTS.md", "CLAUDE.md", "README.md",
  "tsconfig.json", "ruff.toml", "pyrightconfig.json", "eslint.config.js", ".node-version"]);

/** @param {string} dir @returns {string[]} */
function markdownFiles(dir) {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...markdownFiles(full));
    else if (entry.name.endsWith(".md")) out.push(full);
  }
  return out;
}

/**
 * The part of a path pattern that must exist: everything before the first placeholder.
 * `runtimes/<id>/uv.lock` -> `runtimes`; `nodes/*\/node.json` -> `nodes`.
 * @param {string} p
 */
export function literalPrefix(p) {
  const parts = p.split("/");
  const cut = parts.findIndex((part) => /[<>*{}]|NNN/.test(part));
  return (cut === -1 ? parts : parts.slice(0, cut)).filter(Boolean).join("/");
}

/**
 * Paths named in backticks that look like this repo's paths.
 * @param {string} text
 * @returns {string[]}
 */
export function backtickPaths(text) {
  const out = [];
  for (const m of text.matchAll(/`([^`\s]+)`/g)) {
    let p = m[1].replace(/[.,;:]$/, "");
    if (p.startsWith("oneframe/")) p = `engine/src/${p}`;
    const top = p.split("/")[0];
    if ((TOP.includes(top) && p.includes("/")) || ROOT_FILES.has(p)) out.push(p);
  }
  return out;
}

/**
 * Relative link targets in markdown: [text](target), without anchors, skipping URLs.
 * @param {string} text
 */
export function linkTargets(text) {
  return [...text.matchAll(/\]\(([^)\s]+)\)/g)].map((m) => m[1])
    .filter((t) => !/^[a-z]+:/i.test(t) && !t.startsWith("#"))
    .map((t) => t.split("#")[0]);
}

function main() {
  const docs = [...["AGENTS.md", "CLAUDE.md", "README.md"].map((f) => path.join(ROOT, f)),
    ...markdownFiles(path.join(ROOT, "docs")), ...markdownFiles(path.join(ROOT, "specs"))];
  const problems = [];
  for (const file of docs) {
    const rel = path.relative(ROOT, file).split(path.sep).join("/");
    if (rel.startsWith("specs/_template/")) continue;
    const text = readFileSync(file, "utf8");
    for (const target of linkTargets(text)) {
      if (!existsSync(path.resolve(path.dirname(file), target))) problems.push(`${rel}: link to ${target}, which does not exist`);
    }
    if (rel.startsWith("specs/")) continue; // specs name files that are still to be built
    for (const p of backtickPaths(text)) {
      const prefix = literalPrefix(p);
      if (prefix && !existsSync(path.join(ROOT, prefix))) problems.push(`${rel}: names ${p}, and ${prefix} does not exist`);
      else if (prefix === p && p.endsWith("/") && !statSync(path.join(ROOT, p)).isDirectory()) {
        problems.push(`${rel}: names ${p} as a folder, which it is not`);
      }
    }
  }
  if (problems.length) {
    for (const p of problems) console.log(p);
    console.log(`\ncheck-docs: ${problems.length} stale reference(s). Update the docs in the same change as the code.`);
    process.exitCode = 1;
    return;
  }
  console.log(`check-docs: ok (${docs.length} documents).`);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
