# Runtime report: moge

2026-11-07T01:15:45+00:00 · windows · engine 0.1.0

## Machine

- Card 0: NVIDIA GeForce GTX 1070, compute capability 6.1, 8,590 MB total
- Driver: 582.66
- System memory: 18,971 MB free of 34,294 MB (Read from GlobalMemoryStatusEx (available physical memory).)
- Free disk on the data root: 97,522 MB

nvidia-smi said:

```
0, NVIDIA GeForce GTX 1070, 6.1, 8192, 7381, 582.66
```

## Plan

**cu126**: cu126 is the fastest build this machine runs (compute capability 6.1, driver 582.66).

| Build | Chosen | Why |
| --- | --- | --- |
| cu130 | no | cu130 needs compute capability 7.5 or higher; this card is 6.1 |
| cu126 | yes | cu126 is the fastest build this machine runs (compute capability 6.1, driver 582.66). |
| cpu | no | a GPU build runs here |

- flex_gemm (MoGe-3's refinement) is replaced by a stand-in.

## Install

- Build: cu126, Python 3.14.9, uv 0.13.5 (46776a95d 2026-10-21 x86_64-pc-windows-msvc)
- Time: 84.0 s
- Size: 4,746 MB
- Packages: 28
- `torch==2.14.0+cu126`
- `torchvision==0.29.1+cu126`

## Probe

- Seconds: 3.751
- Peak VRAM: 0.0 MB

```json
{
  "torch": "2.14.0+cu126",
  "cuda": "12.6",
  "cuda_available": true,
  "numpy": "2.5.4",
  "opencv": "5.0.0",
  "moge": [
    "MoGeModel",
    "MoGeModel"
  ]
}
```

## Everything, as recorded

```json
{
  "runtime": "moge",
  "date": "2026-11-07T01:15:45+00:00",
  "engine": "0.1.0",
  "profile": {
    "os": "windows",
    "gpus": [
      {
        "index": 0,
        "vendor": "nvidia",
        "name": "NVIDIA GeForce GTX 1070",
        "capability": "6.1",
        "vram_total_mb": 8590,
        "vram_free_mb": 7740
      }
    ],
    "driver": "582.66",
    "nvidia": {
      "found": true,
      "why": "nvidia-smi listed 1 card."
    },
    "raw": "0, NVIDIA GeForce GTX 1070, 6.1, 8192, 7381, 582.66\n",
    "system": {
      "total_mb": 34294,
      "free_mb": 18971,
      "why": "Read from GlobalMemoryStatusEx (available physical memory)."
    },
    "disk_free_mb": 97522
  },
  "plan": {
    "runtime": "moge",
    "build": "cu126",
    "why": "cu126 is the fastest build this machine runs (compute capability 6.1, driver 582.66).",
    "blocked": null,
    "considered": [
      {
        "build": "cu130",
        "chosen": false,
        "why": "cu130 needs compute capability 7.5 or higher; this card is 6.1"
      },
      {
        "build": "cu126",
        "chosen": true,
        "why": "cu126 is the fastest build this machine runs (compute capability 6.1, driver 582.66)."
      },
      {
        "build": "cpu",
        "chosen": false,
        "why": "a GPU build runs here"
      }
    ],
    "notes": [
      "flex_gemm (MoGe-3's refinement) is replaced by a stand-in."
    ]
  },
  "install": {
    "ok": true,
    "already": false,
    "seconds": 84.0,
    "build": "cu126",
    "python": "3.14.9",
    "uv": "uv 0.13.5 (46776a95d 2026-10-21 x86_64-pc-windows-msvc)",
    "size_bytes": 4745600428,
    "freeze": [
      "anyio==4.16.0",
      "click==8.5.0",
      "colorama==0.4.6",
      "filelock==4.2.9",
      "fsspec==2026.9.0",
      "h11==0.16.0",
      "hf-xet==1.8.0",
      "httpcore2==2.16.0",
      "httpx2==2.16.0",
      "huggingface-hub==2.4.0",
      "idna==3.20",
      "jinja2==3.1.6",
      "markupsafe==3.0.4",
      "mpmath==1.3.0",
      "networkx==3.7",
      "numpy==2.5.4",
      "opencv-python-headless==5.0.0.93",
      "packaging==26.3",
      "pillow==13.0.0",
      "pyyaml==6.0.3",
      "scipy==1.18.1",
      "setuptools==84.2.1",
      "sympy==1.14.0",
      "torch==2.14.0+cu126",
      "torchvision==0.29.1+cu126",
      "tqdm==4.70.1",
      "truststore==0.10.5",
      "typing-extensions==4.16.0"
    ]
  },
  "probe": {
    "ran": true,
    "seconds": 3.751,
    "peak_vram_mb": 0.0,
    "result": {
      "torch": "2.14.0+cu126",
      "cuda": "12.6",
      "cuda_available": true,
      "numpy": "2.5.4",
      "opencv": "5.0.0",
      "moge": [
        "MoGeModel",
        "MoGeModel"
      ]
    }
  }
}
```
