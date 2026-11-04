// @ts-check
import assert from "node:assert/strict";
import { test } from "node:test";

import { compareVersions, engineUvPin, failureCount, judge, typesFollowNode, workflowUvProblems } from "../../scripts/check-versions.js";

const now = Date.parse("2026-09-29T00:00:00Z");
const daysAgo = (/** @type {number} */ n) => new Date(now - n * 86_400_000).toISOString();

test("versions compare numerically, not as text", () => {
  assert.equal(compareVersions("10.2.0", "9.9.9"), 1);
  assert.equal(compareVersions("v7.0.1", "7.0.1"), 0);
  assert.equal(compareVersions("3.14", "3.15"), -1);
});

test("an old pin fails once its grace period is over", () => {
  assert.equal(judge({ pinned: "1.0.0", latest: "1.0.0", now }).status, "ok");
  assert.equal(judge({ pinned: "1.0.0", latest: "1.1.0", released: daysAgo(3), now }).status, "grace");
  assert.equal(judge({ pinned: "1.0.0", latest: "1.1.0", released: daysAgo(30), now }).status, "fail");
  assert.equal(judge({ pinned: "3.14", latest: "3.15", now }).status, "fail", "no release date: no grace");
});

test("an exception needs a live review date and must match the pin", () => {
  const exception = { name: "x", pin: "1.0.0", reason: "waiting for wheels", review_by: "2026-11-01" };
  assert.equal(judge({ pinned: "1.0.0", latest: "2.0.0", exception, now }).status, "excepted");
  assert.equal(judge({ pinned: "1.0.1", latest: "2.0.0", exception, now }).status, "fail");
  const expired = { ...exception, review_by: "2026-09-01" };
  assert.match(judge({ pinned: "1.0.0", latest: "2.0.0", exception: expired, now }).note ?? "", /expired/);
});

test("on a pull request only broken rules fail, not pins behind their latest release", () => {
  const behind = judge({ pinned: "1.0.0", latest: "1.1.0", released: daysAgo(30), now });
  const wrongPin = judge({ pinned: "1.0.1", latest: "2.0.0", exception: { name: "x", pin: "1.0.0", reason: "r", review_by: "2026-11-01" }, now });
  assert.equal(wrongPin.rule, true);
  assert.equal(failureCount([behind, wrongPin], 1, false), 3);
  assert.equal(failureCount([behind, wrongPin], 1, true), 2, "the stray exception and the wrong pin, not the age");
  assert.equal(failureCount([behind], 0, true), 0);
});

test("Node's types follow the Node the app runs on, never a newer major", () => {
  assert.equal(typesFollowNode("24.19.2", ">=24.21.0"), null);
  const ahead = typesFollowNode("26.6.4", ">=24.21.0");
  assert.equal(ahead?.status, "fail");
  assert.equal(ahead?.rule, true, "a pull request that raises the major fails, not only the weekly run");
  assert.equal(typesFollowNode("22.18.0", ">=24.21.0")?.status, "fail");
});

test("every workflow installs the uv the engine requires", () => {
  const pin = engineUvPin('[tool.uv]\nrequired-version = ">=0.13.5"\n');
  assert.equal(pin, "0.13.5");
  assert.equal(engineUvPin('required-version = "==0.13.5"'), "0.13.5");
  const step = (/** @type {string} */ version) => `      - uses: astral-sh/setup-uv@${"a".repeat(40)} # v10.3.3\n        with:\n          version: ${version}\n`;
  const workflow = (/** @type {string[]} */ ...steps) => `jobs:\n  a:\n    steps:\n${steps.join("")}      - run: npm test\n`;
  const agrees = workflow(step('"0.13.5"'), step("0.13.5"));
  assert.deepEqual(workflowUvProblems([{ file: "checks.yml", text: agrees }], pin), []);

  const older = workflow(step('"0.13.5"'), step('"0.12.20" # old'));
  assert.deepEqual(workflowUvProblems([{ file: "checks.yml", text: older }], pin), [
    "checks.yml:7 installs uv 0.12.20, but engine/pyproject.toml requires uv 0.13.5.",
  ]);

  const named = "jobs:\n  a:\n    steps:\n      - name: uv\n        uses: astral-sh/setup-uv@x\n      - with:\n          version: 0.13.5\n";
  assert.match(workflowUvProblems([{ file: "w.yml", text: named }], pin)[0] ?? "", /w\.yml:5 gives setup-uv no version/,
    "a version in the next step is not this step's");
  assert.equal(workflowUvProblems([{ file: "w.yml", text: agrees }], undefined).length, 2, "a workflow pin with none in the engine");
});
