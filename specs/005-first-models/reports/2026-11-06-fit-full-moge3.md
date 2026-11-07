# Fit report: depth.moge3

2026-11-07T01:53:02+00:00 · engine 0.1.0

Step `moge3` of `specs\005-first-models\bench\photo-full.json`; inputs: image 4000 x 1800 px.

## Runs

| Settings | Device | Free before | Estimate | Measured peak | Ratio | Within tolerance | System estimate | System peak | System ratio | Seconds | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| resolution_level=0 | cuda | 7,848 MB | 2,695 MB | 2,481 MB | 0.921 | yes | 3,489 MB | 3,489 MB | 1.0 | 20.509 | done |
| resolution_level=5 | cuda | 7,858 MB | 3,236 MB | 2,598 MB | 0.803 | **no** | 3,489 MB | 3,489 MB | 1.0 | 19.887 | done |
| resolution_level=9 | cuda | 7,853 MB | 3,669 MB | 2,888 MB | 0.787 | **no** | 3,489 MB | 3,489 MB | 1.0 | 19.5 | done |
| resolution_level=9, precision=fp16 | cuda | 7,836 MB | 3,919 MB | 3,100 MB | 0.791 | **no** | 3,489 MB | 3,488 MB | 1.0 | 18.799 | done |
