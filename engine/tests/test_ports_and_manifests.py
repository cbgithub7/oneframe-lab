from __future__ import annotations

import json
from pathlib import Path

import pytest

from oneframe import BUILTIN_NODES_DIR, ports
from oneframe.manifest import ManifestError, load, parse
from oneframe.registry import discover


def test_every_port_type_parses_bare_and_its_facets_are_known() -> None:
    for name, ptype in ports.types().items():
        assert ports.parse(name).type == name
        for facet, values in ptype.facets.items():
            spec = ports.parse(f"{name}[{facet}={'|'.join(values)}]")
            assert spec.facets[facet] == values


def test_parse_reads_facets_and_alternatives() -> None:
    spec = ports.parse("Depth[kind=metric|scale_invariant, measure=z]")
    assert spec.type == "Depth"
    assert spec.facets == {"kind": ("metric", "scale_invariant"), "measure": ("z",)}
    assert spec.text() == "Depth[kind=metric|scale_invariant, measure=z]"


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("Dpeth", "Unknown port type"),
        ("Depth[colour=red]", "no facet"),
        ("Depth[kind=metres]", "cannot be metres"),
        ("Depth[kind]", "facet=value"),
        ("Depth[kind=metric, kind=scale_invariant]", "named twice"),
        ("Depth[kind=metric", "Cannot read"),
    ],
)
def test_parse_refuses_bad_specs_with_a_reason(text: str, needle: str) -> None:
    with pytest.raises(ports.PortError, match=needle):
        ports.parse(text)


def test_object_form_carries_optional_doc_and_trust() -> None:
    spec = ports.parse({"type": "Mask[kind=binary]", "optional": True, "doc": "d", "trust": "measured"})
    assert spec.optional and spec.doc == "d" and spec.trust == "measured"
    with pytest.raises(ports.PortError, match="trust"):
        ports.parse({"type": "Mask", "trust": "certain"})
    with pytest.raises(ports.PortError, match="Unknown port keys"):
        ports.parse({"type": "Mask", "colour": "red"})


def test_disparity_never_connects_to_a_metric_input() -> None:
    out = ports.parse("Depth[kind=affine_invariant_disparity, measure=z]")
    assert ports.mismatch(out, ports.parse("Depth[kind=metric]")) is not None
    assert ports.mismatch(out, ports.parse("Depth")) is None
    assert ports.mismatch(out, ports.parse("PointMap")) is not None


def test_an_open_facet_is_checked_on_the_value() -> None:
    open_out = ports.parse("Depth[measure=z]")  # kind decided at run time
    metric_in = ports.parse("Depth[kind=metric]")
    assert ports.mismatch(open_out, metric_in) is None
    assert ports.value_mismatch({"kind": "metric", "measure": "z"}, metric_in) is None
    assert "needs metric" in (ports.value_mismatch({"kind": "scale_invariant"}, metric_in) or "")
    assert "does not say" in (ports.value_mismatch({}, metric_in) or "")


def _manifest(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "x.node",
        "version": "1",
        "title": "X",
        "category": "convert",
        "inputs": {"a": "Depth"},
        "outputs": {"b": "PointMap"},
        "run": {"where": "engine", "entry": "node.py:run"},
    }
    base.update(over)
    return base


def test_manifest_problems_are_reported_together(tmp_path: Path) -> None:
    (tmp_path / "node.py").write_text("def run(ctx): pass\n", encoding="utf-8")
    bad = _manifest(
        id="Bad Id",
        category="magic",
        inputs={"Img": "Image", "b": "Nope"},
        outputs={},
        params={"steps": {"type": "int", "default": 500, "max": 100}, "mode": {"type": "choice"}},
    )
    with pytest.raises(ManifestError) as info:
        parse(bad, tmp_path)
    text = " | ".join(info.value.problems)
    for needle in (
        "id 'Bad Id'",
        "category 'magic'",
        "lower_snake_case",
        "Unknown port type",
        "at least one output",
        "outside [None, 100]",
        "no choices",
    ):
        assert needle in text


def test_manifest_entry_must_exist_and_stay_inside_the_folder(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match=r"not in"):
        parse(_manifest(), tmp_path)
    with pytest.raises(ManifestError, match="inside the node's folder"):
        parse(_manifest(run={"where": "engine", "entry": "../evil.py:run"}), tmp_path)
    with pytest.raises(ManifestError, match=r"run\.runtime is required"):
        parse(_manifest(run={"where": "runtime", "entry": "node.py:run"}), tmp_path)


def test_a_runtime_node_names_its_runtime_and_its_code_file(tmp_path: Path) -> None:
    (tmp_path / "adapter.py").write_text("def run(ctx): pass\n", encoding="utf-8")
    m = parse(_manifest(run={"where": "runtime", "runtime": "moge", "entry": "adapter.py:run"}), tmp_path)
    assert m.run.runtime == "moge" and m.run.file == "adapter.py"


def test_registry_keeps_good_nodes_when_one_is_broken(tmp_path: Path) -> None:
    for name, data in {"good": _manifest(id="good.node"), "bad": {"id": "bad.node"}}.items():
        folder = tmp_path / name
        folder.mkdir()
        (folder / "node.json").write_text(json.dumps(data), encoding="utf-8")
        (folder / "node.py").write_text("def run(ctx): pass\n", encoding="utf-8")
    reg = discover([tmp_path])
    assert list(reg.nodes) == ["good.node"]
    assert any("bad" in p for p in reg.problems)


def test_registry_refuses_a_duplicate_id(tmp_path: Path) -> None:
    for name in ("one", "two"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "node.json").write_text(json.dumps(_manifest()), encoding="utf-8")
        (folder / "node.py").write_text("def run(ctx): pass\n", encoding="utf-8")
    reg = discover([tmp_path])
    assert len(reg.nodes) == 1
    assert any("already used" in " ".join(v) for v in reg.problems.values())


def test_every_builtin_node_loads() -> None:
    reg = discover([BUILTIN_NODES_DIR])
    assert reg.problems == {}
    assert {"source.image", "convert.depth_to_points"} <= set(reg.nodes)
    for path in BUILTIN_NODES_DIR.glob("*/node.json"):
        assert load(path).id == path.parent.name, "a node's folder is named after its id"
