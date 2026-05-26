"""Composable observation-source views.

Source adapters should report the names native to their data or device.
Deployment composition may then namespace image streams before multiple
sources are merged into one observation.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any, cast

from dam.types.observation import Observation


class ImageNamespaceSource:
    """Expose a source's image streams under deployment-level names.

    The wrapped source remains responsible only for reading its native data.
    This view handles namespacing needed when multiple image-bearing sources
    share an observation, live preview, and MCAP session.
    """

    def __init__(
        self,
        source: Any,
        namespace: str,
        *,
        mapping: Mapping[str, str] | None = None,
    ) -> None:
        cleaned = namespace.strip().strip("_")
        if not cleaned:
            raise ValueError("Image source namespace must not be empty")
        self._source = source
        self._prefix = f"{cleaned}_"
        self._mapping = dict(mapping or {})

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)

    def read(self) -> Observation:
        obs = cast(Observation, self._source.read())
        if not obs.images:
            return obs
        images = {
            f"{self._prefix}{self._mapping.get(name, name)}": image
            for name, image in obs.images.items()
        }
        return dataclasses.replace(obs, images=images)
