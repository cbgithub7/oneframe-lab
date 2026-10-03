# Specs

Every change larger than a small fix starts here. The spec is the source of truth for what is
being built; the chat that produced it is not. A fresh session reads the spec and needs nothing
else.

## Stages

| Stage | File | Who | Gate |
| --- | --- | --- | --- |
| 1. Specify | `spec.md`: the problem, requirements, acceptance criteria, design, tests, verification, decisions taken, out of scope, open questions | Agent drafts; an agent reviews the draft | Owner sets `Status: approved` |
| 2. Tasks | `tasks.md`: small ordered steps, each leaving the checks green | Agent | none |
| 3. Implement | code and tests, one task at a time, ticking `tasks.md` | Agent | hooks and CI |
| 4. Validate | the PR checks every acceptance criterion by name; hardware steps come with a report | Agent, then owner | Owner merges |

One gate: the owner approves the spec once, design included. Aim for about 150 lines and at most
8 acceptance criteria. Retry counts, edge cases and the like belong in code and tests; writing them
in the spec as well only makes two places to keep true.

Specs 001 to 003 were written under the earlier process, with a separate `plan.md` approved after
the spec. The test guard reads "- Removed" lists from both files.

## Who decides

The owner decides product behaviour, licences and money, the platform matrix, data formats that
are hard to change, and the rules in [AGENTS.md](../AGENTS.md); these are the spec's open
questions. Every other choice an agent makes itself, logged under "Decisions taken" with its
reason, so the owner can see it and overturn it.

An agent that finds a new ambiguity while implementing stops and adds it to the spec: to
"Decisions taken" if it is reversible, to the open questions if it is the owner's.

## Spikes

A feasibility question ("does this model run on Windows", "how long does it load") is a spike, not
a spec: time-boxed, a throwaway branch, ending in a report committed for the owner to read. A spike
changes no rule and ships no code; what it learns goes into the next spec.

## Writing acceptance criteria

Each one is a check that can fail, and names how it is checked:

- *Good:* "Planning a runtime for compute 6.1 with driver 560 picks the cu126 build (unit test)."
- *Bad:* "Picks a sensible torch build."

Criteria that need a GPU name the hardware and the report that proves them. A report from any real
machine counts, the owner's or a rented one, and names the machine. The spec's Verification
section gives the exact commands; the owner runs them in a local session with `/local-session`
(`.claude/skills/local-session/SKILL.md`), which writes and commits the report under
`specs/<id>/reports/`. Cloud sessions start with `/cloud-session`.

## Numbering and status

Folders are `NNN-short-name`, numbered as they are created; [docs/handoff.md](../docs/handoff.md)
lists the order they are worked in. `Status:` at the top of `spec.md` is one of: draft, approved,
in progress, done, dropped.

Copy `_template/` to start one.
