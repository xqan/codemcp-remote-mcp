from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from codemcp_bridge.checkpoint_service import CheckpointService
from codemcp_bridge.db import Database
from codemcp_bridge.errors import BridgeError
from codemcp_bridge.git_guard import CommitMode, GitGuard
from codemcp_bridge.mcp_server import BridgeService
from codemcp_bridge.operation_service import request_hash
from codemcp_bridge.settings import (
    BridgeSettings,
    CodemcpSettings,
    PolicySettings,
    ProjectSpec,
    ServerSettings,
    StorageSettings,
)


def _git(project: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def _project_spec(project: Path) -> ProjectSpec:
    return ProjectSpec(
        project_id="demo",
        root=project,
        allowed_branches=("main",),
        require_clean_workspace=True,
        codemcp_config=project / "codemcp.toml",
        commands={},
    )


def _settings(project: Path, data_dir: Path) -> BridgeSettings:
    spec = _project_spec(project)
    return BridgeSettings(
        repository_root=project.parent,
        bridge_config_path=project.parent / "bridge.toml",
        projects_config_path=project.parent / "projects.toml",
        server=ServerSettings("127.0.0.1", 46200, "/mcp", "streamable-http"),
        storage=StorageSettings(data_dir, data_dir / "bridge.sqlite3", data_dir / "logs"),
        policy=PolicySettings(False, False, False, True, 1024, 4096, "per-project"),
        codemcp=CodemcpSettings("local", "Ubuntu", None, 10, 10, 5),
        projects={"demo": spec},
    )


class NullAdapter:
    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> Any:
        del project, subtool, arguments, timeout_seconds, mutation
        raise AssertionError("the checkpoint tests must not call codemcp")

    def is_active(self, project_id: str) -> bool:
        del project_id
        return False

    async def close(self) -> None:
        return None


def _start_running_mutation(database: Database, operation_id: str, session_id: str) -> None:
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


def _finish_successful_mutation(database: Database, operation_id: str) -> None:
    database.transition_operation(
        operation_id,
        "succeeded",
        result_data={"operation_id": operation_id, "status": "succeeded"},
    )


@pytest.fixture
def git_project(tmp_path: Path) -> Path:
    project = tmp_path / "phase4 project 中文"
    project.mkdir()
    (project / "src").mkdir()
    (project / "src" / "hello.txt").write_text("hello\n", encoding="utf-8")
    (project / "codemcp.toml").write_text("[commands]\n", encoding="utf-8")
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "Phase 4 test")
    _git(project, "config", "user.email", "phase4@example.invalid")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "test: phase 4 baseline")
    return project


@pytest.mark.asyncio
async def test_commit_file_bytes_commits_only_the_requested_path(git_project: Path) -> None:
    guard = GitGuard()
    before_head = _git(git_project, "rev-parse", "HEAD")

    after_head = await guard.commit_file_bytes(
        git_project,
        path="src/hello.txt",
        content=b"changed\n",
        expected_head=before_head,
        description="replace one tracked file",
        require_exists=True,
    )

    assert after_head != before_head
    assert (git_project / "src" / "hello.txt").read_bytes() == b"changed\n"
    assert _git(git_project, "status", "--porcelain") == ""
    assert _git(git_project, "diff", "--name-only", before_head, after_head) == "src/hello.txt"


