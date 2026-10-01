"""Spec 002: the memory model in a manifest (AC1).

The test node is the plan's: weights of 3000 MB at fp32 and 1500 MB at fp16 and bf16, working
memory of 200 + 0.00025 × resolution² × bytes + 0.25 × chunk_size MB, two speed-only changes, two
quality changes and one upgrade. Its numbers are exact by construction, not measured."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from fixtures import machines

from oneframe import memory as mem
from oneframe.cache import Cache
from oneframe.graph import Graph, plan
from oneframe.manifest import Manifest, ManifestError, parse
from oneframe.memory import (
    Device,
    Fit,
    Formula,
    Learned,
    Settings,
    Target,
    Term,
    estimate,
    fit,
    margin,
    read_settings,
    settings_hash,
)
from oneframe.registry import Registry

SOURCE = "test node, exact by construction"

TEST_MODEL: dict[str, Any] = {
    "precisions": {
        "fp32": {"cuda_min_capability": None, "cpu": True},
        "fp16": {"cuda_min_capability": "6.0", "cpu": False},
        "bf16": {"cuda_min_capability": "8.0", "cpu": True},
    },
    "weights": {"fp32": 3000, "fp16": 1500, "bf16": 1500, "source": SOURCE},
    "working": {
        "mb": 200,
        "terms": [
            {"coef": 0.00025, "of": ["resolution", "resolution", "bytes"]},
            {"coef": 0.25, "of": ["chunk_size"]},
        ],
        "source": SOURCE,
    },
    "changes": [
        {"set": {"chunk_size": 2048}, "costs": "speed"},
        {"set": {"chunk_size": 512}, "costs": "speed"},
        {"set": {"precision": "fp16"}, "costs": "quality"},
        {"set": {"resolution": 512}, "costs": "quality"},
    ],
    "upgrades": [{"set": {"chunk_size": 32768}}],
}

TEST_NODE: dict[str, Any] = {
    "id": "test.memory",
    "version": "1",
    "title": "Memory test node",
    "category": "test",
    "inputs": {"image": {"type": "Image", "optional": True}},
    "outputs": {"depth": "Depth[kind=metric,measure=z]"},
    "params": {
        "resolution": {"type": "int", "default": 1024, "min": 64, "max": 4096},
        "chunk_size": {"type": "int", "default": 8192, "min": 64, "max": 65536, "affects": "speed"},
        "checkpoint": {"type": "choice", "choices": ["small", "large"], "default": "large"},
        "tiled": {"type": "bool", "default": False, "affects": "speed"},
        "prompt": {"type": "string", "default": ""},
    },
    "devices": ["cuda", "cpu"],
    "run": {"where": "runtime", "runtime": "torch", "entry": "node.py:run"},
    "memory": TEST_MODEL,
}


def _node(tmp_path: Path, **memory: Any) -> dict[str, Any]:
    """The test node, its memory model changed by `memory` (None removes a key)."""
    (tmp_path / "node.py").write_text("def run(ctx):\n    pass\n", encoding="utf-8")
    data = copy.deepcopy(TEST_NODE)
    for key, value in memory.items():
        if value is None:
            data["memory"].pop(key, None)
        else:
            data["memory"][key] = value
    return data


def _load(tmp_path: Path, **memory: Any) -> Manifest:
    return parse(_node(tmp_path, **memory), tmp_path)


def _problems(tmp_path: Path, data: dict[str, Any]) -> str:
    with pytest.raises(ManifestError) as info:
        parse(data, tmp_path)
    return " | ".join(info.value.problems)


# -- a model that is right -----------------------------------------------------------------------


def test_the_test_nodes_model_is_read(tmp_path: Path) -> None:
    m = _load(tmp_path)
    model = m.memory
    assert model is not None
    assert list(model.precisions) == ["fp32", "fp16", "bf16"]
    assert model.precisions["fp32"].bytes == 4 and model.precisions["fp16"].bytes == 2
    assert model.precisions["fp16"].cuda_min_capability == "6.0" and not model.precisions["fp16"].cpu
    assert model.weights == {"": {"fp32": 3000, "fp16": 1500, "bf16": 1500}}
    assert model.weights_by is None and model.weights_source == SOURCE
    assert model.working == Formula(
        200, (Term(0.00025, ("resolution", "resolution", "bytes")), Term(0.25, ("chunk_size",))), SOURCE
    )
    assert [c.costs for c in model.changes] == ["speed", "speed", "quality", "quality"]
    assert model.changes[2].set == {"precision": "fp16"}
    assert [dict(u.set) for u in model.upgrades] == [{"chunk_size": 32768}]
    # Unwritten figures default to the weights, and nothing outside torch.
    assert model.weights_on_device.terms == (Term(1, ("weights",)),)
    assert model.system.terms == (Term(1, ("weights",)),)
    assert model.outside_torch.mb == 0 and not model.outside_torch.terms


def test_the_engine_adds_a_precision_param_from_the_model(tmp_path: Path) -> None:
    m = _load(tmp_path)
    precision = m.params["precision"]
    assert precision.type == "choice" and precision.choices == ("fp32", "fp16", "bf16")
    assert precision.default == "fp32" and precision.affects == "output"
    shown = m.to_json()["params"]
    assert shown["precision"]["choices"] == ["fp32", "fp16", "bf16"]
    assert shown["resolution"] == TEST_NODE["params"]["resolution"]
    assert "precision" not in m.raw["params"]  # the manifest as written is kept as written


def test_a_node_without_a_model_is_unchanged(tmp_path: Path) -> None:
    data = _node(tmp_path)
    del data["memory"]
    m = parse(data, tmp_path)
    assert m.memory is None and "precision" not in m.params
    assert "precision" not in m.to_json()["params"]


def test_precision_is_set_by_the_graph_and_is_part_of_the_cache_key(tmp_path: Path) -> None:
    m = _load(tmp_path)
    registry = Registry(nodes={m.id: m})

    def step_params(**params: Any) -> dict[str, Any]:
        graph = Graph.from_json({"version": 1, "nodes": {"n": {"node": m.id, "params": params}}, "edges": []})
        return plan(graph, registry).steps[0].params

    defaults, half = step_params(), step_params(precision="fp16")
    assert defaults["precision"] == "fp32" and half["precision"] == "fp16"
    cache = Cache(tmp_path / "cache")
    assert cache.key(m, defaults, {}) != cache.key(m, half, {})
    # chunk_size only changes speed, so it is not part of the key.
    assert cache.key(m, defaults, {}) == cache.key(m, {**defaults, "chunk_size": 512}, {})


def test_where_each_precision_runs() -> None:
    from oneframe.memory import Precision

    fp16 = Precision("fp16", "6.0", False, 2)
    anywhere = Precision("fp32", None, True, 4)
    assert fp16.runs_on_card("6.1") and fp16.runs_on_card("10.0") and not fp16.runs_on_card("5.2")
    assert not fp16.runs_on_card(None)  # a card whose capability is unknown
    assert anywhere.runs_on_card(None)


def test_unknown_figures_are_null_with_a_source(tmp_path: Path) -> None:
    m = _load(
        tmp_path,
        weights={"fp32": 3000, "fp16": None, "bf16": None, "source": "fp16 not measured yet"},
        working={"mb": None, "source": "not measured yet"},
    )
    assert m.memory is not None
    assert m.memory.weights[""]["fp16"] is None and m.memory.working.mb is None


def test_weights_by_a_checkpoint_choice(tmp_path: Path) -> None:
    sizes = {"fp32": 1000, "fp16": 500, "bf16": 500}
    m = _load(
        tmp_path,
        weights_by="checkpoint",
        weights={"small": sizes, "large": {"fp32": 3000, "fp16": 1500, "bf16": 1500}, "source": SOURCE},
    )
    assert m.memory is not None and m.memory.weights_by == "checkpoint"
    assert m.memory.weights["small"]["fp32"] == 1000 and m.memory.weights["large"]["fp16"] == 1500


def test_factors_from_params_inputs_and_choice_tables(tmp_path: Path) -> None:
    m = _load(
        tmp_path,
        factors={"checkpoint": {"small": 1, "large": 4}},
        working={
            "mb": 100,
            "terms": [
                {"coef": 0.001, "of": ["image.pixels", "bytes"]},
                {"coef": 50, "of": ["checkpoint"]},
                {"coef": -30, "of": ["tiled"]},
                {"coef": 0.1, "of": ["weights"]},
            ],
            "source": SOURCE,
        },
        weights_on_device={
            "terms": [{"coef": 1, "of": ["weights"]}, {"coef": -0.9, "of": ["weights", "tiled"]}],
            "source": SOURCE,
        },
        system_mb={"mb": 500, "terms": [{"coef": 1, "of": ["weights"]}], "source": SOURCE},
        outside_torch_mb={"mb": 120, "source": SOURCE},
        time={
            "cuda:8.6": {"seconds": 4.2, "at": {"resolution": 1024, "precision": "fp16"}, "source": SOURCE},
            "cpu": {"seconds": None, "at": {}, "source": "not measured yet"},
        },
    )
    model = m.memory
    assert model is not None
    assert model.factors == {"checkpoint": {"small": 1, "large": 4}}
    assert model.outside_torch.mb == 120 and model.system.mb == 500
    assert model.time["cuda:8.6"].seconds == 4.2 and model.time["cpu"].seconds is None


def test_the_hash_follows_the_model(tmp_path: Path) -> None:
    a, b = _load(tmp_path), _load(tmp_path)
    changed = _load(tmp_path, upgrades=[{"set": {"chunk_size": 16384}}])
    assert a.memory and b.memory and changed.memory
    assert a.memory.hash == b.memory.hash != changed.memory.hash


# -- AC1: every problem named at once ------------------------------------------------------------


def test_ac1_a_bad_model_is_refused_with_every_problem_named(tmp_path: Path) -> None:
    data = _node(
        tmp_path,
        precisions={
            "fp32": {"cuda_min_capability": None, "cpu": True},
            "fp16": {"cpu": False},  # no rule for cards
            "int4": {"cuda_min_capability": "8.0", "cpu": False},
        },
        weights={"fp32": 3000, "fp16": 1500},  # no source
        weights_by="resolution",  # not a choice
        working={"mb": 200, "terms": [{"coef": 1, "of": ["nope", "mask.width"]}]},  # no source
        changes=[
            {"set": {"steps": 10}, "costs": "speed"},  # unknown param
            {"set": {"resolution": 99999}, "costs": "quality"},  # outside its range
            {"set": {"chunk_size": 512}, "costs": "speed"},  # speed after quality
            {"set": {"resolution": 512}, "costs": "speed"},  # changes the output
        ],
        upgrades=[{"set": {"resolution": 2048}}],  # changes the output
        time={"cuda": {"seconds": 3, "at": {}}},  # no source
    )
    text = _problems(tmp_path, data)
    for needle in (
        "precisions.fp16 needs cuda_min_capability",
        "precisions.int4: unknown precision",
        "memory.weights has no source",
        "weights_by 'resolution' should name one of the node's choice params",
        "'nope' is neither a param",
        "'mask.width' names no input",
        "memory.working has no source",
        "changes[0] sets 'steps', which is not a param",
        "resolution is above its maximum 4096",
        "changes[2] costs speed but comes after a change that costs quality",
        "changes[3] costs speed but sets 'resolution', which changes the output",
        "upgrades[0] costs speed but sets 'resolution', which changes the output "
        '(mark the param "affects": "speed")',
        "memory.time.cuda has no source",
    ):
        assert needle in text, needle


@pytest.mark.parametrize(
    ("memory", "needle"),
    [
        ({"precisions": {}}, "precisions should list at least one"),
        (
            {"precisions": {"fp16": {"cuda_min_capability": "6.0", "cpu": True, "bytes": 0}}},
            "precisions.fp16.bytes should be a positive number",
        ),
        (
            {"precisions": {"fp32": {"cuda_min_capability": "eight", "cpu": True}}},
            'cuda_min_capability should read like "8.0"',
        ),
        ({"weights": {"fp32": 3000, "fp16": 1500, "source": SOURCE}}, "weights has no size for bf16"),
        ({"weights": {"fp32": -1, "fp16": 1, "bf16": 1, "source": SOURCE}}, "weights.fp32 should be MB"),
        ({"working": None}, "working is required"),
        ({"working": {"mb": "lots", "source": SOURCE}}, "working.mb should be a number"),
        ({"working": {"terms": [{"of": ["chunk_size"]}], "source": SOURCE}}, "terms[0] needs a number coef"),
        ({"working": {"terms": [{"coef": 1, "of": ["prompt"]}], "source": SOURCE}}, "a string param"),
        (
            {"working": {"terms": [{"coef": 1, "of": ["checkpoint"]}], "source": SOURCE}},
            "needs a number per choice",
        ),
        ({"factors": {"checkpoint": {"small": 1}}}, "factors.checkpoint has no number for large"),
        (
            {"factors": {"resolution": {"a": 1}}},
            "factors.resolution should name one of the node's choice params",
        ),
        (
            {"outside_torch_mb": {"mb": 10, "terms": [{"coef": 1, "of": ["bytes"]}], "source": SOURCE}},
            "without terms",
        ),
        (
            {"changes": [{"set": {"chunk_size": 512}, "costs": "cheap"}]},
            "changes[0].costs should be speed or quality",
        ),
        ({"changes": [{"set": {}, "costs": "speed"}]}, "changes[0] needs a set of params"),
        (
            {"changes": [{"set": {"precision": "fp8"}, "costs": "quality"}]},
            "precision 'fp8', which the model",
        ),
        (
            {"changes": [{"set": {"tiled": True}, "costs": "quality"}]},
            "costs quality but sets only speed params",
        ),
        (
            {"time": {"mps": {"seconds": 1, "at": {}, "source": SOURCE}}},
            "time.mps: name one of the node's devices",
        ),
        (
            {"time": {"cpu": {"seconds": 1, "at": {"steps": 4}, "source": SOURCE}}},
            "time.cpu.at names steps",
        ),
        ({"budget": 4}, "memory.budget is not part of a memory model"),
    ],
)
def test_ac1_each_mistake_is_named(tmp_path: Path, memory: dict[str, Any], needle: str) -> None:
    assert needle in _problems(tmp_path, _node(tmp_path, **memory))


def test_a_precision_that_runs_on_none_of_the_nodes_devices_is_refused(tmp_path: Path) -> None:
    data = _node(tmp_path)
    data["devices"] = ["cpu"]
    assert "precisions.fp16 runs on none of the node's devices (cpu)" in _problems(tmp_path, data)


def test_a_checkpoint_without_weights_is_refused(tmp_path: Path) -> None:
    data = _node(
        tmp_path,
        weights_by="checkpoint",
        weights={"large": {"fp32": 1, "fp16": 1, "bf16": 1}, "source": SOURCE},
    )
    assert "weights has no sizes for checkpoint 'small'" in _problems(tmp_path, data)


def test_a_manifest_may_not_declare_its_own_precision(tmp_path: Path) -> None:
    data = _node(tmp_path)
    data["params"]["precision"] = {"type": "choice", "choices": ["fp32"], "default": "fp32"}
    assert "params.precision is added by the engine" in _problems(tmp_path, data)
    del data["memory"]
    assert "params.precision is added by the engine" in _problems(tmp_path, data)


def test_memory_problems_come_with_the_manifests_own(tmp_path: Path) -> None:
    data = _node(tmp_path, working={"mb": 1})
    data["category"] = "magic"
    text = _problems(tmp_path, data)
    assert "category 'magic'" in text and "memory.working has no source" in text


# -- the estimate --------------------------------------------------------------------------------


def _values(m: Manifest, **over: Any) -> dict[str, Any]:
    return {**{name: p.default for name, p in m.params.items()}, **over}


CARD = Device("cuda", "cuda:8.6", "8.6", 12000, 12885, 1611, 0)
PROCESSOR = Device("cpu", "cpu", None, 24000, 34360, 3436)


@pytest.mark.parametrize(
    ("over", "device", "need"),
    [
        ({}, CARD, 6296.576),  # 3000 + 200 + 0.00025 × 1024² × 4 + 0.25 × 8192
        ({"chunk_size": 32768}, CARD, 12440.576),
        ({"chunk_size": 2048}, CARD, 4760.576),
        ({"chunk_size": 512}, CARD, 4376.576),
        ({"chunk_size": 512, "precision": "fp16"}, CARD, 2352.288),
        ({"chunk_size": 512, "precision": "fp16", "resolution": 512}, CARD, 1959.072),
        ({"chunk_size": 512, "resolution": 512}, PROCESSOR, 3590.144),
    ],
)
def test_the_estimate_matches_the_plans_table(
    tmp_path: Path, over: dict[str, Any], device: Device, need: float
) -> None:
    m = _load(tmp_path)
    assert m.memory is not None
    got = estimate(m.memory, _values(m, **over), {"image": None}, device, Learned())
    assert got.device == pytest.approx(need) and got.basis == "known"
    weights = 1500 if over.get("precision") == "fp16" else 3000
    assert got.system == pytest.approx(need if device.type == "cpu" else weights)


def test_the_estimate_reads_checkpoints_inputs_and_choice_tables(tmp_path: Path) -> None:
    m = _load(
        tmp_path,
        weights_by="checkpoint",
        weights={
            "small": {"fp32": 1000, "fp16": 500, "bf16": 500},
            "large": {"fp32": 3000, "fp16": 1500, "bf16": 1500},
            "source": SOURCE,
        },
        factors={"checkpoint": {"small": 1, "large": 4}},
        working={
            "mb": 100,
            "terms": [
                {"coef": 0.001, "of": ["image.pixels", "bytes"]},
                {"coef": 10, "of": ["checkpoint"]},
                {"coef": -50, "of": ["tiled"]},
                {"coef": 0.5, "of": ["image.count"]},
            ],
            "source": SOURCE,
        },
        weights_on_device={
            "terms": [{"coef": 1, "of": ["weights"]}, {"coef": -0.9, "of": ["weights", "tiled"]}],
            "source": SOURCE,
        },
        outside_torch_mb={"mb": 120, "source": SOURCE},
    )
    model = m.memory
    assert model is not None
    image = {"width": 2000, "height": 1000, "count": 4}
    small = estimate(model, _values(m, checkpoint="small"), {"image": image}, CARD, Learned())
    # 1000 + (100 + 0.001 × 2,000,000 × 4 + 10 + 2) + 120
    assert small.device == pytest.approx(1000 + 8112 + 120) and small.system == 1000
    tiled = estimate(model, _values(m, checkpoint="large", tiled=True), {"image": image}, CARD, Learned())
    # 3000 × 0.1 on the card + (100 + 8000 + 40 − 50 + 2) + 120; the system still holds 3000
    assert tiled.device == pytest.approx(300 + 8092 + 120) and tiled.system == 3000
    unconnected = estimate(model, _values(m, checkpoint="small"), {"image": None}, CARD, Learned())
    assert unconnected.device == pytest.approx(1000 + 110 + 120)  # an optional input not connected is 0
    for inputs, why in (
        ({}, "the size of input image is not known"),
        ({"image": {"width": 3}}, "width and height"),
    ):
        unknown = estimate(model, _values(m), inputs, CARD, Learned())
        assert unknown.device is None and unknown.basis == "unknown" and why in unknown.why


def test_a_formula_is_never_below_zero(tmp_path: Path) -> None:
    m = _load(tmp_path, working={"mb": 10, "terms": [{"coef": -1, "of": ["chunk_size"]}], "source": SOURCE})
    assert m.memory is not None
    got = estimate(m.memory, _values(m), {"image": None}, CARD, Learned())
    assert got.device == 3000 and got.working == 0


def test_a_correction_scales_working_memory_only(tmp_path: Path) -> None:
    m = _load(tmp_path)
    assert m.memory is not None
    learned = Learned(corrections={"cuda:8.6": 1.5})
    got = estimate(m.memory, _values(m), {"image": None}, CARD, learned)
    assert got.device == pytest.approx(3000 + 3296.576 * 1.5) and got.basis == "corrected"
    assert got.working == pytest.approx(3296.576)  # the uncorrected estimate, which learning compares with
    other_card = estimate(m.memory, _values(m), {"image": None}, replace(CARD, kind="cuda:6.1"), learned)
    assert other_card.basis == "known"


def test_an_unknown_estimate_uses_the_peak_measured_for_those_settings(tmp_path: Path) -> None:
    m = _load(tmp_path, working={"mb": None, "source": "not measured yet"})
    assert m.memory is not None
    values = _values(m)
    inputs = {"image": {"width": 640, "height": 480}}
    learned = Learned(peaks={("cuda:8.6", settings_hash(values, inputs)): 5000})
    got = estimate(m.memory, values, inputs, CARD, learned)
    assert got.device == 5000 and got.basis == "measured"
    bigger = {"image": {"width": 4096, "height": 4096}}
    assert estimate(m.memory, values, bigger, CARD, learned).basis == "unknown"


# -- margins and settings ------------------------------------------------------------------------


def test_the_margin_is_1_5_gib_or_a_tenth_of_the_device() -> None:
    assert margin(Settings(), "cuda", 4295) == 1611
    assert margin(Settings(), "cuda", 25770) == pytest.approx(2577)
    assert margin(Settings(), "cpu", None) == 1611
    assert margin(Settings(margin_mb={"cuda": 500, "cpu": None}), "cuda", 85899) == 500


def test_settings_are_read_from_the_data_root(tmp_path: Path) -> None:
    assert read_settings(tmp_path) == Settings()
    assert read_settings(None) == Settings()
    (tmp_path / "settings.json").write_text(
        json.dumps({"memory": {"margin_mb": {"cuda": 800}, "never_reduce_quality": True, "fit": "off"}}),
        encoding="utf-8",
    )
    got = read_settings(tmp_path)
    assert got.margin_mb == {"cuda": 800, "cpu": None} and got.never_reduce_quality and got.fit == "off"
    assert not got.notes


def test_settings_that_are_not_understood_stay_at_their_defaults_and_say_so(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {"memory": {"margin_mb": {"cuda": -1, "mps": 3}, "never_reduce_quality": "yes", "fit": "auto"}}
        ),
        encoding="utf-8",
    )
    got = read_settings(tmp_path)
    assert got.margin_mb == {"cuda": None, "cpu": None} and not got.never_reduce_quality and got.fit == "on"
    assert len(got.notes) == 4
    path.write_text("{not json", encoding="utf-8")
    assert read_settings(tmp_path).notes and read_settings(tmp_path).fit == "on"


# -- the fit (AC2) -------------------------------------------------------------------------------


def _target(profile: dict[str, Any]) -> Target:
    """The card the runtime's plan picks: the one with the most memory (runtimes._card)."""
    gpus = profile["gpus"]
    return Target(card=max(gpus, key=lambda g: g["vram_total_mb"])["index"] if gpus else None)


