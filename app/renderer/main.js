// @ts-check
// The first page: engine status and the node catalogue, both read from the engine. Nothing here
// names a node; the list is whatever the engine found in its node folders.

const api = window.oneframe;
const status = /** @type {HTMLElement} */ (document.getElementById("engine-status"));
const nodesEl = /** @type {HTMLElement} */ (document.getElementById("nodes"));
const problemsEl = /** @type {HTMLElement} */ (document.getElementById("node-problems"));

/** @param {string} state @param {string} text */
function setStatus(state, text) {
  status.dataset.state = state;
  status.textContent = text;
}

/** @param {string} tag @param {string} className @param {string} [text] */
function el(tag, className, text) {
  const node = document.createElement(tag);
  node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

/** @param {Record<string, unknown>} ports */
function portText(ports) {
  return Object.entries(ports ?? {})
    .map(([name, spec]) => `${name}: ${typeof spec === "string" ? spec : /** @type {any} */ (spec).type}`)
    .join(", ") || "none";
}

async function loadNodes() {
  const { nodes, problems } = await api.request("nodes.list");
  /** @type {Map<string, any[]>} */
  const byCategory = new Map();
  for (const node of nodes) {
    const list = byCategory.get(node.category) ?? [];
    list.push(node);
    byCategory.set(node.category, list);
  }
  const fragment = document.createDocumentFragment();
  for (const [category, list] of [...byCategory].sort(([a], [b]) => a.localeCompare(b))) {
    fragment.append(el("div", "category", category));
    for (const node of list) {
      const card = el("div", "node");
      card.append(el("div", "title", node.title), el("div", "ports", `in  ${portText(node.inputs)}`),
        el("div", "ports", `out ${portText(node.outputs)}`));
      card.title = node.summary ?? "";
      fragment.append(card);
    }
  }
  nodesEl.replaceChildren(fragment);
  problemsEl.replaceChildren(...Object.entries(problems ?? {}).map(([file, list]) =>
    el("div", "problem", `${file}: ${/** @type {string[]} */ (list).join("; ")}`)));
}

api.onEvent((event) => {
  if (event.event === "engine.ready") {
    setStatus("ready", `Engine ${event.engine} · Python ${event.python} · ${event.nodes} nodes`);
    loadNodes().catch((error) => setStatus("failed", `Could not list nodes: ${error.message}`));
  } else if (event.event === "engine.failed") {
    setStatus("failed", [event.message, event.next].filter(Boolean).join(" "));
  } else if (event.event === "engine.exit") {
    setStatus("failed", `The engine stopped (code ${event.code}).`);
  }
});

// The engine may have become ready before this page loaded and subscribed; ask once directly.
api.request("engine.hello").then((hello) => {
  setStatus("ready", `Engine ${hello.engine} · Python ${hello.python} · ${hello.nodes} nodes`);
  return loadNodes();
}).catch(() => {
  // not ready yet: the engine.ready event will arrive
});
