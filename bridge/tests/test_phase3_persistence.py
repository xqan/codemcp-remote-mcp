from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codemcp_bridge.approval_service import ApprovalService
from codemcp_bridge.db import Database
from codemcp_bridge.errors import BridgeError
from codemcp_bridge.operation_service import OperationService, request_hash
from codemcp_bridge.session_service import SessionService


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "bridge.sqlite3")
    database.initialize()
    return database


def _start(
    operations: OperationService,
    *,
    operation_id: str,
    project_id: str = "demo",
    session_id: str | None = "session-1",
    client_request_id: str | None = None,
    mutation: bool = True,
) -> object:
    input_data = {"operation_id": operation_id, "project_id": project_id}
    client_id = client_request_id or f"request-{operation_id}"
    return operations.start(
        operation_id=operation_id,
        project_id=project_id,
        session_id=session_id,
        kind="file_edit" if mutation else "file_read",
        mutation=mutation,
        client_request_id=client_id,
        supplied_request_hash=request_hash(input_data),
        input_data=input_data,
    ).record


def _finish_mutation(
    database: Database,
    *,
    operation_id: str,
    session_id: str,
    state: str = "succeeded",
) -> None:
    record, existing = database.create_operation(
        operation_id=operation_id,
        project_id="demo",
        session_id=session_id,
        owner_id="local-policy",
        client_request_id=f"request-{operation_id}",
        request_hash="0" * 64,
        kind="file_edit",
        mutation=True,
        input_data={"operation_id": operation_id},
    )
    assert not existing
    database.transition_operation(record.operation_id, "validated")
    database.transition_operation(record.operation_id, "dispatched")
    database.transition_operation(record.operation_id, "running")
    database.transition_operation(
        record.operation_id,
        state,
        result_data={"operation_id": operation_id, "status": state},
    )


def _add_checkpoint_evidence(
    database: Database,
    *,
    checkpoint_id: str,
    operation_id: str,
    session_id: str,
    before_head: str,
    after_head: str,
    branch: str = "main",
    after_branch: str | None = None,
    after_dirty: bool = False,
) -> None:
    database.create_checkpoint(
        checkpoint_id=checkpoint_id,
        project_id="demo",
        session_id=session_id,
        operation_id=operation_id,
        owner_id="local-policy",
        kind="mutation",
        branch=branch,
        head=before_head,
        ref_name=f"refs/codemcp-remote/checkpoints/{checkpoint_id}",
        before_data={
            "branch": branch,
            "head": before_head,
            "dirty": False,
            "changed_files": [],
            "file_hashes": {},
        },
    )
    database.finalize_checkpoint(
        checkpoint_id,
        after_data={
            "branch": after_branch if after_branch is not None else branch,
            "head": after_head,
            "dirty": after_dirty,
            "changed_files": ["src/hello.txt"],
            "file_hashes": {},
        },
        diff_hash="a" * 64,
    )