def _fit(
    tmp_path: Path,
    profile: dict[str, Any] | str,
    *,
    explicit: dict[str, Any] | None = None,
    settings: Settings | None = None,
    learned: Learned | None = None,
    after: Fit | None = None,
    model: bool = True,
    **memory: Any,
) -> Fit:
    m = _load(tmp_path, **memory)
    machine = machines.get(profile) if isinstance(profile, str) else profile
    return fit(
        m.memory if model else None,
        _values(m, **(explicit or {})),
        set(explicit or {}),
        {"image": None},
        machine,
        _target(machine),
        learned or Learned(),
        settings or Settings(),
        m.devices,
        after,
    )


# machine, device, changes, upgrades, alternative's changes (or None), card budget, system budget
TABLE = [
    ("no GPU, 32 GB", "cpu", (), (0,), None, None, 20564),
    ("4 GB, compute 5.2, 16 GB", "cpu", (), (), None, 2289, 8282),
    ("4 GB, compute 7.5, 16 GB", "cpu", (), (), [0, 1, 2, 3], 2289, 8282),
    ("4 GB, compute 7.5, 8 GB", "cuda", (0, 1, 2, 3), (), None, 2289, 3389),
    ("6 GB, compute 7.5", "cuda", (0, 1), (), None, 4389, 8282),
    ("8 GB, compute 6.1, 1.5 GB held", "cuda", (0,), (), None, 5389, 8282),
    ("12 GB, compute 8.6", "cuda", (), (), None, 10389, 20564),
    ("24 GB, compute 8.9", "cuda", (), (0,), None, 21923, 43128),
    ("80 GB, compute 9.0", "cuda", (), (0,), None, 75410, 172512),
    ("two cards, 8 GB and 24 GB", "cuda", (), (0,), None, 21923, 43128),
]


