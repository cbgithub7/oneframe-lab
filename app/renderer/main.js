// @ts-check
// The first page (spec 017): a graph to change, run, and see side by side. It learns everything
// from the engine: the nodes (nodes.list), the port types (ports.list), the graphs (graphs.*),
// what fits a port (graph.fits), and what a run did (its events). Nothing here names a node, a
// graph, a model or a runtime; the models it draws from are plain modules beside it, which the
// node tests check without a browser (tests/app/page.test.js).

import { formModel, readControl } from "./form.js";
import {
  addNode, connect, emptyGraph, feeds, forEngine, isShown, nameFile, openGraph, portsOf, removeEdge,
  removeNode, resetParam, setParam, showPane, splitRef, typeName,
} from "./graph-model.js";
import { idle, jobView, runView, starting } from "./run-view.js";
import { showValue, valueUrl } from "./viewer.js";

/** @typedef {import("../protocol.js").Page.Manifest} Manifest */
/** @typedef {import("../protocol.js").Page.Event} Event */
/** @typedef {import("../protocol.js").Page.Failure} Failure */
/** @typedef {import("../protocol.js").Page.Connection} Connection */
/** @typedef {import("../protocol.js").Page.GraphSummary} GraphSummary */
/** @typedef {import("./graph-model.js").Graph} Graph */
/** @typedef {import("./run-view.js").Card} Card */
/** @typedef {import("./run-view.js").Job} Job */
/** @typedef {import("./viewer.js").Linked} Linked */

const api = window.oneframe;

/** @param {string} id */
const byId = (id) => /** @type {HTMLElement} */ (document.getElementById(id));
const ui = {
  status: byId("engine-status"),
  runStatus: byId("run-status"),
  bar: /** @type {HTMLFormElement} */ (byId("graph-bar")),
  pick: /** @type {HTMLSelectElement} */ (byId("graph-pick")),
  fresh: /** @type {HTMLButtonElement} */ (byId("graph-new")),
  name: /** @type {HTMLInputElement} */ (byId("graph-name")),
  save: /** @type {HTMLButtonElement} */ (byId("graph-save")),
  run: /** @type {HTMLButtonElement} */ (byId("run")),
  stop: /** @type {HTMLButtonElement} */ (byId("stop")),
  filter: byId("catalogue-filter"),
  nodes: byId("nodes"),
  problems: byId("node-problems"),
  cards: byId("cards"),
  panes: byId("panes"),
};

/**
 * Everything the page knows. The graph is the person's; the rest is what the engine said.
 * @type {{ ready: boolean, manifests: Map<string, Manifest>, graphs: GraphSummary[], name: string,
 *   graph: Graph | null, run: import("./run-view.js").RunState, jobs: Record<string, Job>,
 *   waits: Record<string, string>, connecting: { ref: string, side: string, graph: Connection[], add: Connection[] } | null,
 *   problems: Record<string, string[]> }}
 */
const state = {
  ready: false,
  manifests: new Map(),
  graphs: [],
  name: "",
  graph: null,
  run: idle(),
  jobs: {},
  // An offer's job, by step: `runtime:<id>` or `files:<job id>`, so its card shows that job.
  waits: {},
  connecting: null,
  problems: {},
};

/**
 * An element, with attributes (`on*` functions become listeners) and children.
 * @param {string} tag @param {Record<string, unknown>} [attrs] @param {...(Node | string | null | undefined | false)} children
 */
function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), /** @type {EventListener} */ (value));
    else if (key === "className") node.className = String(value);
    else if (key === "dataset") Object.assign(node.dataset, value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children) if (child !== null && child !== undefined && child !== false) node.append(child);
  return node;
}

/** @param {HTMLElement} element @param {string} state @param {string} text */
function say(element, state, text) {
  element.dataset.state = state;
  element.textContent = text;
}

/** A failure as one line: its message and what to do. @param {Partial<Failure> | undefined} failure */
const failureText = (failure) => [failure?.message, failure?.next].filter(Boolean).join(" ");

