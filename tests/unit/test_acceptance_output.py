"""Acceptance tools create exclusive evidence paths without CLI path input."""

import sys
from pathlib import Path

import pytest

from scripts.acceptance import runtime_stability


def fake_script(monkeypatch, tmp_path):
    script = tmp_path / "repo" / "scripts" / "acceptance" / "runtime_stability.py"
    script.parent.mkdir(parents=True)
    script.touch()
    monkeypatch.setattr(runtime_stability, "__file__", str(script))
    return script.parents[2] / "logs" / "acceptance-l5"


def test_runs_use_unique_folders_and_preserve_existing_evidence(monkeypatch, tmp_path):
    root = fake_script(monkeypatch, tmp_path)
    first = runtime_stability.create_evidence_directory()
    (first / "fast.json").write_text("original evidence")
    second = runtime_stability.create_evidence_directory()
    assert first != second and first.parent == root and second.parent == root
    assert (first / "fast.json").read_text() == "original evidence"


def test_symlinked_evidence_root_cannot_escape(monkeypatch, tmp_path):
    root = fake_script(monkeypatch, tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    root.parent.mkdir(parents=True)
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        runtime_stability.create_evidence_directory()
    assert list(outside.iterdir()) == []


def test_cli_cannot_request_an_arbitrary_output(monkeypatch, tmp_path):
    fake_script(monkeypatch, tmp_path)
    output = tmp_path / "outside"
    monkeypatch.setattr(sys, "argv", ["stability", "benchmark", "--output", str(output)])
    with pytest.raises(SystemExit) as error:
        runtime_stability.main()
    assert error.value.code == 2
    assert not output.exists()
