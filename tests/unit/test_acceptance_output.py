"""Evidence tools must not turn untrusted output paths into arbitrary writes."""

import argparse
from pathlib import Path

import pytest

from scripts.acceptance.runtime_stability import output_directory


def test_output_outside_acceptance_root_is_rejected(tmp_path):
    with pytest.raises(argparse.ArgumentTypeError, match="inside"):
        output_directory(str(tmp_path / "outside"))
    assert not (tmp_path / "outside").exists()


def test_parent_traversal_is_rejected():
    with pytest.raises(argparse.ArgumentTypeError):
        output_directory("logs/acceptance-l5/../../outside")


def test_symlink_escape_is_rejected(tmp_path):
    root = Path(__file__).resolve().parents[2] / "logs" / "acceptance-l5"
    root.mkdir(parents=True, exist_ok=True)
    link = root / ("escape-" + tmp_path.name)
    link.symlink_to(tmp_path, target_is_directory=True)
    try:
        with pytest.raises(argparse.ArgumentTypeError):
            output_directory(str(link / "outside"))
    finally:
        link.unlink()
    assert not (tmp_path / "outside").exists()


def test_valid_path_is_resolved_without_creating_files():
    root = Path(__file__).resolve().parents[2] / "logs" / "acceptance-l5"
    assert output_directory(str(root / "new-run")) == root / "new-run"


def test_existing_evidence_is_not_overwritten(monkeypatch):
    import sys
    from tempfile import TemporaryDirectory

    from scripts.acceptance.runtime_stability import main

    root = Path(__file__).resolve().parents[2] / "logs" / "acceptance-l5"
    with TemporaryDirectory(dir=root) as directory:
        evidence = Path(directory) / "fast.json"
        evidence.write_text("original evidence")
        monkeypatch.setattr(
            sys, "argv", ["stability", "benchmark", "--probe", "--output", directory]
        )
        with pytest.raises(SystemExit) as error:
            main()
        assert error.value.code == 2
        assert evidence.read_text() == "original evidence"
