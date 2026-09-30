# 002: Research: how local AI apps fit a model to a machine's memory

Checked on 2026-09-30 against each project's current default branch, by reading the code. The
question: how do the best local AI apps decide how to run a model within a machine's memory,
without asking the person to run tests? Figures are quoted from the source named beside them.
Anything not verified says so.

## Summary

None of them asks for a test run. They all decide when a model loads, from the free memory
measured just before it, less a fixed margin, and an estimate of what the model needs. The
strongest share five habits:

1. **Estimate before loading.** Weights come from parameter bytes at the chosen precision.
   Working memory comes from a formula per model, fitted by the developers from their own
   measurements. llama.cpp can do a dry run instead, because its graph is laid out in advance;
   PyTorch allocates as it goes, so torch apps use formulas.
2. **Trade speed before quality.** The main tool is loading part of the weights onto the GPU and
   streaming the rest from system memory. The result is the same, only slower.
3. **Change only what the person left alone.** Settings someone chose are never changed.
4. **Answer out-of-memory narrowly.** Retry the failing step in tiles, or retry the load once.
   No app has a general restart-on-the-next-option loop.
5. **Keep models loaded,** and evict the least recently used when another needs the room.

## ComfyUI

Source: Comfy-Org/ComfyUI @ 8cfe5e1 (2026-09-30, v0.38.0), `comfy/model_management.py` unless
noted.
<https://github.com/Comfy-Org/ComfyUI/blob/8cfe5e1ecb97512dea8deaac15e1228d7e6feeb1/comfy/model_management.py>

- **Memory state.** Total memory comes from `torch.cuda.mem_get_info`. Free memory is CUDA's free
  plus torch's reserved-but-inactive memory. The state defaults to `NORMAL_VRAM`; only the flags
  `--lowvram`, `--novram` and `--highvram` change it. There is no threshold on card size any
  more: the fit happens at each load.
- **Margin.** `EXTRA_RESERVED_VRAM` is 400 MB, and 600 MB on Windows, with the comment
  "Windows is higher because of the shared vram issue". Cards of 16 GB or more add 100 MB.
  `--reserve-vram` overrides it. `minimum_inference_memory()` is 0.8 GB plus that margin, and
  `maximum_vram_for_weights` is `total*0.88 - minimum_inference_memory()`.
