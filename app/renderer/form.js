// @ts-check
// A node's settings form, as data: one control per param of its manifest, drawn by its type
// (oneframe/manifest.py's PARAM_TYPES), with its bounds, choices and default, and whether the
// person set it. The page draws the controls (main.js); a test reads them without a browser.

import { isSet } from "./graph-model.js";

/** @typedef {import("../protocol.js").Page.Manifest} Manifest */
/** @typedef {import("./graph-model.js").Graph} Graph */
/**
 * @typedef {{ name: string, type: string, control: "number" | "checkbox" | "select" | "text" | "photo" | "unknown",
 *   label: string, doc: string, min: number | null, max: number | null, step: number | "any" | null,
 *   choices: string[], default: unknown, value: unknown, set: boolean }} Control
 */

/** The control each param type is drawn with. */
const CONTROLS = /** @type {const} */ ({
  int: "number",
  float: "number",
  bool: "checkbox",
  choice: "select",
  string: "text",
  file: "photo",
});

/** @param {unknown} value @returns {number | null} */
const bound = (value) => (typeof value === "number" && Number.isFinite(value) ? value : null);

/**
 * The controls of a step's form: one per param of its manifest, in the manifest's order.
 * @param {Manifest} manifest @param {Graph} graph @param {string} step
 * @returns {Control[]}
 */
export function formModel(manifest, graph, step) {
  const params = /** @type {Record<string, Record<string, unknown>>} */ (manifest.params ?? {});
  const given = graph.nodes[step]?.params ?? {};
  return Object.entries(params).map(([name, row]) => {
    const type = String(row.type ?? "");
    const set = isSet(graph, step, name);
    return {
      name,
      type,
      control: CONTROLS[/** @type {keyof typeof CONTROLS} */ (type)] ?? "unknown",
      label: String(row.label || name),
      doc: String(row.doc ?? ""),
      min: bound(row.min),
      max: bound(row.max),
      step: type === "int" ? 1 : type === "float" ? "any" : null,
      choices: Array.isArray(row.choices) ? row.choices.map(String) : [],
      default: row.default ?? null,
      value: set ? given[name] : (row.default ?? null),
      set,
    };
  });
}

/**
 * What the person typed or chose, as the param's value, or why it is not one. The engine checks
 * every value again (check_param); this answers at once, while the person types.
 * @param {Control} control @param {string | boolean} input
 * @returns {{ value: unknown } | { problem: string }}
 */
export function readControl(control, input) {
  if (control.control === "checkbox") return { value: Boolean(input) };
  const text = String(input).trim();
  if (control.control === "number") {
    if (text === "") return { problem: `${control.label} needs a number.` };
    const value = Number(text);
    if (!Number.isFinite(value)) return { problem: `${control.label} needs a number.` };
    if (control.type === "int" && !Number.isInteger(value)) return { problem: `${control.label} needs a whole number.` };
    if (control.min !== null && value < control.min) return { problem: `${control.label} is at least ${control.min}.` };
    if (control.max !== null && value > control.max) return { problem: `${control.label} is at most ${control.max}.` };
    return { value };
  }
  if (control.control === "select" && !control.choices.includes(text)) {
    return { problem: `${control.label} is one of ${control.choices.join(", ")}.` };
  }
  return { value: text };
}