/**
 * Ask the engine; a failure is shown in the run's status line and answered null.
 * @template {import("../protocol.js").Page.MethodName} M
 * @param {M} method @param {import("../protocol.js").Page.Params<M>} [params]
 * @returns {Promise<Exclude<import("../protocol.js").Page.Answer<M>, { stopped: boolean }> | null>}
 */
async function ask(method, params) {
  try {
    const answer = await api.request(method, params);
    // A queued request a stop or the engine's close took off the queue: nothing to show.
    if (answer !== null && typeof answer === "object" && "stopped" in answer) return null;
    return /** @type {any} */ (answer);
  } catch (error) {
    const failure = /** @type {Failure} */ (error);
    say(ui.runStatus, "failed", failureText(failure) || String(error));
    throw failure;
  }
}

/** @param {string} step */
const manifestOf = (step) => state.manifests.get(state.graph?.nodes[step]?.node ?? "");

/** @param {string} step */
const labelOf = (step) => {
  const row = state.graph?.nodes[step];
  return String(row?.label || manifestOf(step)?.title || step);
};

// ---- The graph: open, new, save --------------------------------------------------------------

/** @param {Graph} graph */
function edit(graph) {
  state.graph = graph;
  state.connecting = null;
  render();
}

async function listGraphs() {
  const listed = await ask("graphs.list");
  if (!listed) return;
  state.graphs = listed.graphs;
  const options = listed.graphs.map((g) => h("option", { value: g.name },
    g.source === "saved" ? `${g.title} (saved)` : g.title));
  if (state.graph && !listed.graphs.some((g) => g.name === state.name)) {
    options.unshift(h("option", { value: "" }, `${state.graph.title ?? "Untitled"} (not saved)`));
  }
  ui.pick.replaceChildren(...options);
  ui.pick.value = listed.graphs.some((g) => g.name === state.name) ? state.name : "";
  const broken = Object.entries(listed.problems);
  if (broken.length) say(ui.runStatus, "warn", broken.map(([file, list]) => `${file}: ${list.join("; ")}`).join(" "));
}

/** @param {string} name */
async function openNamed(name) {
  const got = await ask("graphs.get", { name });
  if (!got) return;
  state.name = got.name;
  ui.name.value = got.name;
  state.run = idle();
  state.waits = {};
  state.problems = {};
  clearPanes();
  edit(openGraph(got.graph));
  ui.pick.value = got.name;
  say(ui.runStatus, "", "");
}

function newGraph() {
  state.name = "untitled";
  ui.name.value = state.name;
  state.run = idle();
  state.waits = {};
  state.problems = {};
  clearPanes();
  edit(emptyGraph("Untitled"));
  listGraphs().catch(() => {});
}

async function save() {
  if (!state.graph) return;
  const name = ui.name.value.trim();
  if (!name) {
    say(ui.runStatus, "failed", "Give the graph a name to save it under.");
    ui.name.focus();
    return;
  }
  const graph = forEngine(state.graph);
  if (!graph.title || graph.title === "Untitled") graph.title = name;
  const saved = await ask("graphs.save", { name, graph });
  if (!saved) return;
  state.name = saved.name;
  state.graph = openGraph(graph);
  say(ui.runStatus, "ready", `Saved as ${saved.name}.`);
  await listGraphs();
}

// ---- Running ---------------------------------------------------------------------------------

async function run() {
  if (!state.graph) return;
  state.run = starting();
  state.waits = {};
  state.problems = {};
  say(ui.runStatus, "running", "Starting the run…");
  render();
  try {
    const started = await ask("graph.run", { graph: forEngine(state.graph) });
    if (!started) {
      state.run = idle();
      say(ui.runStatus, "", "The run was not started.");
    } else if (state.run.pending) {
      // The answer came before run.start: this is the run to follow.
      state.run = { ...state.run, run: started.run };
    }
  } catch (error) {
    const failure = /** @type {Failure} */ (error);
    state.run = idle();
    for (const problem of failure.problems ?? []) (state.problems[problem.node] ??= []).push(`${problem.port ? `${problem.port}: ` : ""}${problem.message}`);
  }
  render();
}

async function stop() {
  const runId = state.run.run;
  ui.stop.disabled = true;
  say(ui.runStatus, "running", "Stopping…");
  await ask("run.stop", runId ? { run: runId } : {}).catch(() => {});
}

