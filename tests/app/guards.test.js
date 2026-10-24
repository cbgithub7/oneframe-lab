// @ts-check
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { SPEC_MAX_LINES, backtickPaths, checkDocs, linkTargets, literalPrefix, specTooLong } from "../../scripts/check-docs.js";
import {
  allowances, allowedRemovals, collectPython, guard, isTestFile, jsEntries, problems, removedTests, renamedTests, testNames,
} from "../../scripts/test-guard.js";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

test("test names are read from Python and JavaScript sources", () => {
  assert.deepEqual(testNames("a/test_x.py", "def test_one():\n    pass\n\nasync def test_two(x):\n  def helper(): pass\n"),
    ["test_one", "test_two"]);
  assert.deepEqual(testNames("t/a.test.js", 'test("one", () => {});\ntest(`two "quoted"`, fn);\n'), ["one", 'two "quoted"']);
  assert.ok(isTestFile("engine/tests/test_graph.py") && isTestFile("tests/app/x.test.js"));
  assert.ok(!isTestFile("engine/tests/conftest.py") && !isTestFile("app/main/engine.js"));
});

test("a removed test fails unless a plan names it, and a moved test is fine", () => {
  const before = new Map([["a/test_x.py", ["test_kept", "test_moved", "test_gone", "test_listed"]]]);
  const after = new Map([["a/test_x.py", ["test_kept"]], ["b/test_y.py", ["test_moved"]]]);
  const plan = "## Tests\n\n- Added: `test_new`\n- Removed:\n  - `test_listed`: replaced by the ladder tests\n- Other `test_gone`\n";
  const allowed = allowedRemovals(plan);
  assert.deepEqual([...allowed], ["test_listed"]);
  assert.deepEqual(removedTests(before, after, allowed), ["a/test_x.py::test_gone"]);
});

test("only the Tests section can allow a removal", () => {
  const spec = "## Decisions taken\n\n- Removed `test_elsewhere` handling from the API\n\n"
    + "## Tests\n\n- Removed:\n  - `test_listed`: replaced\n\n## Out of scope\n\n- Removed `test_after`\n";
  assert.deepEqual([...allowedRemovals(spec)], ["test_listed"]);
});

test("JavaScript tests are read with their skips and empty bodies", () => {
  const file = [
    'import { test } from "node:test";',
    'test("runs", () => { assert.ok(true); });',
    'test("an option", { skip: process.platform === "win32" }, () => { assert.ok(true); });',
    'test("a todo option", { timeout: 10, todo: "later" }, async () => {',
    "  assert.ok(true);",
    "});",
    'test.skip("a skip modifier", () => { assert.ok(true); });',
    'test.todo("a todo modifier", (t) => { assert.ok(t); });',
    'test("a skip call", (t) => {',
    '  if (process.env.CI) t.skip("not on CI");',
    "  assert.ok(true);",
    "});",
    'test("a todo call", async (ctx) => { ctx.todo(); assert.ok(ctx); });',
    'test("an empty arrow", () => {});',
    'test("an empty function", async function () {',
    "  // nothing yet",
    "});",
    'test("returns first", () => {',
    "  return;",
    "  assert.ok(false);",
    "});",
    'test("no function");',
    "const text = 'test(\"in a string\", () => {});';",
    "  test(`a template name`, function named(t) { assert.ok(t); });",
  ].join("\n");
  assert.deepEqual(jsEntries("t/x.test.js", file).map((e) => [e.id, e.flags.join(" ")]), [
    ["runs", ""],
    ["an option", "@skip"],
    ["a todo option", "@todo"],
    ["a skip modifier", "@skip"],
    ["a todo modifier", "@todo"],
    ["a skip call", "skip()"],
    ["a todo call", "todo()"],
    ["an empty arrow", "empty"],
    ["an empty function", "empty"],
    ["returns first", "empty"],
    ["no function", "empty"],
    ["a template name", ""],
  ]);
});

/** A throwaway git repository on a branch "work", from a "main" that holds these files.
 * @param {Record<string, string>} files */
