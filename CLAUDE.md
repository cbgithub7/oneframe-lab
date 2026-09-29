@AGENTS.md

## Claude Code specifics

- **Hooks run on their own** (`.claude/settings.json`); do not work around them:
    - after every edit, the edited file is formatted and lint-fixed (ruff for Python, ESLint for
      JavaScript), and remaining lint errors are reported back;
    - before a turn ends, if anything changed, `npm run check`, `npm run engine:check` and the test
      guard run, and a failure sends you back to fix it;
    - in web sessions, the start hook installs the pinned Node, uv, engine and npm packages.
- **Plan mode** for anything with a spec: explore, then present the plan, then wait.
- **Subagents** for wide searches and research, so their reading does not fill this context.
- **Before opening a PR,** run `/code-review` on the branch and fix what it finds.
