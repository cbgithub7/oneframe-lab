// @ts-check
// The value protocol serves a run's value by its key and port, and nothing else (spec 016).
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { REPO_ROOT } from "../../app/main/paths.js";
import { VALUE_SCHEME, carrierMimes, valueHandler } from "../../app/main/values.js";

const contract = JSON.parse(readFileSync(path.join(REPO_ROOT, "contracts", "port-types.json"), "utf8"));
const KEY = "ab".repeat(32);

test("the value protocol serves what the engine locates, and nothing else", async () => {
  const root = mkdtempSync(path.join(tmpdir(), "oneframe-values-"));
  try {
    const objects = path.join(root, "cache", "objects");
    const entry = path.join(objects, KEY.slice(0, 2), KEY);
    mkdirSync(entry, { recursive: true });
    writeFileSync(path.join(entry, "image.png"), "png bytes");
    writeFileSync(path.join(root, "secret.txt"), "not a value");
    const asked = /** @type {Array<[string, string]>} */ ([]);
    /** @type {Record<string, import("../../app/main/values.js").Located>} */
    const committed = {
      image: { path: path.join(entry, "image.png"), type: "Image", carrier: "png" },
      // An engine that answered a file outside the objects would still not be served.
      outside: { path: path.join(root, "secret.txt"), type: "Text", carrier: "json" },
      views: { path: entry, type: "ViewSet", carrier: "folder" },
    };
    const handle = valueHandler({
      locate: async (key, port) => {
        asked.push([key, port]);
        if (key !== KEY || !(port in committed)) throw new Error("bad_params: no such committed value");
        return committed[port];
      },
      objects,
      mimes: carrierMimes(contract),
    });
    /** @param {string} url */
    const get = (url) => handle(new Request(url));

    const served = await get(`${VALUE_SCHEME}://${KEY}/image`);
    assert.equal(served.status, 200);
    assert.equal(served.headers.get("content-type"), "image/png");
    assert.equal(await served.text(), "png bytes");

    for (const url of [
      `${VALUE_SCHEME}://${KEY}/outside`, // outside the objects
      `${VALUE_SCHEME}://${KEY}/views`, // a folder is not served
      `${VALUE_SCHEME}://${"cd".repeat(32)}/image`, // refused by the engine
      `${VALUE_SCHEME}://${KEY}/..%2F..%2F..%2Fsecret.txt`, // a path is only a port name to locate
      `file://${path.join(root, "secret.txt")}`,
    ]) {
      const refused = await get(url);
      assert.equal(refused.status, 404, url);
      assert.notEqual(await refused.text(), "not a value", url);
    }
    // The request is only ever a key and a port, handed to the engine as they are.
    assert.deepEqual(asked.at(-1), [KEY, "../../../secret.txt"]);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("every carrier a port type names says how it is served", () => {
  const mimes = carrierMimes(contract);
  for (const [name, type] of Object.entries(contract.types)) {
    const carrier = /** @type {{ carrier: string }} */ (type).carrier;
    assert.ok(carrier in contract.carriers, `${name}'s carrier ${carrier} is declared`);
    assert.equal(carrier in mimes, carrier !== "folder", `${carrier} is served unless it is a folder`);
  }
});