/**
 * What an offer does when pressed: install the step's runtime, download its files, or run again.
 * @param {string} step @param {import("./run-view.js").Offer} offer
 */
async function act(step, offer) {
  const manifest = manifestOf(step);
  if (offer.action === "retry") return run();
  if (!manifest) return;
  if (offer.action === "install") {
    const runtime = String(manifest.run?.runtime ?? "");
    const started = await ask("runtimes.install", { runtime }).catch(() => null);
    if (!started) return;
    state.waits[step] = `runtime:${runtime}`;
    if (started.already) state.jobs = { ...state.jobs, [`runtime:${runtime}`]: { kind: "runtime", id: runtime, state: "done", text: "Installed", done: null, total: null } };
  } else if (offer.action === "download") {
    const started = await ask("models.download", { node: manifest.id }).catch(() => null);
    if (!started) return;
    state.waits[step] = `files:${started.job}`;
  }
  render();
}

// ---- Drawing ---------------------------------------------------------------------------------

/** The catalogue: every node by category, or those that fit the port being connected. */
function renderCatalogue() {
  const connecting = state.connecting;
  /** @type {Map<string, Connection[]>} */
  const fits = new Map();
  for (const c of connecting?.add ?? []) if (c.how !== "no") fits.set(c.node, [...(fits.get(c.node) ?? []), c]);
  ui.filter.replaceChildren(...(connecting
    ? [h("p", { className: "hint" }, `Nodes that fit ${connecting.ref}`),
      h("button", { type: "button", onclick: () => { state.connecting = null; render(); } }, "Show every node")]
    : []));
  /** @type {Map<string, Manifest[]>} */
  const byCategory = new Map();
  for (const node of state.manifests.values()) {
    if (connecting && !fits.has(node.id)) continue;
    byCategory.set(node.category, [...(byCategory.get(node.category) ?? []), node]);
  }
  const fragment = document.createDocumentFragment();
  for (const [category, list] of [...byCategory].sort(([a], [b]) => a.localeCompare(b))) {
    fragment.append(h("h3", { className: "category" }, category));
    for (const node of list.sort((a, b) => a.title.localeCompare(b.title))) {
      const ports = (/** @type {"inputs" | "outputs"} */ side) => portsOf(node, side).map((p) => `${p.name}: ${p.type}`).join(", ") || "none";
      const ways = fits.get(node.id) ?? [];
      fragment.append(h("div", { className: "node", title: node.summary ?? "" },
        h("div", { className: "title" }, node.title),
        h("div", { className: "ports" }, `in  ${ports("inputs")}`),
        h("div", { className: "ports" }, `out ${ports("outputs")}`),
        ...(connecting
          ? ways.map((way) => h("button", { type: "button", onclick: () => addAndConnect(node, way) },
            `Add, ${way.port}${way.converter ? ` via ${way.converter}` : ""}`))
          : [h("button", { type: "button", disabled: !state.graph, "aria-label": `Add ${node.title}`, onclick: () => add(node) }, "Add")])));
    }
  }
  if (connecting && fits.size === 0) fragment.append(h("p", { className: "empty" }, "No node fits this port."));
  ui.nodes.replaceChildren(fragment);
}

/** @param {Manifest} manifest */
function add(manifest) {
  if (!state.graph) return;
  const added = addNode(state.graph, manifest);
  edit(added.graph);
  focusLater(`card-${added.step}`);
}

/** @param {Manifest} manifest @param {Connection} way */
function addAndConnect(manifest, way) {
  if (!state.graph || !state.connecting) return;
  const { ref, side } = state.connecting;
  const added = addNode(state.graph, manifest);
  const other = `${added.step}.${way.port}`;
  edit(side === "input" ? connect(added.graph, other, ref) : connect(added.graph, ref, other));
  focusLater(`card-${added.step}`);
}

/** Ask what fits a port, and offer it. @param {string} ref `step.port` @param {"input" | "output"} side */
async function startConnecting(ref, side) {
  if (!state.graph) return;
  const fits = await ask("graph.fits", { graph: forEngine(state.graph), port: ref }).catch(() => null);
  if (!fits) return;
  state.connecting = { ref, side, graph: fits.graph, add: fits.add };
  render();
  focusLater(`connect-${ref}`);
}

