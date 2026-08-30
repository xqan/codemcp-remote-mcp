"""Small transactional SQLite store used by the Bridge services."""

from __future__ import annotations

import json
import sqlite3
import string
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..resource_auth import REPLAY_KEY_SEPARATOR, decode_replay_key
from .schema import MIGRATIONS, SCHEMA_VERSION

SESSION_STATUSES = {"created", "active", "closing", "closed", "blocked"}
OPERATION_STATES = {
    "received",
    "validated",
    "awaiting_approval",
    "dispatched",
    "running",
    "succeeded",
    "failed",
    "cancelled",
    "unknown",
}
TERMINAL_OPERATION_STATES = {"succeeded", "failed", "cancelled", "unknown"}
ACTIVE_MUTATION_STATES = {
    "received",
    "validated",
    "awaiting_approval",
    "dispatched",
    "running",
    "unknown",
}
SESSION_TRANSITIONS = {
    "created": {"active", "closed", "blocked"},
    "active": {"closing", "blocked"},
    "closing": {"closed", "blocked"},
    "closed": set(),
    "blocked": set(),
}
OPERATION_TRANSITIONS = {
    "received": {"validated", "failed", "cancelled"},
    "validated": {"awaiting_approval", "dispatched", "failed", "cancelled"},
    "awaiting_approval": {"dispatched", "cancelled", "failed"},
    "dispatched": {"running", "failed", "unknown"},
    "running": {"succeeded", "failed", "unknown", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
    "unknown": {"failed", "succeeded"},
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _is_git_head(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) in {40, 64}
        and all(character in string.hexdigits for character in value)
    )


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in string.hexdigits for character in value)
    )


@dataclass(frozen=True, slots=True)
class SessionRecord:
    session_id: str
    project_id: str
    owner_id: str
    status: str
    created_at: str
    updated_at: str
    closed_at: str | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class OperationRecord:
    operation_id: str
    project_id: str
    session_id: str | None
    owner_id: str
    client_request_id: str
    request_hash: str
    kind: str
    state: str
    mutation: bool
    input_json: str
    result_json: str | None
    error_json: str | None
    created_at: str
    updated_at: str
    started_at: str | None
    finished_at: str | None

    @property
    def input_data(self) -> dict[str, Any]:
        return json.loads(self.input_json)

    @property
    def result_data(self) -> dict[str, Any] | None:
        return json.loads(self.result_json) if self.result_json else None

    @property
    def error_data(self) -> dict[str, Any] | None:
        return json.loads(self.error_json) if self.error_json else None


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    checkpoint_id: str
    project_id: str
    session_id: str | None
    operation_id: str | None
    owner_id: str
    kind: str
    branch: str
    head: str
    ref_name: str
    before_json: str
    after_json: str | None
    diff_hash: str | None
    status: str
    created_at: str
    updated_at: str

    @property
    def before_data(self) -> dict[str, Any]:
        return json.loads(self.before_json)

    @property
    def after_data(self) -> dict[str, Any] | None:
        return json.loads(self.after_json) if self.after_json else None


class PersistenceError(RuntimeError):
    """Base class for expected persistence failures."""


class InvalidTransition(PersistenceError):
    pass


class ActiveOperationConflict(PersistenceError):
    def __init__(self, operation: OperationRecord):
        self.operation = operation
        super().__init__(
            f"project {operation.project_id} has active operation {operation.operation_id}"
        )


class ApprovalNotFound(PersistenceError):
    pass


class ApprovalTokenMismatch(PersistenceError):
    pass


class ApprovalAlreadyUsed(PersistenceError):
    pass


class ApprovalExpired(PersistenceError):
    pass


