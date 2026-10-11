"""What the store holds for each node, and removing it safely (spec 003, requirements 6 and 7).

    models.list                               every node's files, by shared and by checkpoint
    models.remove {node, checkpoint?}         a node's files, or one checkpoint's
    models.remove {unpinned: [sha256 ...]}    exactly the files and partials no node pins

Listing never uses the network. Removing deletes only what the store holds of the files named,
keeps (and names) any file another node pins, and is refused while the registry has problems or a
folder of nodes is missing (a node that cannot be read may pin anything), while a job runs in any
process (it holds the store's lock), and for a file a graph running in any process may read.
Nothing is ever deleted that a person did not ask to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oneframe.errors import Failure
from oneframe.files import is_free
from oneframe.manifest import Manifest
from oneframe.models.jobs import claim_store, store_lock
from oneframe.models.runs import in_use, state_of
from oneframe.models.schema import FileSpec
from oneframe.models.store import Store, StoreRefused, check_sha256
from oneframe.registry import Registry


@dataclass(frozen=True)
class Pin:
    node: str
    file: str
    checkpoints: tuple[str, ...]


def pins(registry: Registry) -> dict[str, list[Pin]]:
    """Every pinned sha256, and which node's files name it."""
    found: dict[str, list[Pin]] = {}
    for manifest in registry.nodes.values():
        for spec in manifest.files:
            found.setdefault(spec.sha256, []).append(Pin(manifest.id, spec.name, spec.checkpoints))
    return found


def _file_row(
    store: Store, state: str, spec: FileSpec, downloading: bool, others: list[Pin]
) -> dict[str, Any]:
    found = store.presence(spec.sha256, spec.size, state)
    status, have = state_of(found, downloading), found.have
    source = spec.url or f"{spec.repo}@{spec.revision}:{spec.path}"
    return {
        "name": spec.name,
        "sha256": spec.sha256,
        "size": spec.size,
        "present": have if status == "present" else 0,
        "partial": have if status in ("downloading", "incomplete") else 0,
        "to_fetch": 0 if status == "present" else spec.size - have,
        "status": status,
        "format": spec.format,
        "gated": spec.gated,
        "source": source,
        "pinned_by": sorted({p.node for p in others}),
    }


def _frees(store: Store, rows: list[dict[str, Any]], kept: set[str]) -> int:
    """What removing these files would free: each one no one else pins, and its partial."""
    total = 0
    for row in rows:
        if row["sha256"] in kept:
            continue
        total += row["present"] + row["partial"]
    return total


def listing(store: Store, registry: Registry) -> dict[str, Any]:
    """For each node, its shared files and each checkpoint's: sizes, bytes present and to fetch,
    what removing would free, status, format, gated and licence; then the store's free space, and
    the files and partials no node pins. Never uses the network."""
    pinned = pins(registry)
    downloading = not is_free(store_lock(store).path)
    state = store.state()
    nodes: list[dict[str, Any]] = []
    for manifest in sorted(registry.nodes.values(), key=lambda m: m.id):
        if not manifest.files:
            continue
        groups: list[dict[str, Any]] = []
        names = [None, *(manifest.params[manifest.files_by].choices if manifest.files_by else ())]
        for checkpoint in names:
            specs = [
                f
                for f in manifest.files
                if (not f.checkpoints if checkpoint is None else checkpoint in f.checkpoints)
            ]
            if checkpoint is None and not specs:
                continue
            rows = [
                _file_row(
                    store,
                    state,
                    s,
                    downloading,
                    [p for p in pinned.get(s.sha256, []) if p.node != manifest.id],
                )
                for s in specs
            ]
            kept = _kept(manifest, specs, checkpoint, pinned)
            groups.append(
                {
                    "checkpoint": checkpoint,
                    "default": checkpoint is not None and checkpoint == manifest.default_checkpoint,
                    "files": rows,
                    "size": sum(r["size"] for r in rows),
                    "present": sum(r["present"] for r in rows),
                    "to_fetch": sum(r["to_fetch"] for r in rows),
                    "frees": _frees(store, rows, kept),
                    "status": _group_status(rows),
                }
            )
        nodes.append(
            {
                "node": manifest.id,
                "files_by": manifest.files_by,
                "licence": manifest.licence,
                "groups": groups,
            }
        )
    files, partials = unpinned(store, pinned)
    return {
        "store": state,
        "free_bytes": store.free_bytes(),
        "nodes": nodes,
        "unpinned": {"files": files, "partials": partials},
        "problems": dict(registry.problems),
    }


def _group_status(rows: list[dict[str, Any]]) -> str:
    states = {r["status"] for r in rows}
    if states == {"present"}:
        return "present"
    for state in ("changed", "newer", "unreadable", "downloading", "incomplete"):
        if state in states:
            return state
    return "not_downloaded" if "present" not in states else "partly"


def _kept(
    manifest: Manifest, specs: list[FileSpec], checkpoint: str | None, pinned: dict[str, list[Pin]]
) -> set[str]:
    """Of these files, the sha256 of each that something else still needs: another node, or this
    node's own shared files or other checkpoints when one checkpoint is removed."""
    kept: set[str] = set()
    for spec in specs:
        for pin in pinned.get(spec.sha256, []):
            elsewhere = pin.node != manifest.id
            # removing one checkpoint keeps what this node's shared files or other checkpoints need
            other_checkpoint = checkpoint is not None and (
                not pin.checkpoints or set(pin.checkpoints) - {checkpoint}
            )
            if elsewhere or other_checkpoint:
                kept.add(spec.sha256)
    return kept


