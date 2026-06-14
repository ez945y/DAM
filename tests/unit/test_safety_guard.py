"""Tests for dam.Guardrail, dam.guardrail(), and dam.GuardrailProcessorStep."""

from __future__ import annotations

import contextlib
import textwrap
from pathlib import Path

import numpy as np
import pytest

import dam
from dam.api import Guardrail, guardrail

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_JOINT_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]


def _call(guard: Guardrail, action: object, obs: object) -> object:
    """Adapt the old ``guard(action, obs)`` shape to the dict API: a dict obs is
    a set of named groups, an array obs is the joint vector."""
    if isinstance(obs, dict):
        return guard({**obs, "action": action})
    return guard({"joints": obs, "action": action})


class FakeKinematicsSolver:
    def __init__(self) -> None:
        self.last_target_ee_pose = None
        self.last_current_joints = None
        self.ik_calls = 0
        self.fk_calls = 0

    def inverse_kinematics(
        self,
        target_ee_pose: np.ndarray,
        current_joint_positions: np.ndarray,
    ) -> np.ndarray:
        self.ik_calls += 1
        self.last_target_ee_pose = target_ee_pose.copy()
        self.last_current_joints = current_joint_positions.copy()
        joints = np.zeros(len(_JOINT_NAMES), dtype=np.float64)
        joints[0] = target_ee_pose[0]
        return joints

    def forward_kinematics(self, joint_positions: np.ndarray) -> np.ndarray:
        self.fk_calls += 1
        return np.array(
            [joint_positions[0], 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
            dtype=np.float64,
        )


def enable_ee_layout(guard: Guardrail, solver_name: str = "kinematics") -> Guardrail:
    guard._action_layout = [{"name": "arm", "type": "ee_pose", "solver": solver_name}]
    guard._refresh_action_spec()
    return guard


@pytest.fixture()
def stackfile(tmp_path: Path) -> str:
    """Minimal stackfile with known joint limits (±1.5 rad ≈ ±86°)."""
    path = tmp_path / "safety.yaml"
    path.write_text(
        textwrap.dedent("""\
        version: "1"
        guards:
          - L1: motion
        boundaries:
          safe_joints:
            layer: L1
            type: single
            nodes:
              - callback: joint_position_limits
                params:
                  upper: [1.5, 1.5, 1.5, 1.5, 1.5, 1.5]
                  lower: [-1.5, -1.5, -1.5, -1.5, -1.5, -1.5]
        tasks:
          default:
            description: "test"
            boundaries: [safe_joints]
        safety:
          control_hz: 30
          enforcement_mode: enforce
        """)
    )
    return str(path)


@pytest.fixture()
def ee_stackfile(tmp_path: Path) -> str:
    """A command-space (EE-pose) stackfile — no joint-space guard, so the
    EE-pose action is not joint-clamped."""
    path = tmp_path / "ee_safety.yaml"
    path.write_text(
        textwrap.dedent("""\
        version: "1"
        guards:
          - L1: motion
        boundaries: {}
        tasks:
          default:
            boundaries: []
        safety:
          control_hz: 30
          enforcement_mode: enforce
        """)
    )
    return str(path)


# ---------------------------------------------------------------------------
# TestGuardrail — joint-space arm validation
# ---------------------------------------------------------------------------


class TestGuardrail:
    def test_init_loads_runtime(self, stackfile: str) -> None:
        guard = Guardrail(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        assert guard.runtime is not None
        assert guard._n_joints == 6

    def test_call_dict_roundtrip(self, stackfile: str) -> None:
        guard = Guardrail(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        action = {f"{n}.pos": 0.5 for n in _JOINT_NAMES}
        obs = {f"{n}.pos": 0.4 for n in _JOINT_NAMES}
        result = _call(guard, action, obs)
        assert isinstance(result, dict)
        assert set(result.keys()) == {f"{n}.pos" for n in _JOINT_NAMES}

    def test_call_ndarray_roundtrip(self, stackfile: str) -> None:
        guard = Guardrail(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        action = np.array([0.5, -0.3, 0.8, 0.1, -0.2, 0.6])
        obs = np.array([0.4, -0.2, 0.7, 0.0, -0.1, 0.5])
        result = _call(guard, action, obs)
        assert isinstance(result, np.ndarray)
        assert result.shape == (6,)

    def test_clamps_out_of_bounds(self, stackfile: str) -> None:
        guard = Guardrail(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        action = np.array([2.0, -2.0, 0.5, 0.5, 0.5, 0.5])
        obs = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        result = _call(guard, action, obs)
        assert isinstance(result, np.ndarray)
        # Joints 0,1 should be clamped to ±1.5 (QP may add tiny perturbation)
        assert result[0] <= 1.5 + 1e-3
        assert result[1] >= -1.5 - 1e-3
        # Joints 2-5 should pass through (within limits)
        np.testing.assert_allclose(result[2:], [0.5, 0.5, 0.5, 0.5], atol=1e-3)

    def test_safe_action_within_limits(self, stackfile: str) -> None:
        guard = Guardrail(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        action = np.array([0.3, -0.3, 0.5, 0.1, -0.2, 0.6])
        obs = np.array([0.2, -0.2, 0.4, 0.0, -0.1, 0.5])
        result = _call(guard, action, obs)
        np.testing.assert_allclose(result, action, atol=1e-6)

    def test_last_results_populated(self, stackfile: str) -> None:
        guard = Guardrail(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        action = np.array([0.5] * 6)
        obs = np.array([0.4] * 6)
        _call(guard, action, obs)
        assert len(guard.last_results) > 0

    def test_degrees_mode(self, stackfile: str) -> None:
        """With degrees_mode=True, input/output are degrees but validation uses radians."""
        guard = Guardrail(stackfile, joint_names=_JOINT_NAMES, degrees_mode=True, quiet=True)
        # 85° ≈ 1.48 rad → within ±1.5 rad limit
        action = np.array([85.0, -85.0, 30.0, 10.0, -10.0, 30.0])
        obs = np.array([80.0, -80.0, 25.0, 5.0, -5.0, 25.0])
        result = _call(guard, action, obs)
        assert isinstance(result, np.ndarray)
        np.testing.assert_allclose(result, action, atol=0.5)

    def test_degrees_mode_clamps(self, stackfile: str) -> None:
        """Action at 100° ≈ 1.74 rad should be clamped to ~1.5 rad ≈ 85.9°."""
        guard = Guardrail(stackfile, joint_names=_JOINT_NAMES, degrees_mode=True, quiet=True)
        action = np.array([100.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        obs = np.array([0.0] * 6)
        result = _call(guard, action, obs)
        assert result[0] < 90.0

    def test_missing_joint_names_raises(self, stackfile: str) -> None:
        with pytest.raises(ValueError, match="joint_names"):
            Guardrail(stackfile, quiet=True)  # no hardware.preset, no explicit joint_names

    def test_missing_action_key_raises(self, stackfile: str) -> None:
        guard = Guardrail(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        with pytest.raises(KeyError, match="action"):
            guard({"joints": np.zeros(6)})

    def test_stateful_velocity_estimation(self, stackfile: str) -> None:
        guard = Guardrail(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        obs1 = np.array([0.0] * 6)
        obs2 = np.array([0.1] * 6)
        action = np.array([0.5] * 6)
        _call(guard, action, obs1)
        _call(guard, action, obs2)
        assert guard._prev_positions is not None

    def test_action_layout_defaults_to_empty(self, stackfile: str) -> None:
        guard = Guardrail(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        assert guard.action_layout == []

    def test_action_layout_reads_hardware_config(self, tmp_path: Path) -> None:
        path = tmp_path / "ee_cfg.yaml"
        path.write_text(
            textwrap.dedent("""\
            version: "1"
            hardware:
              preset: so101_follower
              action_layout:
                - name: arm
                  type: ee_pose
                  solver: kinematics
            guards:
              - L1: motion
            boundaries: {}
            tasks:
              default:
                boundaries: []
            """)
        )
        guard = Guardrail(str(path), joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        assert guard.action_layout == [{"name": "arm", "type": "ee_pose", "solver": "kinematics"}]


# ---------------------------------------------------------------------------
# TestEEActionLayout — command-space (EE-pose) action handling
# ---------------------------------------------------------------------------


class TestEEActionLayout:
    def test_layout_action_does_not_require_solver(self, ee_stackfile: str) -> None:
        guard = enable_ee_layout(
            Guardrail(ee_stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        )
        action = np.array([0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
        result = _call(guard, action, np.zeros(6))
        np.testing.assert_allclose(result, action)

    def test_layout_action_does_not_call_solver_automatically(self, ee_stackfile: str) -> None:
        solver = FakeKinematicsSolver()
        guard = enable_ee_layout(
            Guardrail(
                ee_stackfile,
                joint_names=_JOINT_NAMES,
                degrees_mode=False,
                solvers={"kinematics": solver},
                quiet=True,
            )
        )
        action_ee = np.array([2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0])
        result = _call(guard, action_ee, np.zeros(6))
        assert isinstance(result, np.ndarray)
        assert result.shape == (7,)
        np.testing.assert_allclose(result, action_ee)
        assert solver.ik_calls == 0
        assert solver.fk_calls == 0

    def test_layout_action_exposes_solver_and_segments_to_callback(self, tmp_path: Path) -> None:
        solver_type = f"fake_kinematics_{id(self)}"
        callback_name = f"inspect_layout_{id(self)}"
        seen: dict[str, object] = {}

        @dam.solver_factory(solver_type, capabilities=["kinematics"])
        def factory():
            return FakeKinematicsSolver()

        dam.register_callback(
            callback_name,
            lambda action, action_layout, solvers: (
                seen.update(
                    {
                        "raw_action": action.metadata["raw_action"].copy(),
                        "segments": {
                            key: value.copy() if hasattr(value, "copy") else value
                            for key, value in action.metadata["action_segments"].items()
                        },
                        "layout": action_layout,
                        "solvers": solvers,
                    }
                )
                or True
            ),
            layer="L1",
        )
        path = tmp_path / "solver_safety.yaml"
        path.write_text(
            textwrap.dedent(f"""\
            version: "1"
            hardware:
              action_layout:
                - name: arm
                  type: ee_pose
                  solver: arm
            solvers:
              arm:
                type: {solver_type}
            guards:
              - L1: motion
            boundaries:
              inspect:
                layer: L1
                type: single
                nodes:
                  - callback: {callback_name}
            tasks:
              default:
                boundaries: [inspect]
            """)
        )

        guard = Guardrail(str(path), joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        action = np.array([0.5, 0, 0, 0, 0, 0, 1.0])
        result = _call(guard, action, np.zeros(6))
        np.testing.assert_allclose(result, action)
        np.testing.assert_allclose(seen["raw_action"], action)
        assert "arm" in seen["segments"]
        np.testing.assert_allclose(seen["segments"]["arm"], action)
        assert seen["layout"] == [{"name": "arm", "type": "ee_pose", "solver": "arm"}]
        assert "arm" in seen["solvers"]

    def test_mixed_layout_segments_are_exposed(self, tmp_path: Path) -> None:
        callback_name = f"inspect_segments_{id(self)}"
        seen: dict[str, object] = {}
        dam.register_callback(
            callback_name,
            lambda action: (
                seen.update(
                    {
                        key: value.copy() if hasattr(value, "copy") else value
                        for key, value in action.metadata["action_segments"].items()
                    }
                )
                or True
            ),
            layer="L1",
        )
        path = tmp_path / "mixed_layout.yaml"
        path.write_text(
            textwrap.dedent(f"""\
            version: "1"
            hardware:
              action_layout:
                - name: arm
                  type: ee_pose
                  solver: arm
                - name: gripper
                  type: scalar
            guards:
              - L1: motion
            boundaries:
              inspect:
                layer: L1
                type: single
                nodes:
                  - callback: {callback_name}
            tasks:
              default:
                boundaries: [inspect]
            """)
        )

        guard = Guardrail(str(path), joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True)
        action = np.array([0.5, 0, 0, 0, 0, 0, 1.0, 0.25])
        result = _call(guard, action, np.zeros(6))
        np.testing.assert_allclose(result, action)
        np.testing.assert_allclose(seen["arm"], action[:7])
        np.testing.assert_allclose(seen["gripper"], action[7:])

    def test_ee_action_layout_preserves_tensor_dtype(self, ee_stackfile: str) -> None:
        torch = pytest.importorskip("torch")
        solver = FakeKinematicsSolver()
        guard = enable_ee_layout(
            Guardrail(
                ee_stackfile,
                joint_names=_JOINT_NAMES,
                degrees_mode=False,
                solvers={"kinematics": solver},
                quiet=True,
            )
        )
        action_ee = torch.tensor([0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], dtype=torch.float32)
        obs_joints = torch.zeros(6, dtype=torch.float32)
        result = guard({"joints": obs_joints, "action": action_ee})
        assert result.dtype == torch.float32
        assert result.device == action_ee.device
        assert tuple(result.shape) == (7,)

    def test_layout_dict_action_passthrough(self, ee_stackfile: str) -> None:
        guard = enable_ee_layout(
            Guardrail(
                ee_stackfile,
                joint_names=_JOINT_NAMES,
                degrees_mode=False,
                solvers={"kinematics": FakeKinematicsSolver()},
                quiet=True,
            )
        )
        result = guard({"joints": np.zeros(6), "action": {"x": 0.1}})
        assert result == {"x": 0.1}


# ---------------------------------------------------------------------------
# TestGuardrailOneLiner — the dam.guardrail() convenience
# ---------------------------------------------------------------------------


class TestGuardrailOneLiner:
    def test_returns_same_type_dict(self, stackfile: str) -> None:
        action = {f"{n}.pos": 0.5 for n in _JOINT_NAMES}
        obs = {f"{n}.pos": 0.4 for n in _JOINT_NAMES}
        result = guardrail(
            {**obs, "action": action},
            stackfile,
            joint_names=_JOINT_NAMES,
            degrees_mode=False,
        )
        assert isinstance(result, dict)
        assert set(result.keys()) == set(action.keys())

    def test_returns_same_type_array(self, stackfile: str) -> None:
        action = np.array([0.5] * 6)
        obs = np.array([0.4] * 6)
        result = guardrail(
            {"joints": obs, "action": action},
            stackfile,
            joint_names=_JOINT_NAMES,
            degrees_mode=False,
        )
        assert isinstance(result, np.ndarray)
        assert result.shape == action.shape

    def test_missing_stackfile_raises(self) -> None:
        with pytest.raises((FileNotFoundError, OSError)):
            guardrail(
                {"joints": np.zeros(6), "action": np.zeros(6)},
                "/nonexistent/safety.yaml",
                joint_names=_JOINT_NAMES,
            )


# ---------------------------------------------------------------------------
# TestGuardrailProcessorStep — lerobot integration
# ---------------------------------------------------------------------------

try:
    from lerobot.processor.pipeline import RobotActionProcessorStep

    _HAS_LEROBOT = True
except ImportError:
    _HAS_LEROBOT = False


def _transition(obs: object, action: object) -> dict:
    return {
        "observation": obs,
        "action": action,
        "reward": None,
        "done": None,
        "truncated": None,
        "info": None,
        "complementary_data": None,
    }


@pytest.mark.skipif(not _HAS_LEROBOT, reason="lerobot not installed")
class TestGuardrailProcessorStep:
    def test_inherits_base(self) -> None:
        from dam.processor import GuardrailProcessorStep

        assert issubclass(GuardrailProcessorStep, RobotActionProcessorStep)

    def test_lazy_init(self, stackfile: str) -> None:
        from dam.processor import GuardrailProcessorStep

        step = GuardrailProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        assert step._guard is None

    def test_action_with_transition(self, stackfile: str) -> None:
        from dam.processor import GuardrailProcessorStep

        step = GuardrailProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        action = {f"{n}.pos": 0.5 for n in _JOINT_NAMES}
        obs = {f"{n}.pos": 0.4 for n in _JOINT_NAMES}
        result = step(_transition(obs, action))
        assert isinstance(result["action"], dict)
        assert step._guard is not None

    def test_passthrough_without_obs(self, stackfile: str) -> None:
        from dam.processor import GuardrailProcessorStep

        step = GuardrailProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        action = {f"{n}.pos": 0.5 for n in _JOINT_NAMES}
        result = step(_transition(None, action))
        assert result["action"] == action

    def test_reset_clears_guard(self, stackfile: str) -> None:
        from dam.processor import GuardrailProcessorStep

        step = GuardrailProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        action = {f"{n}.pos": 0.5 for n in _JOINT_NAMES}
        obs = {f"{n}.pos": 0.4 for n in _JOINT_NAMES}
        step(_transition(obs, action))
        assert step._guard is not None
        step.reset()
        assert step._guard is None

    def test_get_config(self, stackfile: str) -> None:
        from dam.processor import GuardrailProcessorStep

        step = GuardrailProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        config = step.get_config()
        assert config["stackfile"] == stackfile
        assert config["degrees_mode"] is False

    def test_transform_features_passthrough(self, stackfile: str) -> None:
        from dam.processor import GuardrailProcessorStep

        step = GuardrailProcessorStep(stackfile, joint_names=_JOINT_NAMES, degrees_mode=False)
        features = {"action": {"joint": {"shape": (6,)}}}
        assert step.transform_features(features) is features

    def test_stats_track_clamps(self, stackfile: str) -> None:
        from dam.processor import GuardrailProcessorStep

        step = GuardrailProcessorStep(
            stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True
        )
        obs = {f"{n}.pos": 0.0 for n in _JOINT_NAMES}
        safe_action = {f"{n}.pos": 0.1 for n in _JOINT_NAMES}
        dangerous_action = {f"{n}.pos": 3.0 for n in _JOINT_NAMES}
        step(_transition(obs, safe_action))
        step(_transition(obs, dangerous_action))
        step(_transition(obs, safe_action))
        assert step._stats.total_cycles == 3
        assert step._stats.clamps >= 1  # dangerous action should be clamped

    def test_stats_summary_lines(self, stackfile: str) -> None:
        from dam.processor import GuardrailProcessorStep

        step = GuardrailProcessorStep(
            stackfile, joint_names=_JOINT_NAMES, degrees_mode=False, quiet=True
        )
        obs = {f"{n}.pos": 0.0 for n in _JOINT_NAMES}
        dangerous = {f"{n}.pos": 3.0 for n in _JOINT_NAMES}
        step(_transition(obs, dangerous))
        lines = step._stats.summary_lines()
        assert any("1 cycles" in line or "clamp" in line.lower() for line in lines)


# ---------------------------------------------------------------------------
# TestRecordingStats — unit tests for stats tracker
# ---------------------------------------------------------------------------


class TestRecordingStats:
    def test_empty_stats(self) -> None:
        from dam.processor import _RecordingStats

        stats = _RecordingStats()
        lines = stats.summary_lines()
        assert any("0 cycles" in line for line in lines)

    def test_no_interventions_message(self) -> None:
        from dam.processor import _RecordingStats

        stats = _RecordingStats()
        stats.total_cycles = 100
        lines = stats.summary_lines()
        assert any("No guard interventions" in line for line in lines)

    def test_boundary_ranking(self) -> None:
        from dam.processor import _RecordingStats
        from dam.types.result import GuardDecision, GuardResult

        stats = _RecordingStats()
        for _ in range(5):
            stats.record_cycle(
                [
                    GuardResult(
                        decision=GuardDecision.CLAMP,
                        guard_name="velocity",
                        layer="L1",
                        reason="too fast",
                    ),
                ]
            )
        for _ in range(2):
            stats.record_cycle(
                [
                    GuardResult(
                        decision=GuardDecision.CLAMP,
                        guard_name="position",
                        layer="L1",
                        reason="out of bounds",
                    ),
                ]
            )

        assert stats.clamps == 7
        assert stats.boundary_counts["velocity"] == 5
        assert stats.boundary_counts["position"] == 2

        lines = stats.summary_lines()
        top_line = [line for line in lines if "Top boundaries" in line]
        assert len(top_line) == 1
        assert "velocity (5)" in top_line[0]


# ---------------------------------------------------------------------------
# TestEdgePrinter — only prints transitions, not sustained events
# ---------------------------------------------------------------------------


class TestEdgePrinter:
    def test_only_prints_on_start(self, capsys: pytest.CaptureFixture[str]) -> None:
        from dam.processor import _EdgePrinter
        from dam.types.result import GuardDecision, GuardResult

        printer = _EdgePrinter()
        clamp = [
            GuardResult(
                decision=GuardDecision.CLAMP,
                guard_name="vel",
                layer="L1",
                reason="fast",
            )
        ]

        printer.update(clamp)  # first call — should print
        printer.update(clamp)  # sustained — should NOT print again
        printer.update(clamp)

        captured = capsys.readouterr()
        assert captured.err.count("CLAMP") == 1

    def test_prints_resolved_with_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        from dam.processor import _EdgePrinter
        from dam.types.result import GuardDecision, GuardResult

        printer = _EdgePrinter()
        clamp = [
            GuardResult(
                decision=GuardDecision.CLAMP,
                guard_name="vel",
                layer="L1",
                reason="fast",
            )
        ]

        printer.update(clamp)
        printer.update(clamp)
        printer.update(clamp)
        printer.update([])  # clamp ends

        captured = capsys.readouterr()
        assert "resolved" in captured.err
        assert "3 cycles" in captured.err

    def test_single_cycle_clamp_no_resolved(self, capsys: pytest.CaptureFixture[str]) -> None:
        from dam.processor import _EdgePrinter
        from dam.types.result import GuardDecision, GuardResult

        printer = _EdgePrinter()
        clamp = [
            GuardResult(
                decision=GuardDecision.CLAMP,
                guard_name="vel",
                layer="L1",
                reason="fast",
            )
        ]

        printer.update(clamp)
        printer.update([])  # clamp ends after 1 cycle

        captured = capsys.readouterr()
        assert "CLAMP" in captured.err
        assert "resolved" not in captured.err  # only 1 cycle, no "resolved" message


class TestRecordingStatsStartTime:
    def test_time_starts_at_first_cycle(self) -> None:
        from dam.processor import _RecordingStats

        stats = _RecordingStats()
        assert stats._first_cycle_time is None
        stats.record_cycle([])
        assert stats._first_cycle_time is not None


# ---------------------------------------------------------------------------
# TestDecoupledMobileBase — obs (pose) and action (command) are different spaces
# ---------------------------------------------------------------------------


class TestDecoupledMobileBase:
    """A mobile base observes a pose [x, y, yaw] but commands a twist [v, omega];
    the callback reads each by key."""

    def _stackfile(self, tmp_path: Path) -> str:
        path = tmp_path / "mobile_safety.yaml"
        path.write_text(
            textwrap.dedent(
                """
                version: "1"
                hardware: { preset: jetbot_diff_drive }
                safety: { control_hz: 60, enforcement_mode: enforce }
                guards: [ { L1: motion, phase: 0 } ]
                boundaries:
                  forward_only:
                    layer: L1
                    type: single
                    nodes:
                      - callback: mobile_x_forward_only
                        params: { dt: 0.1 }
                tasks: { default: { boundaries: [forward_only] } }
                """
            ).strip()
        )
        return str(path)

    @staticmethod
    def _register_callback() -> None:
        with contextlib.suppress(ValueError):

            @dam.callback("mobile_x_forward_only", layer="L1")
            def _cb(*, base_pose, action, dt=0.1):  # noqa: ANN001, ANN202
                x, _y, yaw = (float(v) for v in base_pose)
                v, _omega = (float(a) for a in action)
                x_next = x + v * np.cos(yaw) * dt
                return bool(x_next >= 0.0)

    def _guard(self, tmp_path: Path) -> Guardrail:
        self._register_callback()
        return Guardrail(self._stackfile(tmp_path), safe_action=[0.0, 0.0], quiet=True)

    def test_contract_lists_base_pose(self, tmp_path: Path) -> None:
        guard = self._guard(tmp_path)
        assert guard.required_obs_keys == {"base_pose"}

    def test_safe_command_passes_through(self, tmp_path: Path) -> None:
        guard = self._guard(tmp_path)
        out = guard({"base_pose": [0.2, 0.0, 0.0], "action": np.array([0.5, 0.1])})
        np.testing.assert_allclose(out, [0.5, 0.1])
        assert out.shape == (2,)  # action space, not the 3-D obs space

    def test_rejected_command_returns_safe_action(self, tmp_path: Path) -> None:
        guard = self._guard(tmp_path)
        out = guard({"base_pose": [0.0, 0.0, 0.0], "action": np.array([-1.0, 0.0])})
        np.testing.assert_allclose(out, [0.0, 0.0])  # safe_action, not the obs

    def test_missing_base_pose_fails_fast(self, tmp_path: Path) -> None:
        guard = self._guard(tmp_path)
        with pytest.raises(KeyError, match="base_pose"):
            guard({"action": np.array([0.5, 0.1])})

    def test_command_is_not_degree_scaled(self, tmp_path: Path) -> None:
        guard = self._guard(tmp_path)
        out = guard({"base_pose": [0.5, 0.0, 0.0], "action": np.array([1.0, 2.0])})
        np.testing.assert_allclose(out, [1.0, 2.0])

    def test_torch_roundtrip_preserves_command_shape(self, tmp_path: Path) -> None:
        torch = pytest.importorskip("torch")
        guard = self._guard(tmp_path)
        out = guard({"base_pose": [0.2, 0.0, 0.0], "action": torch.tensor([0.5, 0.1])})
        assert out.shape == (2,)
        np.testing.assert_allclose(out.numpy(), [0.5, 0.1])
