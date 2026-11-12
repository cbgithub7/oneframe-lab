// @ts-check
// What the stop hook runs before a turn ends, apart from the hook so a test can read it.
//
// The engine's check is its fast tier: everything but the tests that start the real uv, which
// install real environments and take minutes on Windows (the review of 2026-11-07, decision 5).
// The full `npm run engine:check` runs before a PR (AGENTS.md, rule 5) and in CI.

/** Each check: its name in a failure's message, the command, and its arguments.
 * @type {ReadonlyArray<readonly [string, string, readonly string[]]>} */
export const STOP_CHECKS = [
  ["npm run check", "npm", ["run", "--silent", "check"]],
  ["npm run engine:check:fast", "npm", ["run", "--silent", "engine:check:fast"]],
  ["test guard", "node", ["scripts/test-guard.js"]],
];
