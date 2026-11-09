// @ts-check
// The value protocol: how the page reads what a run made, by its key and port, never by its path.
//
// `oneframe-value://<key>/<port>` is registered on the page's partition (main.js), so an <img> or
// a fetch in the page can read a value. The handler asks the engine `values.locate`, which answers
// only a value a run committed, then serves that file and nothing else: one inside the cache's
// objects folder, with the media type contracts/port-types.json gives its carrier. A request never
// names a path, and anything the engine refuses, or any file outside the objects, is a 404.

import { createReadStream, realpathSync, statSync } from "node:fs";
import path from "node:path";
import { Readable } from "node:stream";

export const VALUE_SCHEME = "oneframe-value";

/**
 * @typedef {{ path: string, type: string, carrier: string | null }} Located what values.locate answers
 * @typedef {(key: string, port: string) => Promise<Located>} Locate
 */

/**
 * The media type of each carrier that is served, from port-types.json's `carriers`.
 * @param {{ carriers?: Record<string, { mime?: string }> }} contract
 * @returns {Record<string, string>}
 */
export function carrierMimes(contract) {
  /** @type {Record<string, string>} */
  const mimes = {};
  for (const [carrier, row] of Object.entries(contract.carriers ?? {})) {
    if (row && typeof row.mime === "string") mimes[carrier] = row.mime;
  }
  return mimes;
}

const notFound = () => new Response("Not found.", { status: 404, headers: { "content-type": "text/plain" } });

/**
 * Whether `file` is a file inside `folder`, both as the system resolves them.
 * @param {string} folder @param {string} file
 */
function inside(folder, file) {
  const relative = path.relative(realpathSync(folder), realpathSync(file));
  return relative !== "" && relative.split(path.sep)[0] !== ".." && !path.isAbsolute(relative) && statSync(file).isFile();
}

/**
 * The protocol's handler, for `protocol.handle`.
 * @param {{ locate: Locate, objects: string, mimes: Record<string, string> }} options `objects`: the
 *   layout's cache/objects, the only folder a value is served from
 * @returns {(request: Request) => Promise<Response>}
 */
export function valueHandler({ locate, objects, mimes }) {
  return async (request) => {
    let key;
    let port;
    try {
      const url = new URL(request.url);
      if (url.protocol !== `${VALUE_SCHEME}:`) return notFound();
      key = url.hostname;
      port = decodeURIComponent(url.pathname.replace(/^\//, ""));
    } catch {
      return notFound();
    }
    try {
      const found = await locate(key, port);
      const mime = found.carrier === null ? undefined : mimes[found.carrier];
      if (mime === undefined || !inside(objects, found.path)) return notFound();
      const body = /** @type {ReadableStream} */ (Readable.toWeb(createReadStream(found.path)));
      return new Response(body, { status: 200, headers: { "content-type": mime } });
    } catch {
      return notFound(); // refused by the engine, or no such file: nothing to say to the page
    }
  };
}
