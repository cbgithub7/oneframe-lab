// A stand-in engine: answers requests after an echo of the method, sends events, splits its
// writes mid-line, and dies on request, so the client's handling of each can be tested.
import { createInterface } from "node:readline";

const write = (/** @type {unknown} */ obj) => process.stdout.write(`${JSON.stringify(obj)}\n`);
process.stderr.write("fake engine starting\n");
write({ event: "engine.ready", engine: "fake", python: "none", nodes: 0 });

for await (const line of createInterface({ input: process.stdin })) {
  const msg = JSON.parse(line);
  if (msg.method === "echo") write({ id: msg.id, result: msg.params });
  else if (msg.method === "split") {
    const text = JSON.stringify({ event: "run.start", run: "r1" }) + "\n" + JSON.stringify({ id: msg.id, result: "ok" }) + "\n";
    process.stdout.write(text.slice(0, 7));
    setTimeout(() => process.stdout.write(text.slice(7)), 20);
  } else if (msg.method === "fail") write({ id: msg.id, error: { message: "The graph cannot run.", problems: [{ node: "x", port: "", message: "bad" }] } });
  else if (msg.method === "noise") {
    process.stdout.write("not json at all\n");
    write({ id: msg.id, result: "after noise" });
  } else if (msg.method === "die") process.exit(7);
  else if (msg.method === "silent") { /* never answers */ }
  else if (msg.method === "late") setTimeout(() => write({ id: msg.id, result: "too late" }), 300);
  else if (msg.method === "null") {
    process.stdout.write("null\n[1, 2]\n");
    write({ id: msg.id, result: "after null" });
  }
}
