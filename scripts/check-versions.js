// @ts-check
// Is everything this project pins the current release? Run: npm run versions
//
// The rule (docs/versions.md): every tool, language and library is on its latest stable release,
// unless versions.json names it with a reason and a date by which that reason is looked at again.
// A release gets a short grace period (GRACE_DAYS) so Dependabot's pull request can land; after
// that, an old pin fails this check. An exception past its review date fails it too, so nothing
// stays old just because nobody looked.
//
// Checked: npm devDependencies, the engine's direct Python dependencies (as locked), the engine's
// Python minor version, the uv the engine requires, the Node LTS the project targets and the
// matching @types/node, and every GitHub Action (pinned by commit, with the version in a comment).
//
// With --rules-only, only the rules a change can break fail the check: exact pins, actions pinned
// by commit, .node-version matching engines.node, and exceptions that name what is pinned. Being
// behind the latest release, or an exception's date passing, is left to the run on main and the
// weekly run, so an upstream release never turns an unrelated pull request red.

import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const GRACE_DAYS = 14;
const DAY = 24 * 60 * 60 * 1000;

/** @typedef {{ area: string, name: string, pinned: string, latest: string, released?: string, status: string, note?: string }} Row */

/** @param {string} file */
const read = (file) => readFileSync(path.join(ROOT, file), "utf8");

/** @param {string} a @param {string} b  Compare dotted numeric versions. */
export function compareVersions(a, b) {
  const pa = a.replace(/^v/, "").split(/[.+-]/).map((x) => Number.parseInt(x, 10) || 0);
  const pb = b.replace(/^v/, "").split(/[.+-]/).map((x) => Number.parseInt(x, 10) || 0);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0);
    if (d !== 0) return Math.sign(d);
  }
  return 0;
}

/** @param {string} url */
async function json(url) {
  const res = await fetch(url, { headers: { accept: "application/json" } });
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return res.json();
}

/**
 * @param {{ pinned: string, latest: string, released?: string, exception?: any, now: number }} v
 * @returns {{ status: string, note?: string, rule?: boolean }}
 */
export function judge({ pinned, latest, released, exception, now }) {
  if (exception) {
    const review = Date.parse(exception.review_by);
    if (Number.isNaN(review) || review < now) {
      return { status: "fail", note: `exception expired ${exception.review_by}: ${exception.reason}` };
    }
    if (exception.pin && exception.pin !== pinned) {
      return { status: "fail", rule: true, note: `exception is for ${exception.pin}, pinned is ${pinned}` };
    }
    return { status: "excepted", note: `${exception.reason} (review by ${exception.review_by})` };
  }
  if (compareVersions(pinned, latest) >= 0) return { status: "ok" };
  if (released) {
    const age = (now - Date.parse(released)) / DAY;
    if (age < GRACE_DAYS) return { status: "grace", note: `released ${Math.floor(age)} days ago` };
  }
  return { status: "fail" };
}

/** @returns {Promise<Array<Omit<Row, "status">>>} */
async function npmRows() {
  const pkg = JSON.parse(read("package.json"));
  const rows = [];
  for (const [name, spec] of Object.entries(/** @type {Record<string, string>} */ (pkg.devDependencies ?? {}))) {
    if (!/^\d+\.\d+\.\d+$/.test(spec)) throw new Error(`${name} is pinned as ${spec}; pins must be exact versions.`);
    const meta = await json(`https://registry.npmjs.org/${name.replace("/", "%2F")}`);
    let latest = meta["dist-tags"].latest;
    if (name === "@types/node") {
      // Types follow the Node the app runs on (Electron's bundled Node, the same LTS major as
      // engines.node), not the newest Node.
      const major = String(pkg.engines.node).match(/(\d+)/)?.[1];
      latest = Object.keys(meta.versions).filter((v) => v.startsWith(`${major}.`) && !v.includes("-"))
        .sort(compareVersions).at(-1);
    }
    rows.push({ area: "npm", name, pinned: spec, latest, released: meta.time?.[latest] });
  }
  return rows;
}

