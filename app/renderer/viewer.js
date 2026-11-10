// @ts-check
// Viewers by port type (spec 017, requirement 6): each module in viewers/ shows one type of
// contracts/port-types.json and says which (its TYPE); any other type is shown as its type, facets
// and meta. A viewer is chosen by the value's type, never by the node that made it, and reads the
// value by its key through the value protocol (app/main/values.js), never by a path.

import * as camera from "./viewers/camera.js";
import * as depth from "./viewers/depth.js";
import * as image from "./viewers/image.js";
import * as points from "./viewers/points.js";

/** @typedef {import("../protocol.js").Page.Value} Value */
/**
 * What a viewer is given: the pane's element, the value, its URL, and the cameras linked across
 * point panes.
 * @typedef {{ host: HTMLElement, value: Value, url: string, link: Set<Linked> }} Show
 * @typedef {{ summary: string, dispose?: () => void }} Shown a viewer's one-line account of what it drew
 * @typedef {{ follow: (from: Linked) => void, pose: () => unknown, setPose: (pose: unknown) => void }} Linked
 * @typedef {{ TYPE: string, show: (show: Show) => Promise<Shown> }} Viewer
 */

/** @type {Map<string, Viewer>} */
const VIEWERS = new Map([camera, depth, image, points].map((viewer) => [viewer.TYPE, viewer]));

/** The scheme app/main/values.js serves. */
export const VALUE_SCHEME = "oneframe-value";

/**
 * A value's URL: `oneframe-value://<entry>/<port>`, from its key, `<entry>:<port>`.
 * @param {Value} value
 */
export function valueUrl(value) {
  const [entry, ...port] = value.key.split(":");
  return `${VALUE_SCHEME}://${entry}/${encodeURIComponent(port.join(":"))}`;
}

/** @param {string} tag @param {string} [text] @param {string} [className] */
function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

/** Any other type: its type, facets, trust and meta, as text. @type {Viewer} */
const DESCRIBED = {
  TYPE: "",
  show: async ({ host, value }) => {
    const list = el("dl", undefined, "facts");
    const rows = [
      ["type", value.type],
      ...Object.entries(value.facets).map(([k, v]) => [k, v]),
      ["trust", value.trust ?? "inherited"],
      ...Object.entries(value.meta).map(([k, v]) => [k, typeof v === "string" ? v : JSON.stringify(v)]),
    ];
    for (const [k, v] of rows) list.append(el("dt", k), el("dd", String(v)));
    host.replaceChildren(list);
    return { summary: `${value.type}: no viewer for this type yet; its facets and meta.` };
  },
};

/** @param {string} type */
export function viewerFor(type) {
  return VIEWERS.get(type) ?? DESCRIBED;
}

/**
 * Show a value in a pane's element.
 * @param {HTMLElement} host @param {Value} value @param {Set<Linked>} link
 * @returns {Promise<Shown>}
 */
export function showValue(host, value, link) {
  return viewerFor(value.type).show({ host, value, url: valueUrl(value), link });
}