@pytest.mark.asyncio
async def test_commit_file_bytes_honors_explicit_session_commit_modes(
    git_project: Path,
) -> None:
    guard = GitGuard()
    before_head = _git(git_project, "rev-parse", "HEAD")

    created_head = await guard.commit_file_bytes(
        git_project,
        path="src/hello.txt",
        content=b"session create\n",
        expected_head=before_head,
        description="explicit create mode",
        require_exists=True,
        commit_mode=CommitMode.CREATE,
        session_id="session-1",
    )
    assert _git(git_project, "rev-list", "--count", "main") == "2"
    assert _git(git_project, "rev-parse", f"{created_head}^") == before_head
    assert await guard.read_session_footer(git_project, head=created_head) == "session-1"

    amended_head = await guard.commit_file_bytes(
        git_project,
        path="src/hello.txt",
        content=b"session amend\n",
        expected_head=created_head,
        description="explicit amend mode is ignored",
        require_exists=True,
        commit_mode=CommitMode.AMEND_SESSION_WIP,
        session_id="session-1",
    )
    assert amended_head != created_head
    assert _git(git_project, "rev-list", "--count", "main") == "2"
    assert _git(git_project, "rev-parse", f"{amended_head}^") == before_head
    assert await guard.read_session_footer(git_project, head=amended_head) == "session-1"