/** @param {Connection} way */
const wayText = (way) => {
  const where = `${way.step ? labelOf(way.step) : way.node}.${way.port}`;
  if (way.how === "via") return `${where}, via ${way.converter}`;
  if (way.how === "depends") return `${where} (checked when its value arrives)`;
  return where;
};

/** The choices of the port being connected, under it. @param {string} ref */
function connectChoices(ref) {
  const connecting = state.connecting;
  if (!connecting || connecting.ref !== ref) return null;
  const usable = connecting.graph.filter((c) => c.how !== "no" && c.step);
  const refused = connecting.graph.filter((c) => c.how === "no" && c.step);
  return h("div", { className: "choices", id: `connect-${ref}`, tabindex: "-1", role: "group", "aria-label": `What fits ${ref}` },
    ...usable.map((way) => h("button", {
      type: "button",
      onclick: () => {
        if (!state.graph) return;
        const other = `${way.step}.${way.port}`;
        edit(connecting.side === "input" ? connect(state.graph, other, ref) : connect(state.graph, ref, other));
      },
    }, `Connect ${wayText(way)}`)),
    usable.length === 0 ? h("p", { className: "hint" }, "Nothing in the graph fits; add a node from the catalogue.") : null,
    refused.length ? h("p", { className: "hint" }, `Not: ${refused.map((c) => `${wayText(c)} (${c.reason ?? "no"})`).join("; ")}`) : null,
    h("button", { type: "button", onclick: () => { state.connecting = null; render(); } }, "Cancel"));
}

/** A step's settings. @param {string} step @param {Manifest} manifest */
function renderForm(step, manifest) {
  if (!state.graph) return null;
  const controls = formModel(manifest, state.graph, step);
  if (controls.length === 0) return null;
  const files = state.graph.page.files;
  return h("fieldset", { className: "form" }, h("legend", {}, "Settings"), ...controls.map((control) => {
    const id = `ctl-${step}-${control.name}`;
    const note = h("span", { className: "note", id: `${id}-note` }, control.set ? "set" : "default");
    const problem = h("span", { className: "problem", id: `${id}-problem`, role: "alert" });
    /** @param {string | boolean} input */
    const apply = (input) => {
      const read = readControl(control, input);
      if ("problem" in read) {
        problem.textContent = read.problem;
        return;
      }
      if (state.graph) edit(setParam(state.graph, step, control.name, read.value));
      focusLater(id);
    };
    const described = `${id}-note ${id}-problem${control.doc ? ` ${id}-doc` : ""}`;
    /** @type {HTMLElement} */
    let input;
    if (control.control === "checkbox") {
      input = h("input", { id, type: "checkbox", checked: Boolean(control.value), "aria-describedby": described,
        onchange: (/** @type {Event & { target: HTMLInputElement }} */ e) => apply(e.target.checked) });
    } else if (control.control === "select") {
      input = h("select", { id, "aria-describedby": described, onchange: (/** @type {Event & { target: HTMLSelectElement }} */ e) => apply(e.target.value) },
        ...control.choices.map((choice) => h("option", { value: choice, selected: choice === control.value }, choice)));
    } else if (control.control === "photo") {
      const id_ = typeof control.value === "string" ? control.value : "";
      const name = id_ ? (files[id_] ?? `a photo (${id_.slice(0, 8)})`) : "none picked";
      input = h("span", { className: "photo" },
        h("button", { id, type: "button", "aria-describedby": `${id}-name ${described}`, onclick: () => pickPhoto(step, control.name, id) }, "Pick a photo"),
        h("span", { className: "name", id: `${id}-name` }, name));
    } else if (control.control === "number" || control.control === "text") {
      input = h("input", {
        id, type: control.control === "number" ? "number" : "text", value: control.value === null ? "" : String(control.value),
        min: control.min ?? undefined, max: control.max ?? undefined, step: control.step ?? undefined, "aria-describedby": described,
        onchange: (/** @type {Event & { target: HTMLInputElement }} */ e) => apply(e.target.value),
      });
    } else {
      input = h("span", { className: "hint" }, `${control.type}: not editable here`);
    }
    const reset = h("button", { type: "button", disabled: !control.set, "aria-label": `Reset ${control.label} to its default`,
      onclick: () => { if (state.graph) edit(resetParam(state.graph, step, control.name)); focusLater(id); } }, "Reset");
    return h("div", { className: "control", dataset: { set: String(control.set) } },
      control.control === "photo" ? h("span", { className: "label" }, control.label) : h("label", { for: id }, control.label),
      input, note, reset, problem,
      control.doc ? h("span", { className: "doc", id: `${id}-doc` }, control.doc) : null);
  }));
}

