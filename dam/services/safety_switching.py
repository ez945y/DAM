"""Review, queue and acknowledge safety changes without controlling actuators."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import yaml

from dam.config.config_diff import config_changes, config_hash, config_structure
from dam.config.schema import StackfileConfig


def _atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SafetySwitchService:
    """A preview is bound to the active config, runner, task and saved file.

    Runtime receipts, rather than file writes, establish whether a change is
    active. Structural changes only save a restart candidate; no start,
    reconnect, reset, or hardware operation is performed here.
    """

    def __init__(self, control: Any = None, root: Path | None = None) -> None:
        self.control = control
        self.root = root or Path(__file__).resolve().parents[2]
        self._lock = threading.RLock()
        self._previews: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, Any] | None = None
        self._history_path = self.root / "data" / "safety-switches" / "history.json"
        self._history: list[dict[str, Any]] = []
        self._history_error: str | None = None
        if self._history_path.exists():
            try:
                rows = json.loads(self._history_path.read_text())
                if not isinstance(rows, list) or not all(
                    isinstance(row, dict) and "id" in row and "status" in row for row in rows
                ):
                    raise ValueError("Expected a list of switch records")
                self._history = rows[-100:]
                for row in self._history:
                    if row["status"] == "pending":
                        row.update(
                            status="rejected", message="Service restarted before acknowledgement"
                        )
            except (ValueError, OSError) as exc:
                self._history_error = (
                    f"Switch history cannot be read; repair it before applying: {exc}"
                )

    def _path(self) -> Path:
        configured = getattr(self.control, "_stack_path", None)
        return Path(configured) if configured else self.root / ".dam_stackfile.yaml"

    def _snapshot(self) -> dict[str, Any]:
        runner = getattr(self.control, "_runner", None)
        runtime = getattr(runner, "runtime", None)
        if runtime is None:
            runtime = getattr(runner, "_runtime", None)
        config, version, active_task, epoch = None, None, None, None
        if runtime is not None:
            with runtime._hot_reload.lock:
                config = getattr(runtime, "_reload_config", None)
                version = getattr(runtime, "_config_version", None)
                active_task = getattr(runtime, "_active_task", None)
                epoch = runtime._hot_reload.task_epoch
        state = str(getattr(self.control, "state", "idle"))
        path = self._path()
        raw, saved_config, saved_hash = None, None, None
        saved_error = None
        try:
            with contextlib.suppress(FileNotFoundError):
                raw = path.read_text(encoding="utf-8")
            if raw is not None:
                saved_config = StackfileConfig.model_validate(yaml.safe_load(raw))
                saved_hash = config_hash(saved_config)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            saved_config = None
            saved_error = str(exc)
        saved_digest = hashlib.sha256(raw.encode()).hexdigest() if raw is not None else None
        active_hash, active_error = None, None
        try:
            active_hash = config_hash(config) if config is not None else None
        except ValueError as exc:
            active_error = str(exc)
        return {
            "runtime": runtime,
            "config": config or saved_config,
            "active_hash": active_hash,
            "active_error": active_error,
            "saved_hash": saved_hash,
            "saved_error": saved_error,
            "version": version,
            "epoch": epoch,
            "task": active_task,
            "state": state,
            "raw": raw,
            "identity": (
                id(runtime),
                active_hash,
                version,
                active_task,
                epoch,
                state,
                str(path),
                saved_digest,
                saved_error,
                active_error,
            ),
        }

    def preview(
        self, content: str, task: str, contract: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        from dam.config.safety_plan import SafetyPlan
        from dam.services.safety_authoring import compile_plan

        plan = SafetyPlan(
            name="switch-preview", base_yaml=content, task=task, contract=contract or {}
        )
        checked = compile_plan(plan)
        candidate = StackfileConfig.model_validate(yaml.safe_load(checked["yaml"]))
        candidate_hash = config_hash(candidate)
        with self._lock:
            snap = self._snapshot()
            before = snap["config"]
            reasons = [
                item["message"]
                for item in checked["diagnostics"]
                if item["status"] in ("missing", "error")
            ]
            if self._history_error:
                reasons.append(self._history_error)
            if snap["saved_error"]:
                reasons.append(f"Saved configuration cannot be verified: {snap['saved_error']}")
            if snap["active_error"]:
                reasons.append(f"Active configuration cannot be verified: {snap['active_error']}")
            if before is not None:
                for name in before.safety.always_active_list():
                    if name not in candidate.safety.always_active_list() or (
                        before.boundaries.get(name) != candidate.boundaries.get(name)
                    ):
                        reasons.append(
                            f"Protected baseline boundary '{name}' cannot be removed or changed"
                        )
                if (
                    str(before.safety.enforcement_mode) == "enforce"
                    and str(candidate.safety.enforcement_mode) != "enforce"
                ):
                    reasons.append("A safety switch cannot disable enforcement")
            runtime = snap["runtime"]
            if self._pending is not None or (
                runtime is not None and runtime._hot_reload.has_pending()
            ):
                reasons.append("Another configuration switch is awaiting acknowledgement")
            hot_eligible = (
                runtime is not None
                and before is not None
                and config_structure(before) == config_structure(candidate)
                and snap["task"] == task
                and getattr(runtime, "_slow_lane", None) is None
                and snap["state"] == "running"
            )
            if hot_eligible and snap["active_hash"] != candidate_hash:
                active = set(candidate.safety.always_active_list()) | set(
                    candidate.tasks[task].boundaries
                )
                l1_spatial = {
                    "workspace",
                    "keep_out_zone",
                    "orientation_limit",
                    "ee_velocity_limit",
                }
                task_spatial = {"task_workspace_bounds", "task_gripper_command_guard"}
                spatial = {
                    name: {node.callback for node in candidate.boundaries[name].nodes}
                    & (l1_spatial | task_spatial)
                    for name in active
                    if name in candidate.boundaries
                }
                if any(spatial.values()):
                    # Inspect already initialized capability objects only. Do not
                    # compute FK, read sensors, construct solvers, or touch IO.
                    try:
                        dynamics = runtime._select_dynamics()
                        fk = runtime._select_solver("fk")
                    except Exception:
                        dynamics, fk = None, None
                    dynamics_ready = (
                        dynamics is not None
                        and getattr(dynamics, "available", False)
                        and getattr(dynamics, "default_frame_id", None) is not None
                        and all(
                            callable(getattr(dynamics, method, None))
                            for method in ("update", "frame_placement", "frame_jacobian")
                        )
                    )
                    fk_ready = (
                        fk is not None
                        and getattr(fk, "available", True)
                        and any(
                            callable(getattr(fk, method, None))
                            for method in ("compute_fk", "forward_kinematics")
                        )
                    )
                    for name, callbacks in spatial.items():
                        message = None
                        if callbacks & l1_spatial and not dynamics_ready:
                            message = "Active runtime has no initialized dynamics/frame Jacobian required by this spatial rule; reload and verify its solver first"
                        elif callbacks & task_spatial and not dynamics_ready:
                            message = (
                                "FK is initialized, but this task callback consumes injected/observed EE data whose availability is unverified"
                                if fk_ready
                                else "Active runtime has no initialized dynamics EE feed; an observation declaration alone does not verify EE availability"
                            )
                        if message:
                            checked["diagnostics"].append(
                                {
                                    "boundary": name,
                                    "status": "missing",
                                    "code": "runtime_capability",
                                    "message": message,
                                }
                            )
                            reasons.append(message)
            mode = "blocked" if reasons or not checked["valid"] else "restart_required"
            if mode != "blocked":
                if snap["active_hash"] == candidate_hash and snap["task"] == task:
                    mode = "unchanged"
                elif hot_eligible:
                    mode = "hot_reload"
                    reasons.append("Parameters will apply at the next control-cycle boundary")
                else:
                    reasons.append(
                        "Save this candidate, then reload the runtime before starting the selected task"
                    )
                    if snap["state"] not in ("idle", "stopped"):
                        reasons.append(
                            "Stop the runtime and preview again before saving a restart candidate"
                        )
                    if getattr(runtime, "_slow_lane", None) is not None:
                        reasons.append("The active slow lane requires a runtime reload")
            token = uuid.uuid4().hex
            result = {
                "token": token,
                "mode": mode,
                "reasons": reasons,
                "changes": config_changes(
                    before.model_dump(mode="json") if before else {},
                    candidate.model_dump(mode="json"),
                ),
                "current_version": snap["version"],
                "candidate_hash": candidate_hash,
                "active_hash": snap["active_hash"],
                "task": task,
                "diagnostics": checked["diagnostics"],
            }
            self._previews[token] = {
                "result": result,
                "identity": snap["identity"],
                "config": candidate,
                "yaml": checked["yaml"],
                "previous_yaml": yaml.safe_dump(before.model_dump(mode="json"), sort_keys=False)
                if before
                else None,
                "previous_task": snap["task"],
                "created": time.monotonic(),
            }
            while len(self._previews) > 128:
                del self._previews[next(iter(self._previews))]
            return result

    def _persist(self) -> None:
        _atomic_text(
            self._history_path, json.dumps(self._history[-100:], indent=2, allow_nan=False)
        )

    def apply(self, token: str) -> dict[str, Any]:
        with self._lock:
            preview = self._previews.get(token)
            if preview is None or time.monotonic() - preview["created"] > 600:
                raise ValueError("Preview expired; preview the configuration again")
            snap = self._snapshot()
            if snap["identity"] != preview["identity"]:
                raise ValueError("Runtime or saved configuration changed; preview again")
            result = preview["result"]
            mode = result["mode"]
            if mode == "blocked":
                raise ValueError("Resolve the blocking diagnostics before applying")
            if self._pending is not None:
                raise ValueError("Another switch is pending")
            if mode == "restart_required" and snap["state"] not in ("idle", "stopped"):
                raise ValueError("Stop the runtime and preview again before saving")
            runtime = snap["runtime"]
            if runtime is not None and runtime._hot_reload.has_pending():
                raise ValueError("Another runtime reload is pending")
            self._previews.pop(token)
            row = {
                "id": uuid.uuid4().hex,
                "status": "pending",
                "message": "Waiting for cycle acknowledgement",
                "candidate_hash": result["candidate_hash"],
                "previous_hash": snap["active_hash"],
                "task": result["task"],
                "previous_task": preview["previous_task"],
                "created_at": time.time(),
                "yaml": preview["yaml"],
                "previous_yaml": preview["previous_yaml"],
                "persisted": False,
            }
            # Persist intent before queueing or writing anything authoritative.
            self._history.append(row)
            self._history = self._history[-100:]
            try:
                self._persist()
            except OSError:
                self._history.remove(row)
                raise
            try:
                with getattr(self.control, "_lock", contextlib.nullcontext()):
                    if self._snapshot()["identity"] != preview["identity"]:
                        raise ValueError("Runtime or saved configuration changed; preview again")
                    if mode == "unchanged":
                        row.update(
                            status="applied",
                            message="This configuration and task are already active",
                            persisted=snap["saved_hash"] == result["candidate_hash"],
                        )
                    elif mode == "restart_required":
                        _atomic_text(self._path(), preview["yaml"])
                        row.update(
                            status="saved_restart_required",
                            message="Saved. Reload the runtime and select the requested task; nothing was started",
                            persisted=True,
                        )
                    else:
                        request_id = runtime.apply_pending_reload(
                            preview["config"],
                            expected_version=snap["version"],
                            expected_task=result["task"],
                            expected_epoch=snap["epoch"],
                        )
                        row["request_id"] = request_id
                        self._pending = {
                            "row": row,
                            "runtime": runtime,
                            "saved_raw": snap["raw"],
                            "path": self._path(),
                        }
            except (ValueError, OSError) as exc:
                row.update(status="rejected", message=str(exc))
                self._persist()
                raise
            try:
                self._persist()
            except OSError as exc:
                # The action may already be committed. Keep its acknowledgement
                # truthful even when the audit file becomes unavailable.
                row["message"] += f"; audit update could not be saved: {exc}"
            if self._pending is not None:
                threading.Thread(
                    target=self._await_receipt, name="safety-switch-receipt", daemon=True
                ).start()
            return self._public_row(row)

    def _refresh(self) -> None:
        pending = self._pending
        if pending is None:
            return
        row, runtime = pending["row"], pending["runtime"]
        snapshot = self._snapshot()
        current_runtime = snapshot["runtime"]
        if snapshot["state"] in ("idle", "stopped", "stopping", "emergency"):
            runtime._hot_reload.cancel_pending(
                row["request_id"], "Runtime stopped before the switch was applied"
            )
        receipt = runtime._hot_reload.reload_receipt(row["request_id"])
        if (
            receipt is not None
            and receipt["status"] == "pending"
            and current_runtime is not runtime
        ):
            runtime._hot_reload.cancel_pending(
                row["request_id"], "Runtime was replaced before acknowledgement"
            )
            # An in-flight cycle may still acknowledge an already consumed
            # request. Its receipt is the authority, even after replacement.
            receipt = runtime._hot_reload.reload_receipt(row["request_id"])
        if receipt is None:
            row.update(status="rejected", message="Runtime no longer has the switch receipt")
        elif receipt["status"] == "pending":
            return
        else:
            row.update(
                status=receipt["status"],
                message=receipt["message"],
                config_version=receipt.get("config_version"),
            )
            if receipt["status"] == "applied":
                path = pending["path"]
                if (
                    current_runtime is not runtime
                    or snapshot["active_hash"] != row["candidate_hash"]
                    or self._path() != path
                ):
                    row["message"] += "; configuration is no longer active, so it was not saved"
                else:
                    try:
                        try:
                            current_raw = path.read_text(encoding="utf-8")
                        except FileNotFoundError:
                            current_raw = None
                        if current_raw != pending["saved_raw"]:
                            row["message"] += (
                                "; saved file changed separately, so it was not overwritten"
                            )
                        else:
                            _atomic_text(path, row["yaml"])
                            row["persisted"] = True
                    except (OSError, UnicodeError) as exc:
                        row["message"] += f"; active in memory, but saving failed: {exc}"
        self._pending = None
        try:
            self._persist()
        except OSError as exc:
            row["message"] += f"; audit update could not be saved: {exc}"

    def _await_receipt(self) -> None:
        # Receipt polling is a service concern, never control-loop file IO.
        while True:
            with self._lock:
                try:
                    self._refresh()
                except (OSError, ValueError):
                    # A failed disk read must not claim the runtime switch failed.
                    return
                if self._pending is None:
                    return
            threading.Event().wait(0.05)

    @staticmethod
    def _public_row(row: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in row.items() if key not in ("yaml", "previous_yaml")}

    def status(self) -> dict[str, Any]:
        with self._lock:
            self._refresh()
            snap = self._snapshot()
            return {
                "active_hash": snap["active_hash"],
                "active_error": snap["active_error"],
                "saved_hash": snap["saved_hash"],
                "saved_error": snap["saved_error"],
                "history_error": self._history_error,
                "config_version": snap["version"],
                "active_task": snap["task"],
                "pending": self._public_row(self._pending["row"]) if self._pending else None,
                "history": [self._public_row(row) for row in reversed(self._history)],
            }

    def rollback_preview(
        self, switch_id: str, contract: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        with self._lock:
            row = next((item for item in self._history if item["id"] == switch_id), None)
            if row is None or not row.get("previous_yaml") or not row.get("previous_task"):
                raise ValueError("This switch has no previous active configuration and task")
            return self.preview(row["previous_yaml"], row["previous_task"], contract)
