"""Spec 003: the files a node reads, declared in its manifest (AC1). A manifest that breaks any rule
of requirement 1 is refused with every problem named at once."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from fixtures import fit_node

from oneframe.manifest import ManifestError, parse
from oneframe.models.schema import unsafe_path

COMMIT = "0123456789abcdef0123456789abcdef01234567"
SHA = "ab" * 32


def _file(name: str, **fields: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"name": name, "hf": "Ruicheng/moge-3-vitl", "revision": COMMIT, "path": "model.pt"}
    row.update(size=1000, sha256=SHA, format="pickle")
    row.update(fields)
    return {k: v for k, v in row.items() if v is not None}


def _node(tmp_path: Path, **fields: Any) -> dict[str, Any]:
    (tmp_path / "node.py").write_text("def run(ctx):\n    pass\n", encoding="utf-8")
    data: dict[str, Any] = {
        "id": "test.files",
        "version": "1",
        "title": "Reads files",
        "category": "test",
        "outputs": {"depth": "Depth[kind=metric,measure=z]"},
        "params": {
            "checkpoint": {"type": "choice", "choices": ["vitl", "vitg"], "default": "vitl"},
            "chunk": {"type": "choice", "choices": ["small", "big"], "default": "small", "affects": "speed"},
        },
        "run": {"where": "runtime", "runtime": "torch", "entry": "node.py:run"},
    }
    data.update(fields)
    return data


def _problems(tmp_path: Path, data: dict[str, Any]) -> list[str]:
    with pytest.raises(ManifestError) as caught:
        parse(data, tmp_path)
    return caught.value.problems


def test_a_pinned_manifest_gives_each_file_its_source_and_checkpoints(tmp_path: Path) -> None:
    url = "https://ml-site.cdn-apple.com/models/depth-pro/depth_pro.pt"
    data = _node(
        tmp_path,
        files_by="checkpoint",
        files=[
            _file("config", path="sub dir/config.json", format="other"),
            _file("vitl", checkpoints=["vitl"]),
            _file(
                "vitg", hf="Ruicheng/moge-3-vitg", checkpoints=["vitg"], gated="https://huggingface.co/x/y"
            ),
            _file("apple", hf=None, revision=None, path=None, url=url),
        ],
    )
    manifest = parse(data, tmp_path)
    assert manifest.files_by == "checkpoint" and manifest.default_checkpoint == "vitl"
    assert [f.name for f in manifest.files_for("vitl")] == ["config", "vitl", "apple"]
    assert [f.name for f in manifest.files_for("vitg")] == ["config", "vitg", "apple"]
    config, _vitl, vitg, apple = manifest.files
    endpoint = "https://hub.example"
    assert (
        config.source(endpoint) == f"{endpoint}/Ruicheng/moge-3-vitl/resolve/{COMMIT}/sub%20dir/config.json"
    )
    assert (vitg.gated, apple.source(endpoint), apple.repo) == ("https://huggingface.co/x/y", url, None)


def test_every_broken_rule_is_named_at_once(tmp_path: Path) -> None:
    data = _node(
        tmp_path,
        files_by="chunk",  # affects speed only, so every checkpoint would share one cache key
        files=[
            _file("branch", revision="main"),
            _file("size", size=-1),
            _file("sha", sha256="ABC"),
            _file("plain", hf=None, revision=None, path=None, url="http://example.com/model.pt"),
            _file("absolute", path="/etc/model.pt"),
            _file("parent", path="weights/../../model.pt"),
            _file("device", path="weights/con.pt"),
            _file("dot", path="weights./model.pt"),
            _file("space", path="model.pt "),
            _file("colon", path="weights:model.pt"),
            _file("upper", path="Model.pt"),
            _file("checkpoint", checkpoints=["huge"]),
            _file("twice", path="a.pt"),
            _file("twice", path="b.pt"),
            _file("format", format="pt"),
        ],
    )
    problems = _problems(tmp_path, data)
    expected = [
        "files_by 'chunk' must affect output",
        "files.branch.revision should be a full commit hash, not a branch or a tag",
        "files.size.size should be the file's size in bytes",
        "files.sha.sha256 should be 64 lower-case hex digits",
        "files.plain.url should be an https URL",
        "files.absolute.path '/etc/model.pt' is absolute",
        "files.parent.path 'weights/../../model.pt' steps out of its folder",
        "files.device.path 'weights/con.pt' has 'con.pt', a name Windows keeps for a device",
        "files.dot.path 'weights./model.pt' has 'weights.', which ends with a dot or a space",
        "files.space.path 'model.pt ' has 'model.pt ', which ends with a dot or a space",
        "files.colon.path 'weights:model.pt' holds a ':'",
        "files.upper: 'Model.pt' and 'model.pt' in Ruicheng/moge-3-vitl differ only in case",
        "files.checkpoint.checkpoints: 'huge' is not a choice of chunk",
        "files: two files are named 'twice'",
        "files.format.format should be one of safetensors, pickle, other",
    ]
    for needle in expected:
        assert any(needle in p for p in problems), (needle, problems)
    assert len(problems) == len(expected)


def test_checkpoints_need_files_by_and_files_need_a_runtime(tmp_path: Path) -> None:
    problems = _problems(
        tmp_path,
        _node(
            tmp_path,
            files=[_file("vitl", checkpoints=["vitl"])],
            run={"where": "engine", "entry": "node.py:run"},
        ),
    )
    assert problems == [
        "files: a node that runs in the engine cannot read files; run it in a runtime",
        "files.vitl.checkpoints needs files_by, the choice param they are values of",
    ]


def test_files_by_names_a_choice_with_a_default_and_never_precision(tmp_path: Path) -> None:
    for files_by, needle in [
        ("precision", "files_by cannot be precision"),
        ("missing", "files_by 'missing' should name one of the node's choice params"),
        ("nodefault", "files_by 'nodefault' needs a default"),
    ]:
        data = _node(tmp_path, files_by=files_by, files=[_file("w")])
        data["params"]["nodefault"] = {"type": "choice", "choices": ["a", "b"]}
        assert any(needle in p for p in _problems(tmp_path, data)), files_by


def test_the_memory_models_checkpoint_param_is_the_files_one_and_affects_output(tmp_path: Path) -> None:
    model = copy.deepcopy(fit_node.TEST_MODEL)
    model["weights_by"] = "chunk"
    model["weights"] = {
        "small": {"fp32": 1000, "fp16": 500, "bf16": 500},
        "big": {"fp32": 3000, "fp16": 1500, "bf16": 1500},
        "source": fit_node.SOURCE,
    }
    model["changes"] = model["upgrades"] = []
    params = {**fit_node.PARAMS, **_node(tmp_path)["params"]}
    data = _node(tmp_path, params=params, memory=model, devices=["cuda", "cpu"], files=[_file("w")])
    problems = _problems(tmp_path, data)
    assert problems == [
        "memory.weights_by 'chunk' must affect output: each checkpoint gives other results, so it is part "
        "of the cache key"
    ]
    data["files_by"] = "checkpoint"
    problems = _problems(tmp_path, data)
    assert problems[0] == "files_by 'checkpoint' and memory.weights_by 'chunk' should name the same param"


@pytest.mark.parametrize(
    ("path", "why"),
    [
        ("model.pt", None),
        ("sub/dir/model.safetensors", None),
        ("", "is empty"),
        ("a//b", "has an empty step"),
        ("a\\b", "uses a backslash; paths in a repository use /"),
        ("C:/model.pt", "is absolute"),
        ("NUL", "has 'NUL', a name Windows keeps for a device"),
        ("lpt¹.txt", "has 'lpt¹.txt', a name Windows keeps for a device"),
        ("a?b", "has 'a?b', with a character Windows refuses"),
    ],
)
def test_unsafe_paths(path: str, why: str | None) -> None:
    assert unsafe_path(path) == why
