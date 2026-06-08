"""Tests for the ``dam`` command-line interface."""

from __future__ import annotations

import json

import pytest

from dam.cli import main

VALID_STACK = "examples/stackfiles/demo.yaml"


class TestValidate:
    def test_valid_stack_returns_zero(self, capsys):
        rc = main(["validate", VALID_STACK])
        out = capsys.readouterr().out
        assert rc == 0
        assert "OK" in out
        assert "1/1 valid" in out

    def test_invalid_stack_returns_one(self, tmp_path, capsys):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{ this: is: : not valid yaml ][")
        rc = main(["validate", str(bad)])
        out = capsys.readouterr().out
        assert rc == 1
        assert "FAIL" in out

    def test_missing_file_returns_one(self, capsys):
        rc = main(["validate", "/no/such/stack.yaml"])
        assert rc == 1
        assert "FAIL" in capsys.readouterr().out

    def test_defaults_to_convention_file(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)  # empty dir, no .dam_stackfile.yaml
        rc = main(["validate"])
        out = capsys.readouterr().out
        assert rc == 1
        assert ".dam_stackfile.yaml" in out
        assert "FAIL" in out

    def test_mixed_reports_each_and_fails(self, capsys):
        rc = main(["validate", VALID_STACK, "/no/such.yaml"])
        out = capsys.readouterr().out
        assert rc == 1
        assert "OK" in out and "FAIL" in out
        assert "1/2 valid" in out


class TestCallbacks:
    def test_lists_all(self, capsys):
        rc = main(["callbacks"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "joint_position_limits" in out
        assert "L1" in out and "L3" in out
        assert "callback(s) total" in out

    def test_layer_filter(self, capsys):
        rc = main(["callbacks", "--layer", "L3"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "force_torque_limit" in out
        assert "joint_position_limits" not in out

    def test_json_output_is_parseable(self, capsys):
        rc = main(["callbacks", "--json"])
        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert isinstance(data, list)
        names = {c["name"] for c in data}
        assert {"ood_detector", "workspace"} <= names
        assert all({"name", "layer", "description"} <= set(c) for c in data)

    def test_force_torque_is_l3_not_l2(self, capsys):
        """Regression: the reclassified callback must appear under L3."""
        main(["callbacks", "--json"])
        data = json.loads(capsys.readouterr().out)
        entry = next(c for c in data if c["name"] == "force_torque_limit")
        assert entry["layer"] == "L3"


class TestHelp:
    def test_help_no_topic(self, capsys):
        rc = main(["help"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "usage: dam" in out
        assert "validate" in out and "callbacks" in out

    def test_help_subcommand(self, capsys):
        rc = main(["help", "validate"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "usage: dam validate" in out

    def test_help_unknown_topic(self, capsys):
        rc = main(["help", "nope"])
        assert rc == 1
        assert "unknown command" in capsys.readouterr().err

    def test_no_args_prints_help_and_fails(self, capsys):
        rc = main([])
        assert rc == 1
        assert "usage: dam" in capsys.readouterr().out

    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0
        assert "dam" in capsys.readouterr().out


class TestReplay:
    def test_missing_file_returns_one(self, capsys):
        rc = main(["replay", "/no/such/session.mcap"])
        assert rc == 1
        assert "no such file" in capsys.readouterr().err


class TestRun:
    def test_bad_stack_returns_one(self, capsys):
        rc = main(["run", "/no/such/stack.yaml"])
        assert rc == 1
        assert "dam run:" in capsys.readouterr().err


class TestDoctor:
    def test_doctor_runs(self, capsys):
        rc = main(["doctor"])
        out = capsys.readouterr().out
        assert rc in (0, 1)
        assert "python" in out
        assert "dam_rs" in out
        # core 'dam' is always importable in the test process
        assert "OK    dam" in out


class TestInspect:
    def test_inspect_valid(self, capsys):
        rc = main(["inspect", VALID_STACK])
        out = capsys.readouterr().out
        assert rc == 0
        assert "boundaries" in out
        assert "tasks" in out
        assert "fallbacks" in out
        assert "guards" in out

    def test_inspect_missing_returns_one(self, capsys):
        rc = main(["inspect", "/no/such/stack.yaml"])
        assert rc == 1
        assert "dam inspect:" in capsys.readouterr().err

    def test_inspect_defaults_to_convention_file(self, tmp_path, monkeypatch, capsys):
        import pathlib
        import shutil

        src = pathlib.Path(VALID_STACK).resolve()
        monkeypatch.chdir(tmp_path)
        shutil.copy(src, tmp_path / ".dam_stackfile.yaml")
        rc = main(["inspect"])
        out = capsys.readouterr().out
        assert rc == 0
        assert ".dam_stackfile.yaml" in out
        assert "boundaries" in out

    def test_inspect_reports_boundary_layers(self, capsys):
        rc = main(["inspect", "examples/stackfiles/so101.yaml"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "task_gripper_sequence" in out
        assert "[L3 single]" in out  # hardware health boundaries at L3
