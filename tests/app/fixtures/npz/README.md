# npz fixtures

Written by numpy, for "the npz reader reads what numpy writes" (`tests/app/page.test.js`, spec 017,
AC2): `stored.npz` by `np.savez`, `deflated.npz` by `np.savez_compressed`, each holding the same two
arrays, which the test builds again to compare:

- `points`: float32, shape (3, 4, 3), `np.arange(36, dtype=np.float32).reshape(3, 4, 3) / 4 - 2`;
- `valid`: bool, shape (3, 4), `np.arange(12).reshape(3, 4) % 3 != 0`.

Written once, from the repo's root, with the engine's numpy:

```sh
uv run --project engine --frozen python -c "import numpy as np; p = np.arange(36, dtype=np.float32).reshape(3, 4, 3) / 4 - 2; v = np.arange(12).reshape(3, 4) % 3 != 0; np.savez('tests/app/fixtures/npz/stored.npz', points=p, valid=v); np.savez_compressed('tests/app/fixtures/npz/deflated.npz', points=p, valid=v)"
```
