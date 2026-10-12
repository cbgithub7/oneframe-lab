# Live manifests for AC8

Spec 003's AC8 runs on the owner's Windows PC against the real hosts: Apple's server, Hugging Face
and GitHub's release assets. These four nodes exist only to give `npm run models:pin` and
`npm run models:download` something to pin and fetch. Each is a stub runtime node (its runtime,
`torch`, need not be installed) whose `node.py` never runs.

| Node | Files | Host |
| --- | --- | --- |
| `live.depth_pro` | `depth_pro.pt` | Apple's server, a plain URL |
| `live.moge2` | `Ruicheng/moge-2-vitl-normal`'s `model.pt` | Hugging Face |
| `live.moge3` | `Ruicheng/moge-3-vitl`'s and `Ruicheng/moge-3-vitg`'s `model.pt`, checkpoints `vitl` and `vitg` | Hugging Face; `vitg` is the large file |
| `live.midas` | MiDaS v3.1's `dpt_beit_large_512.pt` (1.58 GB) | a GitHub release asset, behind a signed redirect |

They were pinned for AC8 on 2026-10-12 (full commits, sizes and sha256s), and AC8 passed:
[the report](../reports/2026-10-12-ac8-live-hosts.md). To run it again, the steps are in the spec's
Verification section:

1. `npm run models:pin -- --nodes specs/003-model-store/live <node>` for each node, then commit the
   pins it writes.
2. Point `ONEFRAME_DATA` at a new, empty folder.
3. `npm run models:download -- --nodes specs/003-model-store/live <node> --all`: press Ctrl+C midway,
   run it again, and let it finish. For MiDaS, wait more than 3600 s before running it again.
4. Write the report AC8 describes, under `specs/003-model-store/reports/`, from the journals in
   the data root's `logs/journal/`: a file's last `downloading` event names the hosts it asked and
   where it started, and its `verifying` event follows the last byte. A proxy is not among the
   hosts: say whether the machine uses one.

MoGe-2 and MoGe-3 load the same way: one `model.pt` per repository, read with
`torch.load(..., weights_only=True)` (microsoft/MoGe, `moge/model/v2.py`; v3's model inherits it).
MoGe-3's code also imports FlexGEMM. Only the files matter here.
