"""Structured errors used by the local Bridge boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ERROR_CODES = frozenset(
    {
        "INVALID_REQUEST",
        "PROJECT_NOT_ALLOWED",
        "PATH_ESCAPE",
        "SENSITIVE_PATH",
        "FILE_NOT_FOUND",
        "FILE_TOO_LARGE",
        "BINARY_FILE",
        "COMMAND_NOT_ALLOWED",
        "APPROVAL_REQUIRED",
        "WORKSPACE_DIRTY",
        "BRANCH_NOT_ALLOWED",
        "SESSION_REQUIRED",
        "SESSION_NOT_FOUND",
        "BRIDGE_RESTARTED",
        "IDEMPOTENCY_CONFLICT",
        "OPERATION_NOT_FOUND",
        "OPERATION_IN_PROGRESS",
        "OPERATION_BLOCKED",
        "APPROVAL_INVALID",
        "APPROVAL_EXPIRED",
        "APPROVAL_ALREADY_USED",
        "OPERATION_NOT_CANCELABLE",
        "RECONCILE_REQUIRED",
        "CONFLICT",
        "BACKEND_UNAVAILABLE",
        "UNKNOWN_SIDE_EFFECT",
        "CHECKPOINT_NOT_FOUND",
        "CHECKPOINT_INVALID",
        "CHECKPOINT_CONFLICT",
    }
)


@dataclass(slots=True)
class BridgeError(Exception):
    """An expected, safe-to-return Bridge error."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    status: str = "rejected"

    def __post_init__(self) -> None:
        if self.code not in ERROR_CODES:
            raise ValueError(f"unknown Bridge error code: {self.code}")
        Exception.__init__(self, self.message)

    def as_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "retryable": self.retryable,
        }


def success_payload(
    *,
    request_id: str,
    session_id: str | None,
    project_id: str | None,
    operation_id: str,
    data: dict[str, Any] | None = None,
    changed_files: list[str] | None = None,
    truncated: bool = False,
    status: str = "succeeded",
) -> dict[str, Any]:
    """Build the stable envelope returned by every Bridge tool."""

    return {
        "request_id": request_id,
        "session_id": session_id,
        "project_id": project_id,
        "operation_id": operation_id,
        "status": status,
        "changed_files": changed_files or [],
        "truncated": truncated,
        "data": data or {},
        "error": None,
    }


def error_payload(
    *,
    request_id: str,
    session_id: str | None,
    project_id: str | None,
    operation_id: str,
    error: BridgeError,
) -> dict[str, Any]:
    """Build a structured error without exposing an internal traceback."""

    return {
        "request_id": request_id,
        "session_id": session_id,
        "project_id": project_id,
        "operation_id": operation_id,
        "status": error.status,
        "changed_files": [],
        "truncated": False,
        "data": {},
        "error": error.as_payload(),
    }