def test_schema_migrations_are_idempotent(tmp_path: Path) -> None:
    database = _database(tmp_path)
    with sqlite3.connect(database.path) as connection:
        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,), (2,), (3,)]
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_events'"
        ).fetchone() == ("audit_events",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'"
        ).fetchone() == ("checkpoints",)

    database.close()
    database.initialize()
    with sqlite3.connect(database.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone() == (3,)
    database.close()


def test_session_wip_evidence_requires_successful_non_noop_checkpoint(tmp_path: Path) -> None:
    database = _database(tmp_path)
    database.create_session("session-1", "demo", "local-policy")
    database.create_session("session-2", "demo", "local-policy")

    _finish_mutation(database, operation_id="op-valid", session_id="session-1")
    _add_checkpoint_evidence(
        database,
        checkpoint_id="1" * 32,
        operation_id="op-valid",
        session_id="session-1",
        before_head="1" * 40,
        after_head="2" * 40,
    )
    valid = database.find_session_wip_checkpoint(
        project_id="demo",
        session_id="session-1",
        branch="main",
        head="2" * 40,
    )
    assert valid is not None
    assert valid.checkpoint_id == "1" * 32

    _finish_mutation(database, operation_id="op-noop", session_id="session-1")
    _add_checkpoint_evidence(
        database,
        checkpoint_id="2" * 32,
        operation_id="op-noop",
        session_id="session-1",
        before_head="3" * 40,
        after_head="3" * 40,
    )
    assert (
        database.find_session_wip_checkpoint(
            project_id="demo",
            session_id="session-1",
            branch="main",
            head="3" * 40,
        )
        is None
    )

    _finish_mutation(database, operation_id="op-failed", session_id="session-1", state="failed")
    _add_checkpoint_evidence(
        database,
        checkpoint_id="3" * 32,
        operation_id="op-failed",
        session_id="session-1",
        before_head="4" * 40,
        after_head="5" * 40,
    )
    assert (
        database.find_session_wip_checkpoint(
            project_id="demo",
            session_id="session-1",
            branch="main",
            head="5" * 40,
        )
        is None
    )

    _finish_mutation(database, operation_id="op-unknown", session_id="session-1", state="unknown")
    _add_checkpoint_evidence(
        database,
        checkpoint_id="4" * 32,
        operation_id="op-unknown",
        session_id="session-1",
        before_head="6" * 40,
        after_head="7" * 40,
    )
    assert (
        database.find_session_wip_checkpoint(
            project_id="demo",
            session_id="session-1",
            branch="main",
            head="7" * 40,
        )
        is None
    )
    database.transition_operation(
        "op-unknown",
        "failed",
        error_data={"code": "UNKNOWN_SIDE_EFFECT"},
    )

    _finish_mutation(database, operation_id="op-other-session", session_id="session-2")
    _add_checkpoint_evidence(
        database,
        checkpoint_id="5" * 32,
        operation_id="op-other-session",
        session_id="session-2",
        before_head="8" * 40,
        after_head="9" * 40,
    )
    assert (
        database.find_session_wip_checkpoint(
            project_id="demo",
            session_id="session-1",
            branch="main",
            head="9" * 40,
        )
        is None
    )

    _finish_mutation(database, operation_id="op-branch", session_id="session-1")
    _add_checkpoint_evidence(
        database,
        checkpoint_id="6" * 32,
        operation_id="op-branch",
        session_id="session-1",
        before_head="a" * 40,
        after_head="b" * 40,
        branch="feature/wip",
    )
    assert (
        database.find_session_wip_checkpoint(
            project_id="demo",
            session_id="session-1",
            branch="main",
            head="b" * 40,
        )
        is None
    )
    database.close()


def test_request_hash_is_bound_to_canonical_input(tmp_path: Path) -> None:
    database = _database(tmp_path)
    operations = OperationService(database)

    with pytest.raises(BridgeError) as mismatch:
        operations.start(
            operation_id="op-mismatch",
            project_id="demo",
            session_id="session-1",
            kind="file_edit",
            mutation=True,
            client_request_id="edit-mismatch-1",
            supplied_request_hash=request_hash({"new_string": "unexpected"}),
            input_data={"new_string": "canonical"},
        )

    assert mismatch.value.code == "INVALID_REQUEST"
    assert database.get_operation("op-mismatch") is None
    database.close()


def test_idempotency_replays_without_repeating_and_detects_hash_conflict(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    operations = OperationService(database)
    first = _start(operations, operation_id="op-1", client_request_id="edit-1")
    operations.dispatch(first.operation_id)
    payload = {
        "operation_id": first.operation_id,
        "status": "succeeded",
        "data": {"changed": True},
        "error": None,
    }
    operations.finish(first.operation_id, state="succeeded", payload=payload)

    replay = operations.start(
        operation_id="op-retry",
        project_id="demo",
        session_id="session-1",
        kind="file_edit",
        mutation=True,
        client_request_id="edit-1",
        supplied_request_hash=request_hash({"operation_id": "op-1", "project_id": "demo"}),
        input_data={"operation_id": "op-1", "project_id": "demo"},
    )
    assert replay.is_replay
    assert replay.replay_payload == payload
    assert replay.record.operation_id == first.operation_id

    with pytest.raises(BridgeError) as conflict:
        operations.start(
            operation_id="op-conflict",
            project_id="demo",
            session_id="session-1",
            kind="file_edit",
            mutation=True,
            client_request_id="edit-1",
            supplied_request_hash=request_hash({"different": True}),
            input_data={"different": True},
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    database.close()


def test_project_mutation_lock_covers_validated_and_unknown_states(tmp_path: Path) -> None:
    database = _database(tmp_path)
    operations = OperationService(database)
    first = _start(operations, operation_id="op-1")

    with pytest.raises(BridgeError) as blocked:
        _start(operations, operation_id="op-2")
    assert blocked.value.code == "OPERATION_BLOCKED"

    operations.finish(
        first.operation_id,
        state="failed",
        payload={"operation_id": first.operation_id, "status": "failed", "error": None},
    )
    second = _start(operations, operation_id="op-2")
    operations.dispatch(second.operation_id)
    operations.finish(
        second.operation_id,
        state="unknown",
        payload={
            "operation_id": second.operation_id,
            "status": "unknown",
            "error": {"code": "UNKNOWN_SIDE_EFFECT"},
        },
    )
    with pytest.raises(BridgeError) as unknown_block:
        _start(operations, operation_id="op-3")
    assert unknown_block.value.code == "OPERATION_BLOCKED"
    operations.finish(
        second.operation_id,
        state="failed",
        payload={
            "operation_id": second.operation_id,
            "status": "failed",
            "error": {"code": "BRIDGE_RESTARTED"},
        },
    )
    assert _start(operations, operation_id="op-3").state == "validated"
    database.close()


def test_restart_blocks_sessions_and_classifies_in_flight_operations(tmp_path: Path) -> None:
    database = _database(tmp_path)
    sessions = SessionService(database)
    session = sessions.create("demo")
    operations = OperationService(database)
    pre_dispatch = _start(operations, operation_id="op-pre", session_id=session.session_id)
    in_flight = _start(operations, operation_id="op-flight", project_id="other")
    operations.dispatch(in_flight.operation_id)
    database.close()

    recovered_database = Database(database.path)
    recovered_database.initialize()
    recovered = recovered_database.recover_after_restart()
    assert recovered["sessions_blocked"] == [session.session_id]
    assert recovered["operations_failed"] == [pre_dispatch.operation_id]
    assert recovered["operations_unknown"] == [in_flight.operation_id]
    assert recovered_database.get_session(session.session_id).status == "blocked"
    assert recovered_database.get_operation(pre_dispatch.operation_id).error_data["code"] == (
        "BRIDGE_RESTARTED"
    )
    assert recovered_database.get_operation(in_flight.operation_id).state == "unknown"
    recovered_database.close()


def test_restart_cancels_pending_approval_and_does_not_leave_lock(tmp_path: Path) -> None:
    database = _database(tmp_path)
    operations = OperationService(database)
    approval_service = ApprovalService(database)
    operation = _start(operations, operation_id="op-approval")
    grant = approval_service.issue(operation, action="format:format")
    operations.await_approval(
        operation.operation_id,
        error_data={
            "code": "APPROVAL_REQUIRED",
            "details": {"approval_token": grant.token},
        },
    )
    database.close()

    recovered_database = Database(database.path)
    recovered_database.initialize()
    recovered = recovered_database.recover_after_restart()
    assert recovered["operations_cancelled"] == [operation.operation_id]
    recovered_operation = recovered_database.get_operation(operation.operation_id)
    assert recovered_operation.state == "cancelled"
    assert recovered_operation.error_data["code"] == "BRIDGE_RESTARTED"
    with sqlite3.connect(recovered_database.path) as connection:
        approval_id, status = connection.execute(
            "SELECT approval_id, status FROM approvals WHERE operation_id=?",
            (operation.operation_id,),
        ).fetchone()
        assert status == "cancelled"
    assert approval_id in recovered["approvals_cancelled"]
    assert "approval_token" not in recovered_operation.error_data.get("details", {})
    event_types = {
        event["event_type"]
        for event in recovered_database.list_audit_events(operation_id=operation.operation_id)
    }
    assert "approval.recovered_cancelled" in event_types
    recovered_database.close()


def test_approval_is_one_time_and_token_is_not_persisted(tmp_path: Path) -> None:
    database = _database(tmp_path)
    operations = OperationService(database)
    approval_service = ApprovalService(database)
    operation = _start(operations, operation_id="op-approval")
    grant = approval_service.issue(operation, action="format:format")
    operations.await_approval(
        operation.operation_id,
        error_data={
            "code": "APPROVAL_REQUIRED",
            "details": {"approval_token": grant.token},
        },
    )
    stored = database.get_operation(operation.operation_id)
    assert "approval_token" not in stored.error_data.get("details", {})

    with pytest.raises(BridgeError) as invalid:
        approval_service.consume(operation.operation_id, "wrong-token")
    assert invalid.value.code == "APPROVAL_INVALID"
    approval_service.consume(operation.operation_id, grant.token)
    with pytest.raises(BridgeError) as reused:
        approval_service.consume(operation.operation_id, grant.token)
    assert reused.value.code == "APPROVAL_ALREADY_USED"
    database.close()
