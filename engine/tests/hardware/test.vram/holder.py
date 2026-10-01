"""Another program holding memory on the card, for AC8: allocates the MB given, says "ready", and
holds them until its stdin closes.

    <torch runtime python> holder.py <mb>
"""

from __future__ import annotations

import sys

import torch  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]


def main() -> int:
    mb = float(sys.argv[1])
    held = torch.empty(max(int(mb * 1_000_000), 1), dtype=torch.uint8, device="cuda")
    held.fill_(1)
    torch.cuda.synchronize()
    print("ready", flush=True)
    sys.stdin.read()  # until the bench closes it
    del held
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
