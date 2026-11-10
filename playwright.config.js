// @ts-check
// The e2e tests (spec 017): Playwright starts the app itself, through its Electron support, so no
// browser is installed. Run: npm run e2e, which builds the page first.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests/e2e",
  testMatch: "*.test.js",
  // One app at a time: each test starts its own, on a data root of its own.
  workers: 1,
  fullyParallel: false,
  // A first start may make the engine's bytecode and a run's cache; every wait inside names itself.
  timeout: 120_000,
  expect: { timeout: 30_000 },
  retries: 0,
  reporter: [["list"]],
  // Traces and screenshots of a failure, beside the built page, which git ignores.
  outputDir: "dist/e2e",
  use: { trace: "retain-on-failure", screenshot: "only-on-failure" },
});
