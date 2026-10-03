// @ts-check
// boot(app) puts Electron's folders under the data root before it asks for the single-instance
// lock (AC3 of spec 006). CI never starts Electron, so a fake app records what it is told.
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { PAGE_PARTITION, boot } from "../../app/main/boot.js";

/** @param {boolean} isPackaged */
function recordingApp(isPackaged) {
  const calls = /** @type {Array<[string, ...unknown[]]>} */ ([]);
  const app = {
    isPackaged,
    /** @param {string} name @param {string} value */
    setPath: (name, value) => {
      calls.push(["setPath", name, value]);
    },
    /** @param {string} [value] */
    setAppLogsPath: (value) => {
      calls.push(["setAppLogsPath", value]);
    },
    requestSingleInstanceLock: () => {
      calls.push(["requestSingleInstanceLock"]);
      return true;
    },
  };
  return { app: /** @type {any} */ (app), calls };
}

test("Electron's folders go under the root before the single-instance lock is asked for", () => {
  const sandbox = mkdtempSync(path.join(tmpdir(), "oneframe-boot-"));
  try {
    const root = path.join(sandbox, "root");
    const { app, calls } = recordingApp(false);
    const { root: chosen, first } = boot(app, { env: { ONEFRAME_DATA: root }, platform: process.platform,
      home: sandbox });
    assert.equal(chosen, root);
    assert.equal(first, true);
    assert.deepEqual(calls.at(-1), ["requestSingleInstanceLock"]);
    const paths = Object.fromEntries(calls.filter((c) => c[0] === "setPath").map((c) => [c[1], c[2]]));
    assert.deepEqual(Object.keys(paths).sort(), ["crashDumps", "sessionData", "temp", "userData"]);
    assert.equal(paths.userData, path.join(root, "electron"));
    assert.equal(paths.sessionData, path.join(root, "cache", "electron"));
    assert.equal(paths.crashDumps, path.join(root, "logs", "crashes"));
    assert.deepEqual(calls.find((c) => c[0] === "setAppLogsPath"), ["setAppLogsPath", path.join(root, "logs")]);
    for (const value of [...Object.values(paths), path.join(root, "logs")]) {
      assert.ok(String(value).startsWith(root + path.sep), String(value));
      assert.ok(existsSync(String(value)), `${value} exists before Electron is told of it`);
    }
  } finally {
    rmSync(sandbox, { recursive: true, force: true });
  }
});

test("the packaged app and a dev checkout get different roots, and so different locks", () => {
  const home = mkdtempSync(path.join(tmpdir(), "oneframe-home-"));
  try {
    const machine = { env: {}, platform: process.platform, home };
    const dev = boot(recordingApp(false).app, machine).root;
    const packaged = boot(recordingApp(true).app, machine).root;
    assert.notEqual(dev, packaged);
    assert.ok(dev.startsWith(home + path.sep) && packaged.startsWith(home + path.sep));
    assert.match(path.basename(dev), /^(OneframeLab-dev|oneframe-lab-dev)$/);
  } finally {
    rmSync(home, { recursive: true, force: true });
  }
});

test("the page's session lives in memory", () => {
  assert.ok(!PAGE_PARTITION.startsWith("persist:"));
});
