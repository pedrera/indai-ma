import json
from dataclasses import dataclass
from typing import Any, Protocol

from tools import (
    TOOL_SPEC_BY_NAME,
    ToolExecution,
    execute_tool_call,
    validate_tool_arguments,
)


PROCUREMENT_TOOL_NAMES = (
    "calculate_supply_position",
    "calculate_spot_exposure",
    "calculate_demand_scenario",
)


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    parameters: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


class ToolRegistry(Protocol):
    def descriptors(self) -> tuple[ToolDescriptor, ...]: ...

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecution: ...

    def validate(self, name: str, arguments: dict[str, Any]) -> dict[str, float]: ...


class LocalToolRegistry:
    def __init__(self, allowed_names: tuple[str, ...]) -> None:
        unknown = set(allowed_names) - set(TOOL_SPEC_BY_NAME)
        if unknown:
            raise ValueError(f"Unknown tools in registry: {sorted(unknown)}")
        self._allowed_names = allowed_names

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return tuple(
            ToolDescriptor(
                name=name,
                description=TOOL_SPEC_BY_NAME[name]["description"],
                parameters=TOOL_SPEC_BY_NAME[name]["parameters"],
            )
            for name in self._allowed_names
        )

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolExecution:
        if name not in self._allowed_names:
            raise ValueError(f"Tool is not available to this agent: {name}")
        return execute_tool_call(name, json.dumps(arguments))

    def validate(self, name: str, arguments: dict[str, Any]) -> dict[str, float]:
        if name not in self._allowed_names:
            raise ValueError(f"Tool is not available to this agent: {name}")
        return validate_tool_arguments(name, json.dumps(arguments))


def procurement_tool_registry() -> LocalToolRegistry:
    return LocalToolRegistry(PROCUREMENT_TOOL_NAMES)


def action_fingerprint(name: str, arguments: dict[str, Any]) -> str:
    return f"{name}:{json.dumps(arguments, sort_keys=True, separators=(',', ':'))}"