def unpinned(store: Store, pinned: dict[str, list[Pin]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The stored files, and the partials, that no node pins."""
    files = [{"sha256": sha, "size": _size(store.blob(sha))} for sha in store.stored() if sha not in pinned]
    partials = [
        {"name": path.name, "size": _size(path)} for path in store.partials() if path.name not in pinned
    ]
    return files, partials


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


class Refused(StoreRefused):
    """A remove that is refused with the files it would have touched."""

    def __init__(self, message: str, reason: str, next: str | None = None, files: list[str] | None = None):
        super().__init__(message, reason, next)
        self.files = files or []

    def to_json(self, **extra: Any) -> dict[str, Any]:
        return super().to_json(files=self.files, **extra)


def _refuse_unknowable(registry: Registry, node_roots: list[Path]) -> None:
    if registry.problems:
        broken = ", ".join(sorted(registry.problems))
        raise Refused(
            f"A node's manifest cannot be read ({broken}), so what it pins is not known; nothing was "
            "deleted.",
            "registry_problems",
            "Fix or remove that manifest, reload the nodes, then remove again.",
        )
    missing = [str(root) for root in node_roots if not root.is_dir()]
    if missing:
        raise Refused(
            f"A folder of nodes is missing ({', '.join(missing)}), so what its nodes pin is not known; "
            "nothing was deleted.",
            "node_root_missing",
            "Bring the folder back (a drive unplugged?), then remove again.",
        )


def _delete(store: Store, shas: list[str], partial_names: list[str], busy_here: str | None) -> dict[str, Any]:
    """Under the store's lock, delete these files (and their partials) and these partials, unless a
    run in any process may read one of them."""
    reading = sorted(set(shas) & in_use(store.layout))
    if reading:
        raise Refused(
            f"A graph that is running may read {len(reading)} of these files; nothing was deleted.",
            "in_use",
            "Stop the run, or wait for it to end, then remove again.",
            reading,
        )
    held = claim_store(store, busy_here)
    removed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    try:
        for sha in shas:
            freed = _size(store.blob(sha)) + _size(store.partial(sha))
            try:
                store.drop(sha)
                store.drop_partial(store.partial(sha))
            except StoreRefused as exc:
                failed.append({"sha256": sha, **exc.to_json()})
                continue
            removed.append({"sha256": sha, "freed": freed})
        for name in partial_names:
            path = store.layout.partials / name
            freed = _size(path)
            try:
                store.drop_partial(path)
            except StoreRefused as exc:
                failed.append({"partial": name, **exc.to_json()})
                continue
            removed.append({"partial": name, "freed": freed})
    finally:
        held.release()
    return {"removed": removed, "failed": failed, "freed": sum(r["freed"] for r in removed)}


def remove_node(
    store: Store,
    registry: Registry,
    node_roots: list[Path],
    manifest: Manifest,
    checkpoint: str | None,
    busy_here: str | None,
) -> dict[str, Any]:
    """Delete a node's files (or one checkpoint's) and their partials, keeping and naming any file
    something else still pins."""
    if checkpoint is not None:
        choices = manifest.params[manifest.files_by].choices if manifest.files_by else ()
        if checkpoint not in choices:
            raise Failure(f"{manifest.id} has no checkpoint {checkpoint!r}.", "bad_params", kind="request")
        specs = [f for f in manifest.files if checkpoint in f.checkpoints]
    else:
        specs = list(manifest.files)
    _refuse_unknowable(registry, node_roots)
    pinned = pins(registry)
    kept_shas = _kept(manifest, specs, checkpoint, pinned)
    kept: list[dict[str, Any]] = []
    shas: list[str] = []
    for spec in specs:
        if spec.sha256 in kept_shas:
            others = sorted({p.node for p in pinned.get(spec.sha256, []) if p.node != manifest.id})
            kept.append({"name": spec.name, "sha256": spec.sha256, "pinned_by": others or [manifest.id]})
        elif spec.sha256 not in shas:
            shas.append(spec.sha256)
    done = _delete(store, shas, [], busy_here)
    names = {s.sha256: s.name for s in specs}
    for row in done["removed"]:
        row["name"] = names.get(row.get("sha256", ""))
    return {"node": manifest.id, "checkpoint": checkpoint, "kept": kept, **done}


def remove_unpinned(
    store: Store, registry: Registry, node_roots: list[Path], listed: list[str], busy_here: str | None
) -> dict[str, Any]:
    """Delete exactly the files and partials named, each of which must be pinned by no node now:
    a sha256 for a stored file or its partial, or a partial's name (`url-…`)."""
    _refuse_unknowable(registry, node_roots)
    pinned = pins(registry)
    files, partials = unpinned(store, pinned)
    stored = {row["sha256"] for row in files}
    loose = {row["name"] for row in partials}
    shas: list[str] = []
    names: list[str] = []
    wrong: list[str] = []
    for item in listed:
        if item in pinned:
            wrong.append(f"{item[:12]}… is pinned by {', '.join(sorted({p.node for p in pinned[item]}))}")
        elif item in stored:
            shas.append(check_sha256(item))
        elif item in loose:
            names.append(item)
        else:
            wrong.append(f"{item[:12]}… is not in the store")
    if wrong:
        raise Failure(
            "Only files and partials that no node pins can be removed this way: " + "; ".join(wrong) + ".",
            "bad_params",
            kind="request",
        )
    return {"unpinned": listed, **_delete(store, shas, names, busy_here)}
