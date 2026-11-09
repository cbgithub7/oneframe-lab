// @ts-check
import js from "@eslint/js";
import globals from "globals";

export default [
  { ignores: ["node_modules/", "engine/", "dist/", "out/"] },
  js.configs.recommended,
  {
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      eqeqeq: ["error", "always"],
      "no-var": "error",
      "prefer-const": "error",
    },
  },
  { files: ["app/main/**/*.js", "scripts/**/*.js", "tests/**/*.js", ".claude/hooks/**/*.js", "eslint.config.js"], languageOptions: { globals: globals.node } },
  { files: ["app/preload/**/*.cjs"], languageOptions: { sourceType: "commonjs", globals: globals.node } },
  {
    // The page is sandboxed: it reaches the engine only through window.oneframe (AGENTS.md, "One
    // door into the page"), so it imports neither Node nor Electron.
    files: ["app/renderer/**/*.js"],
    languageOptions: { globals: globals.browser },
    rules: {
      "no-restricted-imports": ["error", { patterns: [
        { regex: "^node:", message: "The page cannot use Node; ask the engine through window.oneframe." },
        { regex: "^electron(/|$)", message: "The page cannot use Electron; ask through window.oneframe." },
      ] }],
    },
  },
];