@pytest.mark.asyncio
async def test_amend_rechecks_shared_refs_before_git_side_effect(
    git_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = GitGuard()
    baseline_head = _git(git_project, "rev-parse", "HEAD")
    wip_head = await guard.commit_file_bytes(
        git_project,
        path="src/hello.txt",
        content=b"session create\n",
        expected_head=baseline_head,
        description="create session WIP",
        require_exists=True,
        commit_mode=CommitMode.CREATE,
        session_id="session-1",
        expected_branch="main",
    )
    real_run = guard._run

    async def publish_after_stage(project_root: Path, *arguments: str) -> str:
        output = await real_run(project_root, *arguments)
        if arguments[:2] == ("add", "--"):
            _git(project_root, "tag", "race-published", wip_head)
        return output

    monkeypatch.setattr(guard, "_run", publish_after_stage)
    with pytest.raises(BridgeError) as raised:
        await guard.commit_file_bytes(
            git_project,
            path="src/hello.txt",
            content=b"session amend\n",
            expected_head=wip_head,
            description="amend session WIP",
            require_exists=True,
            commit_mode=CommitMode.AMEND_SESSION_WIP,
            session_id="session-1",
            expected_branch="main",
        )

    assert raised.value.code == "UNKNOWN_SIDE_EFFECT"
    assert raised.value.status == "unknown"
    assert _git(git_project, "rev-parse", "HEAD") == wip_head
    assert _git(git_project, "show-ref", "--verify", "refs/tags/race-published")


@pytest.mark.asyncio
async def test_create_wip_commit_writes_and_reads_exact_session_footer(
    git_project: Path,
) -> None:
    guard = GitGuard()
    before_head = _git(git_project, "rev-parse", "HEAD")
    (git_project / "src" / "hello.txt").write_text("session WIP\n", encoding="utf-8")

    after_head = await guard.create_wip_commit(
        git_project,
        paths=("src/hello.txt",),
        description="session footer primitive",
        session_id="session-1",
        expected_head=before_head,
    )

    assert after_head != before_head
    assert await guard.read_session_footer(git_project, head=after_head) == "session-1"
    assert await guard.has_session_footer(
        git_project,
        head=after_head,
        session_id="session-1",
    )
    assert not await guard.has_session_footer(
        git_project,
        head=after_head,
        session_id="session-2",
    )
    assert "Codemcp-Remote-Session: session-1" in _git(
        git_project, "show", "-s", "--format=%B", after_head
    )

    (git_project / "src" / "hello.txt").write_text("amended WIP\n", encoding="utf-8")
    amended_head = await guard.commit_paths(
        git_project,
        paths=("src/hello.txt",),
        commit_mode=CommitMode.AMEND_SESSION_WIP,
        expected_head=after_head,
    )
    assert amended_head != after_head
    assert _git(git_project, "rev-list", "--count", "main") == "2"
    assert await guard.read_session_footer(git_project, head=amended_head) == "session-1"


@pytest.mark.asyncio
async def test_shared_ref_check_ignores_checkpoint_refs_and_blocks_published_refs(
    git_project: Path,
) -> None:
    guard = GitGuard()
    head = _git(git_project, "rev-parse", "HEAD")
    checkpoint_ref = "refs/codemcp-remote/checkpoints/" + ("a" * 32)
    await guard.create_checkpoint_ref(git_project, checkpoint_ref, head)
    assert (
        await guard.shared_refs_containing_head(
            git_project,
            head=head,
            branch="main",
        )
        == ()
    )

    _git(git_project, "update-ref", "refs/remotes/origin/main", head)
    assert await guard.has_shared_refs_containing_head(
        git_project,
        head=head,
        branch="main",
    )

    _git(git_project, "update-ref", "-d", "refs/remotes/origin/main")
    _git(git_project, "tag", "phase1-published", head)
    assert "refs/tags/phase1-published" in await guard.shared_refs_containing_head(
        git_project,
        head=head,
        branch="main",
    )

    _git(git_project, "tag", "-d", "phase1-published")
    _git(git_project, "branch", "phase1-shared")
    assert "refs/heads/phase1-shared" in await guard.shared_refs_containing_head(
        git_project,
        head=head,
        branch="main",
    )


@pytest.mark.asyncio
async def test_checkpoint_commit_mode_requires_database_footer_and_ref_evidence(
    git_project: Path,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "data" / "bridge.sqlite3")
    database.initialize()
    database.create_session("session-1", "demo", "local-policy")
    git = GitGuard()
    service = CheckpointService(database, git)
    spec = _project_spec(git_project)

    _start_running_mutation(database, "op-valid", "session-1")
    baseline = await service.create(
        spec,
        session_id="session-1",
        operation_id="op-valid",
        kind="mutation",
    )
    before_head = baseline.head
    (git_project / "src" / "hello.txt").write_text("session WIP\n", encoding="utf-8")
    after_head = await git.create_wip_commit(
        git_project,
        paths=("src/hello.txt",),
        description="session WIP",
        session_id="session-1",
        expected_head=before_head,
    )
    await service.finalize(spec, baseline)
    _finish_successful_mutation(database, "op-valid")

    _start_running_mutation(database, "op-next", "session-1")
    next_checkpoint = await service.create(
        spec,
        session_id="session-1",
        operation_id="op-next",
        kind="mutation",
    )
    assert next_checkpoint.head == after_head
    assert (
        await service.determine_commit_mode(
            spec,
            session_id="session-1",
            checkpoint=next_checkpoint,
        )
        is CommitMode.AMEND_SESSION_WIP
    )
    database.transition_operation("op-next", "failed", error_data={"code": "TEST"})

    _git(git_project, "tag", "phase1-published", after_head)
    _start_running_mutation(database, "op-shared", "session-1")
    shared_checkpoint = await service.create(
        spec,
        session_id="session-1",
        operation_id="op-shared",
        kind="mutation",
    )
    assert (
        await service.determine_commit_mode(
            spec,
            session_id="session-1",
            checkpoint=shared_checkpoint,
        )
        is CommitMode.CREATE
    )
    database.transition_operation("op-shared", "failed", error_data={"code": "TEST"})
    database.close()


@pytest.mark.asyncio
async def test_commit_file_bytes_finalize_failure_is_unknown(
    git_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = GitGuard()
    before_head = _git(git_project, "rev-parse", "HEAD")
    real_run = guard._run

    async def fail_commit(project_root: Path, *arguments: str) -> str:
        if arguments and arguments[0] == "commit":
            raise BridgeError("BACKEND_UNAVAILABLE", "simulated commit failure")
        return await real_run(project_root, *arguments)

    monkeypatch.setattr(guard, "_run", fail_commit)

    with pytest.raises(BridgeError) as raised:
        await guard.commit_file_bytes(
            git_project,
            path="src/hello.txt",
            content=b"changed but uncommitted\n",
            expected_head=before_head,
            description="simulate finalize failure",
            require_exists=True,
        )

    assert raised.value.code == "UNKNOWN_SIDE_EFFECT"
    assert raised.value.status == "unknown"
    assert _git(git_project, "rev-parse", "HEAD") == before_head
    assert (git_project / "src" / "hello.txt").read_bytes() == b"changed but uncommitted\n"
    assert _git(git_project, "status", "--porcelain")


@pytest.mark.asyncio
async def test_checkpoint_records_git_baseline_and_diff(git_project: Path, tmp_path: Path) -> None:
    database = Database(tmp_path / "data" / "bridge.sqlite3")
    database.initialize()
    service = CheckpointService(database, GitGuard())
    spec = _project_spec(git_project)

    checkpoint = await service.create(
        spec,
        session_id="session-1",
        operation_id=None,
        kind="manual",
    )
    before_head = checkpoint.head
    (git_project / "src" / "hello.txt").write_text("changed\n", encoding="utf-8")
    _git(git_project, "add", ".")
    _git(git_project, "commit", "--amend", "--no-edit")

    finalized = await service.finalize(spec, checkpoint)
    assert finalized.before_data["head"] == before_head
    assert finalized.after_data["head"] != before_head
    assert finalized.after_data["changed_files"] == ["src/hello.txt"]
    assert len(finalized.diff_hash) == 64
    assert "changed" in (await GitGuard().diff_from(git_project, checkpoint.ref_name))[0]
    assert database.get_checkpoint(checkpoint.checkpoint_id).diff_hash == finalized.diff_hash

    with pytest.raises(BridgeError) as nested_root:
        await GitGuard().require_worktree_root(git_project / "src")
    assert nested_root.value.code == "PROJECT_NOT_ALLOWED"

    (git_project / "src" / "hello.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(BridgeError) as raised:
        await service.create(
            spec,
            session_id="session-1",
            operation_id=None,
            kind="manual",
        )
    assert raised.value.code == "WORKSPACE_DIRTY"
    _git(git_project, "reset", "--hard", "HEAD")
    database.close()


@pytest.mark.asyncio
async def test_checkpoint_mcp_approval_diff_and_cas_restore(
    git_project: Path, tmp_path: Path
) -> None:
    bridge = BridgeService(_settings(git_project, tmp_path / "data"), adapter=NullAdapter())
    await bridge.start()
    session = bridge.sessions.create("demo")

    created_request = {"project_id": "demo", "session_id": session.session_id}
    pending = await bridge.checkpoint_create(
        None,
        "demo",
        session.session_id,
        "checkpoint-create-1",
        request_hash(created_request),
    )
    assert pending["status"] == "awaiting_approval"
    checkpoint_id = pending["error"]["details"]["operation_id"]
    token = pending["error"]["details"]["approval_token"]
    created = await bridge.approval_confirm(
        None,
        checkpoint_id,
        session.session_id,
        token,
        "checkpoint-approval-1",
        request_hash(
            {
                "operation_id": checkpoint_id,
                "approval_token_digest": request_hash(token),
            }
        ),
    )
    assert created["status"] == "succeeded"
    checkpoint = created["data"]["approved_operation"]["data"]["checkpoint"]
    checkpoint_id = checkpoint["checkpoint_id"]
    baseline_head = checkpoint["before"]["head"]

    _git(git_project, "checkout", "-b", "phase4-race-branch")
    branch_conflict = await bridge.checkpoint_restore(
        None,
        "demo",
        session.session_id,
        checkpoint_id,
        baseline_head,
        "restore-branch-conflict-1",
        request_hash(
            {
                "project_id": "demo",
                "session_id": session.session_id,
                "checkpoint_id": checkpoint_id,
                "expected_head": baseline_head,
            }
        ),
    )
    assert branch_conflict["error"]["code"] == "CHECKPOINT_CONFLICT"
    _git(git_project, "checkout", "main")

    (git_project / "src" / "hello.txt").write_text("external change\n", encoding="utf-8")
    _git(git_project, "add", ".")
    _git(git_project, "commit", "--amend", "--no-edit")
    changed_head = _git(git_project, "rev-parse", "HEAD")

    diff = await bridge.git_diff(None, "demo", session.session_id, checkpoint_id)
    assert diff["status"] == "succeeded"
    assert diff["data"]["text"]
    assert diff["changed_files"] == ["src/hello.txt"]

    restore_request = {
        "project_id": "demo",
        "session_id": session.session_id,
        "checkpoint_id": checkpoint_id,
        "expected_head": changed_head,
    }
    restore_pending = await bridge.checkpoint_restore(
        None,
        "demo",
        session.session_id,
        checkpoint_id,
        changed_head,
        "restore-cas-1",
        request_hash(restore_request),
    )
    restore_operation_id = restore_pending["error"]["details"]["operation_id"]
    restore_token = restore_pending["error"]["details"]["approval_token"]

    (git_project / "src" / "hello.txt").write_text("race\n", encoding="utf-8")
    _git(git_project, "add", ".")
    _git(git_project, "commit", "--amend", "--no-edit")
    raced_head = _git(git_project, "rev-parse", "HEAD")
    conflicted = await bridge.approval_confirm(
        None,
        restore_operation_id,
        session.session_id,
        restore_token,
        "restore-cas-confirm-1",
        request_hash(
            {
                "operation_id": restore_operation_id,
                "approval_token_digest": request_hash(restore_token),
            }
        ),
    )
    assert conflicted["data"]["approved_operation"]["error"]["code"] == ("CHECKPOINT_CONFLICT")
    assert _git(git_project, "rev-parse", "HEAD") == raced_head

    restore_request_2 = {
        "project_id": "demo",
        "session_id": session.session_id,
        "checkpoint_id": checkpoint_id,
        "expected_head": raced_head,
    }
    restore_pending_2 = await bridge.checkpoint_restore(
        None,
        "demo",
        session.session_id,
        checkpoint_id,
        raced_head,
        "restore-cas-2",
        request_hash(restore_request_2),
    )
    restored = await bridge.approval_confirm(
        None,
        restore_pending_2["error"]["details"]["operation_id"],
        session.session_id,
        restore_pending_2["error"]["details"]["approval_token"],
        "restore-cas-confirm-2",
        request_hash(
            {
                "operation_id": restore_pending_2["error"]["details"]["operation_id"],
                "approval_token_digest": request_hash(
                    restore_pending_2["error"]["details"]["approval_token"]
                ),
            }
        ),
    )
    assert restored["status"] == "succeeded"
    assert _git(git_project, "rev-parse", "HEAD") == baseline_head
    assert (git_project / "src" / "hello.txt").read_text(encoding="utf-8") == "hello\n"

    restored_edit_description = "mutate after checkpoint restore"
    restored_edit = await bridge.file_edit(
        None,
        "demo",
        session.session_id,
        "src/hello.txt",
        "hello",
        "after restore",
        restored_edit_description,
        "restore-follow-up-edit-1",
        request_hash(
            {
                "path": "src/hello.txt",
                "description": restored_edit_description,
                "old_string_digest": request_hash("hello"),
                "new_string_digest": request_hash("after restore"),
            }
        ),
    )
    assert restored_edit["status"] == "succeeded"
    restored_edit_checkpoint = restored_edit["data"]["checkpoint"]
    restored_edit_head = restored_edit_checkpoint["after"]["head"]
    assert restored_edit_checkpoint["before"]["head"] == baseline_head
    assert restored_edit_head != baseline_head
    assert _git(git_project, "rev-list", "--count", "main") == "2"
    assert (
        await bridge.git.read_session_footer(
            git_project,
            head=restored_edit_head,
        )
        == session.session_id
    )
    await bridge.close()
