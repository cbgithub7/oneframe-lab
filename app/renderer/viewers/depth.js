// @ts-check
// A Depth: a false-colour image, near brightest, with its range written beside it, in metres when
// the depth is metric and in the model's own units otherwise.

import { RAMP, rampIndex, robustRange } from "../colour.js";
import { flags, readNpz } from "../npz.js";

export const TYPE = "Depth";

/** @param {import("../viewer.js").Show} show @returns {Promise<import("../viewer.js").Shown>} */
export async function show({ host, value, url }) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`The depth could not be read (${response.status}).`);
  const arrays = await readNpz(await response.arrayBuffer());
  const depth = arrays.get("depth") ?? [...arrays.values()].find((a) => a.shape.length === 2 && a.dtype.startsWith("<f"));
  if (!depth || depth.shape.length !== 2) throw new Error("The depth file holds no H x W array.");
  const [h, w] = depth.shape;
  const values = /** @type {Float32Array} */ (depth.data);
  const valid = flags(arrays.get("valid"));
  const [lo, hi] = robustRange(values, valid);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  canvas.className = "picture";
  const unit = value.facets.kind === "metric" ? "m" : "units";
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", `Depth from ${lo.toFixed(2)} to ${hi.toFixed(2)} ${unit}, near brightest`);
  const context = /** @type {CanvasRenderingContext2D} */ (canvas.getContext("2d"));
  const image = context.createImageData(w, h);
  for (let i = 0; i < w * h; i += 1) {
    const v = values[i];
    const o = i * 4;
    if ((valid !== null && !valid[i]) || !(Number.isFinite(v) && v > 0)) {
      image.data[o + 3] = 0;
      continue;
    }
    const c = rampIndex(v, lo, hi) * 3;
    image.data[o] = RAMP[c];
    image.data[o + 1] = RAMP[c + 1];
    image.data[o + 2] = RAMP[c + 2];
    image.data[o + 3] = 255;
  }
  context.putImageData(image, 0, 0);
  const legend = document.createElement("div");
  legend.className = "legend";
  legend.textContent = `near ${lo.toFixed(2)} ${unit} (bright) · far ${hi.toFixed(2)} ${unit} (dark)`;
  host.replaceChildren(canvas, legend);
  return { summary: `${w} x ${h}, ${lo.toFixed(2)} to ${hi.toFixed(2)} ${unit}` };
}
