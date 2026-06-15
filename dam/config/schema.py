from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dam.types.enforcement import EnforcementMode

# ── Guard pipeline configs ─────────────────────────────────────────────────
# All guard-specific parameters (model paths, thresholds, joint_position_limits, …)
# live in boundary node ``params`` blocks and reach guards via the config pool.


# ── Boundary / task configs ────────────────────────────────────────────────


class NodeConfig(BaseModel):
    """Unified boundary node configuration.

    Only structural / routing fields live at the top level.
    All constraint parameters (max_speed, bounds, joint_position_limits, …)
    belong inside ``params`` and are forwarded as-is to the Guard / callback.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)
    node_id: str = "default"
    params: dict[str, Any] = {}
    callback: str | None = None
    fallback: str | None = None
    timeout_sec: float | None = None
    warn_frames: int = 1


class FallbackConfig(BaseModel):
    """Stackfile-defined fallback strategy.

    The map key is both the local name used by boundary nodes' ``fallback``
    field AND the builtin Context it resolves to (see
    ``dam.runtime.builtin_contexts``) — there is no separate ``type``. Optional
    auto-escalation: when this fallback has been active for
    ``escalate_after_seconds`` and the trigger hasn't cleared, the runtime
    pushes the ``escalate_to`` fallback on top of the stack. Useful for
    ambiguous triggers — e.g. high current is either heavy payload (clears under
    SlowDown) or collision (won't clear).
    """

    model_config = ConfigDict(extra="forbid")
    params: dict[str, Any] = {}
    severity: int | None = None
    requires_proposal: bool | None = None
    monitors_hardware: bool | None = None
    description: str | None = None
    escalate_to: str | None = None
    escalate_after_seconds: float | None = None


# Container type normalisation: accept both "single"/"list"/"graph"
# and "SingleNodeContainer"/"ListContainer"/"GraphContainer"
_CONTAINER_TYPE_MAP: dict[str, str] = {
    "single": "single",
    "singlenodecontainer": "single",
    "list": "list",
    "listcontainer": "list",
    "graph": "graph",
    "graphcontainer": "graph",
}


class ContainerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str  # normalised to lowercase short form
    nodes: list[NodeConfig]
    layer: str = "L2"  # Default to L2 for safety
    loop: bool = False

    @field_validator("type", mode="before")
    @classmethod
    def normalise_type(cls, v: str) -> str:
        key = v.lower().replace("_", "").replace("-", "")
        if key not in _CONTAINER_TYPE_MAP:
            raise ValueError(
                f"Unknown container type '{v}'. "
                f"Valid values: single, list, graph (or CamelCase variants)"
            )
        return _CONTAINER_TYPE_MAP[key]


# ── Task config ────────────────────────────────────────────────────────────


class TaskConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    boundaries: list[str] = []
    description: str = ""


# ── Safety config ──────────────────────────────────────────────────────────


class SlowLaneConfig(BaseModel):
    """Async slow-lane evaluator for expensive guards (L0 vision OOD, L2 task).

    The control loop stays the single writer to the sink; slow-lane guards run
    on a worker thread at ``task_hz`` against the latest post-fast-lane
    snapshot and publish a verdict the control loop consumes as a gate.  A
    verdict older than ``max_staleness_ms`` triggers ``stale_action``.
    """

    model_config = ConfigDict(extra="forbid")
    task_hz: float = 10.0  # async task/OOD lane evaluation rate
    max_staleness_ms: float = 500.0
    stale_action: str = "reject"  # reject (trigger fallback) | warn (log only)

    @field_validator("task_hz")
    @classmethod
    def freq_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("slow_lane.task_hz must be positive")
        return v

    @field_validator("stale_action")
    @classmethod
    def validate_stale_action(cls, v: str) -> str:
        value = str(v).lower()
        if value not in {"reject", "warn"}:
            raise ValueError("slow_lane.stale_action must be 'reject' or 'warn'")
        return value


class SafetyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Phase 2: single string name; Phase 1: list of names. Both accepted.
    always_active: str | list[str] = []
    no_task_behavior: str = "emergency_stop"
    # All three rates live under ``safety``: control_hz (control loop),
    # telemetry_hz (hardware register reads), and slow_lane.task_hz (async lane).
    control_hz: float = 30.0
    # Cadence for telemetry register reads (temperature/current/voltage). None →
    # read every control cycle. A control concern, so it lives here.
    telemetry_hz: float | None = None
    max_obs_age_sec: float = 0.1
    cycle_budget_ms: float = 20.0
    enforcement_mode: EnforcementMode = EnforcementMode.ENFORCE
    # Optional fast/slow lane split — None keeps everything in the control loop.
    slow_lane: SlowLaneConfig | None = None

    @field_validator("control_hz")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("control_hz must be positive")
        return v

    def always_active_list(self) -> list[str]:
        """Normalise to list regardless of YAML format."""
        if isinstance(self.always_active, str):
            return [self.always_active] if self.always_active else []
        return self.always_active


# ── Phase 2+ optional top-level sections ──────────────────────────────────
# All use extra="allow" so future keys don't fail validation.
# Schema will be tightened per section when Phase 2 implementation begins.


class HardwareJointConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    limits_rad: list[float] | None = None


class HardwareSourceConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    # Concrete LeRobot robot namespace, distinct from DAM's source adapter type.
    robot_type: str | None = None
    port: str | None = None
    id: str | None = None
    topic: str | None = None
    msg_type: str | None = None
    mapping: dict[str, str] | None = None
    cameras: dict[str, Any] | None = None
    # Absolute or relative path to the calibration directory / file.
    # Supports shared-volume mounts (e.g. /mnt/dam_data/calibration/).
    calibration_path: str | None = None
    # Native joint-angle unit mode for motor/dataset sources.  When True,
    # adapters convert external degrees into DAM's internal radians.
    degrees_mode: bool | None = None
    # Composition-level namespace for image keys when sources share a frame hub.
    image_namespace: str | None = None


class HardwareInterfaceConfig(HardwareSourceConfig):
    """Public hardware IO declaration.

    ``capabilities`` says what the interface exposes. The runtime lowers this
    into internal read/write endpoints.
    """

    capabilities: list[str] = Field(default_factory=list)
    ref: str | None = None


class HardwareSinkConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    ref: str | None = None
    type: str | None = None
    topic: str | None = None


class HardwareConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    preset: str | None = None
    joint_names: list[str] | None = None
    # deg<->rad mode is an interface concern, not robot identity — presets no
    # longer carry it. The motor interface declares ``source.degrees_mode``;
    # this hardware-level field is the declaration point for interface-less
    # configs (e.g. a tensor-only Guardrail). See ``motor_degrees_mode``.
    degrees_mode: bool | None = None
    asset: dict[str, str] | None = None
    solvers: dict[str, Any] = Field(default_factory=dict)
    action_layout: list[dict[str, Any]] = Field(default_factory=list)
    urdf_path: str | None = None
    joints: dict[str, HardwareJointConfig] | None = None
    interfaces: dict[str, HardwareInterfaceConfig] | None = None
    sources: dict[str, HardwareSourceConfig] | None = None
    sinks: dict[str, HardwareSinkConfig] | None = None

    def motor_degrees_mode(self, default: bool = True) -> bool:
        """Resolve deg<->rad mode: motor interface (``source.degrees_mode``)
        first, then the hardware-level ``degrees_mode``, then *default*.

        lerobot motors are degree-native, so the default is True; radian-native
        robots (e.g. Franka) declare ``degrees_mode: false`` explicitly.
        """
        if self.sources:
            for s in self.sources.values():
                if str(s.type or "").lower() in ("motor", "lerobot") and s.degrees_mode is not None:
                    return bool(s.degrees_mode)
        if self.degrees_mode is not None:
            return bool(self.degrees_mode)
        return default

    @model_validator(mode="before")
    @classmethod
    def _default_interface_type(cls, data: Any) -> Any:
        """Let telemetry interfaces omit ``type`` — the key name *is* the type.

        ``temperature: {capabilities: [robot_telemetry], ref: arm}`` is enough;
        the runtime resolves the adapter channel by its name. Any interface
        without an explicit ``type`` inherits its map key, so ``type`` stays
        required downstream (no None to special-case in the factory).
        """
        if isinstance(data, dict):
            interfaces = data.get("interfaces")
            if isinstance(interfaces, dict):
                for name, spec in interfaces.items():
                    if isinstance(spec, dict) and not spec.get("type"):
                        spec["type"] = name
        return data

    @model_validator(mode="after")
    def lower_interfaces(self) -> HardwareConfig:
        if not self.interfaces:
            return self

        sources: dict[str, HardwareSourceConfig] = {}
        sinks: dict[str, HardwareSinkConfig] = {}

        for name, iface in self.interfaces.items():
            caps = _interface_capabilities(iface)
            payload = iface.model_dump(exclude_none=True)
            payload.pop("capabilities", None)

            if _is_read_interface(caps):
                source_payload = dict(payload)
                source_payload.pop("ref", None)
                sources[name] = HardwareSourceConfig(**source_payload)

            if "command_joints" in caps:
                sink_payload = dict(payload)
                iface_ref = sink_payload.pop("ref", None)
                if name in sources:
                    sink_payload["ref"] = f"sources.{name}"
                elif iface_ref:
                    sink_payload["ref"] = (
                        iface_ref
                        if str(iface_ref).startswith("sources.")
                        else f"sources.{iface_ref}"
                    )
                sinks["command" if "command" not in sinks else f"{name}_command"] = (
                    HardwareSinkConfig(**sink_payload)
                )

        if sources:
            self.sources = sources
        if sinks:
            self.sinks = sinks
        return self


def _interface_capabilities(iface: HardwareInterfaceConfig) -> set[str]:
    caps = {str(c).strip().lower() for c in iface.capabilities if str(c).strip()}
    if caps:
        return caps
    type_name = str(iface.type).lower()
    if type_name in {"motor", "lerobot"}:
        return {"observe_joints", "command_joints"}
    if type_name in {"ros2", "dataset"}:
        return {"observe_joints"}
    if type_name in {"opencv", "camera", "usb"}:
        return {"image"}
    if type_name in {"current", "temperature", "voltage", "effort", "wrench"}:
        return {"robot_telemetry"}
    return {"observe_joints"}


def _is_read_interface(caps: set[str]) -> bool:
    return bool(caps.intersection({"observe_joints", "image", "robot_telemetry", "host_telemetry"}))


class PolicyConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    pretrained_path: str | None = None
    dataset_repo_id: str | None = None
    device: str = "cpu"


class SimulationConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str
    preset: str | None = None
    scene: str | None = None
    lookahead_steps: int = 10
    # Dataset replay — when set, DatasetSimSource replays from this HF repo
    dataset_repo_id: str | None = None
    episode: int = 0
    # LeRobot SO-101 datasets store joint positions in degrees; set True to
    # convert deg→rad before passing observations into the guard pipeline.
    degrees_mode: bool = True


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    mode: str = "passive"  # managed | passive
    control_frequency_hz: float = 30.0
    max_obs_age_sec: float = 0.1
    cycle_budget_ms: float = 20.0


class LoopbackConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    backend: str = "mcap"  # "mcap" | "pickle"
    output_dir: str = "/tmp/dam_loopback"  # session files written here
    window_sec: float = 10.0  # ring-buffer depth for pre-event images
    rotate_mb: float = 500.0  # rotate file after this many MB
    rotate_minutes: float = 60.0  # rotate file after this many minutes
    max_queue_depth: int = 256  # drop normal cycles if queue exceeds this
    capture_images_on_clamp: bool = False  # also fetch images on CLAMP events


class RiskControllerConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    window_sec: float = 10.0
    clamp_threshold: int = 5
    reject_threshold: int = 2


class SolverConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    # The solvers-block key IS the solver type / implementation name (the same
    # name it was registered under). ``type`` is therefore optional — supply it
    # only to alias a different implementation under a custom key.
    type: str | None = None
    capabilities: list[str] | None = None
    params: dict[str, Any] = {}


# ── Top-level Stackfile ────────────────────────────────────────────────────


class StackfileConfig(BaseModel):
    model_config = ConfigDict(extra="allow")
    version: str = "1"
    # Unified architecture
    boundaries: dict[str, ContainerConfig] = {}
    fallbacks: dict[str, FallbackConfig] = {}
    tasks: dict[str, TaskConfig] = {}
    safety: SafetyConfig = SafetyConfig()
    # Hierarchical list of active guards (e.g. [{"L0": "ood"}, {"L1": "motion"}, {"L2": "execution"}, {"L3": "hardware"}])
    # Per-guard entries may carry extra keys ``phase: int`` and ``always: bool``
    # to override the decorator defaults (see project_runtime_context_state_machine memory).
    # Also supports dict format for builtin registration in Phase 1
    guards: list[dict[str, Any]] | list[str] | dict[str, Any] = []
    # Phase 2+  (all optional)
    hardware: HardwareConfig | None = None
    policy: PolicyConfig | None = None
    simulation: SimulationConfig | None = None
    runtime: RuntimeConfig | None = None
    loopback: LoopbackConfig | None = None
    risk_controller: RiskControllerConfig | None = None
    solvers: dict[str, SolverConfig] = {}
