// @ts-check
// The open graph, as plain data, and every edit the page makes to it. Each edit returns a new
// graph and leaves the one it was given as it was, so the page can draw from any state and a test
// can check one without a browser. Nothing here names a node: what a graph holds comes from the
// engine (graphs.get), and what a node is from its manifest (nodes.list).
//
// A graph is spec 015's: `nodes` by step, each with its node id, its params and a label; `edges`
// as `step.port` pairs; and `page`, the page's own fields, which the engine keeps as they are:
// `panes`, the outputs shown, in order, and `files`, the name of each photo picked, by its id.

/** @typedef {import("../protocol.js").Page.Manifest} Manifest */
/** @typedef {import("../protocol.js").Page.Edge} Edge */
/** @typedef {{ node: string, params: Record<string, unknown>, label?: string, [key: string]: unknown }} Step */
/** @typedef {{ step: string, port: string }} Pane */
/**
 * @typedef {{ panes: Pane[], files: Record<string, string>, [key: string]: unknown }} PageFields
 * @typedef {{ version?: number, title?: string, nodes: Record<string, Step>, edges: Edge[],
 *   page: PageFields, [key: string]: unknown }} Graph
 * @typedef {{ name: string, type: string, optional: boolean, doc: string }} Port
 */

/** @template T @param {T} value @returns {T} */
const clone = (value) => structuredClone(value);

/** @param {unknown} value @returns {value is Record<string, unknown>} */
const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

/**
 * A graph as the engine sent it, with what the page reads always there: every step's params, the
 * edges, and the page's panes and file names. Fields it does not know are kept.
 * @param {Record<string, unknown>} raw
 * @returns {Graph}
 */
export function openGraph(raw) {
  const graph = /** @type {Graph} */ (clone(raw));
  /** @type {Record<string, Step>} */
  const nodes = {};
  for (const [step, row] of Object.entries(isObject(graph.nodes) ? graph.nodes : {})) {
    if (!isObject(row)) continue;
    nodes[step] = { ...row, node: String(row.node), params: isObject(row.params) ? { ...row.params } : {} };
  }
  graph.nodes = nodes;
  graph.edges = Array.isArray(graph.edges) ? graph.edges.filter((e) => isObject(e)) : [];
  /** @type {Record<string, unknown>} */
  const page = isObject(graph.page) ? graph.page : {};
  graph.page = {
    ...page,
    panes: Array.isArray(page.panes)
      ? page.panes.filter((/** @type {unknown} */ p) => isObject(p) && typeof p.step === "string" && typeof p.port === "string")
      : [],
    files: isObject(page.files) ? /** @type {Record<string, string>} */ (page.files) : {},
  };
  return graph;
}

/** A graph with no nodes, for "New". @param {string} title @returns {Graph} */
export function emptyGraph(title) {
  return openGraph({ version: 1, title, nodes: {}, edges: [], page: {} });
}

/**
 * A port as a manifest writes it: a type, or an object with one.
 * @param {string} name @param {unknown} spec @returns {Port}
 */
export function portOf(name, spec) {
  if (typeof spec === "string") return { name, type: spec, optional: false, doc: "" };
  const row = isObject(spec) ? spec : {};
  return { name, type: String(row.type ?? ""), optional: row.optional === true, doc: String(row.doc ?? "") };
}

/**
 * A manifest's inputs or outputs, in its order.
 * @param {Manifest} manifest @param {"inputs" | "outputs"} side @returns {Port[]}
 */
export function portsOf(manifest, side) {
  return Object.entries(manifest[side] ?? {}).map(([name, spec]) => portOf(name, spec));
}

/** A type's name without its facets: `Depth[kind=metric]` is `Depth`. @param {string} type */
export function typeName(type) {
  return type.split("[")[0].trim();
}

/** @param {string} ref `step.port` @returns {[string, string]} */
export function splitRef(ref) {
  const dot = ref.lastIndexOf(".");
  return dot < 0 ? [ref, ""] : [ref.slice(0, dot), ref.slice(dot + 1)];
}

