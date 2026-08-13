from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class DataType(StrEnum):
    """The public, intentionally small set of workflow port types."""

    DOCUMENT = "DOCUMENT"
    TEXT = "TEXT"
    OBJECT = "OBJECT"
    ARRAY_OBJECT = "ARRAY_OBJECT"
    BINARY = "BINARY"
    VOID = "VOID"


@dataclass(frozen=True)
class NodeContract:
    input_type: DataType
    output_type: DataType
    output_item_path: str


def item_count(value: Any, data_type: DataType) -> int:
    """Count logical items without treating a fetched document as empty."""
    if value is None:
        return 0
    if data_type == DataType.ARRAY_OBJECT:
        return len(value) if isinstance(value, list) else 0
    if data_type == DataType.VOID:
        return 0
    return 1


def schema_preview(value: Any, limit: int = 5) -> Any:
    """Small serialisable preview used by node runs and the editor."""
    if isinstance(value, list):
        return [schema_preview(item, limit) for item in value[:limit]]
    if isinstance(value, dict):
        return {key: schema_preview(item, limit) for key, item in list(value.items())[:limit]}
    if isinstance(value, str):
        return value[:500]
    return value


@dataclass
class ExecutionContext:
    run_id: str
    project_id: str
    workflow_version_id: str
    user_id: str | None = None
    variables: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)
    artifact_storage: Any | None = None
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    cancelled: bool = False
    effective_run_clock: datetime | None = None
    deadline_at: datetime | None = None
    stop_check: Callable[[], Awaitable[str | None]] | None = None
    heartbeat_interval_seconds: float = 5.0
    executable_plan: dict[str, Any] | None = None

    def log(self, level: str, message: str, **data: Any) -> None:
        from .redaction import redact_value

        values = list(self.secrets.values())
        self.logs.append({
            "level": level,
            "message": redact_value(message, values),
            "data": redact_value(data, values),
        })


class RunCancelledError(RuntimeError):
    """Raised cooperatively so network/browser tasks are cancelled promptly."""


class RunDeadlineExceededError(RuntimeError):
    """Raised when a run-wide deadline wins over an individual node timeout."""


class RunLeaseLostError(RuntimeError):
    """A previous worker may no longer write results after losing its lease."""


class WorkflowNode(Protocol):
    type: str

    async def execute(self, context: ExecutionContext, inputs: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]: ...
