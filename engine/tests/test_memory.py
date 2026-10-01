"""Spec 002: the memory model in a manifest (AC1).

The test node is the plan's: weights of 3000 MB at fp32 and 1500 MB at fp16 and bf16, working
memory of 200 + 0.00025 × resolution² × bytes + 0.25 × chunk_size MB, two speed-only changes, two
quality changes and one upgrade. Its numbers are exact by construction, not measured."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from oneframe.cache import Cache
from oneframe.graph import Graph, plan
from oneframe.manifest import Manifest, ManifestError, parse
from oneframe.memory import Formula, Term
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