/** @param {Graph} graph @param {string} step @param {string} name @param {unknown} value */
export function setParam(graph, step, name, value) {
  const out = clone(graph);
  const row = out.nodes[step];
  if (row) row.params[name] = value;
  return out;
}

/** The param back to its default: gone from the graph, so the fit may choose it. @param {Graph} graph @param {string} step @param {string} name */
export function resetParam(graph, step, name) {
  const out = clone(graph);
  const row = out.nodes[step];
  if (row) delete row.params[name];
  return out;
}

/** Whether the person set this param (a value in the graph), which the fit never changes. @param {Graph} graph @param {string} step @param {string} name */
export function isSet(graph, step, name) {
  const row = graph.nodes[step];
  return row !== undefined && Object.hasOwn(row.params, name);
}

/**
 * A step key for a new node: the last part of its id, numbered when the graph has one already.
 * @param {Graph} graph @param {string} id
 */
export function stepKey(graph, id) {
  const base = (id.split(".").at(-1) ?? "node").replace(/[^a-z0-9_]/gi, "_").toLowerCase() || "node";
  if (!(base in graph.nodes)) return base;
  let n = 2;
  while (`${base}_${n}` in graph.nodes) n += 1;
  return `${base}_${n}`;
}

/**
 * The graph with a node added, at its defaults, and the step it was given.
 * @param {Graph} graph @param {Manifest} manifest @returns {{ graph: Graph, step: string }}
 */
export function addNode(graph, manifest) {
  const out = clone(graph);
  const step = stepKey(out, manifest.id);
  out.nodes[step] = { node: manifest.id, params: {}, label: manifest.title };
  return { graph: out, step };
}

/** The graph without a step, its edges or its panes. @param {Graph} graph @param {string} step */
export function removeNode(graph, step) {
  const out = clone(graph);
  delete out.nodes[step];
  out.edges = out.edges.filter((e) => splitRef(e.from)[0] !== step && splitRef(e.to)[0] !== step);
  out.page.panes = out.page.panes.filter((p) => p.step !== step);
  return out;
}

/**
 * The graph with `from` feeding `to`; an input takes one value, so what fed it before no longer does.
 * @param {Graph} graph @param {string} from `step.port` @param {string} to `step.port`
 */
export function connect(graph, from, to) {
  const out = clone(graph);
  out.edges = [...out.edges.filter((e) => e.to !== to), { from, to }];
  return out;
}

/** @param {Graph} graph @param {Edge} edge */
export function removeEdge(graph, edge) {
  const out = clone(graph);
  out.edges = out.edges.filter((e) => !(e.from === edge.from && e.to === edge.to));
  return out;
}

/** The edge that feeds an input, or null. @param {Graph} graph @param {string} step @param {string} port */
export function feeds(graph, step, port) {
  return graph.edges.find((e) => e.to === `${step}.${port}`) ?? null;
}

/** @param {Graph} graph @param {string} step @param {string} port */
export function isShown(graph, step, port) {
  return graph.page.panes.some((p) => p.step === step && p.port === port);
}

/** The graph with an output shown in a pane, after the others, or hidden. @param {Graph} graph @param {string} step @param {string} port @param {boolean} shown */
export function showPane(graph, step, port, shown) {
  const out = clone(graph);
  out.page.panes = out.page.panes.filter((p) => !(p.step === step && p.port === port));
  if (shown) out.page.panes.push({ step, port });
  return out;
}

/** The graph with a picked photo's name kept by its id, so it is named when the graph opens again. @param {Graph} graph @param {string} id @param {string} name */
export function nameFile(graph, id, name) {
  const out = clone(graph);
  out.page.files[id] = name;
  return out;
}

/**
 * The graph as the engine takes it: `page.files` keeps only the photos a param still holds.
 * @param {Graph} graph
 */
export function forEngine(graph) {
  const out = clone(graph);
  const held = new Set(Object.values(out.nodes).flatMap((row) => Object.values(row.params)));
  out.page.files = Object.fromEntries(Object.entries(out.page.files).filter(([id]) => held.has(id)));
  return out;
}