/** @param {string} step @param {string} param @param {string} id the button, to focus again */
async function pickPhoto(step, param, id) {
  const picked = await ask("photo.pick").catch(() => null);
  if (!picked || !state.graph) return;
  edit(nameFile(setParam(state.graph, step, param, picked.id), picked.id, picked.name));
  focusLater(id);
}

/** A step's ports: what feeds each input, and each output with Show. @param {string} step @param {Manifest} manifest */
function renderPorts(step, manifest) {
  const graph = /** @type {Graph} */ (state.graph);
  const inputs = portsOf(manifest, "inputs").map((port) => {
    const ref = `${step}.${port.name}`;
    const edge = feeds(graph, step, port.name);
    const from = edge ? splitRef(edge.from) : null;
    return h("li", {},
      h("span", { className: "port" }, `${port.name}`), " ",
      h("span", { className: "type" }, port.type), " ",
      h("span", { className: "fed" }, from ? `← ${labelOf(from[0])}.${from[1]}` : port.optional ? "← nothing (optional)" : "← nothing yet"),
      h("button", { type: "button", "aria-label": `Connect ${ref}`, onclick: () => startConnecting(ref, "input") }, "Connect…"),
      edge ? h("button", { type: "button", "aria-label": `Disconnect ${ref}`, onclick: () => edit(removeEdge(graph, edge)) }, "Disconnect") : null,
      connectChoices(ref));
  });
  const outputs = portsOf(manifest, "outputs").map((port) => {
    const ref = `${step}.${port.name}`;
    const id = `show-${ref}`;
    return h("li", {},
      h("span", { className: "port" }, port.name), " ",
      h("span", { className: "type" }, port.type), " ",
      h("input", { type: "checkbox", id, checked: isShown(graph, step, port.name),
        onchange: (/** @type {Event & { target: HTMLInputElement }} */ e) => { edit(showPane(graph, step, port.name, e.target.checked)); focusLater(id); } }),
      h("label", { for: id }, "Show"),
      h("button", { type: "button", "aria-label": `Connect ${ref} to an input`, onclick: () => startConnecting(ref, "output") }, "Connect…"),
      connectChoices(ref));
  });
  return h("div", { className: "ports" },
    inputs.length ? h("div", {}, h("h4", {}, "Inputs"), h("ul", {}, ...inputs)) : null,
    outputs.length ? h("div", {}, h("h4", {}, "Outputs"), h("ul", {}, ...outputs)) : null);
}

/** What a phase says on a card, in words. */
const PHASES = /** @type {Record<string, string>} */ ({
  waiting: "Waiting", fitting: "Fitted", running: "Running", cached: "From the cache", done: "Done",
  failed: "Failed", skipped: "Skipped", stopped: "Stopped",
});

/** @param {Card} card */
function fitText(card) {
  const fit = card.fit;
  if (!fit) return null;
  const where = [fit.device, fit.precision].filter(Boolean).join(", ");
  const changes = fit.changes.map((c) => c.why).filter(Boolean);
  const warning = fit.warning ? String(fit.warning.message ?? fit.warning.kind ?? "") : "";
  return h("div", { className: "fit" },
    `Fit: ${where || fit.outcome}`,
    changes.length ? h("div", {}, `Changed: ${changes.join("; ")}`) : null,
    warning ? h("div", { className: "warn" }, `Warning: ${warning}`) : null,
    fit.message && fit.outcome !== "fits" ? h("div", {}, fit.message) : null);
}

