# Writing a node

A node is a folder in `nodes/` (or any folder the engine is pointed at) holding `node.json` and
the code it names. The folder is named after the node's id.

## node.json

```json
{
  "id": "depth.example",
  "version": "1",
  "title": "Example depth",
  "category": "depth",
  "summary": "One line on what it does and what it is good at.",
  "inputs": {
    "image": "Image",
    "camera": { "type": "Camera[model=pinhole]", "optional": true, "doc": "Known intrinsics, if any." }
  },
  "outputs": {
    "depth": { "type": "Depth[kind=metric, measure=z]", "trust": "predicted" },
    "camera": { "type": "Camera[model=pinhole, source=estimated]", "trust": "predicted" }
  },
  "params": {
    "resolution": { "type": "int", "default": 1024, "min": 256, "max": 2048, "doc": "Longest side." },
    "tile_batch": { "type": "int", "default": 4, "min": 1, "affects": "speed" }
  },
  "run": { "where": "runtime", "runtime": "example", "entry": "node.py:run" },
  "devices": ["cuda", "cpu"],
  "licence": { "code": "MIT", "weights": "CC BY-NC 4.0", "commercial": false },
  "links": { "repo": "https://github.com/..." }
}
```

- **Ports** use the types in `contracts/port-types.json`. On an output, name each facet with the
  one value the node always produces. Leave a facet out only when it depends on a parameter; the
  node then sets it on the value (`ctx.output(..., facets={...})`) and the engine checks it.
- **trust** is `measured` (observed in the photo), `predicted` (inferred by a model) or `synthetic`
  (made from generated views). An output without one inherits the weakest trust of its inputs.
- **Params** with `"affects": "speed"` do not change the result, so they are not part of the
  cache key. Every other parameter is.
- **run.where** is `engine` for light nodes that ship with the app, and `runtime` for anything
  that imports a model library; `runtime` names the environment it runs in, a folder in
  `runtimes/` ([runtimes.md](runtimes.md)). Until that runtime is installed on this machine, the
  node fails with kind `runtime` and the reason: not installed, out of date, or blocked here.
- **category** is one of: source, depth, segment, object, scene, views, reconstruct, render,
  repair, texture, convert, evaluate, export.

## The code

```python
def run(ctx):
    image = ctx.inputs["image"]  # Value: .path, .facets, .meta, .trust
    ctx.stage("infer", "Estimating depth")
    for i, tile in enumerate(tiles):
        ctx.check_stop()  # raises if Stop was pressed
        ctx.progress(i + 1, len(tiles))
    path = ctx.path("depth.npz")  # a file in this run's own folder
    np.savez(path, depth=depth)
    ctx.output("depth", path)
    return {"tiles": len(tiles)}  # stats, kept with the run record
```

Every output in the manifest must be declared with `ctx.output` before `run` returns. A node
writes only into `ctx.path(...)`. A run is cached by its inputs and output-affecting params, so it
must be deterministic for them (seed any randomness from a `seed` parameter).

## The memory model