/** @returns {Promise<Array<Omit<Row, "status">>>} */
async function pythonRows() {
  const pyproject = read("engine/pyproject.toml");
  const lock = read("engine/uv.lock");
  const direct = [...pyproject.matchAll(/^\s*"([A-Za-z0-9_.-]+)\s*[<>=!~]/gm)].map((m) => m[1].toLowerCase());
  const locked = new Map([...lock.matchAll(/\[\[package\]\]\nname = "([^"]+)"\nversion = "([^"]+)"/g)]
    .map((m) => [m[1].toLowerCase(), m[2]]));
  const rows = [];
  for (const name of new Set(direct)) {
    if (name === "uv_build" || name === "uv-build") continue; // checked with uv below
    const pinned = locked.get(name) ?? locked.get(name.replace(/_/g, "-"));
    if (!pinned) continue;
    const meta = await json(`https://pypi.org/pypi/${name}/json`);
    const latest = meta.info.version;
    rows.push({ area: "pypi", name, pinned, latest, released: meta.releases?.[latest]?.[0]?.upload_time_iso_8601 });
  }
  const uvPin = pyproject.match(/required-version\s*=\s*">=([\d.]+)"/)?.[1];
  if (uvPin) {
    const meta = await json("https://pypi.org/pypi/uv/json");
    rows.push({ area: "tool", name: "uv", pinned: uvPin, latest: meta.info.version,
      released: meta.releases?.[meta.info.version]?.[0]?.upload_time_iso_8601 });
  }
  return rows;
}

/** The newest stable CPython minor that uv can install, against engine/.python-version. */
function pythonVersionRow() {
  const pinned = read("engine/.python-version").trim();
  const out = execFileSync("uv", ["python", "list", "--all-versions", "--only-downloads"], { encoding: "utf8" });
  const minors = [...out.matchAll(/cpython-(\d+)\.(\d+)\.(\d+)-/g)].map((m) => `${m[1]}.${m[2]}`);
  const latest = [...new Set(minors)].sort(compareVersions).at(-1) ?? pinned;
  return { area: "python", name: "engine Python", pinned, latest };
}

/** The Node LTS line, against engines.node and .node-version. */
async function nodeRow() {
  const pkg = JSON.parse(read("package.json"));
  const pinned = read(".node-version").trim();
  const engines = String(pkg.engines.node).replace(/^>=/, "");
  if (engines !== pinned) throw new Error(`.node-version (${pinned}) and engines.node (${pkg.engines.node}) disagree.`);
  const index = await json("https://nodejs.org/dist/index.json");
  const lts = index.find((/** @type {any} */ r) => r.lts);
  return { area: "tool", name: "Node (LTS)", pinned, latest: lts.version.replace(/^v/, ""), released: lts.date };
}

/** Every `uses: owner/repo@<sha> # vX.Y.Z` in the workflows, against the repo's newest tag. */
function actionRows() {
  const dir = path.join(ROOT, ".github", "workflows");
  const rows = [];
  const seen = new Set();
  for (const file of readdirSync(dir).filter((f) => /\.ya?ml$/.test(f))) {
    for (const m of read(path.join(".github", "workflows", file)).matchAll(/uses:\s*([\w.-]+\/[\w.-]+)@(\S+)(?:\s*#\s*(v[\d.]+))?/g)) {
      const [, repo, ref, comment] = m;
      if (seen.has(repo)) continue;
      seen.add(repo);
      if (!/^[0-9a-f]{40}$/.test(ref) || !comment) {
        throw new Error(`${file}: ${repo}@${ref} must be pinned to a commit with its version in a comment.`);
      }
      const tags = execFileSync("git", ["ls-remote", "--tags", `https://github.com/${repo}`], { encoding: "utf8" });
      const latest = [...tags.matchAll(/refs\/tags\/(v\d+\.\d+\.\d+)$/gm)].map((t) => t[1]).sort(compareVersions).at(-1);
      rows.push({ area: "action", name: repo, pinned: comment, latest: latest ?? comment });
    }
  }
  return rows;
}

/**
 * How many failures count. Rules-only counts the rules a change can break (`rule` failures and
 * exceptions for names pinned nowhere), not pins behind their latest release.
 * @param {Array<{ status: string, rule?: boolean }>} rows
 * @param {number} strayExceptions
 * @param {boolean} rulesOnly
 */
export function failureCount(rows, strayExceptions, rulesOnly) {
  return rows.filter((r) => r.status === "fail" && (!rulesOnly || r.rule)).length + strayExceptions;
}

async function main() {
  const rulesOnly = process.argv.includes("--rules-only");
  const policy = JSON.parse(read("versions.json"));
  const exceptions = new Map((policy.exceptions ?? []).map((/** @type {any} */ e) => [e.name, e]));
  const now = Date.now();
  const raw = [...(await npmRows()), ...(await pythonRows()), pythonVersionRow(), await nodeRow(), ...actionRows()];
  /** @type {Row[]} */
  const rows = raw.map((r) => ({ ...r, ...judge({ ...r, exception: exceptions.get(r.name), now }) }));
  const width = Math.max(...rows.map((r) => r.name.length));
  for (const r of rows) {
    const mark = { ok: "  ok  ", grace: " grace", excepted: "except", fail: " FAIL " }[r.status];
    console.log(`${mark}  ${r.area.padEnd(6)} ${r.name.padEnd(width)}  ${r.pinned.padEnd(10)} latest ${r.latest}${r.note ? `  — ${r.note}` : ""}`);
  }
  for (const name of exceptions.keys()) {
    if (!rows.some((r) => r.name === name)) console.log(` FAIL   versions.json has an exception for ${name}, which is not pinned anywhere.`);
  }
  const stray = [...exceptions.keys()].filter((n) => !rows.some((r) => r.name === n)).length;
  const failed = failureCount(rows, stray, rulesOnly);
  if (rulesOnly && !failed) console.log("\nrules only: pins follow the rules (being behind is judged on main and weekly).");
  if (failed) {
    console.log(`\n${failed} pin(s) are behind with no current reason. Update them, or add a dated exception with a reason to versions.json.`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error.message);
    process.exitCode = 2;
  });
}