@pytest.mark.parametrize(("name", "device", "changes", "upgrades", "alternative", "card", "system"), TABLE)
def test_ac2_the_test_table(
    tmp_path: Path,
    name: str,
    device: str,
    changes: tuple[int, ...],
    upgrades: tuple[int, ...],
    alternative: list[int] | None,
    card: int | None,
    system: int,
) -> None:
    got = _fit(tmp_path, name)
    assert got.outcome == "fits", got.message
    assert (got.device, got.applied, got.upgrades) == (device, changes, upgrades)
    assert got.reduced == any(i >= 2 for i in changes)  # changes 3 and 4 cost quality
    rows = got.to_json()["devices"]
    assert rows["cpu"]["budget"] == system
    assert (rows["cuda"]["budget"] if "cuda" in rows else None) == card
    if device == "cpu":  # off the node's first device: the slow warning, and the alternative if any
        assert got.warning is not None and got.warning["kind"] == "slow"
        assert got.warning["basis"] == "unknown" and got.warning["seconds"] is None
        found = got.warning["alternative"]
        assert (found and found["changes"]) == alternative
        if found:
            assert found["device"] == "cuda" and found["reduced"]
    else:
        assert got.warning is None


def test_ac2_the_two_card_machine_fits_on_the_card_its_runtime_was_planned_for(tmp_path: Path) -> None:
    got = _fit(tmp_path, "two cards, 8 GB and 24 GB")
    assert got.card == 1 and got.kind == "cuda:8.9"


