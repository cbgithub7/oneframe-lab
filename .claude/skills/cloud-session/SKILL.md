---
name: cloud-session
description: Start of every Oneframe Lab session in Claude Code on the web (a cloud container with no GPU). Confirms the environment the start hook built, finds the active spec and its stage, runs the baseline checks, and reports what happens next under the project's rules. Use at the start of a cloud session, and whenever asked "where are we" or "what's next".
---

# Cloud session

A cloud session writes specs, plans and code, and proves everything a CPU can prove. It cannot
prove anything that needs a GPU, and it may be unable to reach Hugging Face. Work that needs real
hardware is handed to a local session (`/local-session`) through the spec's plan.

Do these steps in order, then report. Change nothing until step 5 is done.

## 1. Rules

Read `AGENTS.md` and `docs/handoff.md` in full. They override anything in this skill that
disagrees with them; if they disagree, say so in the report.

## 2. Environment

The SessionStart hook (`.claude/hooks/session-start.sh`) should have installed everything.
Confirm it did:

- `node --version` matches `.node-version`.
- `uv --version` is at least the `required-version` in `engine/pyproject.toml`.
- `uv run --project engine --frozen python --version` prints the Python in
  `engine/.python-version`, without installing anything.
- `node_modules/.bin/eslint` exists.

If any of these fail, run `CLAUDE_CODE_REMOTE=true .claude/hooks/session-start.sh` once and check
again. If it still fails, stop and report the failing command and its output. Do not work around
a broken environment by installing other versions.

## 3. Git

- `git fetch origin`, then `git status -sb` and `git log --oneline -5`.
- Work never happens on `main` (it is protected). If the harness named a branch for this session,
  use it. Otherwise make one from `origin/main`: `spec/NNN-short-name` for spec work, or
  `fix/short-name` for a small fix with no spec.
- If the branch is behind `origin/main`, merge `origin/main` into it before starting. Never
  rebase or force-push a branch that is already pushed.

## 4. The active spec and its stage

List `specs/*/spec.md` and the table in `docs/handoff.md`. The owner's message usually names the
spec; if it does not, the active spec is the lowest-numbered one that is not `done` or `dropped`.
Its stage decides the next action:

| What you find | Next action |
| --- | --- |
| `spec.md` Status: draft | Help the owner finish it: open questions answered, acceptance criteria that can fail. Do not plan or code. |
| Spec approved; `plan.md` is a placeholder | Write `plan.md` and `tasks.md` from the templates in `specs/_template/`, then stop for the owner's approval. |
| Plan written, not approved | Wait. Answer questions about it and revise it on request. |
| Plan approved; tasks left | Implement from the first unticked task in `tasks.md`. |
| All tasks ticked | Validate against every acceptance criterion, then open the PR (step 7). |

A plan counts as approved only when the owner says so in this session or `plan.md` says
`Status: approved` with a date. Never approve your own plan.

## 5. Baseline checks

Run `npm run check`, `npm run engine:check`, `node scripts/test-guard.js` and `npm run versions`.
Anything red before you have changed anything is not yours to hide. Report it first; fix it only
if the owner agrees, or if it blocks the task and the fix is small, in its own commit.

## 6. Report, then work

Tell the owner, in a few lines:

- the environment (Node, uv, Python), the branch, and the checks (green or what is red);
- the active spec, its stage, and the next action from the table;
- anything that needs the owner: a question, an approval, or a hardware step.

Then do the next action. While working:

- **Small steps.** One task at a time. Tick it in `tasks.md` and commit when it lands and the
  checks are green. The hooks format each edit and rerun the checks before a turn ends; fix what
  they report, never weaken or delete a test to quiet them.
- **Context.** Send wide searches and research to subagents. If the session grows long (it has
  compacted, or you are re-reading settled things), stop at a clean point: tasks ticked, work
  committed and pushed, and a line under the last ticked task saying where to pick up. Tell the
  owner to continue in a fresh session.
- **New ambiguity.** Stop and add it to the spec's open questions rather than guessing.
- **No GPU here.** For every acceptance criterion marked hardware, make sure the plan's
  Verification section gives the exact commands for `/local-session` and the report they write.
  Mark those criteria "pending hardware" in the PR, never as passed.

## 7. Opening the PR

Your job ends when the PR is open.

1. Run `/code-review` on the branch. Fix what it finds, or answer each finding in the PR.
2. Update the docs in the same change if anything structural moved: `AGENTS.md`,
   `docs/architecture.md`, `docs/handoff.md` (the spec's status), and the spec's `Status:`.
3. Push the branch and open the PR against `main` using `.github/pull_request_template.md`:
    - each acceptance criterion with how it was checked;
    - hardware criteria as "pending hardware", with the `/local-session` commands;
    - anything unverified, said plainly.
4. Tell the owner the PR link, what is green, and what is waiting on hardware.