/** A step's run state, its offer and its job. @param {string} step */
function renderStatus(step) {
  const card = state.run.cards[step];
  const problems = state.problems[step] ?? [];
  const job = jobFor(step, card);
  const children = [];
  if (card) {
    let line = PHASES[card.phase] ?? card.phase;
    if (card.phase === "done" && card.seconds !== undefined) line += ` in ${card.seconds.toFixed(1)} s`;
    if (card.phase === "done" && card.peak_vram_mb) line += `, ${Math.round(card.peak_vram_mb)} MB on the card at most`;
    children.push(h("div", { className: "phase" }, line));
    children.push(fitText(card));
    if (card.phase === "running" && (card.stage || card.progress)) {
      const p = card.progress;
      children.push(h("div", {}, [card.stage, p?.what].filter(Boolean).join(": ")));
      if (p && p.total) children.push(h("progress", { max: p.total, value: p.done ?? 0, "aria-label": `${labelOf(step)}: progress` }));
    }
    if (card.note) children.push(h("div", { className: "hint" }, card.note));
    if (card.failure) children.push(h("div", { className: "problem" }, failureText(card.failure)));
  }
  for (const problem of problems) children.push(h("div", { className: "problem" }, problem));
  if (job) {
    children.push(h("div", { className: "job", dataset: { state: job.state } }, job.text));
    if (job.state === "running" && job.total) children.push(h("progress", { max: job.total, value: job.done ?? 0, "aria-label": `${labelOf(step)}: ${job.kind === "runtime" ? "install" : "download"}` }));
    if (job.state === "failed" && job.failure?.next) children.push(h("div", { className: "hint" }, job.failure.next));
  }
  const offer = offerNow(step, card, job);
  if (offer) children.push(offer);
  return children;
}

/**
 * The install or download a card follows: the one its offer started, or, for a wait, the job
 * mending what it failed on (its runtime's install, or the latest download of its node's files).
 * @param {string} step @param {Card | undefined} card
 * @returns {Job | undefined}
 */
function jobFor(step, card) {
  if (state.waits[step]) return state.jobs[state.waits[step]];
  if (card?.offer?.action !== "wait") return undefined;
  const manifest = manifestOf(step);
  if (!manifest) return undefined;
  if (card.offer.for === "runtime") return state.jobs[`runtime:${String(manifest.run?.runtime ?? "")}`];
  return Object.values(state.jobs).filter((job) => job.kind === "files" && job.node === manifest.id).at(-1);
}

/** The button a card offers now, if any. @param {string} step @param {Card | undefined} card @param {Job | undefined} job */
function offerNow(step, card, job) {
  const running = state.run.pending || (state.run.run !== null && state.run.ended === null);
  if (running) return null;
  if (job && job.state === "running") return null;
  if (job && job.state === "done") return h("button", { type: "button", className: "primary", onclick: () => run() }, "Run again");
  const offer = card?.offer;
  if (!offer) return null;
  if (offer.action === "wait") {
    return h("div", { className: "hint" }, offer.for === "runtime" ? "Waiting for its runtime's install to end." : "Waiting for its files' download to end.");
  }
  const words = { install: "Install", download: "Download", retry: "Run again" }[offer.action];
  const what = offer.action === "install" ? ` ${String(manifestOf(step)?.run?.runtime ?? "")}` : "";
  return h("button", { type: "button", className: "primary", "aria-label": `${words}${what} for ${labelOf(step)}`, onclick: () => act(step, offer) }, words);
}

function renderCards() {
  const graph = state.graph;
  if (!graph) return;
  const steps = Object.keys(graph.nodes);
  if (steps.length === 0) {
    ui.cards.replaceChildren(h("p", { className: "empty" }, "No nodes yet: add one from the catalogue."));
    return;
  }
  ui.cards.replaceChildren(...steps.map((step) => {
    const row = graph.nodes[step];
    const manifest = state.manifests.get(row.node);
    const card = state.run.cards[step];
    const headingId = `card-${step}-title`;
    return h("article", { className: "card", id: `card-${step}`, tabindex: "-1", "aria-labelledby": headingId,
      dataset: { step, phase: card?.phase ?? "idle" } },
    h("header", {},
      h("h3", { id: headingId }, labelOf(step)),
      h("span", { className: "id" }, row.node),
      h("button", { type: "button", "aria-label": `Remove ${labelOf(step)}`, onclick: () => edit(removeNode(graph, step)) }, "Remove")),
    manifest ? renderPorts(step, manifest) : h("p", { className: "problem" }, `No node ${row.node} is installed.`),
    manifest ? renderForm(step, manifest) : null,
    h("div", { className: "run", role: "status", "aria-live": "polite" }, ...renderStatus(step)));
  }));
}

