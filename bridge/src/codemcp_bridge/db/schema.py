"""SQLite schema migrations for Bridge lifecycle and Git state."""

from __future__ import annotations

SCHEMA_VERSION = 3

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            owner_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK (
                status IN ('created', 'active', 'closing', 'closed', 'blocked')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT,
            reason TEXT
        );

        CREATE INDEX IF NOT EXISTS sessions_project_status_idx
            ON sessions(project_id, status);

        CREATE TABLE IF NOT EXISTS operations (
            operation_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            owner_id TEXT NOT NULL,
            client_request_id TEXT NOT NULL,
            request_hash TEXT NOT NULL,
            kind TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN (
                    'received', 'validated', 'awaiting_approval', 'dispatched',
                    'running', 'succeeded', 'failed', 'cancelled', 'unknown'
                )
            ),
            mutation INTEGER NOT NULL CHECK (mutation IN (0, 1)),
            input_json TEXT NOT NULL,
            result_json TEXT,
            error_json TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS operations_idempotency_idx
            ON operations(project_id, session_id, client_request_id);

        CREATE UNIQUE INDEX IF NOT EXISTS operations_active_mutation_idx
            ON operations(project_id)
            WHERE mutation = 1 AND state IN (
                'received', 'validated', 'awaiting_approval', 'dispatched', 'running', 'unknown'
            );

        CREATE INDEX IF NOT EXISTS operations_session_created_idx
            ON operations(session_id, created_at);

        CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY,
            operation_id TEXT NOT NULL UNIQUE REFERENCES operations(operation_id),
            project_id TEXT NOT NULL,
            session_id TEXT,
            action TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK (status IN ('pending', 'consumed', 'expired', 'cancelled')),
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS approvals_operation_status_idx
            ON approvals(operation_id, status);

        CREATE TABLE IF NOT EXISTS audit_events (
            event_id TEXT PRIMARY KEY,
            operation_id TEXT,
            project_id TEXT,
            session_id TEXT,
            owner_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS audit_events_operation_created_idx
            ON audit_events(operation_id, created_at);
        CREATE INDEX IF NOT EXISTS audit_events_project_created_idx
            ON audit_events(project_id, created_at);
        """,
    ),
    (
        2,
        """
        DROP INDEX IF EXISTS operations_active_mutation_idx;

        CREATE UNIQUE INDEX IF NOT EXISTS operations_active_mutation_idx
            ON operations(project_id)
            WHERE mutation = 1 AND state IN (
                'received', 'validated', 'awaiting_approval', 'dispatched', 'running', 'unknown'
            );
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            session_id TEXT,
            operation_id TEXT REFERENCES operations(operation_id),
            owner_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK (
                kind IN ('manual', 'mutation', 'rollback_safety')
            ),
            branch TEXT NOT NULL,
            head TEXT NOT NULL,
            ref_name TEXT NOT NULL UNIQUE,
            before_json TEXT NOT NULL,
            after_json TEXT,
            diff_hash TEXT,
            status TEXT NOT NULL CHECK (status IN ('created', 'restored')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS checkpoints_project_created_idx
            ON checkpoints(project_id, created_at);

        CREATE INDEX IF NOT EXISTS checkpoints_operation_idx
            ON checkpoints(operation_id, created_at);
        """,
    ),
)
