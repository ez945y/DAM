from __future__ import annotations

from pathlib import Path

import yaml

from dam.config.schema import StackfileConfig


class StackfileLoader:
    @staticmethod
    def load(path: str) -> StackfileConfig:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Stackfile not found: {path}")
        with p.open() as f:
            data = yaml.safe_load(f)
        if data is None:
            raise ValueError(f"Stackfile is empty: {path}")
        try:
            return StackfileConfig(**data)
        except Exception as e:
            raise ValueError(f"Stackfile schema error in '{path}': {e}") from e

    @staticmethod
    def validate(path: str) -> None:
        StackfileLoader.load(path)
