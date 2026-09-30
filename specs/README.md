# Specs

Every change larger than a small fix starts here. The spec is the source of truth for what is
being built; the chat that produced it is not. A fresh session reads the spec and needs nothing
else.

## Stages

| Stage | File | Who | Gate |
| --- | --- | --- | --- |
| 1. Specify | `spec.md`: the problem, what it must do, acceptance criteria, out of scope, open questions | Agent drafts | Owner sets `Status: approved` |
| 2. Plan | `plan.md`: files touched, design, risks, tests (added and removed), how it is verified | Agent drafts after the spec is approved | Owner approves |
| 3. Tasks | `tasks.md`: small ordered steps, each leaving the checks green | Agent | none |
| 4. Implement | code and tests, one task at a time, ticking `tasks.md` | Agent | hooks and CI |
| 5. Validate | the PR checks every acceptance criterion by name; hardware steps come with a report | Agent, then owner | Owner merges |

Open questions in a spec are answered by the owner before the spec is approved. An agent that
finds a new ambiguity while implementing stops and adds it to the spec rather than guessing.

## Writing acceptance criteria

Each one is a check that can fail, and names how it is checked:

- *Good:* "Planning a runtime for compute 6.1 with driver 560 picks the cu126 build (unit test)."
- *Bad:* "Picks a sensible torch build."

Criteria that need a GPU name the hardware and the report that proves them. The plan's
Verification section gives the exact commands; the owner runs them in a local session with
`/local-session` (`.claude/skills/local-session/SKILL.md`), which writes and commits the report
under `specs/<id>/reports/`. Cloud sessions start with `/cloud-session`.

## Numbering and status

Folders are `NNN-short-name`. `Status:` at the top of `spec.md` is one of: draft, approved,
in progress, done, dropped. [docs/handoff.md](../docs/handoff.md) lists the specs in order.

Copy `_template/` to start one.
