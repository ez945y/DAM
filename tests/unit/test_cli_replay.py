"""End-to-end tests for `dam replay` (summary + --through-guards)."""

from __future__ import annotations

import json

import pytest

from dam.cli import main

mcap_writer = pytest.importorskip("mcap.writer")

STACK = "examples/stackfiles/manipulation_safe.yaml"  # has joint_position_limits


def _write_session(path, cycles):
    """cycles: list of (cycle_id, joint_positions, recorded_decision)."""
    from mcap.writer import Writer

    with open(path, "wb") as fh:
        w = Writer(fh)
        w.start()
        sid = w.register_schema(name="json", encoding="jsonschema", data=b"{}")
        chans = {
            t: w.register_channel(topic=t, message_encoding="json", schema_id=sid)
            for t in ("/dam/obs", "/dam/action", "/dam/cycle")
        }

        def msg(topic, cid, payload):
            payload = {"cycle_id": cid, "timestamp": float(cid), **payload}
            w.add_message(
                chans[topic],
                log_time=cid * 1_000_000,
                data=json.dumps(payload).encode(),
                publish_time=cid * 1_000_000,
                sequence=cid,
            )

        for cid, jp, decision in cycles:
            msg("/dam/obs", cid, {"joint_positions": jp})
            msg("/dam/action", cid, {"target_positions": jp})
            msg(
                "/dam/cycle",
                cid,
                {
                    "has_violation": decision == "REJECT",
                    "has_clamp": decision == "CLAMP",
                    "active_task": "collaborative_pick_place",
                },
            )
        w.finish()


class TestReplaySummary:
    def test_summary_runs(self, tmp_path, capsys):
        p = tmp_path / "s.mcap"
        _write_session(p, [(0, [0.0] * 6, "PASS"), (1, [0.0] * 6, "PASS")])
        rc = main(["replay", str(p)])
        out = capsys.readouterr().out
        assert rc == 0
        assert "session :" in out
        assert "cycles  :" in out


class TestReplayThroughGuards:
    def test_requires_stack(self, tmp_path, capsys):
        p = tmp_path / "s.mcap"
        _write_session(p, [(0, [0.0] * 6, "PASS")])
        rc = main(["replay", "--through-guards", str(p)])
        assert rc == 1
        assert "--stack" in capsys.readouterr().err

    def test_missing_file(self, capsys):
        rc = main(["replay", "--through-guards", "/no/x.mcap", "--stack", STACK])
        assert rc == 1
        assert "no such file" in capsys.readouterr().err

    def test_detects_divergence(self, tmp_path, capsys):
        # cycle 0: in-limits, recorded PASS  -> replay PASS  (match)
        # cycle 1: way out of joint limits, recorded PASS -> replay CLAMP (divergence)
        p = tmp_path / "s.mcap"
        _write_session(
            p,
            [
                (0, [0.0] * 6, "PASS"),
                (1, [10.0, 0.0, 0.0, 0.0, 0.0, 0.0], "PASS"),
            ],
        )
        rc = main(["replay", "--through-guards", str(p), "--stack", STACK])
        out = capsys.readouterr().out
        assert rc == 0
        assert "cycles compared : 2" in out
        assert "divergences     : 1" in out
        assert "PASS" in out and "CLAMP" in out
        assert "reconstructed  :" in out
        assert "comparable     :" in out
        assert "degraded       :" in out
