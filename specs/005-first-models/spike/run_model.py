"""Spec 005's spike: one model at one setting, in this process, run by `spike.py`.

It runs in the model's own throwaway environment (Python 3.14, torch), so it imports nothing of
the engine. It refuses every network connection before any model code is imported, as a runtime
child does, and prints one JSON line: seconds to import, load and infer (twice), the card's peak
memory, the process's peak memory, and what came out. A failure is a result too, with its kind.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def refuse_network() -> None:
    def refused(*_args: Any, **_kwargs: Any) -> Any:
        raise ConnectionRefusedError("the spike allows no network during a run")

    socket.socket.connect = refused  # type: ignore[method-assign]
    socket.socket.connect_ex = refused  # type: ignore[method-assign]
    socket.create_connection = refused  # type: ignore[assignment]


def peak_rss_mb() -> float | None:
    """The process's peak resident memory, in MB."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess()  # type: ignore[attr-defined]
        if not ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb):  # type: ignore[attr-defined]
            return None
        return round(counters.PeakWorkingSetSize / 2**20, 1)
    import resource

    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)  # KB on Linux


def photo_array(spec: str) -> Any:
    """The photo as an RGB uint8 array: a file, or `synthetic:WxH`, a gradient for a check."""
    import numpy as np
    from PIL import Image, ImageOps

    if spec.startswith("synthetic:"):
        width, height = (int(n) for n in spec.split(":", 1)[1].split("x"))
        y, x = np.mgrid[0:height, 0:width]
        return np.stack([x * 255 // width, y * 255 // height, (x + y) * 255 // (width + height)], -1).astype(
            np.uint8
        )
    with Image.open(spec) as image:
        return np.asarray(ImageOps.exif_transpose(image).convert("RGB"))


def depth_pro(args: argparse.Namespace, torch: Any, rgb: Any, dtype: Any, device: Any) -> tuple[Any, Any]:
    import dataclasses

    import depth_pro as upstream
    from depth_pro.depth_pro import DEFAULT_MONODEPTH_CONFIG_DICT

    # No weights: a check with random ones (checkpoint_uri None loads nothing).
    config = dataclasses.replace(DEFAULT_MONODEPTH_CONFIG_DICT, checkpoint_uri=args.weights)
    model, transform = upstream.create_model_and_transforms(config, device=device, precision=dtype)
    model.eval()

    def infer() -> dict[str, Any]:
        with torch.no_grad():
            out = model.infer(transform(rgb).to(device), f_px=None)
        return {"depth": out["depth"], "focal_px": out["focallength_px"]}

    return model, infer


def moge(args: argparse.Namespace, torch: Any, rgb: Any, dtype: Any, device: Any) -> tuple[Any, Any]:
    sys.path.insert(0, str(HERE / "moge" / "stand-ins"))  # flex_gemm, for v3.py's imports
    version3 = args.model == "moge3"
    if version3:
        from moge.model.v3 import MoGeModel
    else:
        from moge.model.v2 import MoGeModel

    if args.weights:
        # MoGe-3 without its refiner: refine_steps=0, and the refiner's weights are left unloaded
        # (from_pretrained loads with strict=False).
        model = MoGeModel.from_pretrained(args.weights, model_kwargs={"refiner": None} if version3 else None)
    else:
        config = json.loads((HERE / "moge" / "configs" / f"{args.model}.json").read_text(encoding="utf-8"))
        config.pop("refiner", None)
        model = MoGeModel(**config)
    model = model.to(device).eval()
    image = torch.tensor(rgb, dtype=torch.float32, device=device).permute(2, 0, 1) / 255
    options: dict[str, Any] = {"resolution_level": args.resolution_level, "use_fp16": dtype == torch.float16}
    if version3:
        options["refine_steps"] = 0

    def infer() -> dict[str, Any]:
        with torch.no_grad():
            return model.infer(image, **options)

    return model, infer


def summary(torch: Any, out: dict[str, Any]) -> dict[str, Any]:
    """Shapes, and the depth's spread where it is finite: enough to see the output is sane."""
    found: dict[str, Any] = {}
    for name, value in out.items():
        if isinstance(value, torch.Tensor):
            found[name] = {"shape": list(value.shape), "dtype": str(value.dtype).removeprefix("torch.")}
        else:
            found[name] = value
    depth = out.get("depth")
    if isinstance(depth, torch.Tensor):
        values = depth.float().flatten()
        finite = values[torch.isfinite(values)]
        found["depth"]["finite_share"] = round(finite.numel() / max(values.numel(), 1), 4)
        if finite.numel():
            found["depth"]["min_median_max_m"] = [
                round(float(q), 3) for q in (finite.min(), finite.median(), finite.max())
            ]
    focal = out.get("focal_px")
    if isinstance(focal, torch.Tensor):
        found["focal_px"] = round(float(focal), 1)
    intrinsics = out.get("intrinsics")
    if isinstance(intrinsics, torch.Tensor):
        found["intrinsics"] = [[round(float(v), 4) for v in row] for row in intrinsics.reshape(3, 3)]
    return found


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["depth_pro", "moge2", "moge3"], required=True)
    parser.add_argument("--weights", default=None, help="the checkpoint; none: random weights, a check")
    parser.add_argument("--photo", required=True, help="a file, or synthetic:WxH")
    parser.add_argument("--device", choices=["cuda", "cpu"], required=True)
    parser.add_argument("--precision", choices=["fp32", "fp16"], default="fp32")
    parser.add_argument("--resolution-level", type=int, default=9, help="MoGe only: 0 to 9")
    parser.add_argument("--out", type=Path, default=None, help="where to keep the outputs (.npz)")
    args = parser.parse_args()
    refuse_network()
    result: dict[str, Any] = {
        key: value for key, value in vars(args).items() if key not in ("weights", "out")
    }
    result["weights"] = "random" if args.weights is None else "pinned"
    if args.model == "depth_pro":
        del result["resolution_level"]  # MoGe's alone
    stage = "import"
    try:
        started = time.perf_counter()
        import numpy as np
        import torch

        result["torch"] = torch.__version__
        result["numpy"] = np.__version__
        device = torch.device(args.device)
        if args.device == "cuda":
            torch.cuda.init()
            result["card"] = torch.cuda.get_device_name(0)
            result["capability"] = ".".join(map(str, torch.cuda.get_device_capability(0)))
        dtype = torch.float16 if args.precision == "fp16" else torch.float32
        rgb = photo_array(args.photo)
        result["photo_px"] = [int(rgb.shape[1]), int(rgb.shape[0])]
        build = depth_pro if args.model == "depth_pro" else moge
        result["import_s"] = round(time.perf_counter() - started, 2)

        stage = "load"
        started = time.perf_counter()
        model, infer = build(args, torch, rgb, dtype, device)
        if args.device == "cuda":
            torch.cuda.synchronize()
            result["weights_on_card_mb"] = round(torch.cuda.memory_allocated() / 2**20, 1)
            torch.cuda.reset_peak_memory_stats()
        result["load_s"] = round(time.perf_counter() - started, 2)
        result["parameters_m"] = round(sum(p.numel() for p in model.parameters()) / 1e6, 1)

        stage = "infer"
        seconds = []
        out: dict[str, Any] = {}
        for _ in range(2):  # the first pays for kernel selection; the second is the steady time
            started = time.perf_counter()
            out = infer()
            if args.device == "cuda":
                torch.cuda.synchronize()
            seconds.append(round(time.perf_counter() - started, 2))
        result["infer_s"] = seconds
        if args.device == "cuda":
            result["peak_allocated_mb"] = round(torch.cuda.max_memory_allocated() / 2**20, 1)
            result["peak_reserved_mb"] = round(torch.cuda.max_memory_reserved() / 2**20, 1)
        result["outputs"] = summary(torch, out)
        if args.out is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            arrays = {k: v.float().cpu().numpy() for k, v in out.items() if isinstance(v, torch.Tensor)}
            np.savez_compressed(args.out, **arrays)  # type: ignore[arg-type]
        result["outcome"] = "passed"
    except Exception as exc:
        oom = type(exc).__name__ == "OutOfMemoryError"
        result.update(
            outcome="oom" if oom else "failed",
            stage=stage,
            error=f"{type(exc).__name__}: {exc}"[:2000],
            trace=traceback.format_exc()[-3000:],
        )
    result["peak_rss_mb"] = peak_rss_mb()
    print(json.dumps(result), flush=True)
    return 0 if result["outcome"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
