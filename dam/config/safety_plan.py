"""Strict authoring models; compiled output remains an ordinary Stackfile."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dam.config.schema import ContainerConfig


def validate_plan_name(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value) or ".." in value:
        raise ValueError("Use 1–80 letters, digits, dots, underscores or hyphens; no '..'")
    return value


class SafetyContract(BaseModel):
    """User declarations, never evidence that runtime sensors are healthy."""

    model_config = ConfigDict(extra="forbid")
    observation_keys: list[str] = Field(default_factory=list)
    frame: str | None = Field(default=None, min_length=1)
    action_units: list[str] | None = None
    joint_names: list[str] | None = None


class SafetyComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    kind: Literal["baseline", "scene", "task", "mode"]
    boundaries: dict[str, ContainerConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def strict_nodes(self) -> SafetyComponent:
        for name, boundary in self.boundaries.items():
            if not name.strip() or not boundary.nodes:
                raise ValueError("Boundary names and nodes must not be empty")
            for node in boundary.nodes:
                if node.model_extra:
                    raise ValueError("Component node parameters belong in params")
        return self


class SafetyPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    base_yaml: str = Field(min_length=1, max_length=2_000_000)
    task: str = Field(min_length=1)
    components: list[SafetyComponent] = Field(default_factory=list)
    contract: SafetyContract = Field(default_factory=SafetyContract)

    _valid_name = field_validator("name")(validate_plan_name)

    @model_validator(mode="after")
    def distinct_components(self) -> SafetyPlan:
        names = [component.name for component in self.components]
        if len(names) != len(set(names)):
            raise ValueError("Component names must be unique")
        return self


class SafetyDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20_000)
    frame: str | None = Field(default=None, min_length=1)