def test_ac2_the_needs_of_the_table(tmp_path: Path) -> None:
    assert _fit(tmp_path, "8 GB, compute 6.1, 1.5 GB held").to_json()["needs"] == {
        "device": 4761,
        "system": 3000,
    }
    assert _fit(tmp_path, "24 GB, compute 8.9").to_json()["needs"] == {"device": 12441, "system": 3000}
    assert _fit(tmp_path, "4 GB, compute 7.5, 8 GB").to_json()["needs"] == {"device": 1959, "system": 1500}


def test_ac2_nothing_fits_on_two_gb_and_the_message_says_what_would(tmp_path: Path) -> None:
    got = _fit(tmp_path, "2 GB, compute 6.1, 4 GB")
    assert got.outcome == "memory" and got.device is None
    assert got.message.startswith(
        "Needs 3570 MB free on the card (1959 MB at the smallest settings plus a 1611 MB margin); "
        "1900 MB are free. Needs 5201 MB free in system memory for the processor (3590 MB, fp16 "
        "cannot run there, plus 1611 MB); 3000 MB are free."
    )
    assert "Closing other programs" in got.message


def test_ac2_a_setting_the_graph_sets_is_tried_anyway(tmp_path: Path) -> None:
    got = _fit(tmp_path, "2 GB, compute 6.1, 4 GB", explicit={"resolution": 1024})
    assert got.outcome == "tried_anyway" and got.device == "cuda" and got.applied == (0, 1, 2)
    assert got.warning is not None and got.warning["kind"] == "tried_anyway"
    assert {"change": 3, "why": "the graph sets resolution"} in got.skipped
    assert not mem.can_retry(got)


