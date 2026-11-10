// @ts-check
// Builds the page (spec 017): esbuild bundles app/renderer/main.js, three.js with it, into one
// page.js, beside copies of index.html and styles.css, in dist/renderer/, which the app loads.
// One bundle, so the page needs no import map (an inline script the content security policy would
// allow only by its hash): it stays `script-src 'self'`.
// Run: npm run page:build (npm start and npm run e2e run it first).

import { build } from "esbuild";
import { copyFileSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = path.join(ROOT, "app", "renderer");
/** Where the built page is; the app loads its index.html (app/main/main.js). */
export const PAGE_DIR = path.join(ROOT, "dist", "renderer");

export async function buildPage() {
  rmSync(PAGE_DIR, { recursive: true, force: true });
  mkdirSync(PAGE_DIR, { recursive: true });
  await build({
    entryPoints: [path.join(SOURCE, "main.js")],
    outfile: path.join(PAGE_DIR, "page.js"),
    bundle: true,
    format: "esm",
    platform: "browser",
    // The language the sources are checked at (tsconfig.base.json); Electron's Chromium runs it as is.
    target: "es2024",
    sourcemap: "linked",
    legalComments: "linked",
    logLevel: "warning",
  });
  for (const file of ["index.html", "styles.css"]) copyFileSync(path.join(SOURCE, file), path.join(PAGE_DIR, file));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await buildPage();
  console.log(`page-build: ${path.relative(ROOT, PAGE_DIR)}`);
}
