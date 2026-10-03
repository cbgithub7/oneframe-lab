# NNN: Title

Status: draft
Owner approval: (date, once approved)

One document: what is built and how. Aim for about 150 lines and at most 8 acceptance criteria.
Mechanisms belong in code and tests; write here what someone needs to judge and check the work.

## Problem

What is missing or wrong, and for whom. Two or three sentences.

## Requirements

What it must do, as numbered statements.

1. ...

## Acceptance criteria

Each a check that can fail, with how it is checked (unit test, integration test, CI job, or a
hardware report on named hardware).

- [ ] AC1: ...

## Design

How it works: the files touched and why, the main mechanisms, the risks and what catches them.
Reference the architecture doc rather than repeating it.

## Tests

- Added: ...
- Removed: none (each removed test named here with its reason, or the test guard fails)

## Verification

Which acceptance criterion is checked how. Hardware steps: the exact commands the owner runs in a
local session and the report they produce.

## Decisions taken

Reversible choices an agent made, each with its reason, so the owner can see and overturn them.

## Out of scope

What this spec deliberately does not do, and which later spec does.

## Open questions

Only what the owner must decide (product behaviour, licences, the platform matrix, data formats
that are hard to change, the rules). Answered before approval.
