from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from dam.guard.base import Guard

logger = logging.getLogger(__name__)


class GuardRegistry:
    """Registry for discovering and instantiating safety Guard classes."""

    def __init__(self) -> None:
        # kind -> GuardClass
        self._guards: dict[str, type[Guard]] = {}
        # metadata: kind -> {layer, description}
        self._metadata: dict[str, dict[str, Any]] = {}

    def register(
        self, kind: str, cls: type[Guard], layer: str | None = None, description: str | None = None
    ) -> None:
        """Register a Guard class with a functional kind and optional metadata."""
        if kind in self._guards:
            logger.debug("GuardRegistry: overwriting kind '%s'", kind)
        self._guards[kind] = cls

        # Extract default metadata from class attributes if not provided
        default_layer = getattr(cls, "_guard_layer", None)
        if hasattr(default_layer, "name"):
            default_layer = default_layer.name

        self._metadata[kind] = {
            "kind": kind,
            "layer": layer or default_layer,
            "description": description or (cls.__doc__ or "").strip().split("\n")[0],
            "class_name": cls.__name__,
        }
        logger.debug("GuardRegistry: registered guard '%s' (%s)", kind, cls.__name__)

    def get(self, kind: str) -> type[Guard] | None:
        return self._guards.get(kind)

    def list_all(self) -> list[dict[str, Any]]:
        """Return a sorted list of guard metadata for catalog display."""
        items = list(self._metadata.values())
        # Sort by layer L0, L1...
        items.sort(key=lambda x: str(x.get("layer", "L9")))
        return items


_REGISTRY = GuardRegistry()


def get_guard_registry() -> GuardRegistry:
    return _REGISTRY
