from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import write_runtime

from oneframe import BUILTIN_NODES_DIR, ports
from oneframe.manifest import ManifestError, load, parse
from oneframe.registry import discover
from oneframe.runtimes.definition import discover as discover_runtimes


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


def test_run_load_is_refused_as_run_entry_is(tmp_path: Path) -> None:
    (tmp_path / "node.py").write_text("def load(ctx): pass\ndef run(ctx, state): pass\n", encoding="utf-8")

    def problems(run: dict[str, str]) -> list[str]:
        with pytest.raises(ManifestError) as info:
            parse(_manifest(run=run), tmp_path)
        return info.value.problems

    for bad in ("node.py", "../evil.py:load", str(tmp_path / "node.py") + ":load", "other.py:load"):
        as_entry = problems({"where": "engine", "entry": bad})
        as_load = problems({"where": "engine", "entry": "node.py:run", "load": bad})
        assert as_load and as_load == [p.replace("run.entry", "run.load") for p in as_entry], bad
    named = parse(
        _manifest(run={"where": "engine", "entry": "node.py:run", "load": "node.py:load"}), tmp_path
    )
    assert named.run.load == "node.py:load"
    assert parse(_manifest(), tmp_path).run.load is None


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


PROCESSOR = {"name": "cpu", "vendor": "none"}


def _without(key: str) -> dict[str, object]:
    return {k: v for k, v in _manifest().items() if k != key}


MEMORY = {
    "precisions": {"fp32": {"cuda_min_capability": None, "cpu": True}},
    "weights": {"fp32": 100, "source": "measured"},
    "working": {"mb": 10, "source": "measured"},
}


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        pytest.param(_without("version"), "version should be a non-empty string", id="no version"),
        pytest.param(_without("title"), "title should be a non-empty string", id="no title"),
        pytest.param(_manifest(summary=5), "summary should be text", id="summary a number"),
        pytest.param(_manifest(devices={"cuda": True}), "devices should be a list", id="devices an object"),
        pytest.param(_manifest(devices={}), "devices should be a list", id="devices an empty object"),
        pytest.param(_manifest(devices=5), "devices should be a list", id="devices a number"),
        pytest.param(
            _manifest(run={"where": "runtime", "runtime": 5}),
            "run.runtime should be text",
            id="run.runtime a number",
        ),
        pytest.param(_manifest(run=["node.py:run"]), "run should be an object", id="run a list"),
        pytest.param(_manifest(licence="MIT"), "licence should be an object", id="licence text"),
        pytest.param(_manifest(licence=""), "licence should be an object", id="licence empty text"),
        pytest.param(
            _manifest(links=["https://example.com"]), "links should be an object", id="links a list"
        ),
        pytest.param(
            _manifest(params={"steps": {"type": "int", "default": 1, "min": "0"}}),
            "params.steps.min should be a number",
            id="a param's min text",
        ),
        pytest.param(
            _manifest(params={"size": {"type": "choice", "choices": 5}}),
            "params.size.choices should be a list",
            id="a param's choices a number",
        ),
        pytest.param(_manifest(inputs={"a": 5}), "inputs.a: A port should be a type", id="a port a number"),
        pytest.param(
            _manifest(memory={**MEMORY, "changes": [{"set": {"precision": []}, "costs": "quality"}]}),
            "sets precision [], which the model does not list",
            id="a change's precision a list",
        ),
        pytest.param(
            ("runtime.json", b'{"id": "bad", "title": "Caf\xe9"}'), "cannot read", id="runtime.json not utf-8"
        ),
        pytest.param(("runtime.json", b"[" * 100_000), "cannot read", id="runtime.json nested too deep"),
        pytest.param(
            ("pyproject.toml", b"\xff\xfe"), "pyproject.toml: cannot read", id="pyproject.toml not utf-8"
        ),
        pytest.param(
            ("pyproject.toml", b"tool = 5\n"),
            "package = false is missing",
            id="pyproject.toml's tool a number",
        ),
        pytest.param(
            ("uv.lock", b"version = 1\npackage = [5]\n"),
            "package should be a list of tables",
            id="uv.lock's packages not tables",
        ),
    ],
)
def test_a_malformed_definition_is_a_problem_never_a_crash(
    tmp_path: Path, data: dict[str, object] | tuple[str, bytes], needle: str
) -> None:
    if isinstance(data, tuple):  # a file of a runtime's definition, beside a good runtime
        for name in ("good", "bad"):
            write_runtime(tmp_path, {"id": name, "title": name, "python": "3.12", "builds": [PROCESSOR]})
        filename, written = data
        (tmp_path / "bad" / filename).write_bytes(written)
        found = discover_runtimes([tmp_path])
        good, problems = list(found.runtimes), found.problems
        assert good == ["good"]
    else:
        for name, manifest in {"good": _manifest(id="good.node"), "bad": {**data, "id": "bad.node"}}.items():
            folder = tmp_path / name
            folder.mkdir()
            (folder / "node.json").write_text(json.dumps(manifest), encoding="utf-8")
            (folder / "node.py").write_text("def run(ctx): pass\n", encoding="utf-8")
        registry = discover([tmp_path])
        good, problems = list(registry.nodes), registry.problems
        assert good == ["good.node"]
    [(path, listed)] = problems.items()
    assert Path(path).parent.name == "bad"
    assert any(needle in p for p in listed), listed


def test_blank_optional_text_is_read_as_written(tmp_path: Path) -> None:
    """Only a field of the wrong type is refused: a blank summary or runtime loads, as before."""
    (tmp_path / "node.py").write_text("def run(ctx): pass\n", encoding="utf-8")
    m = parse(_manifest(summary="", run={"where": "engine", "runtime": ""}), tmp_path)
    assert m.summary == "" and m.run.runtime == "" and m.run.file == "node.py"
    with pytest.raises(ManifestError, match=r"run\.runtime is required"):
        parse(_manifest(run={"where": "runtime", "runtime": ""}), tmp_path)


def test_every_builtin_node_loads() -> None:
    reg = discover([BUILTIN_NODES_DIR])
    assert reg.problems == {}
    assert {"source.image", "convert.depth_to_points"} <= set(reg.nodes)
    for path in BUILTIN_NODES_DIR.glob("*/node.json"):
        assert load(path).id == path.parent.name, "a node's folder is named after its id"
