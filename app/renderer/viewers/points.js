// @ts-check
// A PointMap: a point cloud to orbit, drawn with three.js. Panes of point maps share their camera,
// so turning one turns the others, and two results are compared from one view.
//
// The budget (spec 017): at most POINT_BUDGET points a pane, at the smallest whole stride over the
// map's rows and columns that fits, so two panes of one photo draw the same pixels. A pane keeps
// only the points it draws, positions and colours; the .npz it read is let go once they are on the
// card.

import { BufferAttribute, BufferGeometry, PerspectiveCamera, Points, PointsMaterial, Scene, WebGLRenderer } from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import { RAMP, rampIndex, robustRange } from "../colour.js";
import { flags, readNpz } from "../npz.js";

export const TYPE = "PointMap";

/** The most points a pane draws. */
export const POINT_BUDGET = 1_000_000;

/**
 * The smallest whole stride over rows and columns that keeps an h x w map within the budget.
 * @param {number} h @param {number} w @param {number} [budget]
 */
export function strideFor(h, w, budget = POINT_BUDGET) {
  let stride = 1;
  while (Math.ceil(h / stride) * Math.ceil(w / stride) > budget) stride += 1;
  return stride;
}

/**
 * The points drawn, in three's axes (x right, y up, z towards the viewer) from the map's OpenCV
 * ones (x right, y down, z forward), coloured by distance, near brightest.
 * @param {Float32Array} map h x w x 3 @param {ArrayLike<number> | null} valid h x w
 * @param {number} h @param {number} w @param {number} stride
 */
export function sample(map, valid, h, w, stride) {
  const most = Math.ceil(h / stride) * Math.ceil(w / stride);
  // The range from the pixels drawn: their distances, and whether each is valid.
  const zs = new Float32Array(most);
  const ok = new Uint8Array(most);
  let k = 0;
  for (let y = 0; y < h; y += stride) {
    for (let x = 0; x < w; x += stride, k += 1) {
      const i = y * w + x;
      zs[k] = map[i * 3 + 2];
      ok[k] = valid === null || valid[i] ? 1 : 0;
    }
  }
  const [lo, hi] = robustRange(zs, ok);
  const positions = new Float32Array(most * 3);
  const colours = new Uint8Array(most * 3);
  let n = 0;
  for (let y = 0; y < h; y += stride) {
    for (let x = 0; x < w; x += stride) {
      const i = y * w + x;
      const z = map[i * 3 + 2];
      if ((valid !== null && !valid[i]) || !(Number.isFinite(z) && z > 0)) continue;
      positions[n * 3] = map[i * 3];
      positions[n * 3 + 1] = -map[i * 3 + 1];
      positions[n * 3 + 2] = -z;
      const c = rampIndex(z, lo, hi) * 3;
      colours.set(RAMP.subarray(c, c + 3), n * 3);
      n += 1;
    }
  }
  return { positions: positions.slice(0, n * 3), colours: colours.slice(0, n * 3), count: n, median: (lo + hi) / 2 };
}

/** @param {import("../viewer.js").Show} show @returns {Promise<import("../viewer.js").Shown>} */
export async function show({ host, url, link }) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`The points could not be read (${response.status}).`);
  let drawn;
  let total;
  {
    // In a block of its own, so the file and its arrays are let go once the points are taken.
    const arrays = await readNpz(await response.arrayBuffer());
    const map = arrays.get("points") ?? [...arrays.values()].find((a) => a.shape.length === 3 && a.shape[2] === 3);
    if (!map || map.shape.length !== 3 || map.shape[2] !== 3 || !(map.data instanceof Float32Array)) {
      throw new Error("The point file holds no H x W x 3 float32 array.");
    }
    const [h, w] = map.shape;
    const valid = flags(arrays.get("valid"));
    total = h * w;
    drawn = sample(map.data, valid, h, w, strideFor(h, w));
  }

  const canvas = document.createElement("canvas");
  canvas.className = "points";
  canvas.tabIndex = 0;
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label", `A point cloud of ${drawn.count} points; drag or use the arrow keys to turn it`);
  host.replaceChildren(canvas);

  const renderer = new WebGLRenderer({ canvas, antialias: false, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(2, globalThis.devicePixelRatio || 1));
  const scene = new Scene();
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(drawn.positions, 3));
  geometry.setAttribute("color", new BufferAttribute(drawn.colours, 3, true));
  geometry.computeBoundingSphere();
  const material = new PointsMaterial({ size: 1.5, sizeAttenuation: false, vertexColors: true });
  scene.add(new Points(geometry, material));

  // From where the photo was taken, looking into the scene, turning about its middle distance.
  const camera = new PerspectiveCamera(50, 1, 0.01, 1e4);
  camera.position.set(0, 0, 0);
  const controls = new OrbitControls(camera, canvas);
  controls.target.set(0, 0, -Math.max(drawn.median, 0.1));
  controls.listenToKeyEvents(canvas);
  controls.update();

  const render = () => renderer.render(scene, camera);
  const resize = () => {
    const { clientWidth: width, clientHeight: height } = host;
    if (width === 0 || height === 0) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    render();
  };
  const observer = new ResizeObserver(resize);
  observer.observe(host);

  let following = false;
  /** @type {import("../viewer.js").Linked} */
  const me = {
    pose: () => ({ position: camera.position.toArray(), target: controls.target.toArray(), up: camera.up.toArray() }),
    setPose: (pose) => {
      const p = /** @type {{ position: [number, number, number], target: [number, number, number], up: [number, number, number] }} */ (pose);
      following = true;
      camera.position.fromArray(p.position);
      camera.up.fromArray(p.up);
      controls.target.fromArray(p.target);
      controls.update();
      following = false;
      render();
    },
    follow: (from) => me.setPose(from.pose()),
  };
  link.add(me);
  controls.addEventListener("change", () => {
    render();
    if (following) return;
    for (const other of link) if (other !== me) other.follow(me);
  });
  resize();
  render();

  const kept = drawn.count;
  return {
    summary: `drew ${kept.toLocaleString("en")} of ${total.toLocaleString("en")} points`,
    dispose: () => {
      link.delete(me);
      observer.disconnect();
      controls.dispose();
      geometry.dispose();
      material.dispose();
      renderer.dispose();
      // Chromium keeps a few WebGL contexts at most; a pane's goes with it, not when collected.
      renderer.forceContextLoss();
    },
  };
}