def test_ac2_a_chunk_size_the_graph_sets_moves_the_node_to_the_processor(tmp_path: Path) -> None:
    got = _fit(tmp_path, "8 GB, compute 6.1, 1.5 GB held", explicit={"chunk_size": 8192})
    assert (got.device, got.applied, got.upgrades) == ("cpu", (), ())
    assert {"change": 0, "why": "the graph sets chunk_size"} in got.skipped
    assert {"upgrade": 0, "why": "the graph sets chunk_size"} in got.skipped
    assert got.warning is not None and got.warning["kind"] == "slow"
    assert got.warning["alternative"] == {
        "device": "cuda",
        "changes": [2],
        "set": {"precision": "fp16"},
        "reduced": True,
    }


def test_ac2_a_precision_the_card_cannot_run_rules_the_card_out(tmp_path: Path) -> None:
    got = _fit(tmp_path, "8 GB, compute 6.1, 1.5 GB held", explicit={"precision": "bf16"})
    assert (got.device, got.values["precision"], got.applied) == ("cpu", "bf16", ())
    assert got.warning is not None and got.warning["kind"] == "slow" and got.warning["alternative"] is None


def test_ac2_system_memory_limits_a_cards_fit(tmp_path: Path) -> None:
    small = machines.with_system(machines.get("12 GB, compute 8.6"), 4000, 4295)
    got = _fit(tmp_path, small)
    assert (got.device, got.applied, got.reduced) == ("cuda", (0, 1, 2), True)


