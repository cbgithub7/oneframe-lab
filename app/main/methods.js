// @ts-check
// The engine methods the page may call, kept apart from main.js so that a test can check them
// against the methods the engine really has without starting Electron.

/** What the renderer may ask the engine. Anything else is refused in main.js. */
export const ENGINE_METHODS = new Set([
  "engine.hello",
  "nodes.list",
  "nodes.reload",
  "ports.list",
  "graph.validate",
  "graph.run",
  "run.stop",
  "runtimes.list",
  "runtimes.plan",
  "runtimes.install",
  "runtimes.stop",
  "runtimes.remove",
]);