- **Precision.**
    - Compute major 8 or higher allows fp16 and bf16; below 6, no fp16.
    - GeForce 10-series cards are found by name (`nvidia_10_series`, "1070" among them). They get
      fp16 on Windows ("FP16 is confirmed working on a 1080 (GP104)") and fp32 on Linux ("weird
      linux behavior where fp32 is faster").
    - This repo's rules forbid deciding on a name; compute capability 6.1 identifies the same
      cards.
- **Estimate.**
    - Weights are the sum of the parameters' bytes.
    - Working memory is `BaseModel.memory_required` (`comfy/model_base.py`): `area*0.15*memory_usage_factor`
      MB without an efficient attention kernel, and `area*dtype_size*0.01*memory_usage_factor` MB
      with one. `memory_usage_factor` is set per model.
    - Image decoders carry fitted formulas (`comfy/sd.py`).
- **Loading.**
    - `free_memory` frees `required*1.1 + extra`, unloading by use.
    - A model that does not fit is loaded in part: modules go to the GPU up to the budget, and
      the rest are cast from system memory layer by layer (`comfy/model_patcher.py`).
    - The current default ("DynamicVRAM", from the comfy-aimdo allocator) faults weights in on
      demand, and needs CUDA 12.8+, PyTorch 2.8+ and Windows 11+.
    - Not verified: whether aimdo works on compute 6.x.
- **Out of memory.**
    - `is_oom()` catches `OutOfMemoryError`, and accelerator errors that say "out of memory".
    - Image decoding and encoding retry in tiles ("Ran out of memory when regular VAE decoding,
      retrying with tiled VAE decoding").
    - Any other out-of-memory fails the node, after which every model is unloaded.
- **Allocator.** ComfyUI does not call `set_per_process_memory_fraction` anywhere.

## InvokeAI

Source: invoke-ai/InvokeAI @ e927a2e (2026-09-27, v6.14.1).
<https://github.com/invoke-ai/InvokeAI/tree/e927a2ebf59d4e3014fc0f3eec53da13ad8cd38c>

- **Settings.**
    - Partial loading (`enable_partial_loading`) is on by default; it streams layers between
      system memory and the GPU.
    - `device_working_mem_gb` defaults to 3 GB.
    - The docs tell Windows users to set NVIDIA's "CUDA - Sysmem Fallback Policy" to "Prefer No
      Sysmem Fallback" (`docs/src/content/docs/configuration/low-vram-mode.mdx`).
- **Budget** (`model_cache.py`, `_get_vram_available`): free, plus allocated, less working memory,
  less what the cache holds. Working memory is the larger of the step's own estimate and
  `device_working_mem_gb`.
- **Working-memory formulas** (`invokeai/backend/util/vae_working_memory.py`): for example
  `H*W*element_size*2200` to decode, ×1.25 when tiled, plus 250 MB at fp32 ("determined
  experimentally").
- **Out of memory.** A load that runs out is logged and re-raised. There is no general retry;
  one decoder retries in tiles.

## Stable Diffusion WebUI Forge

Source: lllyasviel/stable-diffusion-webui-forge @ dfdcbab (2025-06-26, the last commit on that
branch).

- **Budget.** A "GPU Weights (MB)" budget sets the room left for inference, 1024 MB by default. A
  model that does not fit is swapped in part.
- **Out of memory.**
    - Attention retries with more slices.
    - "Never OOM" is an opt-in extension that forces tiling.

## Ollama

Source: ollama/ollama @ 1abe35e (2026-09-29).

- **Who decides.** Since [PR #16031](https://github.com/ollama/ollama/pull/16031) (merged
  2026-05-29), Ollama runs llama.cpp's `llama-server`, and llama.cpp's own fit decides layer
  placement. Ollama passes no layer count unless the person set one.
- **Estimate** (`llm.PredictServerVRAM()`, "intentionally conservative"): file size plus the
  context's cache. It is used only to decide what to evict (`server/sched.go`: evict when the
  prediction exceeds 80% of free memory).
- **Measurement.** The real buffer sizes are read back from llama-server's log after loading.
- **Reserves.** `MinimumMemory()` is 457 MiB. `OLLAMA_GPU_OVERHEAD` defaults to 0.
- **Out of memory.** It retries once:
    - with a smaller automatic context, if the context was automatic;
    - otherwise after evicting every other model.
- **Residency.**
    - `OLLAMA_KEEP_ALIVE` defaults to 5 minutes.
    - Up to 3 models per GPU stay loaded.
    - Idle runners are evicted first.

## llama.cpp

Source: ggml-org/llama.cpp @ 8df332d (2026-09-30), `common/fit.cpp`;
[PR #16653](https://github.com/ggml-org/llama.cpp/pull/16653), merged 2025-12-15.

- **What it does.** It "automatically set[s] parameters not set by the user in such a way that
  maximizes GPU utilization". `--fit` is on by default. `--fit-target` is a 1024 MiB margin per
  device.
- **How it measures.** A dry run: the model and its compute graph are built with `no_alloc`,
  which allocates nothing, and each device's need is read back. It takes about 0.1 s.
- **What it changes.** Only parameters the person left at their defaults: first the context
  length, then which layers go on the GPU.
- **Limitation.** It "assumes system memory is unlimited".

## LM Studio

Source: lmstudio-ai/docs @ 9b8bc20 (2026-09-08). The app is closed source; lmstudio.ai could not
be reached from the research session.

- **Estimate.** `lms load --estimate-only` prints the estimated GPU and total memory, taking
  context length, flash attention and vision into account.
- **Automatic offload.** "If not specified, LM Studio will automatically determine optimal GPU
  usage."
- **Guardrails.** Guardrails warn before loading and can be overridden. Unverified (from search
  snippets): the modes Strict, Balanced, Relaxed and Off.
- **Residency.** Models loaded on first request are unloaded after 60 minutes idle.

## Hugging Face Accelerate and diffusers

Sources: huggingface/accelerate and huggingface/diffusers, main on 2026-09-30.

- **Accelerate.**
    - Placement is arithmetic from parameter sizes, with no working-memory estimate.
    - With one GPU and no limit set, it uses 90% of free memory ("90% is a good compromise").
    - Its docs warn that the CUDA context takes "about 1-2GB"
      (`docs/source/concept_guides/big_model_inference.md`).
- **diffusers.**
    - CPU offload, group offload and image-decoder tiling are all the person's choice
      (`docs/source/en/optimization/memory.md`).
    - The one automatic mode, `ComponentsManager.enable_auto_cpu_offload(memory_reserve_margin="3GB")`,
      compares each component's footprint with free memory less the margin. It does not estimate
      working memory.

## The models this project plans to use

None of the model repositories reads the machine; every low-memory option is a flag the person
sets.

| Model | Published memory figure | Settings that trade speed | Settings that trade quality | Source |
| --- | --- | --- | --- | --- |
| Hunyuan3D-2, 2mini | "6 GB VRAM for shape generation and 16 GB for shape and texture" | `num_chunks`, CPU offload (`--low_vram_mode`) | `octree_resolution`; mini, fast and turbo checkpoints | Tencent-Hunyuan/Hunyuan3D-2 @ f8db630 |
| Hunyuan3D-2.1 | "10 GB VRAM for shape generation, 21GB for texture generation and 29GB for shape and texture" | none in the app (the offload line is commented out) | paint views and resolution | Tencent-Hunyuan/Hunyuan3D-2.1 @ 82920d6 |
| TripoSR | "about 6GB VRAM for a single image input" | `--chunk-size` ("Smaller chunk size reduces VRAM usage but increases computation time") | `--mc-resolution` | VAST-AI-Research/TripoSR @ 107cefd |
| TRELLIS | "at least 16GB" | none | none | microsoft/TRELLIS @ 442aa1e |
| TRELLIS.2 | "at least 24GB" | `low_vram` (on by default) | `pipeline_type` (512 to 1536) | microsoft/TRELLIS.2 @ 75fbf01 |
| SAM 2.1 | none (parameters: tiny 38.9M, small 46M, base+ 80.8M, large 224.4M) | none | checkpoint size | facebookresearch/sam2 @ 2b90b9f |
| Depth Pro | none | none | precision only; input fixed at 1536 x 1536 | apple/ml-depth-pro @ 9e65e4d |
| MoGe-2 | none | none | `num_tokens` (1200 to 3600), checkpoint size, fp16 | microsoft/MoGe @ 74fbce0 |

So a published figure per setting would mostly read "unknown". Real figures have to be measured,
and each measurement names the card and settings it was made on.

## Wrappers

| Wrapper | Does it read the machine? | Source |
| --- | --- | --- |
| kijai/ComfyUI-Hunyuan3DWrapper | No: offload on by default, other settings are node inputs | @ 2609efa |
| ComfyUI-3D-Pack | No: offload profiles are a toggle | @ 9e8096e |
| Pinokio's Hunyuan3D-2 low-VRAM launcher | No: the person picks a profile from a menu ("24GB RAM + 6GB VRAM" and so on) | @ 9c41502 |
| Stability Matrix | Yes, but only to pre-fill a launch flag: under 4 GiB Low, under 8 GiB Medium, else High | @ fcfab8b |

## Windows: spilling into shared memory

- **When it started.** Since driver 536.40, a CUDA allocation that exhausts the card can spill
  into shared system memory instead of failing. Driver 546.01 added a switch to turn this off,
  "CUDA - Sysmem Fallback Policy", which can be set per program in the NVIDIA Control Panel.
  Not verified at the source: NVIDIA's article could not be reached; this comes from a quote in
  search results and from oobabooga/text-generation-webui discussion #4484.
- **Setting it from code.** The public NVAPI header (NVIDIA/nvapi @ 70d337d) has no ID for the
  setting. NVIDIA Profile Inspector lists it as `0x10ECECC9`. Setting it from code would rely on
  an undocumented ID; not tested.
- **What PyTorch's cap covers.**
    - `torch.cuda.set_per_process_memory_fraction` caps only the caching allocator, at the
      fraction times the card's total memory (pytorch @ 07070bb, `CUDACachingAllocator.cpp`).
    - It does not cover the CUDA context, or memory that extensions allocate themselves.
    - `PYTORCH_CUDA_ALLOC_CONF=per_process_memory_fraction:…` is documented on main. Not checked:
      which stable release first has it.

## What follows for this project

- Estimate before loading, and fit to the free memory measured then. Never make a person run a
  test.
- Each node carries its own memory model, fitted from measurements at several settings, each
  naming the card it was measured on. Estimates made on one card are corrected on each machine
  by the peaks measured there, which is how the app learns about cards nobody here has.
- Change only what the person left alone: speed before quality, and a label when quality was
  given up.
- One narrow retry on out-of-memory.
- Keep models loaded between runs. That needs a long-lived worker per runtime, a change to the
  architecture's one child process per run, and is decided with the owner.