def test_ac2_an_upgrade_the_graph_blocks_is_skipped(tmp_path: Path) -> None:
    got = _fit(tmp_path, "24 GB, compute 8.9", explicit={"chunk_size": 8192})
    assert (got.device, got.applied, got.upgrades) == ("cuda", (), ())


def test_ac2_never_reduce_quality(tmp_path: Path) -> None:
    never = Settings(never_reduce_quality=True)
    got = _fit(tmp_path, "4 GB, compute 7.5, 8 GB", settings=never)
    assert got.outcome == "memory"
    got = _fit(tmp_path, "4 GB, compute 7.5, 16 GB", settings=never)
    assert got.device == "cpu" and got.warning is not None and got.warning["alternative"] is None


def test_ac2_fit_off_runs_at_the_values_on_the_first_device(tmp_path: Path) -> None:
    got = _fit(tmp_path, "8 GB, compute 6.1, 1.5 GB held", settings=Settings(fit="off"))
    assert (got.outcome, got.device, got.applied) == ("off", "cuda", ())
    assert got.budget == pytest.approx(5389)
    assert not mem.can_retry(got)


def test_ac2_a_learned_correction_changes_the_fit(tmp_path: Path) -> None:
    got = _fit(tmp_path, "8 GB, compute 6.1, 1.5 GB held", learned=Learned(corrections={"cuda:6.1": 1.5}))
    assert got.applied == (0, 1) and got.to_json()["estimate"] == "corrected"
    other = _fit(tmp_path, "8 GB, compute 6.1, 1.5 GB held", learned=Learned(corrections={"cuda:8.6": 1.5}))
    assert other.applied == (0,)


