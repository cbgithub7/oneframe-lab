// @ts-check
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { SPEC_MAX_LINES, backtickPaths, checkDocs, linkTargets, literalPrefix, specTooLong } from "../../scripts/check-docs.js";
import { allowedRemovals, isTestFile, removedTests, testNames } from "../../scripts/test-guard.js";

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
