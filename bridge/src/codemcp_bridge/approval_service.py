"""Short-lived, one-time approval tokens for high-risk operations."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .db import (
    ApprovalAlreadyUsed,
    ApprovalExpired,
    ApprovalNotFound,
    ApprovalTokenMismatch,
    Database,
    OperationRecord,
)
from .errors import BridgeError


@dataclass(frozen=True, slots=True)
class ApprovalGrant:
    approval_id: str
    token: str
    expires_at: str


def _now() -> datetime:
    return datetime.now(UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ApprovalService:
    def __init__(self, database: Database, *, ttl_seconds: float = 300):
        self._database = database
        self._ttl_seconds = ttl_seconds

    def issue(self, operation: OperationRecord, *, action: str) -> ApprovalGrant:
        token = secrets.token_urlsafe(32)
        expires_at = _timestamp(_now() + timedelta(seconds=self._ttl_seconds))
        approval_id = secrets.token_hex(16)
        self._database.create_approval(
            approval_id=approval_id,
            operation=operation,
            action=action,
            token_hash=_token_hash(token),
            expires_at=expires_at,
        )
        return ApprovalGrant(approval_id, token, expires_at)

    def consume(self, operation_id: str, token: str) -> OperationRecord:
        if not token or len(token) > 256:
            raise BridgeError("APPROVAL_INVALID", "approval token is invalid")
        try:
            return self._database.consume_approval(
                operation_id, _token_hash(token), now=_timestamp(_now())
            )
        except ApprovalNotFound as exc:
            raise BridgeError("APPROVAL_INVALID", "approval does not exist") from exc
        except ApprovalTokenMismatch as exc:
            raise BridgeError("APPROVAL_INVALID", "approval token is invalid") from exc
        except ApprovalExpired as exc:
            raise BridgeError("APPROVAL_EXPIRED", "approval has expired") from exc
        except ApprovalAlreadyUsed as exc:
            raise BridgeError("APPROVAL_ALREADY_USED", "approval is no longer pending") from exc

    def cancel(self, operation_id: str, *, reason: str) -> None:
        try:
            self._database.cancel_approval(operation_id, reason=reason)
        except ApprovalNotFound as exc:
            raise BridgeError(
                "OPERATION_NOT_CANCELABLE", "operation has no pending approval"
            ) from exc