def test_ac2_a_node_without_a_model_runs_at_its_values_on_its_first_device(tmp_path: Path) -> None:
    got = _fit(tmp_path, "8 GB, compute 6.1, 1.5 GB held", model=False)
    assert (got.outcome, got.device, got.applied) == ("unfitted", "cuda", ())
    assert got.budget == pytest.approx(5389) and got.to_json()["estimate"] == "unknown"
    assert not mem.can_retry(got)
    off_card = _fit(tmp_path, "no GPU, 32 GB", model=False)
    assert off_card.device == "cpu" and off_card.warning is not None and off_card.warning["kind"] == "slow"


def test_ac2_renaming_every_card_changes_no_fit(tmp_path: Path) -> None:
    for name, profile in machines.MACHINES.items():
        assert _fit(tmp_path, profile).to_json() == _fit(tmp_path, machines.renamed(profile)).to_json(), name


def test_a_node_with_no_device_here_fails_with_the_reason(tmp_path: Path) -> None:
    data = _node(tmp_path)
    data["devices"] = ["cuda"]
    m = parse(data, tmp_path)
    got = fit(
        m.memory,
        _values(m),
        set(),
        {"image": None},
        machines.get("no GPU, 32 GB"),
        Target(),
        Learned(),
        Settings(),
        m.devices,
    )
    assert got.outcome == "memory" and "a card" in got.message


# -- unknowns --------------------------------------------------------------------------------------


def test_an_unknown_figure_ends_the_walk_where_it_is_reached(tmp_path: Path) -> None:
    got = _fit(
        tmp_path,
        "4 GB, compute 7.5, 8 GB",
        weights={"fp32": 3000, "fp16": None, "bf16": None, "source": "fp16 not measured yet"},
    )
    assert (got.outcome, got.device, got.applied) == ("unknown", "cuda", (0, 1, 2))
    assert got.warning is not None and "not known" in got.warning["why"]
    assert mem.can_retry(got)


def test_free_memory_that_cannot_be_read_ends_the_walk(tmp_path: Path) -> None:
    profile = machines.get("8 GB, compute 6.1, 1.5 GB held")
    profile["gpus"][0]["vram_free_mb"] = None
    got = _fit(tmp_path, profile)
    assert (got.outcome, got.device, got.applied) == ("unknown", "cuda", ())
    assert got.warning is not None and "could not be read" in got.warning["why"]


def test_system_memory_that_cannot_be_read_skips_the_cards_system_check(tmp_path: Path) -> None:
    profile = machines.get("12 GB, compute 8.6")
    profile["system"] = {"total_mb": None, "free_mb": None, "why": "unreadable"}
    got = _fit(tmp_path, profile)
    assert (got.outcome, got.device, got.applied) == ("fits", "cuda", ())
    assert any("System memory could not be read" in n for n in got.notes)