function gitRepo(files) {
  const dir = mkdtempSync(path.join(tmpdir(), "oneframe-guard-"));
  const git = (/** @type {string[]} */ ...a) => {
    const r = spawnSync("git", a, { cwd: dir, encoding: "utf8" });
    if (r.status !== 0) throw new Error(`git ${a.join(" ")}: ${r.stderr}`);
    return r.stdout;
  };
  const write = (/** @type {string} */ rel, /** @type {string} */ text) => {
    mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
    writeFileSync(path.join(dir, rel), text);
  };
  const commit = (/** @type {string} */ message) => {
    git("add", "-A");
    git("commit", "-qm", message);
  };
  git("init", "-q", "-b", "main");
  git("config", "user.email", "t@t");
  git("config", "user.name", "t");
  for (const [rel, text] of Object.entries(files)) write(rel, text);
  commit("main");
  git("checkout", "-qb", "work");
  return { dir, git, write, commit };
}

test("a rename lets the old name go only to a name that exists", () => {
  const spec = "## Tests\n\n- Removed: none\n- Renamed: `test_old` -> `test_new`, and\n  - `test_case[a]` -> `test_case[b]`\n";
  const allow = { removed: new Set(), renamed: renamedTests(spec) };
  assert.deepEqual(allow.renamed, [["test_old", "test_new"], ["test_case[a]", "test_case[b]"]]);
  const e = (/** @type {string} */ id) => ({ id, flags: [], where: "engine" });
  const before = [e("test_old[x]"), e("test_old[y]"), e("test_case[a]"), e("test_case[c]")];
  const renamed = [e("test_new[x]"), e("test_new[y]"), e("test_case[b]"), e("test_case[c]")];
  assert.deepEqual(problems(before, renamed, allow), []);
  assert.deepEqual(problems(before, [e("test_new[x]"), e("test_case[c]")], allow), [
    "engine: test_old[y] is renamed to test_new[y], which does not exist",
    "engine: test_case[a] is renamed to test_case[b], which does not exist",
  ]);
  assert.deepEqual(problems(before, before, allow), [], "while the old test stands, the line does nothing");
  assert.equal(problems(before, renamed, { removed: new Set(), renamed: [] }).length, 3, "without the line, each is gone");
});

test("only specs being worked on, or changed on the branch, allow a removal", () => {
  const spec = (/** @type {string} */ name, status = "approved") =>
    `# Spec\n\nStatus: ${status}\n\n## Tests\n\n- Removed:\n  - \`${name}\`: the reason\n`;
  const repo = gitRepo({
    "specs/_template/spec.md": spec("test_template", "draft"),
    "specs/001-done/spec.md": spec("test_done", "done"),
    "specs/002-approved/spec.md": spec("test_approved"),
    "specs/003-committed/spec.md": spec("test_committed"),
    "specs/004-edited/spec.md": spec("test_edited"),
  });
  try {
    const base = repo.git("merge-base", "HEAD", "main").trim();
    const allowed = () => [...allowances(repo.dir, base).removed].sort();
    assert.deepEqual(allowed(), [], "no spec changed on the branch yet");
    repo.write("specs/003-committed/tasks.md", "- [x] 1. Started.\n");
    repo.write("specs/003-committed/plan.md", spec("test_plan"));
    repo.commit("start 003");
    repo.write("specs/004-edited/spec.md", `${spec("test_edited")}\nEdited, not committed.\n`);
    repo.write("specs/005-new/spec.md", spec("test_untracked", "draft"));
    repo.write("specs/_template/spec.md", `${spec("test_template", "draft")}\nChanged.\n`);
    assert.deepEqual(allowed(), ["test_committed", "test_edited", "test_untracked"]);
  } finally {
    rmSync(repo.dir, { recursive: true, force: true });
  }
});

