// @ts-check
// The methods the page may call, kept apart from main.js so that a test can check them against
// the methods the engine really has without starting Electron.

/** What the renderer may ask the engine. Anything else is refused in door.js. */
export const ENGINE_METHODS = new Set([
  "engine.hello",
  "nodes.list",
  "nodes.reload",
  "nodes.fit",
  "ports.list",
  "graph.validate",
  "graph.fits",
  "graph.run",
  "graphs.list",
  "graphs.get",
  "graphs.save",
  "graphs.remove",
  "run.stop",
  "runtimes.list",
  "runtimes.plan",
  "runtimes.install",
  "runtimes.stop",
  "runtimes.remove",
  "models.download",
  "models.stop",
]);

/**
 * The app's own methods, which the page asks through the same door and the main process answers
 * (door.js): `photo.pick`, the system's open dialog, and the engine's import of the photo chosen.
 */
export const APP_METHODS = new Set(["photo.pick"]);
