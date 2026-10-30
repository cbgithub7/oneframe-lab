// @ts-check
// scripts/tool.js starts every command-line tool that opens a data root (spec 003, AC7): uv's
// temporary files go under the tool's root, so with TMP, TEMP and TMPDIR pointed at an empty
// folder, each tool leaves nothing there.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { REPO_ROOT, dataRoot, findUv } from "../../app/main/paths.js";
import { toolCommand, toolRoot } from "../../scripts/tool.js";

test("the tool's root is --data, then ONEFRAME_DATA, then the dev root", () => {
  const env = { ONEFRAME_DATA: "/srv/oneframe" };
  assert.equal(toolRoot(["x", "--data", "rel/root"], env, "/work", "linux", "/home/ana"), path.resolve("/work", "rel/root"));
  assert.equal(toolRoot(["--data=/abs/root"], env, "/work", "linux", "/home/ana"), path.resolve("/abs/root"));
  assert.equal(toolRoot(["x"], env, "/work", "linux", "/home/ana"), "/srv/oneframe");
  assert.equal(toolRoot([], {}, "/work", "linux", "/home/ana"), dataRoot({}, "linux", "/home/ana", false));
});

test("uv runs the tool from the locked engine with its temporary folder under the root", () => {
  const root = path.resolve("/data/root");
  const made = toolCommand("uv", "oneframe.diagnose", ["--data", root], { PATH: "p", TMP: "/tmp" }, "/");
  const tmp = path.join(root, "cache", "tools", "tmp");
  assert.equal(made.tmp, tmp);
  assert.deepEqual([made.env.TMP, made.env.TEMP, made.env.TMPDIR, made.env.PATH], [tmp, tmp, tmp, "p"]);
  assert.deepEqual(made.args, ["run", "--project", path.join(REPO_ROOT, "engine"), "--frozen", "python", "-B", "-m",
    "oneframe.diagnose", "--data", root]);
});

test("a tool given as a .py path runs that file, by its absolute path", () => {
  const made = toolCommand("uv", "engine/tests/fit_check.py", ["--data", "/d"], { PATH: "p" }, "/elsewhere");
  assert.deepEqual(made.args, ["run", "--project", path.join(REPO_ROOT, "engine"), "--frozen", "python", "-B",
    path.join(REPO_ROOT, "engine", "tests", "fit_check.py"), "--data", "/d"]);
  const absolute = path.join(REPO_ROOT, "engine", "tests", "fit_check.py");
  assert.equal(toolCommand("uv", absolute, [], { PATH: "p" }, "/").args[6], absolute);
});

test("every npm tool that opens a data root starts through scripts/tool.js", () => {
  const scripts = JSON.parse(readFileSync(path.join(REPO_ROOT, "package.json"), "utf8")).scripts;
  for (const name of ["bench:runtime", "bench:fit", "diagnose", "models:download", "models:pin"]) {
    assert.match(scripts[name], /^node scripts\/tool\.js oneframe\./, name);
  }
});

const uv = findUv();
const usable = uv !== null && spawnSync(uv, ["--version"]).status === 0;

test("with TMP, TEMP and TMPDIR pointed at an empty folder, each tool leaves nothing there",
  { skip: !usable && "uv not found", timeout: 300_000 }, () => {
    const scratch = mkdtempSync(path.join(tmpdir(), "oneframe-tool-"));
    const empty = path.join(scratch, "tmp");
    const data = path.join(scratch, "data");
    /** @type {NodeJS.ProcessEnv} */
    const env = { ...process.env, TMP: empty, TEMP: empty, TMPDIR: empty };
    delete env.ONEFRAME_DATA;
    try {
      mkdirSync(empty);
      const runs = [
        ["oneframe.diagnose", "--data", data],
        ["oneframe.bench_runtime", "no-such-runtime", "--data", data],
        ["oneframe.bench_fit", "no.such.node", "--data", data],
        ["oneframe.models.tools", "download", "no.such.node", "--data", data],
        ["oneframe.models.tools", "pin", "no.such.node", "--data", data],
      ];
      for (const args of runs) {
        const done = spawnSync(process.execPath, [path.join(REPO_ROOT, "scripts", "tool.js"), ...args],
          { cwd: REPO_ROOT, env, encoding: "utf8", timeout: 120_000 });
        assert.notEqual(done.status, null, `${args[0]} did not end: ${done.stderr}`);
        assert.deepEqual(readdirSync(empty), [], `${args.join(" ")} left something in TMP:\n${done.stderr}`);
      }
      assert.ok(readdirSync(path.join(data, "reports")).length > 0, "diagnose wrote its report under the root");
    } finally {
      rmSync(scratch, { recursive: true, force: true });
    }
  });