test("a test that gains a skip or an empty body counts as removed", () => {
  const e = (/** @type {string} */ id, /** @type {string[]} */ ...flags) => ({ id, flags, where: "engine" });
  const before = [e("test_a[x]"), e("test_b", "@skipif"), e("test_c"), e("test_d", "@skip"), e("test_old")];
  const after = [e("test_a[x]", "@skip"), e("test_b", "@skipif"), e("test_c", "empty"), e("test_d"), e("test_new", "skip()")];
  const renamed = /** @type {[string, string][]} */ ([["test_old", "test_new"]]);
  assert.deepEqual(problems(before, after, { removed: new Set(), renamed }), [
    "engine: test_a[x] gained @skip",
    "engine: test_c gained empty",
    "engine: test_old is renamed to test_new, which gained skip()",
  ], "a flag kept or lost is fine");
  assert.deepEqual(problems(before, after, { removed: new Set(["test_a", "test_c", "test_new"]), renamed }), []);
  const js = (/** @type {string} */ text) => jsEntries("t/x.test.js", text);
  assert.deepEqual(problems(js('test("x", () => { assert.ok(1); });'), js('test("x", { skip: true }, () => {});'),
    { removed: new Set(), renamed: [] }), ["t/x.test.js: x gained @skip empty"]);
});

test("a base without a committed list is compared by source names", () => {
  const repo = gitRepo({ "engine/tests/test_a.py": "def test_one():\n    assert True\n\n\ndef test_two():\n    assert True\n" });
  try {
    repo.write("engine/tests/test_a.py", "def test_one():\n    assert True\n");
    const result = guard({ root: repo.dir, baseRef: "main", collect: () => ["test_one"] });
    assert.deepEqual([result.code, result.out.slice(0, 3)], [1, [
      "test-guard: main has no engine/tests/collected.txt; its engine tests are compared by the names in their source.",
      "test-guard: 1 test(s) at main are gone, skipped or emptied, with no line in a spec this branch changed:",
      "  engine/tests/test_a.py::test_two is gone",
    ]]);
  } finally {
    rmSync(repo.dir, { recursive: true, force: true });
  }
});

