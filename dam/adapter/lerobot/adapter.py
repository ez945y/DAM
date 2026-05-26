"""LeRobotAdapter — unified Read+Write hardware adapter for lerobot robots.

Bridges a lerobot robot to DAM as both a ``SensorAdapter`` and an
``ActionAdapter``. One class covers all read/write paths so the runtime
factory can use the same instance for the source AND the sink when they
share a physical bus, or separate instances when they don't.

Modern lerobot API (``get_observation()`` returns ``{"joint.pos": deg, …}``)
and legacy (``capture_observation()`` returning state tensors) are both
supported. DAM internally uses **radians**; the adapter converts when
``degrees_mode=True``.

Observation channels (temperature, current, voltage, …) are driven by the
stackfile: only declared channels are sync_read from the bus. Declared
health channels (current/temperature/voltage) additionally populate
``obs.metadata["hardware_status"]`` for L3 ``HardwareGuard``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

_DEG2RAD = float(np.pi / 180.0)
_RAD2DEG = float(180.0 / np.pi)

from dam.adapter.base import ActionAdapter, SensorAdapter
from dam.types.action import ValidatedAction
from dam.types.observation import Observation

# Feetech STS3215 servo extended register map.
# Stackfile observation-channel name → (bus register name, unit conversion divisor).
# Divisor of 1 means raw value; 1000 means mA→A; 10 means 0.1V→V.
STS3215_REGISTER_MAP: dict[str, tuple[str, float]] = {
    "current": ("Present_Current", 1000.0),
    "temperature": ("Present_Temperature", 1.0),
    "load": ("Present_Load", 1.0),
    "voltage": ("Present_Voltage", 10.0),
    "velocity": ("Present_Velocity", 1.0),
}

logger = logging.getLogger(__name__)

_DEFAULT_JOINT_NAMES: list[str] = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]


class LeRobotAdapter(SensorAdapter, ActionAdapter):
    """Unified adapter for lerobot robots (SO-ARM101, Koch, …).

    Implements both ``SensorAdapter`` (read positions / cameras / declared
    telemetry channels) and ``ActionAdapter`` (send motor commands).
    """

    # SO-101 arm joints in pinocchio order (excludes gripper).
    _ARM_JOINT_NAMES: list[str] = [
        "shoulder_pan",
        "shoulder_lift",
        "elbow_flex",
        "wrist_flex",
        "wrist_roll",
    ]

    # Declared-channel → (hardware_status list-key, scalar-key). Channels
    # not in this map (velocity, load, …) still populate obs.channels but
    # don't surface in hardware_status — HardwareGuard skips those checks.
    _HEALTH_STATUS_KEYS: dict[str, tuple[str, str | None]] = {
        "current": ("currents", "current_a"),
        "temperature": ("temperatures", "temperature_c"),
        "voltage": ("voltages", None),
    }

    def __init__(
        self,
        robot: Any,
        joint_names: list[str] | None = None,
        degrees_mode: bool = True,
        obs_hz: float = 50.0,
        urdf_path: str | None = None,
    ) -> None:
        self._robot = robot
        self._joint_names: list[str] = joint_names or list(_DEFAULT_JOINT_NAMES)
        self._degrees_mode = degrees_mode
        self._obs_hz = obs_hz

        # Vectorised unit-conversion scale, bound once at init.  ALL joints
        # — including the gripper — share the same deg↔rad conversion: the
        # gripper servo on so100/so101 reports a degree-like angle, and the
        # framework's default joint_position_limits encode the gripper as
        # ``1.75 rad ≈ 100°``.  An earlier "gripper exception" left gripper
        # raw while limits were converted, which exploded ratios at J6 — that
        # special-case is gone.  Hot path: positions = raw * _pos_scale.
        scale_in = _DEG2RAD if degrees_mode else 1.0
        scale_out = _RAD2DEG if degrees_mode else 1.0
        self._pos_scale_in = np.full(len(self._joint_names), scale_in, dtype=np.float64)
        self._pos_scale_out = np.full(len(self._joint_names), scale_out, dtype=np.float64)

        # Sensor cache — read()'s exception path returns these so consumers
        # don't see a sudden gap on a transient bus glitch.
        self._prev_positions: np.ndarray | None = None
        self._prev_velocities: np.ndarray | None = None
        self._prev_ee_pose: np.ndarray | None = None
        self._prev_time: float | None = None

        # Sink state
        self._last_action: ValidatedAction | None = None

        self._connected = False

        self._register_map = STS3215_REGISTER_MAP
        self._observation_channels: list[str] = []

        # Per-cycle latency breakdown, logged at INFO once per second.
        self._lat: dict[str, float] = {}
        self._lat_log_t: float = 0.0

        # Pinocchio FK + Jacobian (sentinel "unavailable" until init succeeds).
        from dam.types.dynamics import DynamicsContext

        self._pin_model = None
        self._pin_data = None
        self._pin_ee_frame_id: int | None = None
        self._dynamics: DynamicsContext = DynamicsContext.unavailable()
        if urdf_path is not None:
            self._init_pinocchio(urdf_path)

    # ── Pinocchio FK setup ─────────────────────────────────────────────────

    def _init_pinocchio(self, urdf_path: str) -> None:
        """Load URDF and build a reduced pinocchio model for the 5 arm joints."""
        from dam.types.dynamics import DynamicsContext

        try:
            import pinocchio as pin

            full_model = pin.buildModelFromUrdf(urdf_path)
            all_names = [full_model.names[i] for i in range(1, full_model.njoints)]
            lock_ids = [
                full_model.getJointId(n) for n in all_names if n not in self._ARM_JOINT_NAMES
            ]
            q_ref = pin.neutral(full_model)
            self._pin_model = pin.buildReducedModel(full_model, lock_ids, q_ref)
            self._pin_data = self._pin_model.createData()
            self._pin_ee_frame_id = self._pin_model.getFrameId("gripper_link")
            self._dynamics = DynamicsContext(
                model=self._pin_model,
                data=self._pin_data,
                joint_names=list(self._ARM_JOINT_NAMES),
                frame_ids={"gripper_link": self._pin_ee_frame_id},
            )
            logger.info("LeRobotAdapter: pinocchio FK initialised from %s", urdf_path)
        except Exception as exc:
            logger.warning("LeRobotAdapter: pinocchio FK unavailable — %s", exc)
            self._dynamics = DynamicsContext.unavailable()

    @property
    def dynamics(self) -> Any:
        """Shared FK/Jacobian context — exposed via injection pool to guards."""
        return self._dynamics

    def _compute_ee_pose(self, positions_rad: np.ndarray) -> np.ndarray | None:
        if not self._dynamics.available:
            return None
        try:
            import pinocchio as pin

            self._dynamics.update(positions_rad)
            o_mf = self._dynamics.frame_placement(self._pin_ee_frame_id)
            quat = pin.Quaternion(o_mf.rotation)
            return np.array(
                [*o_mf.translation, quat.x, quat.y, quat.z, quat.w],
                dtype=np.float64,
            )
        except Exception as exc:
            logger.debug("FK computation failed: %s", exc)
            return None

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def connect(self) -> None:
        if self._connected:
            return
        try:
            if hasattr(self._robot, "connect"):
                self._connect_without_calibration_prompt()
            self._connected = True
            self._prev_time = time.monotonic()
            logger.info(
                "LeRobotAdapter connected  joints=%s  degrees_mode=%s",
                self._joint_names,
                self._degrees_mode,
            )
        except Exception as e:
            err_msg = str(e).lower()
            if "already connected" in err_msg or "already open" in err_msg:
                self._connected = True
                self._prev_time = time.monotonic()
                logger.info("LeRobotAdapter: already connected, synchronizing state.")
            else:
                raise

    def _connect_without_calibration_prompt(self) -> None:
        """Connect LeRobot hardware without stdin prompts in the host process."""
        calibration = getattr(self._robot, "calibration", None)
        calibration_file = getattr(self._robot, "calibration_fpath", None)
        if calibration is not None and not calibration:
            location = f" at {calibration_file}" if calibration_file else ""
            raise RuntimeError(
                f"No saved LeRobot calibration file found{location}. "
                "Create one with the LeRobot calibration command before starting DAM."
            )

        if calibration and hasattr(self._robot, "bus") and hasattr(self._robot, "calibrate"):
            original_calibrate = self._robot.calibrate

            def apply_saved_calibration() -> None:
                logger.info(
                    "LeRobotAdapter: applying saved calibration without interactive prompt: %s",
                    calibration_file or "<configured calibration>",
                )
                self._robot.bus.write_calibration(calibration)

            self._robot.calibrate = apply_saved_calibration
            try:
                self._robot.connect()
            finally:
                self._robot.calibrate = original_calibrate
            return

        self._robot.connect()

    def disconnect(self) -> None:
        if not self._connected and self._robot is None:
            return
        if not self._connected:
            logger.debug("LeRobotAdapter.disconnect(): already disconnected")
            return
        self._connected = False
        if self._robot is not None:
            try:
                if hasattr(self._robot, "disconnect"):
                    self._robot.disconnect()
                elif hasattr(self._robot, "close"):
                    self._robot.close()
            except Exception as e:
                logger.debug("LeRobotAdapter: robot disconnect/close failed: %s", e)
        logger.info("LeRobotAdapter disconnected")

    def verify(self) -> None:
        """Verify cameras and motors are responsive before the control loop starts.

        Checks performed
        ----------------
        1. **Cameras** — reads one frame from every camera attached to the robot.
           A ``None`` frame or any exception is reported as a failure.
        2. **Motors** — calls ``robot.get_observation()`` to confirm all motors
           respond without errors.
        """
        errors: list[str] = []

        # NOTE: cameras are managed as peer-level DAM OpenCVSourceAdapter
        # instances (registered separately by the factory), NOT through
        # lerobot's robot.cameras.  Each camera adapter runs its own verify().
        # The legacy nested-cameras code path was removed.

        # Motor check
        try:
            obs = (
                self._robot.get_observation()
                if hasattr(self._robot, "get_observation")
                else self._robot.capture_observation()
            )
            if obs is None:
                errors.append("motors: get_observation() returned None")
        except Exception as exc:
            errors.append(f"motors: {exc}")

        if errors:
            bullet_list = "\n".join(f"  • {e}" for e in errors)
            raise RuntimeError(
                f"Hardware preflight check failed ({len(errors)} issue(s)):\n{bullet_list}\n"
                "Fix the above before starting the control loop."
            )

        logger.info("Hardware preflight check passed (motors OK).")

    def is_healthy(self) -> bool:
        return self._connected and self._robot is not None

    @property
    def camera_shapes(self) -> dict[str, tuple[int, int]]:
        # LeRobotAdapter no longer manages cameras — peer-level
        # OpenCVSourceAdapter instances expose their own ``camera_shapes``.
        return {}

    def supported_channels(self) -> set[str]:
        return set(self._register_map)

    def set_observation_channels(self, channels: list[str]) -> None:
        self._observation_channels = list(channels)

    # ── SensorAdapter: read ────────────────────────────────────────────────

    def read(self) -> Observation:
        try:
            t0 = time.perf_counter()
            if hasattr(self._robot, "get_observation"):
                raw = self._robot.get_observation()
            else:
                raw = self._robot.capture_observation()
            self._lat["get_observation"] = (time.perf_counter() - t0) * 1000.0

            obs = self._convert(raw)
            return obs
        except Exception as e:
            logger.error("LeRobotAdapter hardware read failure: %s", e)
            # Keep the *old* timestamp so watchdog staleness accumulates
            # across consecutive failures.  If no previous read succeeded,
            # use epoch-zero to trigger an immediate staleness reject.
            fail_ts = self._prev_time if self._prev_time is not None else 0.0
            return Observation(
                timestamp=fail_ts,
                joint_positions=self._prev_positions
                if self._prev_positions is not None
                else np.zeros(len(self._joint_names), dtype=np.float64),
                joint_velocities=self._prev_velocities,
                end_effector_pose=self._prev_ee_pose,
                images=None,
                metadata={
                    "read_failure": True,
                    "hardware_status": {"error_codes": [-1], "reason": f"Hardware read error: {e}"},
                },
            )

    def _convert(self, raw: dict[str, Any]) -> Observation:
        if any(k.endswith(".pos") for k in raw):
            return self._convert_named(raw)
        return self._convert_legacy(raw)

    def _convert_named(self, raw: dict[str, Any]) -> Observation:
        """Modern lerobot API: ``{joint.pos: deg, joint.vel: deg/s, …}``."""
        now = time.monotonic()

        # Joint positions — vectorised conversion via pre-bound scale array.
        raw_pos = np.fromiter(
            (float(raw.get(f"{n}.pos", 0.0)) for n in self._joint_names),
            dtype=np.float64,
            count=len(self._joint_names),
        )
        positions = raw_pos * self._pos_scale_in

        # Joint velocities — same scale (deg/s ↔ rad/s is the same ratio).
        has_vel = any(f"{n}.vel" in raw for n in self._joint_names)
        if has_vel:
            raw_vel = np.fromiter(
                (float(raw.get(f"{n}.vel", 0.0)) for n in self._joint_names),
                dtype=np.float64,
                count=len(self._joint_names),
            )
            velocities: np.ndarray | None = raw_vel * self._pos_scale_in
        else:
            velocities = self._estimate_velocity(positions, now)

        self._prev_positions = positions.copy()
        self._prev_time = now

        # Cameras are NOT read here — they are peer-level DAM
        # OpenCVSourceAdapter instances that publish frames to the shared
        # CameraFrameHub from their own capture threads.  The runtime merges
        # those frames into the observation downstream.
        images: dict[str, np.ndarray] | None = None

        ee_pose = self._compute_ee_pose(positions)
        self._prev_velocities = velocities.copy() if velocities is not None else None
        self._prev_ee_pose = ee_pose

        t_ch = time.perf_counter()
        channels, hw_status = self._read_observation_data()
        self._lat["channels"] = (time.perf_counter() - t_ch) * 1000.0

        metadata: dict[str, Any] = {}
        if hw_status:
            metadata["hardware_status"] = hw_status

        return Observation(
            timestamp=now,
            joint_positions=positions,
            joint_velocities=velocities,
            end_effector_pose=ee_pose,
            images=images,
            channels=channels,
            metadata=metadata,
        )

    def _convert_legacy(self, raw: dict[str, Any]) -> Observation:
        """Legacy lerobot API: ``{"observation.state": tensor, …}``."""
        now = time.monotonic()

        state = raw.get("observation.state", raw.get("state"))
        if state is None:
            raise KeyError(
                "LeRobot obs dict missing 'observation.state'. "
                "If using modern lerobot, ensure get_observation() is available."
            )
        positions = np.asarray(state, dtype=np.float64).flatten()

        vel_raw = raw.get("observation.velocity", raw.get("velocity"))
        if vel_raw is not None:
            velocities: np.ndarray | None = np.asarray(vel_raw, dtype=np.float64).flatten()
        else:
            velocities = self._estimate_velocity(positions, now)

        self._prev_positions = positions.copy()
        self._prev_time = now

        ee_raw = raw.get("observation.end_effector_pose")
        if ee_raw is not None:
            ee_pose: np.ndarray = np.asarray(ee_raw, dtype=np.float64).flatten()
        else:
            ee_pose = np.array([0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0], dtype=np.float64)

        images: dict[str, np.ndarray] | None = None
        for key, val in raw.items():
            if key.startswith("observation.images."):
                if images is None:
                    images = {}
                cam_name = key[len("observation.images.") :]
                images[cam_name] = np.asarray(val)

        return Observation(
            timestamp=now,
            joint_positions=positions,
            joint_velocities=velocities,
            end_effector_pose=ee_pose,
            images=images,
        )

    def _read_observation_data(
        self,
    ) -> tuple[dict[str, np.ndarray] | None, dict[str, Any] | None]:
        """Read each *declared* observation channel and populate two views.

        Stackfile drives which registers are touched: only channels in
        ``self._observation_channels`` are sync_read. Each declared health
        channel (current/temperature/voltage) additionally surfaces in
        ``hardware_status`` for L3 ``HardwareGuard``.
        """
        if not self._observation_channels:
            return None, None
        bus = getattr(self._robot, "bus", None)
        if bus is None:
            return None, None

        channels: dict[str, np.ndarray] = {}
        status: dict[str, Any] = {}

        for name in self._observation_channels:
            mapping = self._register_map.get(name)
            if mapping is None:
                continue
            register, divisor = mapping
            try:
                raw: dict[str, float] = bus.sync_read(register)
            except Exception as exc:  # noqa: BLE001 — telemetry is best-effort
                logger.debug("channel '%s' sync_read failed: %s", name, exc)
                continue
            if not raw:
                continue

            channels[name] = np.array(
                [raw[m] / divisor for m in sorted(raw)],
                dtype=np.float64,
            )

            health = self._HEALTH_STATUS_KEYS.get(name)
            if health is not None:
                list_key, scalar_key = health
                per_motor = {m: v / divisor for m, v in raw.items()}
                status[list_key] = per_motor
                if scalar_key:
                    status[scalar_key] = max(per_motor.values())

        return (channels or None, status or None)

    def _estimate_velocity(self, positions: np.ndarray, now: float) -> np.ndarray:
        if self._prev_positions is not None and self._prev_time is not None:
            dt = max(now - self._prev_time, 1e-9)
            return (positions - self._prev_positions) / dt
        return np.zeros_like(positions)

    # ── ActionAdapter: write ───────────────────────────────────────────────
    def _convert_action(self, action: ValidatedAction) -> dict[str, Any]:
        """Convert a validated action to a dictionary of actions."""
        positions = np.asarray(action.target_joint_positions, dtype=np.float64)
        n = min(len(positions), len(self._joint_names))
        # Vectorised conversion via the inverse scale bound at init.
        scaled = positions[:n] * self._pos_scale_out[:n]

        action_dict: dict[str, Any] = {
            f"{self._joint_names[i]}.pos": float(scaled[i]) for i in range(n)
        }

        if action.gripper_action is not None and "gripper" in self._joint_names:
            action_dict["gripper.pos"] = float(action.gripper_action)

        return action_dict

    def apply(self, action: ValidatedAction) -> None:
        """Send a validated joint-position command (rad → deg)."""
        self._last_action = action
        action_dict = self._convert_action(action)
        self._robot.send_action(action_dict)

    def write(self, action: ValidatedAction) -> None:
        """Deprecated alias for apply()."""
        self.apply(action)

    def emergency_stop(self) -> None:
        """Disable motor torque so joints go limp, then disconnect."""
        logger.error("LeRobotAdapter: EMERGENCY STOP — releasing motors")
        if hasattr(self._robot, "emergency_stop"):
            self._robot.emergency_stop()
        # Release torque on every motor bus so the arm goes limp.
        for bus in getattr(self._robot, "buses", {}).values():
            if hasattr(bus, "disable_torque"):
                try:
                    bus.disable_torque()
                except Exception as e:  # noqa: BLE001
                    logger.error("emergency_stop: disable_torque failed on %s: %s", bus, e)
        self.disconnect()

    @property
    def last_action(self) -> ValidatedAction | None:
        return self._last_action

    def get_hardware_status(self) -> dict[str, Any]:
        """ActionAdapter health snapshot (mirrors declared-channel reads)."""
        status: dict[str, Any] = {
            "connected": self._connected,
            "latency_ms": (time.monotonic() - self._prev_time) * 1000 if self._prev_time else 0,
        }
        if self._connected and hasattr(self._robot, "bus"):
            try:
                bus = self._robot.bus
                for name in self._observation_channels:
                    mapping = self._register_map.get(name)
                    health = self._HEALTH_STATUS_KEYS.get(name)
                    if mapping is None or health is None:
                        continue
                    register, divisor = mapping
                    raw = bus.sync_read(register)
                    if not raw:
                        continue
                    list_key, scalar_key = health
                    per_motor = {k: v / divisor for k, v in raw.items()}
                    status[list_key] = per_motor
                    if scalar_key:
                        status[scalar_key] = max(per_motor.values())
            except Exception as exc:  # noqa: BLE001 — best-effort
                logger.debug("get_hardware_status bus read failed: %s", exc)
        return status