# -- the slower device's time --------------------------------------------------------------------


def test_the_slow_warning_says_what_its_time_is_based_on(tmp_path: Path) -> None:
    time = {"cpu": {"seconds": 240, "at": {"resolution": 1024}, "source": SOURCE}}
    published = _fit(tmp_path, "4 GB, compute 7.5, 16 GB", time=time)
    assert published.warning is not None
    assert (published.warning["seconds"], published.warning["basis"]) == (240, "published")
    m = _load(tmp_path, time=time)
    values = _values(m)
    learned = Learned(seconds={("cpu", settings_hash(values, {"image": None})): 95.5})
    measured = _fit(tmp_path, "4 GB, compute 7.5, 16 GB", learned=learned, time=time)
    assert measured.warning is not None
    assert (measured.warning["seconds"], measured.warning["basis"]) == (95.5, "measured")


# -- the retry after running out of memory -------------------------------------------------------


def test_a_retry_drops_the_upgrades(tmp_path: Path) -> None:
    first = _fit(tmp_path, "24 GB, compute 8.9")
    assert first.upgrades == (0,) and mem.can_retry(first)
    again = _fit(tmp_path, "24 GB, compute 8.9", after=first)
    assert (again.outcome, again.device, again.applied, again.upgrades) == ("fits", "cuda", (), ())


def test_a_retry_takes_at_least_the_next_change(tmp_path: Path) -> None:
    first = _fit(tmp_path, "8 GB, compute 6.1, 1.5 GB held")
    assert first.applied == (0,)
    again = _fit(tmp_path, "8 GB, compute 6.1, 1.5 GB held", after=first)
    assert (again.device, again.applied) == ("cuda", (0, 1))


def test_a_retry_after_an_unknown_estimate_takes_the_next_change(tmp_path: Path) -> None:
    profile = machines.get("8 GB, compute 6.1, 1.5 GB held")
    profile["gpus"][0]["vram_free_mb"] = None
    first = _fit(tmp_path, profile)
    assert first.outcome == "unknown"
    again = _fit(tmp_path, profile, after=first)
    assert (again.outcome, again.applied) == ("unknown", (0,))


def test_a_retry_with_nothing_smaller_left_fails(tmp_path: Path) -> None:
    first = _fit(tmp_path, "4 GB, compute 7.5, 8 GB")
    assert first.applied == (0, 1, 2, 3)
    again = _fit(tmp_path, "4 GB, compute 7.5, 8 GB", after=first)
    assert again.outcome == "memory" and "Nothing smaller" in again.message


def test_a_retry_moves_on_to_the_next_device_when_the_first_has_no_change_left(tmp_path: Path) -> None:
    profile = machines.get("6 GB, compute 7.5")
    first = _fit(tmp_path, profile, settings=Settings(never_reduce_quality=True))
    assert (first.device, first.applied) == ("cuda", (0, 1))
    again = _fit(tmp_path, profile, settings=Settings(never_reduce_quality=True), after=first)
    assert (again.device, again.applied) == ("cpu", ())


# -- the graph remembers what it set -------------------------------------------------------------


def test_a_step_knows_which_params_the_graph_set(tmp_path: Path) -> None:
    m = _load(tmp_path)
    graph = Graph.from_json(
        {"version": 1, "nodes": {"n": {"node": m.id, "params": {"resolution": 512}}}, "edges": []}
    )
    step = plan(graph, Registry(nodes={m.id: m})).steps[0]
    assert step.explicit == frozenset({"resolution"})
    assert step.params["resolution"] == 512 and step.params["chunk_size"] == 8192


@pytest.mark.parametrize(
    "explicit",
    [
        {"resolution": 1024},
        {"resolution": 512},
        {"chunk_size": 8192},
        {"precision": "fp16"},
        {"precision": "fp32"},
    ],
)
def test_ac2_a_setting_the_graph_sets_is_never_changed_on_any_machine(
    tmp_path: Path, explicit: dict[str, Any]
) -> None:
    for name in machines.MACHINES:
        got = _fit(tmp_path, name, explicit=explicit)
        if got.outcome != "memory":
            for key, value in explicit.items():
                assert got.values[key] == value, name
        alternative = (got.warning or {}).get("alternative") or {}
        assert not set(alternative.get("set", {})) & set(explicit), name


def test_the_message_names_system_memory_when_it_is_what_blocks_the_card(tmp_path: Path) -> None:
    got = _fit(tmp_path, machines.with_system(machines.get("12 GB, compute 8.6"), 2500, 4295))
    assert got.outcome == "memory"
    assert (
        "Needs 3111 MB free in system memory to load its weights for the card (1500 MB plus a 1611 MB"
        in got.message
    )