/** What each pane shows, so a pane is drawn again only when its value changes. @type {Map<string, { key: string, dispose?: () => void }>} */
const drawn = new Map();
/** The point panes' cameras, linked. @type {Set<Linked>} */
const link = new Set();

/** Every pane's drawing let go, for a graph opened anew: a pane of the same step and port in it is not this one. */
function clearPanes() {
  for (const shown of drawn.values()) shown.dispose?.();
  drawn.clear();
  ui.panes.replaceChildren();
}

function renderPanes() {
  const graph = state.graph;
  const panes = graph?.page.panes ?? [];
  const wanted = new Set(panes.map((p) => `${p.step}.${p.port}`));
  for (const [id, shown] of drawn) {
    if (!wanted.has(id)) {
      shown.dispose?.();
      drawn.delete(id);
    }
  }
  if (!graph || panes.length === 0) {
    ui.panes.replaceChildren(h("p", { className: "empty" }, "Tick Show on an output to see it here."));
    return;
  }
  const existing = new Map([...ui.panes.querySelectorAll("section.pane")].map((el) => [/** @type {HTMLElement} */ (el).dataset.pane, el]));
  const elements = panes.map((pane) => {
    const id = `${pane.step}.${pane.port}`;
    const manifest = manifestOf(pane.step);
    const port = manifest ? portsOf(manifest, "outputs").find((p) => p.name === pane.port) : undefined;
    const value = state.run.cards[pane.step]?.outputs?.[pane.port];
    let element = /** @type {HTMLElement | undefined} */ (existing.get(id));
    if (!element) {
      const body = h("div", { className: "body" });
      const caption = h("p", { className: "caption", role: "status" });
      element = h("section", { className: "pane", dataset: { pane: id, state: "empty" }, "aria-label": `${labelOf(pane.step)}: ${pane.port}` },
        h("header", {},
          h("h3", {}, `${labelOf(pane.step)} · ${pane.port}`),
          h("span", { className: "type" }, port ? typeName(port.type) : ""),
          h("button", { type: "button", "aria-label": `Hide ${labelOf(pane.step)}: ${pane.port}`,
            onclick: () => { if (state.graph) edit(showPane(state.graph, pane.step, pane.port, false)); } }, "Hide")),
        body, caption);
    }
    const body = /** @type {HTMLElement} */ (element.querySelector(".body"));
    const caption = /** @type {HTMLElement} */ (element.querySelector(".caption"));
    const shown = drawn.get(id);
    if (!value) {
      if (!shown) {
        element.dataset.state = "empty";
        caption.textContent = "Run the graph to see this.";
      }
    } else if (shown?.key !== value.key) {
      shown?.dispose?.();
      const mine = { key: value.key };
      drawn.set(id, mine);
      element.dataset.state = "loading";
      caption.textContent = "Reading…";
      const pane = element;
      showValue(body, value, link).then((result) => {
        if (drawn.get(id) !== mine) return result.dispose?.();
        Object.assign(mine, { dispose: result.dispose });
        pane.dataset.state = "drawn";
        pane.dataset.value = valueUrl(value);
        caption.textContent = result.summary;
      }, (/** @type {Error} */ error) => {
        if (drawn.get(id) !== mine) return;
        pane.dataset.state = "failed";
        caption.textContent = `Could not show it: ${error.message ?? String(error)}`;
      });
    }
    return element;
  });
  // Put back only when the panes changed: moving an element blurs it, and a point pane is
  // turned from the keyboard while its run's events arrive.
  const now = [...ui.panes.children];
  if (now.length !== elements.length || now.some((child, i) => child !== elements[i])) ui.panes.replaceChildren(...elements);
}

