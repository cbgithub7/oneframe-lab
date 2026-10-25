# Runtime report: torch

2026-10-25T18:34:07+00:00 · windows · engine 0.1.0

## Machine

- Card 0: NVIDIA GeForce GTX 1070, compute capability 6.1, 8,590 MB total
- Driver: 582.66
- System memory: 17,115 MB free of 34,294 MB (Read from GlobalMemoryStatusEx (available physical memory).)
- Free disk on the data root: 338,798 MB

nvidia-smi said:

```
0, NVIDIA GeForce GTX 1070, 6.1, 8192, 7236, 582.66
```

## Plan

**cu126**: cu126 is the fastest build this machine runs (compute capability 6.1, driver 582.66).

| Build | Chosen | Why |
| --- | --- | --- |
| cu130 | no | cu130 needs compute capability 7.5 or higher; this card is 6.1 |
| cu126 | yes | cu126 is the fastest build this machine runs (compute capability 6.1, driver 582.66). |
| cpu | no | a GPU build runs here |

## Install

- Failed: uv sync failed (exit 2).

```
error: failed to remove directory `%USERPROFILE%\AppData\Local\OneframeLab-dev\runtimes\torch\cu126\Lib\site-packages\setuptools\config\_validate_pyproject\__pycache__`: The process cannot access the file because it is being used by another process. (os error 32)
```

## Everything, as recorded

```json
{
  "runtime": "torch",
  "date": "2026-10-25T18:34:07+00:00",
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
        "vram_free_mb": 7587
      }
    ],
    "driver": "582.66",
    "nvidia": {
      "found": true,
      "why": "nvidia-smi listed 1 card."
    },
    "raw": "0, NVIDIA GeForce GTX 1070, 6.1, 8192, 7236, 582.66\n",
    "system": {
      "total_mb": 34294,
      "free_mb": 17115,
      "why": "Read from GlobalMemoryStatusEx (available physical memory)."
    },
    "disk_free_mb": 338798
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
    "ok": false,
    "message": "uv sync failed (exit 2).",
    "detail": "error: failed to remove directory `%USERPROFILE%\\AppData\\Local\\OneframeLab-dev\\runtimes\\torch\\cu126\\Lib\\site-packages\\setuptools\\config\\_validate_pyproject\\__pycache__`: The process cannot access the file because it is being used by another process. (os error 32)"
  }
}
```
