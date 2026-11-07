# Fit report: depth.depth_pro

2026-11-07T01:50:46+00:00 · engine 0.1.0

Step `depth_pro` of `specs\005-first-models\bench\photo-full.json`; inputs: image 4000 x 1800 px.

## Runs

| Settings | Device | Free before | Estimate | Measured peak | Ratio | Within tolerance | System estimate | System peak | System ratio | Seconds | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| precision=fp32 | cpu | 21,085 MB | 9,280 MB | 8,986 MB | 0.968 | yes | 9,280 MB | 8,986 MB | 0.968 | 110.49 | done |
| precision=fp16 | cuda | 7,876 MB | 6,259 MB | 6,187 MB | 0.988 | yes | 2,657 MB | 2,657 MB | 1.0 | 12.779 | done |
