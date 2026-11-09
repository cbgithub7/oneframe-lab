from __future__ import annotations

from pathlib import Path

from conftest import MakeEngine, call

from oneframe import inputs
from oneframe.files import sha256_file
from oneframe.layout import Layout


def test_an_import_is_kept_once_by_content(tmp_path: Path) -> None:
    layout = Layout(tmp_path / "data")
    photo = b"\xff\xd8 a photo's bytes"
    one, two = tmp_path / "Pictures" / "beach.JPG", tmp_path / "Copies" / "beach copy.jpg"
    for path in (one, two):
        path.parent.mkdir(parents=True)
        path.write_bytes(photo)

    first, name = inputs.import_file(layout, one)
    second, other = inputs.import_file(layout, two)

    assert first == second == sha256_file(one)
    assert (name, other) == ("beach.JPG", "beach copy.jpg")
    kept = sorted(layout.inputs.iterdir())
    assert [p.name for p in kept] == [f"{first}.jpg"]
    assert kept[0].read_bytes() == photo
    assert inputs.find(layout.inputs, first) == kept[0]
    assert inputs.find(layout.inputs, "0" * 64) is None
    assert inputs.find(layout.inputs, str(one)) is None


def test_the_main_process_imports_through_the_engine(tmp_path: Path, make_engine: MakeEngine) -> None:
    engine = make_engine(tmp_path / "data", [], lambda _event: None)
    photo = tmp_path / "photo.png"
    photo.write_bytes(b"png bytes")
    answer = call(engine, "inputs.import", path=str(photo))["result"]
    assert answer == {"id": sha256_file(photo), "name": "photo.png"}
    for bad in (str(tmp_path / "missing.png"), "photo.png"):
        refused = call(engine, "inputs.import", path=bad)["error"]
        assert (refused["kind"], refused["reason"]) == ("request", "bad_params"), bad
