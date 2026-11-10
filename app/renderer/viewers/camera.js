// @ts-check
// A Camera: its numbers (intrinsics in pixels, and a pose when it has one).

export const TYPE = "Camera";

/** @param {unknown} value */
const text = (value) => (typeof value === "number" ? String(Math.round(value * 1000) / 1000) : JSON.stringify(value));

/** @param {import("../viewer.js").Show} show @returns {Promise<import("../viewer.js").Shown>} */
export async function show({ host, value, url }) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`The camera could not be read (${response.status}).`);
  const camera = /** @type {Record<string, unknown>} */ (await response.json());
  const list = document.createElement("dl");
  list.className = "facts";
  for (const [k, v] of Object.entries({ ...value.facets, ...camera })) {
    const dt = document.createElement("dt");
    const dd = document.createElement("dd");
    dt.textContent = k;
    dd.textContent = typeof v === "string" ? v : text(v);
    list.append(dt, dd);
  }
  host.replaceChildren(list);
  const f = typeof camera.fx === "number" ? ` fx ${text(camera.fx)} px` : "";
  return { summary: `${value.facets.model ?? "camera"}${f}` };
}
