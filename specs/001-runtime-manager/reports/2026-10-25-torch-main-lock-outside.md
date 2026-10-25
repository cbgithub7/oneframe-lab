# Runtime report: torch

2026-10-25T20:50:43+00:00 · windows · engine 0.1.0

## Machine

- Card 0: NVIDIA GeForce GTX 1070, compute capability 6.1, 8,590 MB total
- Driver: 582.66
- System memory: 18,417 MB free of 34,294 MB (Read from GlobalMemoryStatusEx (available physical memory).)
- Free disk on the data root: 334,620 MB

nvidia-smi said:

```
0, NVIDIA GeForce GTX 1070, 6.1, 8192, 7477, 582.66
```

## Plan

**cu126**: cu126 is the fastest build this machine runs (compute capability 6.1, driver 582.66).

| Build | Chosen | Why |
| --- | --- | --- |
| cu130 | no | cu130 needs compute capability 7.5 or higher; this card is 6.1 |
| cu126 | yes | cu126 is the fastest build this machine runs (compute capability 6.1, driver 582.66). |
| cpu | no | a GPU build runs here |

## Install

- Build: cu126, Python 3.14.7, uv 0.13.5 (46776a95d 2026-10-21 x86_64-pc-windows-msvc)
- Time: 55.1 s
- Size: 4,359 MB
- Packages: 10
- `torch==2.14.0+cu126`

## Probe

- Seconds: 0.179
- Peak VRAM: 209.8 MB

```json
{
  "torch": "2.14.0+cu126",
  "cuda": "12.6",
  "cuda_available": true,
  "capability": [
    6,
    1
  ],
  "device": "cuda",
  "matmul_4096_seconds": 0.0318
}
```

## Everything, as recorded

```json
{
  "runtime": "torch",
  "date": "2026-10-25T20:50:43+00:00",
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
        "vram_free_mb": 7840
      }
    ],
    "driver": "582.66",
    "nvidia": {
      "found": true,
      "why": "nvidia-smi listed 1 card."
    },
    "raw": "0, NVIDIA GeForce GTX 1070, 6.1, 8192, 7477, 582.66\n",
    "system": {
      "total_mb": 34294,
      "free_mb": 18417,
      "why": "Read from GlobalMemoryStatusEx (available physical memory)."
    },
    "disk_free_mb": 334620
  },
  "plan": {
    "runtime": "torch",
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
    "notes": []
  },
  "install": {
    "ok": true,
    "already": false,
    "seconds": 55.1,
    "build": "cu126",
    "python": "3.14.7",
    "uv": "uv 0.13.5 (46776a95d 2026-10-21 x86_64-pc-windows-msvc)",
    "size_bytes": 4359442176,
    "freeze": [
      "filelock==4.0.7",
      "fsspec==2026.9.0",
      "jinja2==3.1.6",
      "markupsafe==3.0.3",
      "mpmath==1.3.0",
      "networkx==3.7",
      "setuptools==84.0.0",
      "sympy==1.14.0",
      "torch==2.14.0+cu126",
      "typing-extensions==4.16.0"
    ]
  },
  "probe": {
    "ran": true,
    "seconds": 0.179,
    "peak_vram_mb": 209.8,
    "result": {
      "torch": "2.14.0+cu126",
      "cuda": "12.6",
      "cuda_available": true,
      "capability": [
        6,
        1
      ],
      "device": "cuda",
      "matmul_4096_seconds": 0.0318
    }
  }
}
```