A node that loads a model declares what it needs, as data in `node.json`, so the engine can fit it
to the memory this machine has free before it loads, without running anything
([spec 002](../specs/002-fit-to-memory/spec.md)). The engine measures free memory, keeps a margin
(the larger of 1.5 GiB and 10% of the device, or the person's own), and changes only settings the
graph left alone: speed-only ones first, quality last, and it says which it changed.

```json
"memory": {
  "precisions": {
    "fp32": { "cuda_min_capability": null, "cpu": true },
    "fp16": { "cuda_min_capability": "6.0", "cpu": false },
    "bf16": { "cuda_min_capability": "8.0", "cpu": true }
  },
  "weights": { "fp32": 3000, "fp16": 1500, "bf16": 1500, "source": "specs/005-.../reports/....md" },
  "working": {
    "mb": 200,
    "terms": [
      { "coef": 0.00025, "of": ["resolution", "resolution", "bytes"] },
      { "coef": 0.25, "of": ["chunk_size"] }
    ],
    "source": "measured with bench:fit on <card>, at three settings"
  },
  "changes": [
    { "set": { "chunk_size": 2048 }, "costs": "speed" },
    { "set": { "precision": "fp16" }, "costs": "quality" },
    { "set": { "resolution": 512 }, "costs": "quality" }
  ],
  "upgrades": [ { "set": { "chunk_size": 32768 } } ],
  "time": { "cpu": { "seconds": 240, "at": { "resolution": 1024 }, "source": "..." } }
}
```

- **Precisions** are `fp32`, `fp16` and `bf16`. Each says the lowest compute capability it needs
  on a card (`null` for any card) and whether it runs on the processor, and may give `bytes`, the
  bytes per value of its working memory (4 at fp32 and 2 otherwise by default). The engine adds a
  `precision` param with these as its choices, the first being the default; the node reads it as
  `ctx.precision`. Do not declare a `precision` param yourself.
- **Weights** are MB per precision. With `"weights_by": "<choice param>"` they are given per
  checkpoint instead: `{ "small": { "fp32": ... }, "large": { ... } }`.
- **Working memory** is `mb` plus terms, each a coefficient times a product of factors. A factor
  is a numeric or bool param (bool as 0 or 1), `bytes`, `weights`, `<input>.<field>` (a number
  in that input's `meta`; `<input>.pixels` is width × height), or a choice param through a table:
  `"factors": { "pipeline": { "fast": 1, "full": 4 } }`. Coefficients may be negative; a result is
  never below 0. An optional input that is not connected counts as 0. Nothing is evaluated: it is
  numbers multiplied.
- **Other figures,** each a formula of the same form: `weights_on_device` (default: the weights;
  how offloading lowers the card's need, as `weights − 0.9 × weights × offload`), `system_mb`
  (default: the weights, which pass through system memory as they load) and `outside_torch_mb`
  (`mb` only: what the node's own extensions allocate on the card, default 0).
- **Changes** are what the engine may set to save memory, in order, each `costs` `speed` or
  `quality`, speed-only ones first. A speed change, and every **upgrade** (what the engine may set
  when there is room), may set only params marked `"affects": "speed"`. A quality change sets an
  output-affecting param or `precision`. The engine never changes a param the graph sets.
- **Time** is optional, per kind of device (`cpu`, `cuda`, or `cuda:8.6`): the seconds, the
  settings they were measured at, and their source. It is what the warning says when a node has
  to run on a slower device.
- **Sources.** Every figure names where it comes from: a report in this repo naming the card and
  settings, or a URL. A figure nobody has measured is `null`, with a source saying so, never a
  guess. An estimate that cannot be worked out is "unknown", and the node runs at its values.

A bad model is refused with every problem named. A node without one is not fitted, but a runtime
node still runs within a budget and under a cap. Each machine corrects a node's estimate from the
peaks its own runs measure (`<data>/memory/learned.json`, cleared by `nodes.forget`).

## What a node knows about its memory

```python
def run(ctx):
    # Where it runs and at which precision; the fitted values are in ctx.params.
    device, precision = ctx.device, ctx.precision
    budget = ctx.memory_budget_mb  # the fit's budget on this device; None if not fitted
    left = ctx.memory_free_mb()  # what is left under the cap (on the processor, of the budget)
    attempt = ctx.attempt  # 1, or 2 on the engine's one retry after running out of memory
    image = ctx.fallbacks(
        "decode",
        [
            ("whole", lambda: decode(latents)),
            ("tiled", lambda: decode_tiled(latents, tile=256)),
        ],
    )
```

On a card, torch's allocator is capped before the node's code runs, at the smaller of the budget
and the free memory measured once CUDA has started (less 256 MB), less `outside_torch_mb`. An
estimate that is too low then becomes torch's `OutOfMemoryError`, not a slow spill into system
memory.

`ctx.fallbacks(step, ways)` retries one step a cheaper way in the same process. Only torch's
`OutOfMemoryError` moves it to the next way; the failed way is released first. Every way must give
the same result within rounding: a way that costs quality belongs in `changes`, where the fit can
weigh it. Each output records the way each step finished with in `meta.made_with`.

When a runtime node still runs out of memory, the engine retries it once, in a new process, with
a strictly smaller fit. A second time, it fails with kind `oom`.

## Measuring a node: bench:fit

```
npm run bench:fit -- <node> --set resolution=512 --set resolution=1024 --set resolution=2048
```

runs the node once per `--set` group (from an empty cache, with nothing learned) and writes a
report of each run's estimate, measured peak, ratio, seconds and the free memory before. Measure a
model's memory model at several settings this way, and name the report in its `source`. With no
node, it runs spec 002's hardware check (AC8) on this machine's card.

## Windows: shared GPU memory

Since driver 536.40, a CUDA allocation that runs past the card can spill into shared system memory
instead of failing, and the run slows to a crawl. The engine's cap keeps a node's own allocations
inside the card, but not the CUDA context or other programs. To make the driver fail instead of
spilling, open the NVIDIA Control Panel, Manage 3D settings, and set "CUDA - Sysmem Fallback
Policy" to "Prefer No Sysmem Fallback", for everything or for the runtime's `python.exe`. The app
does not change this setting for you: it has no documented interface.

## File conventions per type

| Type | File | Contents |
| --- | --- | --- |
| Image | `.png` | sRGB |
| Depth | `.npz` | `depth`: float32 H x W; optional `valid` |
| PointMap | `.npz` | `points`: float32 H x W x 3; `valid` |
| Camera | `.json` | `fx`, `fy`, `cx`, `cy`, `width`, `height` (pixels); optional `pose` 4x4 world_from_camera |
| Normals, Confidence | `.npz` | `normals` / `confidence` |
| Mask | `.png` | 0/255, or 8-bit soft |
| Mesh | `.glb` | |
| GaussianSplat | `.ply` | Inria layout, raw (log scale, logit opacity, wxyz rotation) |
| ViewSet | folder | `views.json` plus images |

## Testing a node

Put a test beside the engine tests that runs the node through the scheduler with tiny inputs. For
model nodes, keep a CPU path or a small checkpoint so CI can run it without a GPU, and measure its
memory model on a card with `bench:fit`.
