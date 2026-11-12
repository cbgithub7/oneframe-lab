# 024: The memory model's larger-of

Status: draft
Owner approval: (date, once approved)

A small spec beside step 3c's five, from the [review of 2026-11-07](../../docs/reviews/2026-11-07.md),
section 1 ("the open findings") and decision 4, which the owner took on 2026-11-12: the memory
model gains a larger-of, in its own spec. It needs none of 019 to 023 and none needs it; its last
task is a run on a real card.

## Problem

A memory model's formula is a base plus terms (`oneframe/memory/model.py`). MoGe's peak on a card
is the larger of two parts: one that grows with the resolution level, one that grows with the
photo ([AC7's report](../005-first-models/reports/2026-11-06-ac7.md), finding 1). A sum that covers
both over-estimates, and more with every megapixel: at 7.2 MP the peaks at levels 5 and 9 are 79
to 80 % of their estimates. On a small card the fit then gives up quality it could have kept. By
the fitted figures, on a card with 6,000 MB free and a 16 MP photo, the estimate at level 9 is
4,753 MB against a budget of 4,389, so the fit picks level 5, where the two parts say level 9
would peak at about 3.4 GB (the reviewer's arithmetic, not measured).

## Requirements

1. **A formula may be the larger of several parts** (open question 1), each a base plus terms as
   today. Its value is the largest part's; it is unknown when any part is, for that part's reason.
   Every formula that takes terms may be one: `working`, `weights_on_device` and `system_mb`.
2. **A malformed one is refused, every problem named,** as the rest of a memory model is: fewer
   than two parts, a part that is itself a larger-of, or `mb` or `terms` beside the parts.
3. **The fit, the retry after `oom` and what a machine learns work on it unchanged:** they read a
   formula's value, never its form.
4. **MoGe-2's and MoGe-3's working memory is fitted again as a larger-of,** from runs on a real
   card on photos from 0.3 to 24 MP, so that every peak is at most its estimate and the estimate
   on the full photo is close to the peak.
5. **The photo tool makes the larger photos:** the bench needs 16 and 24 MP, which the owner's
   7.2 MP photo gives when enlarged. Enlarged photos are not committed.

## Acceptance criteria

- [ ] AC1: `test_a_formula_may_be_the_larger_of_its_parts` (`test_memory.py`): a model whose working
  memory is the larger of a part in a param and a part in the input's pixels evaluates to the
  first where the param is high and the photo small, to the second where they are the other way
  round, and to unknown, with the reason, when a part's base is null.
- [ ] AC2: `test_a_malformed_larger_of_is_refused_by_name` (`test_memory.py`): one part, a part
  that is a larger-of, and `mb` beside the parts are each a problem that names the formula.
- [ ] AC3: `test_the_fit_keeps_the_quality_a_sum_would_give_up` (`test_memory.py`): two test models
  with the same two parts, one as a sum and one as a larger-of, on a test machine whose budget
  lies between the two estimates: the fit of the sum makes its quality change, and the fit of the
  larger-of makes none.
- [ ] AC4: `test_the_photo_tool_enlarges_to_megapixels` (`test_model_photos.py`): asked for 16 MP
  of a small test photo, the tool writes a photo of 16 MP within 1 %, in the photo's proportions,
  into the folder it is given, and writes nothing else.
- [ ] AC5: a report from a real card, the owner's GTX 1070 or another, named in it: for MoGe-3 and
  MoGe-2 at levels 0, 5 and 9, on the full photo (7.2 MP) and the 24 MP photo, neither of which
  the figures were fitted on, every run ends `done` and every card peak is at most its estimate.
- [ ] AC6: the same report: on the full photo, no estimate at levels 0, 5 or 9 is more than 15 %
  above its peak (today: up to 27 %).

## Design

**The form** (open question 1). A formula is `{mb, terms, source}`, or:

```json
"working": {
  "larger_of": [
    { "mb": 0, "terms": [ { "coef": 104.07, "of": ["bytes"] }, { "coef": 27.05, "of": ["bytes", "resolution_level"] } ] },
    { "mb": 0, "terms": [ { "coef": 0.0001232, "of": ["image.pixels"] } ] }
  ],
  "source": "..."
}
```

(the figures shown are today's, split, not the refit's). One `source` covers the formula.

**`oneframe/memory/model.py`:** `Formula` gains `larger_of: tuple[Formula, ...]`, empty for a plain
one; `read_formula` reads the parts with the code that reads a plain formula, and adds the three
refusals. `outside_torch_mb`, which takes no terms, takes no parts. The model's hash is of its
raw JSON, so a manifest that changes form starts its learning over, as any change to a model does.

**`oneframe/memory/estimate.py`:** `evaluate` answers the largest part, each part evaluated as a
formula is today. Nothing else changes: `estimate`, the fit and `learned` call `evaluate`.

**The photo tool** (`engine/tests/models/photos.py`): `photos.py <original> --megapixels 16 24
--out <folder>` writes `photo-16mp.jpg` and `photo-24mp.jpg` there, enlarged with the filter the
tool already uses, and none of the committed sizes. `.gitignore` gains `photo-*mp.jpg`.
`specs/024-larger-of/bench/` holds two graphs, `photo-16mp.json` and `photo-24mp.json`, spec 005's
bench graphs with the photo's path changed.

**The refit** (task 4, a local session; Verification gives the commands). The figures are fitted
by spec 005's rule, on its 768 and 1536 px photos and the 16 MP one: each part the smallest line
that covers every peak where that part is the larger. The full photo and the 24 MP one are then
run with the new figures and are never fitted on. Depth Pro's model is left as it is: its report
shows no second part.

**Docs:** nodes.md's memory model section (the form, when to use it); the handoff's open item on
MoGe's peak goes with the PR that lands the refit.

Risks: an enlarged photo's peak differs from a real 24 MP photo's (memory follows the pixel count
and the settings, not the picture: the reviewer's reasoning, and the report says so beside its
figures); at 24 MP the estimate does not fit the 1070 and the run is refused before it is measured
(the report then gives the largest size that ran, and AC5 names that size instead); two parts are
still not the peak's true form (AC6 fails, and the report's ratios say what to try next).

## Tests

- Added: the four tests the criteria name; a case in the manifest tests that `larger_of` on
  `outside_torch_mb` is refused.
- Removed: none
- Renamed: none

## Verification

AC1 to AC4 are tests in `npm run engine:check`, in CI on Windows and Ubuntu. AC5 and AC6 need a
card: a local session (`/local-session`) on the owner's PC, from the branch's root, in a terminal
outside Claude desktop's package, on the data root spec 005's AC6 made (both runtimes installed,
the files downloaded):

1. `uv run --project engine --frozen python engine/tests/models/photos.py <the original>
   --megapixels 16 24 --out engine/tests/models`.
2. For each `<graph>` of `specs/005-first-models/bench/photo-768.json`, `photo-1536.json` and
   `specs/024-larger-of/bench/photo-16mp.json`, and each `<key>` of `moge3` and `moge2`:
   `npm run bench:fit -- --graph <graph> <key> --set resolution_level=0 --set resolution_level=5
   --set resolution_level=9`.
3. Fit the two parts from step 2's reports into both manifests, as "The refit" says.
4. Step 2's command on `specs/005-first-models/bench/photo-full.json` and
   `specs/024-larger-of/bench/photo-24mp.json`.
5. Write `specs/024-larger-of/reports/<date>-local.md`: the machine and card, each run's peak,
   estimate and ratio, and AC5 and AC6 as passed or failed; copy the bench's reports beside it,
   and commit with the manifests.

A cloud session cannot run any of it; the PR marks AC5 and AC6 "pending hardware".

## Decisions taken

- `larger_of` holds plain formulas only: one level says what the report found, and nesting would
  be a language nobody has measured a need for.
- Every formula that takes terms may be a larger-of, not `working` alone: one reader, one rule.
- The enlarged photos are made on the machine that measures and never committed: they are tens of
  megabytes, and anyone with the original can make them again.
- The full photo is held out again, as in spec 005's AC7, with the 24 MP one: a figure is only
  checked on a photo it was not fitted on.

## Out of scope

- Depth Pro's fp32 figure on a card, and deriving the margin again: the session on a larger card
  (the review of 2026-11-07, recommendation 6).
- A speed model per precision, and any other growth of the memory model's language: the roadmap.

## Open questions (answered by the owner, 2026-11-12: as recommended)

1. **The form** shown under Design: `larger_of`, a list of parts beside one `source`. A manifest's
   memory model is a format a person's own node also writes. Recommended: as shown.
