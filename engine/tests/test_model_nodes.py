"""Spec 005, AC1: the model nodes' memory models, fitted on a range of machines.

Each model node in `nodes/` is fitted at its defaults on every machine of `fixtures/machines.py`, and
on each of them with 50 GB of system memory free, for the card its runtime's plan picks. What the
fit should do is worked out from the node's own estimates and each device's budget, never from a
card: so the test holds the rule, not the figures, which a local session fits again (spec 005)."""

from __future__ import annotations

from typing import Any

from fixtures import machines

from oneframe.manifest import Manifest
from oneframe.memory.estimate import Device, Learned, Target, estimate
from oneframe.memory.fit import Fit, fit
from oneframe.memory.settings import Settings
from oneframe.registry import discover
from oneframe.runtimes import definition
from oneframe.runtimes.plan import plan, planned_card

PHOTO = {"width": 1536, "height": 691}  # engine/tests/models/photo-1536.jpg
FREE = 50000  # MB of system memory free in each machine's roomy twin


def profiles() -> dict[str, dict[str, Any]]:
    found = dict(machines.MACHINES)
    for name, profile in machines.MACHINES.items():
        total = max(profile["system"]["total_mb"], FREE + 4000)
        found[f"{name}, 50 GB free"] = machines.with_system(profile, FREE, total)
    return found


def target(manifest: Manifest, profile: dict[str, Any]) -> Target:
    """The card the node's runtime is planned for, as the runtime manager gives it (device_target)."""
    runtime = definition.discover().runtimes[str(manifest.run.runtime)]
    build = runtime.build(str(plan(runtime, profile).build))
    card = planned_card(profile)
    if build is None or build.vendor != "nvidia" or card is None:
        return Target()
    return Target(card=int(card["index"]))


def defaults(manifest: Manifest) -> dict[str, Any]:
    return {name: param.default for name, param in manifest.params.items()}


def inputs(manifest: Manifest) -> dict[str, dict[str, Any] | None]:
    return {port: None if spec.optional else dict(PHOTO) for port, spec in manifest.inputs.items()}


def fitted(manifest: Manifest, profile: dict[str, Any]) -> Fit:
    return fit(
        manifest.memory,
        defaults(manifest),
        set(),
        inputs(manifest),
        profile,
        target(manifest, profile),
        Learned(),
        Settings(),
        manifest.devices,
    )


class Budgets:
    """Whether the node's estimate at some values fits each device the fit measured."""

    def __init__(self, manifest: Manifest, found: Fit):
        self.manifest = manifest
        self.rows = found.to_json()["devices"]

    def need(self, kind: str, values: dict[str, Any]) -> tuple[float, float]:
        assert self.manifest.memory is not None
        device = Device(kind, kind, None, None, None, 0)
        got = estimate(self.manifest.memory, values, inputs(self.manifest), device, Learned())
        assert got.device is not None and got.system is not None, got.why
        return got.device, got.system

    def fits(self, kind: str, **over: Any) -> bool:
        row = self.rows.get(kind)
        if row is None:
            return False
        need, system = self.need(kind, {**defaults(self.manifest), **over})
        if need > row["budget"]:
            return False
        return kind == "cpu" or system <= self.rows["cpu"]["budget"]


def offered(found: Fit) -> tuple[str, dict[str, Any]] | None:
    """The faster way the slow warning offers: its device and what it sets."""
    alternative = (found.warning or {}).get("alternative")
    return None if alternative is None else (alternative["device"], alternative["set"])


def depth_pro(found: Fit, budgets: Budgets) -> str:
    """fp32 on a card its fp32 estimate fits, else on the processor, offering fp16 on the card where
    fp16 fits; with neither, fp16 on the card where that fits; else nothing."""
    if budgets.fits("cuda"):
        assert (found.outcome, found.device, found.values["precision"]) == ("fits", "cuda", "fp32")
        assert found.warning is None and not found.reduced
        return "the card at fp32"
    if budgets.fits("cpu"):
        assert (found.outcome, found.device, found.values["precision"]) == ("fits", "cpu", "fp32")
        fp16 = budgets.fits("cuda", precision="fp16")
        assert offered(found) == (("cuda", {"precision": "fp16"}) if fp16 else None)
        return "the processor, offering fp16 on the card" if fp16 else "the processor"
    if budgets.fits("cuda", precision="fp16"):
        assert (found.outcome, found.device, found.values["precision"]) == ("fits", "cuda", "fp16")
        assert found.reduced
        return "the card at fp16"
    assert found.outcome == "memory", found.to_json()
    return "nowhere"


def moge(found: Fit, budgets: Budgets) -> str:
    """Its defaults on a card that holds them, else on the processor; its precision never changed."""
    assert found.values["precision"] == "fp32"
    if budgets.fits("cuda"):
        assert (found.outcome, found.device, found.values) == ("fits", "cuda", defaults(budgets.manifest))
        return "the card at its defaults"
    assert (found.device, found.values) != ("cuda", defaults(budgets.manifest))
    if budgets.fits("cpu"):
        assert (found.outcome, found.device, found.values) == ("fits", "cpu", defaults(budgets.manifest))
        return "the processor at its defaults"
    return "reduced" if found.outcome == "fits" and found.reduced else found.outcome


RULES = {"depth.depth_pro": depth_pro, "depth.moge3": moge, "depth.moge2": moge}


def test_the_model_nodes_fit_a_range_of_machines() -> None:
    registry = discover()
    seen: dict[str, set[str]] = {}
    for node, rule in RULES.items():
        manifest = registry.get(node)
        for name, profile in profiles().items():
            found = fitted(manifest, profile)
            assert found.to_json() == fitted(manifest, machines.renamed(profile)).to_json(), (node, name)
            try:
                seen.setdefault(node, set()).add(rule(found, Budgets(manifest, found)))
            except AssertionError as exc:
                raise AssertionError(f"{node} on {name}: {exc}") from exc
    # The range reaches every branch of each rule, so none of them holds vacuously.
    assert seen["depth.depth_pro"] >= {
        "the card at fp32",
        "the processor, offering fp16 on the card",
        "the processor",
        "the card at fp16",
        "nowhere",
    }
    for node in ("depth.moge3", "depth.moge2"):
        assert seen[node] >= {"the card at its defaults", "the processor at its defaults"}, node
