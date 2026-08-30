"""Read-only audit access for lifecycle diagnostics."""

from __future__ import annotations

from typing import Any

from .db import Database


class AuditStore:
    def __init__(self, database: Database):
        self._database = database

    def for_operation(self, operation_id: str) -> list[dict[str, Any]]:
        return self._database.list_audit_events(operation_id=operation_id)