class Database:
    """A single-process SQLite store with explicit transaction boundaries."""

    def __init__(self, path: Path):
        self.path = path
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    def initialize(self) -> None:
        with self._lock:
            if self._connection is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(
                self.path,
                timeout=30,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            current = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
            if current > SCHEMA_VERSION:
                connection.close()
                raise PersistenceError("database schema is newer than this Bridge")
            self._connection = connection
            try:
                for version, migration in MIGRATIONS:
                    if version <= current:
                        continue
                    with self._transaction():
                        connection.executescript(migration)
                        connection.execute(
                            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                            (version, utc_now()),
                        )
            except Exception:
                self._connection = None
                connection.close()
                raise

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._require_connection()
        with self._lock:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise PersistenceError("database is not initialized")
        return self._connection

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        *,
        operation_id: str | None,
        project_id: str | None,
        session_id: str | None,
        owner_id: str,
        event_type: str,
        from_state: str | None,
        to_state: str | None,
        details: dict[str, Any],
    ) -> None:
        connection.execute(
            "INSERT INTO audit_events("
            "event_id, operation_id, project_id, session_id, owner_id, event_type, "
            "from_state, to_state, details_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                operation_id,
                project_id,
                session_id,
                owner_id,
                event_type,
                from_state,
                to_state,
                json_dumps(details),
                utc_now(),
            ),
        )

    @staticmethod
    def _session(row: sqlite3.Row | None) -> SessionRecord | None:
        if row is None:
            return None
        return SessionRecord(
            session_id=row["session_id"],
            project_id=row["project_id"],
            owner_id=row["owner_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            closed_at=row["closed_at"],
            reason=row["reason"],
        )

    @staticmethod
    def _operation(row: sqlite3.Row | None) -> OperationRecord | None:
        if row is None:
            return None
        return OperationRecord(
            operation_id=row["operation_id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            owner_id=row["owner_id"],
            client_request_id=decode_replay_key(row["client_request_id"]),
            request_hash=row["request_hash"],
            kind=row["kind"],
            state=row["state"],
            mutation=bool(row["mutation"]),
            input_json=row["input_json"],
            result_json=row["result_json"],
            error_json=row["error_json"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
        )

    @staticmethod
    def _checkpoint(row: sqlite3.Row | None) -> CheckpointRecord | None:
        if row is None:
            return None
        return CheckpointRecord(
            checkpoint_id=row["checkpoint_id"],
            project_id=row["project_id"],
            session_id=row["session_id"],
            operation_id=row["operation_id"],
            owner_id=row["owner_id"],
            kind=row["kind"],
            branch=row["branch"],
            head=row["head"],
            ref_name=row["ref_name"],
            before_json=row["before_json"],
            after_json=row["after_json"],
            diff_hash=row["diff_hash"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_session(
        self,
        session_id: str,
        project_id: str,
        owner_id: str,
        *,
        auth_details: dict[str, Any] | None = None,
    ) -> SessionRecord:
        now = utc_now()
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO sessions(session_id, project_id, owner_id, status, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, 'created', ?, ?)",
                (session_id, project_id, owner_id, now, now),
            )
            self._audit(
                connection,
                operation_id=None,
                project_id=project_id,
                session_id=session_id,
                owner_id=owner_id,
                event_type="session.created",
                from_state=None,
                to_state="created",
                details={},
            )
            connection.execute(
                "UPDATE sessions SET status='active', updated_at=? WHERE session_id=?",
                (utc_now(), session_id),
            )
            self._audit(
                connection,
                operation_id=None,
                project_id=project_id,
                session_id=session_id,
                owner_id=owner_id,
                event_type="session.activated",
                from_state="created",
                to_state="active",
                details={},
            )
            if auth_details is not None:
                self._audit(
                    connection,
                    operation_id=None,
                    project_id=project_id,
                    session_id=session_id,
                    owner_id=owner_id,
                    event_type="auth.session.context",
                    from_state=None,
                    to_state=None,
                    details=auth_details,
                )
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        result = self._session(row)
        assert result is not None
        return result

    def get_session(self, session_id: str) -> SessionRecord | None:
        connection = self._require_connection()
        with self._lock:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        return self._session(row)

    def get_session_auth_context(self, session_id: str) -> dict[str, Any] | None:
        connection = self._require_connection()
        with self._lock:
            row = connection.execute(
                "SELECT details_json FROM audit_events "
                "WHERE session_id=? AND event_type='auth.session.context' "
                "ORDER BY created_at, event_id LIMIT 1",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            details = json.loads(row["details_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PersistenceError("session auth context is malformed") from exc
        if not isinstance(details, dict):
            raise PersistenceError("session auth context must be a JSON object")
        return details

    def transition_session(
        self, session_id: str, to_state: str, *, reason: str | None = None
    ) -> SessionRecord:
        if to_state not in SESSION_STATUSES:
            raise InvalidTransition(f"unknown session state: {to_state}")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            current = self._session(row)
            if current is None:
                raise PersistenceError("session not found")
            if to_state != current.status and to_state not in SESSION_TRANSITIONS[current.status]:
                raise InvalidTransition(
                    f"invalid session transition {current.status} -> {to_state}"
                )
            now = utc_now()
            closed_at = now if to_state in {"closed", "blocked"} else current.closed_at
            connection.execute(
                "UPDATE sessions SET status=?, updated_at=?, closed_at=?, reason=? "
                "WHERE session_id=?",
                (to_state, now, closed_at, reason, session_id),
            )
            if to_state != current.status:
                self._audit(
                    connection,
                    operation_id=None,
                    project_id=current.project_id,
                    session_id=current.session_id,
                    owner_id=current.owner_id,
                    event_type="session.transition",
                    from_state=current.status,
                    to_state=to_state,
                    details={"reason": reason} if reason else {},
                )
            updated = connection.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)
            ).fetchone()
        result = self._session(updated)
        assert result is not None
        return result

    def close_active_sessions(self, reason: str) -> None:
        connection = self._require_connection()
        with self._lock:
            session_ids = [
                row["session_id"]
                for row in connection.execute(
                    "SELECT session_id FROM sessions WHERE status IN ('active', 'closing')"
                ).fetchall()
            ]
        for session_id in session_ids:
            current = self.get_session(session_id)
            if current is None or current.status == "closed":
                continue
            if current.status == "active":
                self.transition_session(session_id, "closing", reason=reason)
            self.transition_session(session_id, "closed", reason=reason)

    def block_active_sessions_for_project(self, project_id: str, reason: str) -> list[str]:
        connection = self._require_connection()
        with self._lock:
            session_ids = [
                row["session_id"]
                for row in connection.execute(
                    "SELECT session_id FROM sessions "
                    "WHERE project_id=? AND status IN ('active', 'closing')",
                    (project_id,),
                ).fetchall()
            ]
        blocked: list[str] = []
        for session_id in session_ids:
            current = self.get_session(session_id)
            if current is None or current.status not in {"active", "closing"}:
                continue
            self.transition_session(session_id, "blocked", reason=reason)
            blocked.append(session_id)
        return blocked

    def recover_after_restart(self) -> dict[str, list[str]]:
        recovered = {
            "sessions_blocked": [],
            "operations_cancelled": [],
            "operations_failed": [],
            "operations_unknown": [],
            "approvals_cancelled": [],
        }
        with self._transaction() as connection:
            sessions = connection.execute(
                "SELECT * FROM sessions WHERE status IN ('active', 'closing')"
            ).fetchall()
            for row in sessions:
                connection.execute(
                    "UPDATE sessions SET status='blocked', updated_at=?, closed_at=?, reason=? "
                    "WHERE session_id=?",
                    (utc_now(), utc_now(), "bridge_restart", row["session_id"]),
                )
                self._audit(
                    connection,
                    operation_id=None,
                    project_id=row["project_id"],
                    session_id=row["session_id"],
                    owner_id=row["owner_id"],
                    event_type="session.recovered_blocked",
                    from_state=row["status"],
                    to_state="blocked",
                    details={"reason": "bridge_restart"},
                )
                recovered["sessions_blocked"].append(row["session_id"])

            operations = connection.execute(
                "SELECT * FROM operations WHERE state IN "
                "('received', 'validated', 'awaiting_approval', 'dispatched', 'running')"
            ).fetchall()
            for row in operations:
                mutation = bool(row["mutation"])
                if row["state"] == "awaiting_approval":
                    to_state = "cancelled"
                else:
                    to_state = (
                        "unknown"
                        if mutation and row["state"] in {"dispatched", "running"}
                        else "failed"
                    )
                error = (
                    {
                        "code": "UNKNOWN_SIDE_EFFECT",
                        "message": "Bridge restarted while mutation was in flight",
                        "retryable": False,
                    }
                    if to_state == "unknown"
                    else {
                        "code": "BRIDGE_RESTARTED",
                        "message": (
                            "approval was cancelled because Bridge restarted"
                            if row["state"] == "awaiting_approval"
                            else "operation did not reach execution before Bridge restart"
                        ),
                        "retryable": True,
                    }
                )
                if row["state"] == "awaiting_approval":
                    pending_approvals = connection.execute(
                        "SELECT approval_id, action FROM approvals "
                        "WHERE operation_id=? AND status='pending'",
                        (row["operation_id"],),
                    ).fetchall()
                    connection.execute(
                        "UPDATE approvals SET status='cancelled' "
                        "WHERE operation_id=? AND status='pending'",
                        (row["operation_id"],),
                    )
                    for approval in pending_approvals:
                        self._audit(
                            connection,
                            operation_id=row["operation_id"],
                            project_id=row["project_id"],
                            session_id=row["session_id"],
                            owner_id=row["owner_id"],
                            event_type="approval.recovered_cancelled",
                            from_state="pending",
                            to_state="cancelled",
                            details={
                                "approval_id": approval["approval_id"],
                                "action": approval["action"],
                                "reason": "bridge_restart",
                            },
                        )
                        recovered["approvals_cancelled"].append(approval["approval_id"])
                connection.execute(
                    "UPDATE operations SET state=?, updated_at=?, finished_at=?, error_json=? "
                    "WHERE operation_id=?",
                    (to_state, utc_now(), utc_now(), json_dumps(error), row["operation_id"]),
                )
                self._audit(
                    connection,
                    operation_id=row["operation_id"],
                    project_id=row["project_id"],
                    session_id=row["session_id"],
                    owner_id=row["owner_id"],
                    event_type="operation.recovered",
                    from_state=row["state"],
                    to_state=to_state,
                    details=error,
                )
                if to_state == "unknown":
                    key = "operations_unknown"
                elif to_state == "cancelled":
                    key = "operations_cancelled"
                else:
                    key = "operations_failed"
                recovered[key].append(row["operation_id"])

            orphaned_approvals = connection.execute(
                "SELECT a.approval_id, a.operation_id, a.project_id, a.session_id, "
                "o.owner_id, a.action FROM approvals a "
                "JOIN operations o ON o.operation_id=a.operation_id "
                "WHERE a.status='pending' AND o.state != 'awaiting_approval'"
            ).fetchall()
            for row in orphaned_approvals:
                connection.execute(
                    "UPDATE approvals SET status='cancelled' "
                    "WHERE approval_id=? AND status='pending'",
                    (row["approval_id"],),
                )
                self._audit(
                    connection,
                    operation_id=row["operation_id"],
                    project_id=row["project_id"],
                    session_id=row["session_id"],
                    owner_id=row["owner_id"],
                    event_type="approval.recovered_cancelled",
                    from_state="pending",
                    to_state="cancelled",
                    details={"reason": "bridge_restart", "action": row["action"]},
                )
                recovered["approvals_cancelled"].append(row["approval_id"])
        return recovered

    def create_operation(
        self,
        *,
        operation_id: str,
        project_id: str,
        session_id: str | None,
        owner_id: str,
        client_request_id: str,
        request_hash: str,
        kind: str,
        mutation: bool,
        input_data: dict[str, Any],
    ) -> tuple[OperationRecord, bool]:
        with self._transaction() as connection:
            existing_row = connection.execute(
                "SELECT * FROM operations WHERE project_id=? AND session_id IS ? "
                "AND client_request_id=?",
                (project_id, session_id, client_request_id),
            ).fetchone()
            existing = self._operation(existing_row)
            if existing is not None:
                return existing, True
            if mutation:
                active_row = connection.execute(
                    "SELECT * FROM operations WHERE project_id=? AND mutation=1 "
                    "AND state IN ('received', 'validated', 'awaiting_approval', "
                    "'dispatched', 'running', 'unknown') "
                    "LIMIT 1",
                    (project_id,),
                ).fetchone()
                active = self._operation(active_row)
                if active is not None:
                    raise ActiveOperationConflict(active)
            now = utc_now()
            connection.execute(
                "INSERT INTO operations(operation_id, project_id, session_id, owner_id, "
                "client_request_id, "
                "request_hash, kind, state, mutation, input_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'received', ?, ?, ?, ?)",
                (
                    operation_id,
                    project_id,
                    session_id,
                    owner_id,
                    client_request_id,
                    request_hash,
                    kind,
                    int(mutation),
                    json_dumps(input_data),
                    now,
                    now,
                ),
            )
            self._audit(
                connection,
                operation_id=operation_id,
                project_id=project_id,
                session_id=session_id,
                owner_id=owner_id,
                event_type="operation.created",
                from_state=None,
                to_state="received",
                details={"kind": kind, "mutation": mutation},
            )
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
        result = self._operation(row)
        assert result is not None
        return result, False

    def get_operation(self, operation_id: str) -> OperationRecord | None:
        connection = self._require_connection()
        with self._lock:
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
        return self._operation(row)

    def get_operation_by_client_request_id(
        self,
        *,
        project_id: str,
        session_id: str | None,
        client_request_id: str,
    ) -> OperationRecord | None:
        """Find one exact persisted replay key, including legacy unscoped keys."""

        connection = self._require_connection()
        with self._lock:
            row = connection.execute(
                "SELECT * FROM operations WHERE project_id=? AND session_id IS ? "
                "AND client_request_id=?",
                (project_id, session_id, client_request_id),
            ).fetchone()
        return self._operation(row)

    def get_operations_by_client_request_id(
        self,
        *,
        project_id: str,
        session_id: str | None,
        client_request_id: str,
    ) -> list[OperationRecord]:
        """Find all namespace variants for legacy OAuth identity checks."""

        connection = self._require_connection()
        escaped_request_id = (
            client_request_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        with self._lock:
            rows = connection.execute(
                "SELECT * FROM operations WHERE project_id=? AND session_id IS ? "
                "AND (client_request_id=? OR client_request_id LIKE ? ESCAPE '\\') "
                "ORDER BY created_at, operation_id",
                (
                    project_id,
                    session_id,
                    client_request_id,
                    f"%{REPLAY_KEY_SEPARATOR}{escaped_request_id}",
                ),
            ).fetchall()
        records = [self._operation(row) for row in rows]
        return [
            record
            for record in records
            if record is not None and record.client_request_id == client_request_id
        ]

    def create_checkpoint(
        self,
        *,
        checkpoint_id: str,
        project_id: str,
        session_id: str | None,
        operation_id: str | None,
        owner_id: str,
        kind: str,
        branch: str,
        head: str,
        ref_name: str,
        before_data: dict[str, Any],
    ) -> CheckpointRecord:
        now = utc_now()
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO checkpoints("
                "checkpoint_id, project_id, session_id, operation_id, owner_id, kind, "
                "branch, head, ref_name, before_json, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)",
                (
                    checkpoint_id,
                    project_id,
                    session_id,
                    operation_id,
                    owner_id,
                    kind,
                    branch,
                    head,
                    ref_name,
                    json_dumps(before_data),
                    now,
                    now,
                ),
            )
            self._audit(
                connection,
                operation_id=operation_id,
                project_id=project_id,
                session_id=session_id,
                owner_id=owner_id,
                event_type="checkpoint.created",
                from_state=None,
                to_state="created",
                details={
                    "checkpoint_id": checkpoint_id,
                    "kind": kind,
                    "branch": branch,
                    "head": head,
                    "ref_name": ref_name,
                },
            )
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)
            ).fetchone()
        result = self._checkpoint(row)
        assert result is not None
        return result

    def finalize_checkpoint(
        self,
        checkpoint_id: str,
        *,
        after_data: dict[str, Any],
        diff_hash: str,
    ) -> CheckpointRecord:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)
            ).fetchone()
            current = self._checkpoint(row)
            if current is None:
                raise PersistenceError("checkpoint not found")
            now = utc_now()
            connection.execute(
                "UPDATE checkpoints SET after_json=?, diff_hash=?, updated_at=? "
                "WHERE checkpoint_id=?",
                (json_dumps(after_data), diff_hash, now, checkpoint_id),
            )
            self._audit(
                connection,
                operation_id=current.operation_id,
                project_id=current.project_id,
                session_id=current.session_id,
                owner_id=current.owner_id,
                event_type="checkpoint.finalized",
                from_state=current.status,
                to_state=current.status,
                details={
                    "checkpoint_id": checkpoint_id,
                    "after_head": after_data.get("head"),
                    "diff_hash": diff_hash,
                },
            )
            updated = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)
            ).fetchone()
        result = self._checkpoint(updated)
        assert result is not None
        return result

    def mark_checkpoint_restored(self, checkpoint_id: str) -> CheckpointRecord:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)
            ).fetchone()
            current = self._checkpoint(row)
            if current is None:
                raise PersistenceError("checkpoint not found")
            now = utc_now()
            connection.execute(
                "UPDATE checkpoints SET status='restored', updated_at=? WHERE checkpoint_id=?",
                (now, checkpoint_id),
            )
            self._audit(
                connection,
                operation_id=current.operation_id,
                project_id=current.project_id,
                session_id=current.session_id,
                owner_id=current.owner_id,
                event_type="checkpoint.restored",
                from_state=current.status,
                to_state="restored",
                details={"checkpoint_id": checkpoint_id, "head": current.head},
            )
            updated = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)
            ).fetchone()
        result = self._checkpoint(updated)
        assert result is not None
        return result

    def get_checkpoint(self, checkpoint_id: str) -> CheckpointRecord | None:
        connection = self._require_connection()
        with self._lock:
            row = connection.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)
            ).fetchone()
        return self._checkpoint(row)

    def list_checkpoints(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        operation_id: str | None = None,
    ) -> list[CheckpointRecord]:
        connection = self._require_connection()
        clauses: list[str] = []
        values: list[str] = []
        if project_id is not None:
            clauses.append("project_id=?")
            values.append(project_id)
        if session_id is not None:
            clauses.append("session_id=?")
            values.append(session_id)
        if operation_id is not None:
            clauses.append("operation_id=?")
            values.append(operation_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = connection.execute(
                f"SELECT * FROM checkpoints{where} ORDER BY created_at, checkpoint_id",
                values,
            ).fetchall()
        return [checkpoint for row in rows if (checkpoint := self._checkpoint(row))]

    def find_session_wip_checkpoint(
        self,
        *,
        project_id: str,
        session_id: str,
        branch: str,
        head: str,
    ) -> CheckpointRecord | None:
        """Find a finalized successful mutation that proves session WIP ownership.

        This is intentionally a read-only evidence query.  JSON fields are
        validated in Python so malformed historical data is treated as absent
        evidence instead of being allowed to authorize a later amend.
        """

        if not _is_git_head(head) or not isinstance(branch, str) or not branch:
            return None
        connection = self._require_connection()
        with self._lock:
            rows = connection.execute(
                "SELECT c.* FROM checkpoints c "
                "JOIN operations o ON o.operation_id=c.operation_id "
                "JOIN sessions s ON s.session_id=c.session_id "
                "WHERE c.project_id=? AND c.session_id=? "
                "AND c.kind='mutation' AND c.status='created' "
                "AND c.after_json IS NOT NULL AND c.diff_hash IS NOT NULL "
                "AND o.project_id=c.project_id AND o.session_id=c.session_id "
                "AND o.owner_id='local-policy' AND o.mutation=1 AND o.state='succeeded' "
                "AND s.project_id=c.project_id AND s.owner_id='local-policy' "
                "AND s.status='active' AND c.owner_id='local-policy' "
                "ORDER BY c.created_at DESC, c.checkpoint_id DESC",
                (project_id, session_id),
            ).fetchall()

        normalized_head = head.lower()
        for row in rows:
            checkpoint = self._checkpoint(row)
            if checkpoint is None:
                continue
            try:
                before = checkpoint.before_data
                after = checkpoint.after_data
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if not isinstance(before, dict) or not isinstance(after, dict):
                continue
            before_head = before.get("head")
            after_head = after.get("head")
            before_branch = before.get("branch")
            after_branch = after.get("branch")
            if not all(
                isinstance(value, str)
                for value in (before_head, after_head, before_branch, after_branch)
            ):
                continue
            if not _is_git_head(before_head) or not _is_git_head(after_head):
                continue
            if checkpoint.head.lower() != before_head.lower() or checkpoint.branch != before_branch:
                continue
            if not isinstance(after.get("dirty"), bool) or after["dirty"]:
                continue
            if before_head.lower() == after_head.lower():
                continue
            if after_branch != branch or after_head.lower() != normalized_head:
                continue
            if before_branch != after_branch:
                continue
            if not _is_sha256(checkpoint.diff_hash):
                continue
            return checkpoint
        return None

    def transition_operation(
        self,
        operation_id: str,
        to_state: str,
        *,
        expected_state: str | None = None,
        result_data: dict[str, Any] | None = None,
        error_data: dict[str, Any] | None = None,
    ) -> OperationRecord:
        if to_state not in OPERATION_STATES:
            raise InvalidTransition(f"unknown operation state: {to_state}")
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
            current = self._operation(row)
            if current is None:
                raise PersistenceError("operation not found")
            if expected_state is not None and current.state != expected_state:
                raise InvalidTransition(
                    f"operation {operation_id} expected {expected_state}, got {current.state}"
                )
            if to_state != current.state and to_state not in OPERATION_TRANSITIONS[current.state]:
                raise InvalidTransition(
                    f"invalid operation transition {current.state} -> {to_state}"
                )
            now = utc_now()
            started_at = (
                now if to_state == "running" and current.started_at is None else current.started_at
            )
            finished_at = now if to_state in TERMINAL_OPERATION_STATES else current.finished_at
            connection.execute(
                "UPDATE operations SET state=?, updated_at=?, started_at=?, finished_at=?, "
                "result_json=?, error_json=? WHERE operation_id=?",
                (
                    to_state,
                    now,
                    started_at,
                    finished_at,
                    json_dumps(result_data) if result_data is not None else current.result_json,
                    json_dumps(error_data) if error_data is not None else current.error_json,
                    operation_id,
                ),
            )
            if to_state != current.state:
                self._audit(
                    connection,
                    operation_id=operation_id,
                    project_id=current.project_id,
                    session_id=current.session_id,
                    owner_id=current.owner_id,
                    event_type="operation.transition",
                    from_state=current.state,
                    to_state=to_state,
                    details={
                        "kind": current.kind,
                        "error_code": error_data.get("code") if error_data else None,
                    },
                )
            updated = connection.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
        result = self._operation(updated)
        assert result is not None
        return result

    def record_operation_auth_context(
        self,
        operation_id: str,
        *,
        details: dict[str, Any],
    ) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM operations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            operation = self._operation(row)
            if operation is None:
                raise PersistenceError("operation not found")
            self._audit(
                connection,
                operation_id=operation.operation_id,
                project_id=operation.project_id,
                session_id=operation.session_id,
                owner_id=operation.owner_id,
                event_type="auth.context",
                from_state=None,
                to_state=None,
                details=details,
            )

    def get_operation_auth_context(self, operation_id: str) -> dict[str, Any] | None:
        connection = self._require_connection()
        with self._lock:
            row = connection.execute(
                "SELECT details_json FROM audit_events "
                "WHERE operation_id=? AND event_type='auth.context' "
                "ORDER BY created_at, event_id LIMIT 1",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            details = json.loads(row["details_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PersistenceError("operation auth context is malformed") from exc
        if not isinstance(details, dict):
            raise PersistenceError("operation auth context must be a JSON object")
        return details

    def create_approval(
        self,
        *,
        approval_id: str,
        operation: OperationRecord,
        action: str,
        token_hash: str,
        expires_at: str,
    ) -> None:
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO approvals(approval_id, operation_id, project_id, session_id, action, "
                "token_hash, status, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                (
                    approval_id,
                    operation.operation_id,
                    operation.project_id,
                    operation.session_id,
                    action,
                    token_hash,
                    utc_now(),
                    expires_at,
                ),
            )
            self._audit(
                connection,
                operation_id=operation.operation_id,
                project_id=operation.project_id,
                session_id=operation.session_id,
                owner_id=operation.owner_id,
                event_type="approval.created",
                from_state=None,
                to_state="pending",
                details={"approval_id": approval_id, "action": action, "expires_at": expires_at},
            )

    def consume_approval(self, operation_id: str, token_hash: str, *, now: str) -> OperationRecord:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT a.*, o.* FROM approvals a "
                "JOIN operations o ON o.operation_id=a.operation_id "
                "WHERE a.operation_id=?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise ApprovalNotFound("approval not found")
            status = row["status"]
            if row["token_hash"] != token_hash:
                raise ApprovalTokenMismatch("approval token is invalid")
            if status != "pending":
                if status == "expired":
                    raise ApprovalExpired("approval has expired")
                raise ApprovalAlreadyUsed("approval is no longer pending")
            if row["expires_at"] <= now:
                connection.execute(
                    "UPDATE approvals SET status='expired' WHERE operation_id=?",
                    (operation_id,),
                )
                self._audit(
                    connection,
                    operation_id=operation_id,
                    project_id=row["project_id"],
                    session_id=row["session_id"],
                    owner_id=row["owner_id"],
                    event_type="approval.expired",
                    from_state="pending",
                    to_state="expired",
                    details={},
                )
                raise ApprovalExpired("approval has expired")
            connection.execute(
                "UPDATE approvals SET status='consumed', consumed_at=? WHERE operation_id=?",
                (now, operation_id),
            )
            self._audit(
                connection,
                operation_id=operation_id,
                project_id=row["project_id"],
                session_id=row["session_id"],
                owner_id=row["owner_id"],
                event_type="approval.consumed",
                from_state="pending",
                to_state="consumed",
                details={},
            )
            operation_row = connection.execute(
                "SELECT * FROM operations WHERE operation_id=?", (operation_id,)
            ).fetchone()
        result = self._operation(operation_row)
        assert result is not None
        return result

    def cancel_approval(self, operation_id: str, *, reason: str) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT a.*, o.owner_id FROM approvals a "
                "JOIN operations o ON o.operation_id=a.operation_id "
                "WHERE a.operation_id=? AND a.status='pending'",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise ApprovalNotFound("pending approval not found")
            connection.execute(
                "UPDATE approvals SET status='cancelled' WHERE operation_id=?",
                (operation_id,),
            )
            self._audit(
                connection,
                operation_id=operation_id,
                project_id=row["project_id"],
                session_id=row["session_id"],
                owner_id=row["owner_id"],
                event_type="approval.cancelled",
                from_state="pending",
                to_state="cancelled",
                details={"reason": reason},
            )

    def list_audit_events(self, *, operation_id: str | None = None) -> list[dict[str, Any]]:
        connection = self._require_connection()
        with self._lock:
            if operation_id is None:
                rows = connection.execute(
                    "SELECT * FROM audit_events ORDER BY created_at, event_id"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM audit_events WHERE operation_id=? ORDER BY created_at, event_id",
                    (operation_id,),
                ).fetchall()
        return [
            {
                "event_id": row["event_id"],
                "operation_id": row["operation_id"],
                "project_id": row["project_id"],
                "session_id": row["session_id"],
                "owner_id": row["owner_id"],
                "event_type": row["event_type"],
                "from_state": row["from_state"],
                "to_state": row["to_state"],
                "details": json.loads(row["details_json"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