test("a collection that fails fails the guard, saying why", () => {
  const repo = gitRepo({ "README.md": "x\n" });
  try {
    const pytest = (/** @type {string} */ script) => () => collectPython(repo.dir, [process.execPath, "-e", script]);
    const failed = guard({ root: repo.dir, baseRef: "main",
      collect: pytest("console.log('E   ModuleNotFoundError: No module named numpy'); process.exit(2)") });
    assert.equal(failed.code, 1);
    assert.match(failed.out.join("\n"), /collecting the engine's tests failed:\nE {3}ModuleNotFoundError: No module named numpy/);
    const none = guard({ root: repo.dir, baseRef: "main", collect: pytest("console.log('collected 0 items')") });
    assert.equal(none.code, 1);
    assert.match(none.out.join("\n"), /listed none/);
    const missing = guard({ root: repo.dir, baseRef: "main", collect: () => collectPython(repo.dir, ["oneframe-no-such-uv"]) });
    assert.equal(missing.code, 1);
    assert.match(missing.out.join("\n"), /cannot collect the engine's tests: .*ENOENT.*npm run engine:sync/);
    const listed = collectPython(repo.dir, [process.execPath, "-e", "console.log('tests/x.py: 2\\nlisted\\ttest_a\\nlisted\\ttest_b\\t@skip')"]);
    assert.deepEqual(listed, ["test_a", "test_b\t@skip"]);
  } finally {
    rmSync(repo.dir, { recursive: true, force: true });
  }
});

test("the docs check reads paths, placeholders and links", () => {
  assert.deepEqual(backtickPaths("see `app/main/main.js`, `oneframe/ports.py` and `npm run check` or `package.json`."),
    ["app/main/main.js", "engine/src/oneframe/ports.py", "package.json"]);
  assert.equal(literalPrefix("runtimes/<id>/uv.lock"), "runtimes");
  assert.equal(literalPrefix("nodes/*/node.json"), "nodes");
  assert.deepEqual(linkTargets("[a](docs/x.md#part) [b](https://x.y) [c](#here) [d](../y.md)"), ["docs/x.md", "../y.md"]);
});

test("a spec being worked on is held to its length; a finished one is history", () => {
  const spec = (/** @type {string} */ status, /** @type {number} */ lines) =>
    [`# 007: Title`, "", `Status: ${status}`, ...Array.from({ length: lines - 3 }, (_, i) => `line ${i}`)].join("\n") + "\n";
  assert.equal(specTooLong(spec("draft", SPEC_MAX_LINES)), null);
  assert.match(String(specTooLong(spec("draft", SPEC_MAX_LINES + 1))), /is 201 lines/);
  assert.match(String(specTooLong(spec("in progress", 300))), /at most 200/);
  assert.match(String(specTooLong(spec("approved (2026-10-12)", 300))), /at most 200/);
  assert.equal(specTooLong(spec("done (merged in PR #9)", 400)), null);
  assert.equal(specTooLong(spec("dropped", 400)), null);
});

test("the docs check fails a long spec being worked on, and a link to nothing, in a real tree", () => {
  const root = mkdtempSync(path.join(tmpdir(), "oneframe-docs-"));
  try {
    const write = (/** @type {string} */ rel, /** @type {string} */ text) => {
      mkdirSync(path.dirname(path.join(root, rel)), { recursive: true });
      writeFileSync(path.join(root, rel), text);
    };
    const body = (/** @type {string} */ status) =>
      [`# 007: Title`, "", `Status: ${status}`, ...Array.from({ length: SPEC_MAX_LINES }, () => "line")].join("\n");
    write("specs/007-long/spec.md", body("draft"));
    write("specs/003-old/spec.md", body("done (merged)"));
    write("docs/handoff.md", "See [the roadmap](roadmap.md).\n");
    assert.deepEqual(checkDocs(root).problems, [
      "docs/handoff.md: link to roadmap.md, which does not exist",
      `specs/007-long/spec.md: is ${SPEC_MAX_LINES + 3} lines; a spec being worked on is at most ${SPEC_MAX_LINES}`,
    ]);
    write("docs/roadmap.md", "Parked.\n");
    write("specs/007-long/spec.md", body("done (merged)"));
    assert.deepEqual(checkDocs(root).problems, []);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

/** A throwaway git repo whose checks pass or fail on command, for running the stop hook.
 * @param {boolean} checkPasses */
function fakeProject(checkPasses) {
  const dir = mkdtempSync(path.join(tmpdir(), "oneframe-stop-"));
  const git = (/** @type {string[]} */ ...a) => spawnSync("git", a, { cwd: dir, encoding: "utf8" });
  git("init", "-q");
  git("config", "user.email", "t@t");
  git("config", "user.name", "t");
  const exit = (/** @type {boolean} */ ok) => `node -e "process.exit(${ok ? 0 : 1})"`;
  writeFileSync(path.join(dir, "package.json"), JSON.stringify({
    scripts: { check: exit(checkPasses), "engine:check": exit(true) },
  }));
  mkdirSync(path.join(dir, "scripts"));
  writeFileSync(path.join(dir, "scripts", "test-guard.js"), "");
  git("add", ".");
  git("commit", "-qm", "start");
  return dir;
}

/** @param {string} dir @param {string} session */
function stop(dir, session) {
  const r = spawnSync(process.execPath, [path.join(REPO, ".claude", "hooks", "before-stop.js")], {
    input: JSON.stringify({ session_id: session }), encoding: "utf8",
    env: { ...process.env, CLAUDE_PROJECT_DIR: dir },
  });
  return r.stdout ? JSON.parse(r.stdout) : null;
}

test("the stop hook blocks on a failing check, and stays quiet when nothing changed", () => {
  const session = `t${Date.now()}`;
  const failing = fakeProject(false);
  const passing = fakeProject(true);
  try {
    assert.equal(stop(failing, `${session}a`), null, "a clean tree is not checked");
    writeFileSync(path.join(failing, "new.txt"), "x");
    const blocked = stop(failing, `${session}a`);
    assert.equal(blocked.decision, "block");
    assert.match(blocked.reason, /npm run check fails/);
    stop(failing, `${session}a`);
    stop(failing, `${session}a`);
    assert.match(stop(failing, `${session}a`).systemMessage, /still failing/, "it gives up after three blocks");

    writeFileSync(path.join(passing, "new.txt"), "x");
    assert.equal(stop(passing, `${session}b`), null, "passing checks let the turn end");
  } finally {
    rmSync(failing, { recursive: true, force: true });
    rmSync(passing, { recursive: true, force: true });
  }
});
