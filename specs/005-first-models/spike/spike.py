"""Spec 005's spike: do Depth Pro, MoGe-2 and MoGe-3 install and run here, and in what time and memory?

Throwaway: not a runtime, not a node, no engine code changed. For each family it builds an
environment from the locks beside this file into `--work`, then runs each model at each setting
in a fresh process (`run_model.py`), and writes one JSON of everything to `--work`. The weights are
read from the model store, by the sha256s spec 003's live manifests pin; fetch them first with
`npm run models:download`. uv's cache and Pythons are the data root's, so torch is not fetched
twice.

    uv run --project engine --frozen python specs/005-first-models/spike/spike.py \
        --work <folder> --photo <file>

`--self-test` runs every model once on the processor with random weights and a synthetic photo:
a check of the environments and the code, without the weights.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from oneframe.layout import Layout, default_root

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LIVE = REPO / "specs" / "003-model-store" / "live"
# model -> (family, the live node that pins its weights, that node's checkpoint or None)
MODELS = {
    "depth_pro": ("depth_pro", "live.depth_pro", None),
    "moge2": ("moge", "live.moge2", None),
    "moge3": ("moge", "live.moge3", "vitl"),
}
# The settings each model is measured at, on a card. Depth Pro's network runs at a fixed 1536 px.
ON_CARD = {
    "depth_pro": [{"precision": "fp32"}, {"precision": "fp16"}],
    "moge2": [{"resolution_level": 0}, {"resolution_level": 5}, {"resolution_level": 9},
              {"resolution_level": 9, "precision": "fp16"}],
    "moge3": [{"resolution_level": 0}, {"resolution_level": 5}, {"resolution_level": 9},
              {"resolution_level": 9, "precision": "fp16"}],
}  # fmt: skip
# On the processor, one setting each: the time a slower device would be announced with.
ON_CPU = {
    "depth_pro": [{"precision": "fp32"}],
    "moge2": [{"resolution_level": 0}],
    "moge3": [{"resolution_level": 0}],
}
RUN_TIMEOUT_S = 3600


def weights_file(layout: Layout, node: str, checkpoint: str | None) -> Path:
    manifest = json.loads((LIVE / node / "node.json").read_text(encoding="utf-8"))
    pinned = [f for f in manifest["files"] if checkpoint is None or checkpoint in f.get("checkpoints", [])]
    sha = pinned[0]["sha256"]
    return layout.blobs / sha[:2] / sha


def env_python(env: Path) -> Path:
    return env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def folder_mb(path: Path) -> float:
    return round(sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 2**20, 1)


def scrub(value: Any) -> Any:
    """No user name in what the report keeps: the home folder, in any case and with either slash,
    in every string of a row."""
    text = str(Path.home())
    parts = [re.escape(part) for part in re.split(r"[\\/]", text) if part]
    lead = r"[\\/]" if text.startswith(("/", "\\")) else ""  # a POSIX home begins with its slash
    home = re.compile(lead + r"[\\/]".join(parts) + r"(?![^\\/\s\"'])", re.IGNORECASE)
    if isinstance(value, str):
        return home.sub("%USERPROFILE%", value)
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def child_env(work: Path, layout: Layout) -> dict[str, str]:
    """What every uv and model process here gets: uv's folders under the data root, the libraries'
    caches and temporary files under the work folder, hubs offline, weights_only forced."""
    caches = work / "caches"
    tmp = work / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for name in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME"):
        env.pop(name, None)
    env.update(
        UV_CACHE_DIR=str(layout.uv_cache),
        UV_PYTHON_INSTALL_DIR=str(layout.uv_python),
        UV_PYTHON_INSTALL_BIN="0",
        UV_PYTHON_INSTALL_REGISTRY="0",
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        HF_HOME=str(caches / "huggingface"),
        TORCH_HOME=str(caches / "torch"),
        TRITON_HOME=str(caches / "triton"),
        XDG_CACHE_HOME=str(caches / "xdg"),
        MPLCONFIGDIR=str(caches / "matplotlib"),
        CUDA_CACHE_PATH=str(caches / "cuda"),
        TORCH_FORCE_WEIGHTS_ONLY_LOAD="1",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
        TMP=str(tmp),
        TEMP=str(tmp),
        TMPDIR=str(tmp),
    )
    return env


def sync(family: str, extra: str, work: Path, env: dict[str, str]) -> dict[str, Any]:
    target = work / "envs" / f"{family}-{extra}"
    command = [
        "uv",
        "sync",
        "--project",
        str(HERE / family),
        "--frozen",
        "--extra",
        extra,
        "--no-install-project",
    ]
    print(f"== {family} ({extra}): building its environment", flush=True)
    started = time.monotonic()
    done = subprocess.run(command, env={**env, "UV_PROJECT_ENVIRONMENT": str(target)}, check=False)
    row = {"family": family, "extra": extra, "seconds": round(time.monotonic() - started, 1)}
    row["outcome"] = "passed" if done.returncode == 0 else f"failed (exit {done.returncode})"
    if done.returncode == 0:
        row["disk_mb"] = folder_mb(target)
    return {**row, "env": target}


def run(model: str, python: Path, setting: dict[str, Any], device: str, args: argparse.Namespace,
        weights: Path | None, env: dict[str, str], index: int) -> dict[str, Any]:  # fmt: skip
    command = [
        str(python),
        str(HERE / "run_model.py"),
        "--model",
        model,
        "--device",
        device,
        "--photo",
        args.photo,
    ]
    command += ["--precision", setting.get("precision", "fp32")]
    if "resolution_level" in setting:
        command += ["--resolution-level", str(setting["resolution_level"])]
    if weights is not None:
        command += ["--weights", str(weights)]
    command += ["--out", str(args.work / "outputs" / f"{index:02d}-{model}-{device}.npz")]
    print(f"-- {model} on {device}, {setting}", flush=True)
    started = time.monotonic()
    try:
        done = subprocess.run(command, env=env, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=RUN_TIMEOUT_S, check=False)  # fmt: skip
    except subprocess.TimeoutExpired:
        return {"model": model, "device": device, **setting, "outcome": f"timed out after {RUN_TIMEOUT_S} s"}
    row: dict[str, Any] | None = None
    for line in reversed(done.stdout.splitlines()):  # the result is the last JSON object printed
        try:
            found = json.loads(line)
        except ValueError:
            continue
        if isinstance(found, dict) and "outcome" in found:
            row = found
            break
    if row is None:
        row = {"model": model, "device": device, **setting, "outcome": f"died (exit {done.returncode})"}
        row["stderr"] = done.stderr[-3000:]
    row["process_s"] = round(time.monotonic() - started, 1)
    row = scrub(row)
    print(
        f"   {row.get('outcome')}: infer {row.get('infer_s')} s, peak {row.get('peak_reserved_mb')} MB",
        flush=True,
    )
    return row


def machine() -> dict[str, Any]:
    found: dict[str, Any] = {"os": platform.platform(), "python_engine": platform.python_version()}
    smi = shutil.which("nvidia-smi")
    if smi:
        query = "name,compute_cap,memory.total,memory.used,driver_version"
        done = subprocess.run([smi, f"--query-gpu={query}", "--format=csv,noheader"],
                              capture_output=True, text=True, check=False)  # fmt: skip
        found["gpu"] = done.stdout.strip()
    uv = subprocess.run(["uv", "--version"], capture_output=True, text=True, check=False)
    found["uv"] = uv.stdout.strip()
    return found


def table(rows: list[dict[str, Any]]) -> str:
    head = "| model | device | setting | outcome | load s | infer s | peak reserved MB | peak RSS MB |"
    lines = [head, "|" + " --- |" * 8]
    for r in rows:
        setting = ", ".join(f"{k}={r[k]}" for k in ("precision", "resolution_level") if k in r)
        lines.append(
            f"| {r.get('model')} | {r.get('device')} | {setting} | {r.get('outcome')} | "
            f"{r.get('load_s', '')} | "
            f"{r.get('infer_s', '')} | {r.get('peak_reserved_mb', '')} | {r.get('peak_rss_mb', '')} |"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(prog="spike", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--work", type=Path, required=True, help="a folder of the spike's own, outside the repo"
    )
    parser.add_argument("--photo", default=None, help="the photo to run on (a real one gives sane depths)")
    parser.add_argument("--devices", default=None, help="cuda,cpu (default: both if a card is found)")
    parser.add_argument("--models", default=",".join(MODELS), help="which of depth_pro,moge2,moge3")
    parser.add_argument(
        "--self-test", action="store_true", help="the processor, random weights, a synthetic photo"
    )
    args = parser.parse_args()
    args.work = args.work.resolve()
    if REPO in (args.work, *args.work.parents):
        parser.error("--work must be outside the repo")
    layout = Layout(default_root())
    models = [m for m in args.models.split(",") if m]
    if args.self_test:
        args.photo, devices = "synthetic:640x480", ["cpu"]
    else:
        if not args.photo or not Path(args.photo).is_file():
            parser.error("--photo must name a photo")
        devices = (
            args.devices.split(",")
            if args.devices
            else (["cuda", "cpu"] if shutil.which("nvidia-smi") else ["cpu"])
        )
        missing = []
        for model in models:
            path = weights_file(layout, MODELS[model][1], MODELS[model][2])
            if not path.is_file():
                checkpoint = f" --checkpoint {MODELS[model][2]}" if MODELS[model][2] else ""
                missing.append(
                    "npm run models:download -- --nodes specs/003-model-store/live "
                    f"{MODELS[model][1]}{checkpoint}"
                )
        if missing:
            print("The weights are not in the model store yet. Fetch them first:\n  " + "\n  ".join(missing))
            return 2
    args.work.mkdir(parents=True, exist_ok=True)
    env = child_env(args.work, layout)
    record: dict[str, Any] = {
        "spike": "005-first-models",
        "date": datetime.date.today().isoformat(),
        "self_test": args.self_test,
        "machine": machine(),
        "photo": "synthetic" if args.self_test else Path(args.photo).name,
        "environments": [],
        "runs": [],
    }
    extras = {"cuda": "cu126", "cpu": "cpu"}
    envs: dict[tuple[str, str], Path | None] = {}
    for device in devices:
        for family in dict.fromkeys(MODELS[m][0] for m in models):
            row = sync(family, extras[device], args.work, env)
            envs[(family, device)] = env_python(row.pop("env")) if row["outcome"] == "passed" else None
            record["environments"].append(row)
    index = 0
    for device in devices:
        for model in models:
            python = envs.get((MODELS[model][0], device))
            weights = None if args.self_test else weights_file(layout, MODELS[model][1], MODELS[model][2])
            settings = [{"resolution_level": 0}] if args.self_test and model != "depth_pro" else (
                ON_CARD[model] if device == "cuda" else ON_CPU[model]
            )  # fmt: skip
            for setting in settings:
                index += 1
                if python is None:
                    record["runs"].append(
                        {"model": model, "device": device, **setting, "outcome": "no environment"}
                    )
                    continue
                record["runs"].append(run(model, python, setting, device, args, weights, env, index))
    out = args.work / f"{record['date']}-spike{'-self-test' if args.self_test else ''}.json"
    out.write_text(json.dumps(record, indent=1, default=str) + "\n", encoding="utf-8")
    print("\n" + table(record["runs"]) + f"\n\nWritten: {out}")
    # A model that fails or runs out of memory is a finding; the spike fails only when a setting
    # could not be measured at all (no environment, a process that died or timed out).
    return 0 if all(r.get("outcome") in ("passed", "oom", "failed") for r in record["runs"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
