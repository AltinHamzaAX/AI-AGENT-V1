from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Any
from uuid import UUID

from app.modules.posts.domain.enums import PostWorkflowSection

WORKFLOW_STATE_SCHEMA_VERSION = 8
OBJECT_SECTIONS = frozenset(PostWorkflowSection) - {
    PostWorkflowSection.ASSETS,
    PostWorkflowSection.GENERATION_ARTIFACTS,
    PostWorkflowSection.REVISION_HISTORY,
}
LIST_SECTIONS = frozenset(PostWorkflowSection) - OBJECT_SECTIONS


def empty_workflow_state() -> dict[str, Any]:
    return {
        section.value: {} if section in OBJECT_SECTIONS else [] for section in PostWorkflowSection
    }


def validate_workflow_state(data: dict[str, Any]) -> dict[str, Any]:
    expected = {section.value for section in PostWorkflowSection}
    if set(data) != expected:
        raise ValueError("Workflow state must contain exactly the supported sections")
    for section in OBJECT_SECTIONS:
        if not isinstance(data[section.value], dict):
            raise ValueError(f"Workflow section '{section.value}' must be an object")
    for section in LIST_SECTIONS:
        if not isinstance(data[section.value], list):
            raise ValueError(f"Workflow section '{section.value}' must be an array")
    return deepcopy(data)


def validate_section_value(section: PostWorkflowSection, value: Any) -> Any:
    expected_type = dict if section in OBJECT_SECTIONS else list
    if not isinstance(value, expected_type):
        expected_name = "object" if expected_type is dict else "array"
        raise ValueError(f"Workflow section '{section.value}' must be an {expected_name}")
    _validate_json_value(value)
    return deepcopy(value)


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Workflow state cannot contain non-finite numbers")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("Workflow state object keys must be strings")
        for item in value.values():
            _validate_json_value(item)
        return
    raise ValueError("Workflow state values must be JSON-compatible")


@dataclass(frozen=True, slots=True)
class PostGenerationState:
    generation_id: UUID
    schema_version: int
    version: int
    data: dict[str, Any] = field(repr=False)
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PostGenerationStateSnapshot:
    generation_id: UUID
    version: int
    schema_version: int
    changed_section: PostWorkflowSection | None
    data: dict[str, Any] = field(repr=False)
    created_at: datetime


__all__ = [
    "LIST_SECTIONS",
    "OBJECT_SECTIONS",
    "WORKFLOW_STATE_SCHEMA_VERSION",
    "PostGenerationState",
    "PostGenerationStateSnapshot",
    "empty_workflow_state",
    "validate_section_value",
    "validate_workflow_state",
]
