// @ts-check
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { envWithPinnedTools, pinnedToolDirs } from "../../.claude/hooks/pinned-tools.js";

/** A project pinning Node 24.21.0 and uv 0.12.20, and a home where the tools may be installed.
 * @param {{ node?: boolean, uv?: boolean }} installed */
function fixture({ node = false, uv = false }) {
  const base = mkdtempSync(path.join(tmpdir(), "oneframe-pins-"));
  const root = path.join(base, "project");
  const home = path.join(base, "home");
  mkdirSync(path.join(root, "engine"), { recursive: true });
  writeFileSync(path.join(root, ".node-version"), "24.21.0\n");
  writeFileSync(path.join(root, "engine", "pyproject.toml"), '[tool.uv]\nrequired-version = ">=0.12.20"\n');
  const tools = path.join(home, ".local", "share", "oneframe-tools");
  if (node) mkdirSync(path.join(tools, "node-v24.21.0", "bin"), { recursive: true });
  if (uv) mkdirSync(path.join(tools, "uv-0.12.20"), { recursive: true });
  return { base, root, home, tools };
}

test("the hooks put the pinned Node and uv first, and only those that are installed", () => {
  const both = fixture({ node: true, uv: true });
  const onlyUv = fixture({ uv: true });
  const neither = fixture({});
  try {
    assert.deepEqual(pinnedToolDirs(both.root, both.home), [
      path.join(both.tools, "node-v24.21.0", "bin"),
      path.join(both.tools, "uv-0.12.20"),
    ]);
    assert.deepEqual(pinnedToolDirs(onlyUv.root, onlyUv.home), [path.join(onlyUv.tools, "uv-0.12.20")]);
    assert.deepEqual(pinnedToolDirs(neither.root, neither.home), []);

    const env = envWithPinnedTools(both.root, { PATH: "/usr/bin", KEEP: "1" }, both.home);
    assert.equal(env.PATH?.split(path.delimiter)[0], path.join(both.tools, "node-v24.21.0", "bin"));
    assert.ok(env.PATH?.endsWith(`${path.delimiter}/usr/bin`), "the old PATH follows the pinned tools");
    assert.equal(env.KEEP, "1");

    const windows = envWithPinnedTools(both.root, { Path: "C:\\Windows" }, both.home);
    assert.ok(windows.Path?.includes("uv-0.12.20") && !("PATH" in windows), "Windows spells it Path");

    const untouched = { PATH: "/usr/bin" };
    assert.equal(envWithPinnedTools(neither.root, untouched, neither.home), untouched);
  } finally {
    for (const { base } of [both, onlyUv, neither]) rmSync(base, { recursive: true, force: true });
  }
});

test("a project that pins nothing leaves the environment alone", () => {
  const base = mkdtempSync(path.join(tmpdir(), "oneframe-pins-"));
  try {
    assert.deepEqual(pinnedToolDirs(base, base), []);
  } finally {
    rmSync(base, { recursive: true, force: true });
  }
});