function renderBar() {
  const running = state.run.pending || (state.run.run !== null && state.run.ended === null);
  for (const control of [ui.pick, ui.fresh, ui.name, ui.save]) control.disabled = !state.ready;
  ui.run.disabled = !state.ready || !state.graph || running;
  ui.stop.disabled = !running || state.run.pending;
  ui.save.disabled = !state.ready || !state.graph;
  if (state.run.ended === "done") say(ui.runStatus, "ready", "The run is done.");
  else if (state.run.ended === "stopped") say(ui.runStatus, "", "The run was stopped.");
  else if (state.run.ended === "failed") say(ui.runStatus, "failed", `The run ended with a failure: ${failureText(state.run.failure)}`);
  else if (state.run.run && !state.run.ended) say(ui.runStatus, "running", "Running…");
}

/** Focus an element by id once the page is drawn again, so the keyboard keeps its place. @param {string} id */
function focusLater(id) {
  queueMicrotask(() => document.getElementById(id)?.focus());
}

function render() {
  const focused = document.activeElement?.id;
  renderBar();
  renderCatalogue();
  renderCards();
  renderPanes();
  if (focused && document.activeElement?.id !== focused) document.getElementById(focused)?.focus();
}

/** Only what a run's event changes: the cards' status, the panes and the bar. */
function renderRun() {
  for (const step of Object.keys(state.graph?.nodes ?? {})) {
    const card = document.getElementById(`card-${step}`);
    if (!card) continue;
    card.dataset.phase = state.run.cards[step]?.phase ?? "idle";
    card.querySelector(".run")?.replaceChildren(...renderStatus(step).filter((c) => c !== null));
  }
  renderPanes();
  renderBar();
}

// ---- The engine ------------------------------------------------------------------------------

/** @param {import("../protocol.js").Page.Hello} hello */
async function ready(hello) {
  if (state.ready) return;
  state.ready = true;
  say(ui.status, "ready", `Engine ${hello.engine} · Python ${hello.python} · ${hello.nodes} nodes`);
  const listed = await ask("nodes.list");
  if (listed) {
    state.manifests = new Map(listed.nodes.map((n) => [n.id, n]));
    ui.problems.replaceChildren(...Object.entries(listed.problems).map(([file, list]) =>
      h("div", { className: "problem" }, `${file}: ${list.join("; ")}`)));
  }
  await listGraphs();
  if (state.graphs.length) await openNamed(state.graphs[0].name);
  else newGraph();
  render();
}

/** @param {Event} event */
function onEvent(event) {
  if (event.event === "engine.ready") {
    ready(event).catch((error) => say(ui.status, "failed", `Could not list nodes: ${failureText(error) || error}`));
    return;
  }
  if (event.event === "engine.failed") return say(ui.status, "failed", failureText(event));
  if (event.event === "engine.exit") {
    state.ready = false;
    say(ui.status, "failed", `The engine stopped (code ${event.code}).`);
    return render();
  }
  if (event.event === "engine.warning") return;
  const jobs = jobView(state.jobs, event);
  const runState = runView(state.run, event);
  if (jobs === state.jobs && runState === state.run) return;
  state.jobs = jobs;
  state.run = runState;
  renderRun();
}

ui.bar.addEventListener("submit", (event) => {
  event.preventDefault();
  save().catch(() => {});
});
ui.pick.addEventListener("change", () => {
  if (ui.pick.value) openNamed(ui.pick.value).catch(() => {});
});
ui.fresh.addEventListener("click", newGraph);
ui.run.addEventListener("click", () => run());
ui.stop.addEventListener("click", () => stop());

api.onEvent(onEvent);

// The engine may have become ready before this page loaded and subscribed; ask once directly.
api.request("engine.hello").then(ready).catch((/** @type {Failure | undefined} */ failure) => {
  // Not ready yet, and the engine.ready event will arrive; or it failed before this page subscribed,
  // and the app answers with why.
  if (failure?.kind === "request" && failure?.reason === "not_running" && failure?.retry) return;
  say(ui.status, "failed", failureText(failure));
});
