# Spec 005's spike: do the first models run here?

A spike, not a spec (AGENTS.md, rule 3): throwaway code that answers feasibility before spec 005 is
approved (the owner, 2026-11-04, from the [review of 2026-11-04](../../../docs/reviews/2026-11-04.md)).
It changes no engine, app or node code. Its product is a report, under `../reports/`, that 005 is
then cut down to.

## What it answers

1. Do Depth Pro (Apple, `9e65e4d`) and MoGe (Microsoft, `74fbce0`) install on Windows with
   Python 3.14 and torch 2.14.0's `cu126` build, from their GitHub code, with no FlexGEMM, gradio
   or upstream torch index?
2. Does Depth Pro run on numpy 2, against upstream's `numpy<2`?
3. Does MoGe-3 load and run without its refiner, with `flex_gemm` replaced by a stand-in
   (`moge/stand-ins/`) and `refine_steps=0`? Does MoGe-2 run from the same package?
4. Do the weights load with `weights_only` forced on, from the model store, with every network
   connection refused?
5. On a GTX 1070 and on the processor: each model's seconds to load and to infer (first and
   steady), its peak card memory and peak process memory, at the settings `spike.py` lists, and
   whether the depths look sane (finite share, metres).

## Run it (a local session)

From the repo's root on the owner's PC, with the checks green. `<work>` is a new folder of the
spike's own, outside the repo: for example `%LOCALAPPDATA%\OneframeLab-spike` in cmd,
`$env:LOCALAPPDATA\OneframeLab-spike` in PowerShell, `"$LOCALAPPDATA/OneframeLab-spike"` in Git Bash.

**Steps 2 and 3 install environments, so run them from a terminal opened from the Start menu,
outside Claude desktop's package:** uv's installs from inside it failed on this PC on 2026-10-25
(os error 32; the handoff's "Open" list). A session inside the package gives the owner the commands
and reads the JSON they write.

1. The weights into the model store (about 1.9, 1.3 and 1.5 GB; skip any already there):

   ```
   npm run models:download -- --nodes specs/003-model-store/live live.depth_pro
   npm run models:download -- --nodes specs/003-model-store/live live.moge2
   npm run models:download -- --nodes specs/003-model-store/live live.moge3 --checkpoint vitl
   ```

2. A check of the environments and the code, without the weights (the processor, random weights,
   a synthetic photo; builds both families' `cpu` environments, about 1 GB):

   ```
   uv run --project engine --frozen python specs/005-first-models/spike/spike.py --work <work> --self-test
   ```

3. The spike itself, on a real photo of the owner's choosing (a room or a street, about 12 MP or
   less), with the Oneframe Lab app closed and nothing heavy on the card. It builds the `cu126`
   environments too (about 5 GB), then runs 10 settings on the card and 3 on the processor, each in
   a fresh process; the processor's take minutes each:

   ```
   uv run --project engine --frozen python specs/005-first-models/spike/spike.py --work <work> --photo <photo>
   ```

   The work folder is the spike's own: the environments, the libraries' caches and the outputs (`outputs/*.npz`) go there, and
   nothing else outside the data root, whose uv cache it shares. Delete it once the report is in.

## What passing looks like

The spike answers its questions whichever way they go; a model that fails is a finding, not a
failed spike. It is done when its JSON (`<work>/<date>-spike.json`) holds a row for every setting,
each `passed`, `oom` or `failed` with its error. Copy the JSON to
`specs/005-first-models/reports/<date>-spike.json` and write `<date>-spike.md` beside it: the
machine, the table `spike.py` prints, the answer to each question above, and what 005 should
change. Commit both on the branch that holds this folder and push; the report goes to the owner.

## Files

- `depth_pro/`, `moge/`: one uv project per family, locked (`uv.lock`); `--extra cu126` or `cpu`.
- `moge/stand-ins/flex_gemm/`: the names MoGe-3's code imports from FlexGEMM, each raising on use.
- `moge/configs/`: MoGe-2's and MoGe-3's model shapes, from MoGe's `configs/train/` (MIT), used only
  by `--self-test` to build a model with random weights.
- `run_model.py`: one model at one setting, in the family's environment; one JSON line.
- `spike.py`: the whole spike, run by the engine's Python.
