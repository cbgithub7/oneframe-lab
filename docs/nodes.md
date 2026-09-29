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
  that imports a model library; `runtime` names the environment it runs in.
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
model nodes, keep a CPU path or a small checkpoint so CI can run it without a GPU.
