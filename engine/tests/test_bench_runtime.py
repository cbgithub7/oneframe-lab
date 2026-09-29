"""The runtime report, made for the tiny runtime: the same code the owner runs for a real one."""

from __future__ import annotations

from pathlib import Path

import pytest

from oneframe import bench_runtime


def test_the_bench_installs_a_runtime_runs_its_probe_and_writes_a_report(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path
) -> None:
    out = tmp_path / "report.md"
    code = bench_runtime.main(
        [
            "tiny",
            "--out",
            str(out),
            "--data",
            str(tmp_path / "data"),
            "--runtimes",
            str(tiny.parent),
            "--uv",
            uv_exe,
            "--uv-home",
            str(uv_home),
        ]
    )
    report = out.read_text(encoding="utf-8")
    assert code == 0, report
    for heading in ("## Machine", "## Plan", "## Install", "## Probe", "## Everything, as recorded"):
        assert heading in report
    assert "| cu130 |" in report and "| cpu |" in report  # every build considered
    assert '"python": "3.11.' in report  # the probe ran in the runtime's own Python
    assert '"tinyext": "stand-in"' in report and '"env": "on"' in report
    assert "nvidia-smi said:" in report


def test_the_bench_works_with_relative_paths(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    code = bench_runtime.main(
        [
            "tiny",
            "--out",
            "report.md",
            "--data",
            "data",
            "--runtimes",
            str(tiny.parent.relative_to(tmp_path)),
            "--uv",
            uv_exe,
            "--uv-home",
            str(uv_home),
        ]
    )
    assert code == 0, (tmp_path / "report.md").read_text(encoding="utf-8")


def test_a_runtime_that_does_not_exist_is_an_exit_code_not_a_crash(
    tmp_path: Path, tiny: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = bench_runtime.main(["nope", "--data", str(tmp_path), "--runtimes", str(tiny.parent)])
    assert code == 1
    assert "No runtime called 'nope' is defined." in capsys.readouterr().err
