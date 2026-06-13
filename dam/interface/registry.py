from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

InterfaceFactory = Callable[[str, Any, Mapping[str, Any]], Any]


@dataclass(frozen=True)
class InterfaceEntry:
    interface_type: str
    factory: InterfaceFactory
    role: str


class InterfaceRegistry:
    """Registry for user-defined runtime read/write interfaces.

    Presets describe robot semantics. Solvers describe action/layout
    conversion. Interfaces only bridge runtime IO and telemetry for a declared
    hardware interface type.
    """

    def __init__(self) -> None:
        self._read: dict[str, InterfaceEntry] = {}
        self._write: dict[str, InterfaceEntry] = {}
        self._robot_telemetry: dict[str, InterfaceEntry] = {}
        self._host_telemetry: dict[str, InterfaceEntry] = {}

    def register_read(
        self,
        interface_type: str,
        factory: InterfaceFactory,
        *,
        replace: bool = False,
    ) -> InterfaceFactory:
        key = _normalize(interface_type)
        if not key:
            raise ValueError("Read interface type must not be empty")
        if key in self._read and not replace:
            raise ValueError(f"Read interface '{interface_type}' is already registered")
        self._read[key] = InterfaceEntry(key, factory, "read")
        return factory

    def register_write(
        self,
        interface_type: str,
        factory: InterfaceFactory,
        *,
        replace: bool = False,
    ) -> InterfaceFactory:
        key = _normalize(interface_type)
        if not key:
            raise ValueError("Write interface type must not be empty")
        if key in self._write and not replace:
            raise ValueError(f"Write interface '{interface_type}' is already registered")
        self._write[key] = InterfaceEntry(key, factory, "write")
        return factory

    def register_robot_telemetry(
        self,
        interface_type: str,
        factory: InterfaceFactory,
        *,
        replace: bool = False,
    ) -> InterfaceFactory:
        key = _normalize(interface_type)
        if not key:
            raise ValueError("Robot telemetry interface type must not be empty")
        if key in self._robot_telemetry and not replace:
            raise ValueError(f"Robot telemetry interface '{interface_type}' is already registered")
        self._robot_telemetry[key] = InterfaceEntry(key, factory, "robot_telemetry")
        return factory

    def register_host_telemetry(
        self,
        interface_type: str,
        factory: InterfaceFactory,
        *,
        replace: bool = False,
    ) -> InterfaceFactory:
        key = _normalize(interface_type)
        if not key:
            raise ValueError("Host telemetry interface type must not be empty")
        if key in self._host_telemetry and not replace:
            raise ValueError(f"Host telemetry interface '{interface_type}' is already registered")
        self._host_telemetry[key] = InterfaceEntry(key, factory, "host_telemetry")
        return factory

    def has_read(self, interface_type: str) -> bool:
        return _normalize(interface_type) in self._read

    def has_write(self, interface_type: str) -> bool:
        return _normalize(interface_type) in self._write

    def has_robot_telemetry(self, interface_type: str) -> bool:
        return _normalize(interface_type) in self._robot_telemetry

    def has_host_telemetry(self, interface_type: str) -> bool:
        return _normalize(interface_type) in self._host_telemetry

    def build_read(self, name: str, cfg: Any, context: Mapping[str, Any] | None = None) -> Any:
        type_key = _normalize(getattr(cfg, "type", ""))
        if type_key not in self._read:
            raise KeyError(f"Read interface '{type_key}' not found")
        return self._read[type_key].factory(name, cfg, dict(context or {}))

    def build_write(self, name: str, cfg: Any, context: Mapping[str, Any] | None = None) -> Any:
        type_key = _normalize(getattr(cfg, "type", ""))
        if type_key not in self._write:
            raise KeyError(f"Write interface '{type_key}' not found")
        return self._write[type_key].factory(name, cfg, dict(context or {}))

    def build_robot_telemetry(
        self, name: str, cfg: Any, context: Mapping[str, Any] | None = None
    ) -> Any:
        type_key = _normalize(getattr(cfg, "type", ""))
        if type_key not in self._robot_telemetry:
            raise KeyError(f"Robot telemetry interface '{type_key}' not found")
        return self._robot_telemetry[type_key].factory(name, cfg, dict(context or {}))

    def build_host_telemetry(
        self, name: str, cfg: Any, context: Mapping[str, Any] | None = None
    ) -> Any:
        type_key = _normalize(getattr(cfg, "type", ""))
        if type_key not in self._host_telemetry:
            raise KeyError(f"Host telemetry interface '{type_key}' not found")
        return self._host_telemetry[type_key].factory(name, cfg, dict(context or {}))

    def list_read(self) -> list[str]:
        return sorted(self._read)

    def list_write(self) -> list[str]:
        return sorted(self._write)

    def list_robot_telemetry(self) -> list[str]:
        return sorted(self._robot_telemetry)

    def list_host_telemetry(self) -> list[str]:
        return sorted(self._host_telemetry)


def _normalize(value: str) -> str:
    return str(value).strip().lower().replace("-", "_")


_registry = InterfaceRegistry()


def get_global_interface_registry() -> InterfaceRegistry:
    return _registry
