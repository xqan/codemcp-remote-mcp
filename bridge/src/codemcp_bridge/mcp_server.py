"""Loopback MCP server and the policy-controlled tool surface."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Protocol

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from .approval_service import ApprovalService
from .audit_store import AuditStore
from .checkpoint_service import CheckpointService
from .codemcp_adapter import CodemcpAdapter
from .command_runner import RegisteredCommandRunner
from .db import CheckpointRecord, Database, OperationRecord, SessionRecord
from .errors import BridgeError, error_payload, success_payload
from .git_guard import CommitMode, GitGuard
from .mcp_transport import BridgeStreamableHTTPSessionManager
from .network_trust import NetworkTrustConfig, NetworkTrustMiddleware
from .operation_service import OperationService
from .operation_service import request_hash as calculate_request_hash
from .policy_engine import PolicyEngine
from .project_readiness import inspect_development_readiness
from .project_registry import ProjectRegistry, is_sensitive_relative_path
from .resource_auth import OAuthResourceServerAuthenticator
from .session_service import SessionService
from .settings import BridgeSettings, CommandSpec, ProjectSpec

logger = logging.getLogger(__name__)


# Keep the public MCP surface explicit. The network-trusted profile changes
# authentication semantics, not the project and mutation safety boundary.
PUBLIC_MCP_TOOL_NAMES = frozenset(
    {
        "project_open",
        "project_status",
        "file_read",
        "code_search",
        "file_list",
        "file_edit",
        "file_create",
        "file_write",
        "file_move",
        "file_delete",
        "directory_create",
        "registered_command_run",
        "format_run",
        "test_run",
        "git_status",
        "git_diff",
        "checkpoint_create",
        "checkpoint_restore",
        "operation_status",
        "approval_confirm",
        "operation_cancel",
        "operation_reconcile",
    }
)


@dataclass(frozen=True, slots=True)
class _Outcome:
    data: dict[str, Any]
    changed_files: list[str]
    truncated: bool = False
    status: str = "succeeded"


@dataclass(frozen=True, slots=True)
class _MutationContext:
    checkpoint: CheckpointRecord
    commit_mode: CommitMode


class AdapterLike(Protocol):
    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> Any: ...

    def is_active(self, project_id: str) -> bool: ...

    async def close(self) -> None: ...


Operation = Callable[[str], Awaitable[_Outcome]]


def _validate_public_tool_surface(server: FastMCP) -> None:
    """Fail startup if a registered server loses part of the Bridge contract."""

    registered_names = frozenset(
        tool.name
        for tool in server._tool_manager.list_tools()  # noqa: SLF001
    )
    missing = sorted(PUBLIC_MCP_TOOL_NAMES - registered_names)
    if missing:
        raise RuntimeError("Bridge public MCP tool contract is incomplete: " + ", ".join(missing))


class BridgeFastMCP(FastMCP):
    """FastMCP adapter that installs the Bridge-wide network trust boundary."""

    def __init__(
        self,
        *args: Any,
        network_trust: NetworkTrustConfig | None = None,
        network_resource: str | None = None,
        **kwargs: Any,
    ) -> None:
        if network_trust is not None and not isinstance(network_trust, NetworkTrustConfig):
            raise TypeError("network_trust requires NetworkTrustConfig")
        self._codemcp_network_trust_config = network_trust
        self._codemcp_network_resource = network_resource
        if network_trust is not None:
            kwargs["transport_security"] = TransportSecuritySettings(
                enable_dns_rebinding_protection=False
            )
        super().__init__(*args, **kwargs)

    def streamable_http_app(self) -> Any:
        app = super().streamable_http_app()
        if self._codemcp_network_trust_config is not None:
            app.add_middleware(
                NetworkTrustMiddleware,
                config=self._codemcp_network_trust_config,
                resource=self._codemcp_network_resource,
            )
        return app


def _is_codemcp_error(result: Any) -> bool:
    return bool(result.is_error) or result.text.lstrip().lower().startswith("error")


def _decode_utf8_text(raw: bytes, *, path: str) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BridgeError(
            "BINARY_FILE",
            "file is not valid UTF-8 text",
            {"path": path},
        ) from exc


def _match_existing_line_endings(raw: bytes, value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if b"\r\n" in raw and raw.count(b"\n") == raw.count(b"\r\n"):
        return normalized.replace("\n", "\r\n")
    return normalized


def _replace_exact_text(raw: bytes, *, path: str, old_string: str, new_string: str) -> bytes:
    text = _decode_utf8_text(raw, path=path)
    old_value = _match_existing_line_endings(raw, old_string)
    new_value = _match_existing_line_endings(raw, new_string)
    matches = text.count(old_value)
    if matches == 0:
        raise BridgeError("CONFLICT", "old_string was not found in the file", {"path": path})
    if matches > 1:
        raise BridgeError(
            "CONFLICT",
            "old_string must match exactly one occurrence",
            {"path": path, "matches": matches},
        )
    return text.replace(old_value, new_value, 1).encode("utf-8")


def _encode_existing_text(raw: bytes, *, path: str, content: str) -> bytes:
    _decode_utf8_text(raw, path=path)
    return _match_existing_line_endings(raw, content).encode("utf-8")


_GREP_HEADER_PATTERN = re.compile(r"^Found \d+ files?$")


def _is_sensitive_grep_line(
    line: str,
    *,
    project: ProjectSpec,
    registry: ProjectRegistry,
) -> bool:
    candidate = line.strip().replace("\\", "/")
    if not candidate:
        return False
    roots = {
        str(project.root.resolve(strict=False)).replace("\\", "/").rstrip("/"),
        registry.worker_path(project.root).replace("\\", "/").rstrip("/"),
    }
    candidate_casefold = candidate.casefold()
    for root in roots:
        root_casefold = root.casefold()
        if candidate_casefold.startswith(root_casefold + "/"):
            relative = candidate[len(root) + 1 :]
            return is_sensitive_relative_path(relative)
    return is_sensitive_relative_path(candidate)


def _filter_sensitive_grep_result(
    text: str,
    *,
    project: ProjectSpec,
    registry: ProjectRegistry,
) -> str:
    """Remove sensitive filenames from codemcp's formatted Grep response."""

    lines = text.splitlines()
    if not lines:
        return text
    header = lines[0].strip()
    if _GREP_HEADER_PATTERN.fullmatch(header):
        matches = [
            line
            for line in lines[1:]
            if line.strip()
            and not line.strip().startswith("(")
            and not _is_sensitive_grep_line(line, project=project, registry=registry)
        ]
        if not matches:
            return "No files found"
        result = f"Found {len(matches)} file{'s' if len(matches) != 1 else ''}\n"
        return result + "\n".join(matches)
    return "\n".join(
        line
        for line in lines
        if not _is_sensitive_grep_line(line, project=project, registry=registry)
    )


class BridgeService:
    def __init__(self, settings: BridgeSettings, adapter: AdapterLike | None = None):
        self.settings = settings
        self.registry = ProjectRegistry(settings)
        self.git = GitGuard(max_output_bytes=settings.policy.max_result_bytes)
        self.policy = PolicyEngine(settings, self.registry, self.git)
        self.adapter = adapter or CodemcpAdapter(settings, self.registry)
        self.command_runner = RegisteredCommandRunner(settings)
        self.database = Database(settings.storage.sqlite_file)
        self.sessions = SessionService(self.database)
        self.operations = OperationService(self.database)
        self.approvals = ApprovalService(
            self.database, ttl_seconds=settings.policy.approval_ttl_seconds
        )
        self.audit = AuditStore(self.database)
        self.checkpoints = CheckpointService(self.database, self.git)
        self._started = False
        self._mutation_locks: dict[str, asyncio.Lock] = {}

    def _mutation_lock(self, project_id: str) -> asyncio.Lock:
        lock = self._mutation_locks.get(project_id)
        if lock is None:
            lock = asyncio.Lock()
            self._mutation_locks[project_id] = lock
        return lock

    def _refresh_project_registry(self) -> bool:
        before = self.registry.snapshot()
        if not self.registry.refresh_if_changed():
            return False
        after = self.registry.snapshot()
        for project_id in before.keys() - after.keys():
            self.sessions.revoke_project(project_id)
        return True

    @staticmethod
    def _request_id(ctx: Context | None) -> str:
        if ctx is not None:
            try:
                return ctx.request_id
            except (LookupError, ValueError):
                pass
        return uuid.uuid4().hex

    async def _execute(
        self,
        ctx: Context | None,
        *,
        project_id: str | None,
        session_id: str | None,
        operation: Operation,
        operation_kind: str,
        operation_input: dict[str, Any] | None = None,
        mutation: bool = False,
        client_request_id: str | None = None,
        supplied_request_hash: str | None = None,
        approval_required: bool = False,
        approval_action: str | None = None,
        approval_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict[str, Any]:
        await self.start()
        self._refresh_project_registry()
        request_id = self._request_id(ctx)
        input_data = operation_input or {}
        request_hash_value = supplied_request_hash or (
            "" if mutation else calculate_request_hash(input_data)
        )
        operation_id = uuid.uuid4().hex
        if client_request_id:
            client_id = client_request_id
        elif mutation:
            client_id = ""
        elif request_id == "0":
            # Some Streamable HTTP clients reuse JSON-RPC id "0" for every
            # request. It cannot serve as a per-session read operation key.
            client_id = f"{operation_kind}-{operation_id}"
        else:
            client_id = request_id
        try:
            started = self.operations.start(
                operation_id=operation_id,
                project_id=project_id or "__bridge__",
                session_id=session_id,
                kind=operation_kind,
                mutation=mutation,
                client_request_id=client_id,
                supplied_request_hash=request_hash_value,
                input_data=input_data,
            )
        except BridgeError as exc:
            return error_payload(
                request_id=request_id,
                session_id=session_id,
                project_id=project_id,
                operation_id=operation_id,
                error=exc,
            )
        if started.replay_payload is not None:
            return started.replay_payload
        operation_id = started.record.operation_id
        try:
            needs_approval = approval_required
            if approval_check is not None:
                needs_approval = await approval_check()
        except BridgeError as exc:
            payload = error_payload(
                request_id=request_id,
                session_id=session_id,
                project_id=project_id,
                operation_id=operation_id,
                error=exc,
            )
            self.operations.finish(operation_id, state="failed", payload=payload)
            return payload
        if needs_approval:
            action = approval_action or operation_kind
            grant = self.approvals.issue(started.record, action=action)
            approval_error = BridgeError(
                "APPROVAL_REQUIRED",
                "explicit approval is required before this operation can run",
                {
                    "operation_id": operation_id,
                    "approval_id": grant.approval_id,
                    "approval_token": grant.token,
                    "expires_at": grant.expires_at,
                    "action": action,
                },
                status="awaiting_approval",
            )
            payload = error_payload(
                request_id=request_id,
                session_id=session_id,
                project_id=project_id,
                operation_id=operation_id,
                error=approval_error,
            )
            self.operations.await_approval(operation_id, error_data=payload["error"])
            return payload
        try:
            self.operations.dispatch(operation_id)
            outcome = await operation(operation_id)
            response_session_id = session_id
            returned_session_id = outcome.data.get("session_id")
            if response_session_id is None and isinstance(returned_session_id, str):
                response_session_id = returned_session_id
            payload = success_payload(
                request_id=request_id,
                session_id=response_session_id,
                project_id=project_id,
                operation_id=operation_id,
                data=outcome.data,
                changed_files=outcome.changed_files,
                truncated=outcome.truncated,
                status=outcome.status,
            )
            terminal_state = "failed" if outcome.status == "failed" else "succeeded"
            self.operations.finish(operation_id, state=terminal_state, payload=payload)
            return payload
        except BridgeError as exc:
            payload = error_payload(
                request_id=request_id,
                session_id=session_id,
                project_id=project_id,
                operation_id=operation_id,
                error=exc,
            )
            terminal_state = "unknown" if exc.status == "unknown" else "failed"
            self.operations.finish(operation_id, state=terminal_state, payload=payload)
            return payload
        except asyncio.CancelledError:
            if mutation:
                error = BridgeError(
                    "UNKNOWN_SIDE_EFFECT",
                    "mutation request was cancelled while the backend outcome may be unknown",
                    {"operation_id": operation_id, "cause": "client_request_cancelled"},
                    status="unknown",
                )
                terminal_state = "unknown"
            else:
                error = BridgeError(
                    "CLIENT_REQUEST_CANCELLED",
                    "request was cancelled before a result could be returned",
                    {"operation_id": operation_id},
                    retryable=True,
                    status="failed",
                )
                terminal_state = "failed"
            payload = error_payload(
                request_id=request_id,
                session_id=session_id,
                project_id=project_id,
                operation_id=operation_id,
                error=error,
            )
            try:
                self.operations.finish(operation_id, state=terminal_state, payload=payload)
            except Exception:
                logger.exception("failed to persist cancelled Bridge operation state")
            raise
        except Exception:
            logger.exception("unexpected Bridge operation failure")
            error = BridgeError(
                "BACKEND_UNAVAILABLE",
                "Bridge backend operation failed",
                {"project_id": project_id},
                retryable=True,
                status="failed",
            )
            payload = error_payload(
                request_id=request_id,
                session_id=session_id,
                project_id=project_id,
                operation_id=operation_id,
                error=error,
            )
            self.operations.finish(operation_id, state="failed", payload=payload)
            return payload

    async def _require_session(self, project_id: str, session_id: str | None) -> SessionRecord:
        self._refresh_project_registry()
        self.registry.get(project_id)
        return self.sessions.require_active(project_id, session_id)

    def require_operation_for_session(self, operation_id: str, session_id: str) -> OperationRecord:
        self._refresh_project_registry()
        record = self.operations.operation(operation_id)
        if record.owner_id != self.sessions.owner_id or record.session_id != session_id:
            raise BridgeError(
                "OPERATION_NOT_FOUND",
                "operation_id is not available to this session",
                {"operation_id": operation_id},
            )
        self.sessions.require_active(record.project_id, session_id)
        return record

    def require_operation_for_reconcile(
        self, operation_id: str, session_id: str
    ) -> OperationRecord:
        self._refresh_project_registry()
        record = self.operations.operation(operation_id)
        if record.owner_id != self.sessions.owner_id:
            raise BridgeError(
                "OPERATION_NOT_FOUND",
                "operation_id is not available to this session",
                {"operation_id": operation_id},
            )
        if record.session_id == session_id:
            self.sessions.require_active(record.project_id, session_id)
            return record

        successor = self.sessions.require_active(record.project_id, session_id)
        if record.state != "unknown" or record.session_id is None:
            raise BridgeError(
                "OPERATION_NOT_FOUND",
                "operation_id is not available to this session",
                {"operation_id": operation_id},
            )
        origin = self.database.get_session(record.session_id)
        if (
            origin is None
            or origin.project_id != record.project_id
            or origin.owner_id != successor.owner_id
            or (origin.status, origin.reason)
            not in {("blocked", "bridge_restart"), ("closed", "bridge_shutdown")}
            or not self.sessions.auth_contexts_match(origin.session_id, successor.session_id)
        ):
            raise BridgeError(
                "OPERATION_NOT_FOUND",
                "operation_id is not available to this session",
                {"operation_id": operation_id},
            )
        return record

    async def _begin_mutation(
        self,
        project: ProjectSpec,
        *,
        session_id: str,
        operation_id: str,
    ) -> _MutationContext:
        await self.policy.require_mutation_preconditions(project)
        checkpoint = await self.checkpoints.create(
            project,
            session_id=session_id,
            operation_id=operation_id,
            kind="mutation",
        )
        commit_mode = await self.checkpoints.determine_commit_mode(
            project,
            session_id=session_id,
            checkpoint=checkpoint,
        )
        return _MutationContext(checkpoint=checkpoint, commit_mode=commit_mode)

    async def _finish_mutation(
        self,
        project: ProjectSpec,
        checkpoint: CheckpointRecord,
        *,
        expected_after_head: str,
    ) -> dict[str, Any]:
        try:
            finalized = await self.checkpoints.finalize(
                project,
                checkpoint,
                expected_after_head=expected_after_head,
                expected_after_branch=checkpoint.branch,
            )
        except BridgeError as exc:
            raise BridgeError(
                "UNKNOWN_SIDE_EFFECT",
                "mutation completed but its Git result could not be recorded",
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "cause": exc.code,
                },
                status="unknown",
            ) from exc
        return self.checkpoints.summary(finalized)

    async def start(self) -> None:
        if self._started:
            return
        self.database.initialize()
        self.database.recover_after_restart()
        self._started = True

    async def project_open(self, ctx: Context | None, project_id: str) -> dict[str, Any]:
        async def operation(_operation_id: str) -> _Outcome:
            project = self.registry.get(project_id)
            status = await self.policy.inspect_project(project)
            session = self.sessions.create(project_id)
            return _Outcome(
                data={
                    "session_id": session.session_id,
                    "root": str(project.root),
                    "branch": status.branch,
                    "head": status.head,
                    "dirty": status.dirty,
                    "worker_active": self.adapter.is_active(project_id),
                },
                changed_files=list(status.changed_files),
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=None,
            operation=operation,
            operation_kind="project_open",
            operation_input={"project_id": project_id},
            client_request_id=uuid.uuid4().hex,
            supplied_request_hash=calculate_request_hash({"project_id": project_id}),
        )

    async def project_status(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str | None,
    ) -> dict[str, Any]:
        async def operation(_operation_id: str) -> _Outcome:
            project = self.registry.get(project_id)
            if session_id:
                await self._require_session(project_id, session_id)
            status = await self.policy.inspect_project(project)
            readiness = inspect_development_readiness(project)
            return _Outcome(
                data={
                    "root": str(project.root),
                    "branch": status.branch,
                    "head": status.head,
                    "dirty": status.dirty,
                    "worker_active": self.adapter.is_active(project_id),
                    "profile": readiness.profile_id,
                    "profile_source": readiness.profile_source,
                    "profile_resolved": readiness.profile_id is not None,
                    "detection": {
                        "candidates": list(readiness.detection_candidates),
                        "ambiguous": readiness.detection_ambiguous,
                    },
                    "commands_resolved": bool(readiness.available_commands),
                    "available_commands": list(readiness.available_commands),
                    "command_verification": {
                        "matched": list(readiness.matched_commands),
                        "missing": list(readiness.missing_commands),
                        "mismatched": list(readiness.mismatched_commands),
                    },
                    "codemcp_config_source": readiness.codemcp_config_source,
                    "codemcp_config_ready": readiness.codemcp_config_ready,
                    "development_ready": readiness.development_ready,
                    "issues": list(readiness.issues),
                },
                changed_files=list(status.changed_files),
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="project_status",
            operation_input={"project_id": project_id, "session_id": session_id},
        )

    async def file_read(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        path: str,
        offset: int | None,
        limit: int | None,
    ) -> dict[str, Any]:
        async def operation(_operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            if offset is not None and offset < 0:
                raise BridgeError("INVALID_REQUEST", "offset must not be negative")
            if limit is not None and not 0 <= limit <= 10_000:
                raise BridgeError("INVALID_REQUEST", "limit must be between 0 and 10000")
            project, target, _ = self.registry.resolve_path(project_id, path)
            self.policy.require_regular_file(target)
            self.policy.validate_file_size(target)
            self.policy.require_text_file(target)
            try:
                before_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            except OSError as exc:
                raise BridgeError("FILE_NOT_FOUND", "file cannot be read") from exc
            result = await self.adapter.call(
                project,
                "ReadFile",
                {"path": target, "offset": offset, "limit": limit, "chat_id": session_id},
            )
            if _is_codemcp_error(result):
                raise BridgeError(
                    "BACKEND_UNAVAILABLE",
                    "codemcp rejected ReadFile",
                    {"subtool": "ReadFile"},
                    status="failed",
                )
            after_size = self.policy.validate_file_size(target)
            try:
                after_sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
            except OSError as exc:
                raise BridgeError("FILE_NOT_FOUND", "file cannot be read") from exc
            if after_sha256 != before_sha256:
                raise BridgeError(
                    "CONFLICT",
                    "file content changed while it was being read",
                    {"path": self.registry.relative_path(project, target)},
                )
            return _Outcome(
                {
                    "path": self.registry.relative_path(project, target),
                    "text": result.text,
                    "sha256": after_sha256,
                    "size_bytes": after_size,
                },
                [],
                result.truncated,
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="file_read",
            operation_input={
                "path": path,
                "offset": offset,
                "limit": limit,
            },
        )

    async def code_search(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        pattern: str,
        path: str | None,
        include: str | None,
    ) -> dict[str, Any]:
        async def operation(_operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            self.policy.validate_pattern(pattern)
            project, target, _ = self.registry.resolve_path(project_id, path, allow_root=True)
            search_paths = self.registry.safe_search_paths(project, target)
            results: list[str] = []
            truncated = False
            for search_path in search_paths:
                if search_path != target and include:
                    relative = self.registry.relative_path(project, search_path)
                    if not (
                        fnmatch.fnmatchcase(PurePosixPath(relative).name, include)
                        or fnmatch.fnmatchcase(relative, include)
                    ):
                        continue
                result = await self.adapter.call(
                    project,
                    "Grep",
                    {
                        "pattern": pattern,
                        "path": search_path,
                        "include": include,
                        "chat_id": session_id,
                    },
                )
                if _is_codemcp_error(result):
                    raise BridgeError(
                        "BACKEND_UNAVAILABLE", "codemcp rejected Grep", status="failed"
                    )
                filtered = _filter_sensitive_grep_result(
                    result.text,
                    project=project,
                    registry=self.registry,
                )
                if filtered and filtered != "No files found":
                    results.append(filtered)
                truncated = truncated or result.truncated
            return _Outcome(
                {
                    "path": self.registry.relative_path(project, target),
                    "text": "\n".join(results) if results else "No files found",
                },
                [],
                truncated,
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="code_search",
            operation_input={"pattern": pattern, "path": path, "include": include},
        )

    async def file_list(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        path: str | None,
    ) -> dict[str, Any]:
        async def operation(_operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            project, target, _ = self.registry.resolve_path(project_id, path, allow_root=True)
            if not target.is_dir():
                raise BridgeError("FILE_NOT_FOUND", "a directory is required")

            entries, traversal_truncated = self.registry.safe_directory_tree(
                project,
                target,
                max_entries=1000,
            )
            relative_target = self.registry.relative_path(project, target)
            root_label = "./" if relative_target == "." else f"{relative_target}/"
            lines = [f"- {root_label}"]
            for relative_entry, is_directory in entries:
                parts = PurePosixPath(relative_entry).parts
                suffix = "/" if is_directory else ""
                lines.append(f"{'  ' * len(parts)}- {parts[-1]}{suffix}")

            text = "\n".join(lines)
            encoded = text.encode("utf-8")
            output_truncated = len(encoded) > self.settings.policy.max_result_bytes
            if output_truncated:
                text = encoded[: self.settings.policy.max_result_bytes].decode(
                    "utf-8",
                    errors="ignore",
                )
            return _Outcome(
                {"path": relative_target, "text": text},
                [],
                traversal_truncated or output_truncated,
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="file_list",
            operation_input={"path": path},
        )

    async def file_edit(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        path: str,
        old_string: str,
        new_string: str,
        description: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        async def operation(operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            if not description or len(description) > 500:
                raise BridgeError("INVALID_REQUEST", "description must be 1-500 characters")
            if len(old_string.encode("utf-8")) > self.settings.policy.max_file_bytes:
                raise BridgeError("FILE_TOO_LARGE", "old_string exceeds the configured size limit")
            if len(new_string.encode("utf-8")) > self.settings.policy.max_file_bytes:
                raise BridgeError("FILE_TOO_LARGE", "new_string exceeds the configured size limit")
            project, target, normalized = self.registry.resolve_path(project_id, path)
            self.policy.require_regular_file(target)
            self.policy.validate_file_size(target)
            self.policy.require_text_file(target)
            async with self._mutation_lock(project_id):
                mutation = await self._begin_mutation(
                    project,
                    session_id=session_id,
                    operation_id=operation_id,
                )
                checkpoint = mutation.checkpoint
                self.policy.require_regular_file(target)
                self.policy.validate_file_size(target)
                self.policy.require_text_file(target)
                try:
                    raw = target.read_bytes()
                except OSError as exc:
                    raise BridgeError("FILE_NOT_FOUND", "file cannot be read") from exc
                updated = _replace_exact_text(
                    raw,
                    path=normalized,
                    old_string=old_string,
                    new_string=new_string,
                )
                if len(updated) > self.settings.policy.max_file_bytes:
                    raise BridgeError(
                        "FILE_TOO_LARGE",
                        "edited file exceeds the configured size limit",
                    )
                new_head = await self.git.commit_file_bytes(
                    project.root,
                    path=normalized,
                    content=updated,
                    expected_head=checkpoint.head,
                    description=description,
                    require_exists=True,
                    commit_mode=mutation.commit_mode,
                    session_id=session_id,
                    expected_branch=checkpoint.branch,
                )
                checkpoint_data = await self._finish_mutation(
                    project,
                    checkpoint,
                    expected_after_head=new_head,
                )
            return _Outcome(
                {
                    "text": f"Bridge file edit committed at {new_head}",
                    "checkpoint": checkpoint_data,
                },
                list(checkpoint_data["after"]["changed_files"]),
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="file_edit",
            operation_input={
                "path": path,
                "description": description,
                "old_string_digest": calculate_request_hash(old_string),
                "new_string_digest": calculate_request_hash(new_string),
            },
            mutation=True,
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
        )

    async def file_create(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        path: str,
        content: str,
        description: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        async def operation(operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            if not description or len(description) > 500:
                raise BridgeError("INVALID_REQUEST", "description must be 1-500 characters")
            encoded = content.encode("utf-8")
            if len(encoded) > self.settings.policy.max_file_bytes:
                raise BridgeError("FILE_TOO_LARGE", "content exceeds the configured size limit")
            if "\x00" in content:
                raise BridgeError("BINARY_FILE", "binary content is not exposed by the Bridge")
            project, target, normalized = self.registry.resolve_path(project_id, path)
            parent = target.parent
            if not parent.exists() or not parent.is_dir():
                raise BridgeError(
                    "FILE_NOT_FOUND",
                    "parent directory does not exist",
                    {"path": normalized},
                )
            async with self._mutation_lock(project_id):
                if target.exists():
                    raise BridgeError(
                        "CONFLICT",
                        "file already exists",
                        {"path": normalized},
                    )
                mutation = await self._begin_mutation(
                    project,
                    session_id=session_id,
                    operation_id=operation_id,
                )
                checkpoint = mutation.checkpoint
                if target.exists():
                    raise BridgeError(
                        "CONFLICT",
                        "file appeared after the mutation baseline was recorded",
                        {"path": normalized},
                    )
                new_head = await self.git.commit_file_bytes(
                    project.root,
                    path=normalized,
                    content=encoded,
                    expected_head=checkpoint.head,
                    description=description,
                    require_exists=False,
                    commit_mode=mutation.commit_mode,
                    session_id=session_id,
                    expected_branch=checkpoint.branch,
                )
                checkpoint_data = await self._finish_mutation(
                    project,
                    checkpoint,
                    expected_after_head=new_head,
                )
            return _Outcome(
                {
                    "path": normalized,
                    "text": f"Bridge file create committed at {new_head}",
                    "checkpoint": checkpoint_data,
                },
                list(checkpoint_data["after"]["changed_files"]),
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="file_create",
            operation_input={
                "path": path,
                "description": description,
                "content_digest": calculate_request_hash(content),
            },
            mutation=True,
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
        )

    async def file_write(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        path: str,
        content: str,
        expected_sha256: str,
        description: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        normalized_expected = expected_sha256.lower()

        async def operation(operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            if not description or len(description) > 500:
                raise BridgeError("INVALID_REQUEST", "description must be 1-500 characters")
            if re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256) is None:
                raise BridgeError(
                    "INVALID_REQUEST",
                    "expected_sha256 must be a SHA-256 hex digest",
                )
            encoded = content.encode("utf-8")
            if len(encoded) > self.settings.policy.max_file_bytes:
                raise BridgeError("FILE_TOO_LARGE", "content exceeds the configured size limit")
            if "\x00" in content:
                raise BridgeError("BINARY_FILE", "binary content is not exposed by the Bridge")
            project, target, normalized = self.registry.resolve_path(project_id, path)
            self.policy.require_regular_file(target)
            self.policy.validate_file_size(target)
            self.policy.require_text_file(target)
            async with self._mutation_lock(project_id):
                mutation = await self._begin_mutation(
                    project,
                    session_id=session_id,
                    operation_id=operation_id,
                )
                checkpoint = mutation.checkpoint
                self.policy.require_regular_file(target)
                self.policy.validate_file_size(target)
                self.policy.require_text_file(target)
                try:
                    raw = target.read_bytes()
                except OSError as exc:
                    raise BridgeError("FILE_NOT_FOUND", "file cannot be read") from exc
                actual_sha256 = hashlib.sha256(raw).hexdigest()
                if actual_sha256 != normalized_expected:
                    raise BridgeError(
                        "CONFLICT",
                        "file content changed since it was read",
                        {
                            "path": normalized,
                            "expected_sha256": normalized_expected,
                            "actual_sha256": actual_sha256,
                        },
                    )
                prepared = _encode_existing_text(raw, path=normalized, content=content)
                if len(prepared) > self.settings.policy.max_file_bytes:
                    raise BridgeError(
                        "FILE_TOO_LARGE",
                        "replacement file exceeds the configured size limit",
                    )
                new_head = await self.git.commit_file_bytes(
                    project.root,
                    path=normalized,
                    content=prepared,
                    expected_head=checkpoint.head,
                    description=description,
                    require_exists=True,
                    commit_mode=mutation.commit_mode,
                    session_id=session_id,
                    expected_branch=checkpoint.branch,
                )
                checkpoint_data = await self._finish_mutation(
                    project,
                    checkpoint,
                    expected_after_head=new_head,
                )
            return _Outcome(
                {
                    "path": normalized,
                    "text": f"Bridge file write committed at {new_head}",
                    "checkpoint": checkpoint_data,
                },
                list(checkpoint_data["after"]["changed_files"]),
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="file_write",
            operation_input={
                "path": path,
                "description": description,
                "expected_sha256": normalized_expected,
                "content_digest": calculate_request_hash(content),
            },
            mutation=True,
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
        )

    async def file_move(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        source_path: str,
        destination_path: str,
        description: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        async def operation(operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            if not description or len(description) > 500:
                raise BridgeError(
                    "INVALID_REQUEST",
                    "description must be 1-500 characters",
                )

            project, source, normalized_source = self.registry.resolve_path(project_id, source_path)
            _, destination, normalized_destination = self.registry.resolve_path(
                project_id, destination_path
            )
            if source == destination or normalized_source == normalized_destination:
                raise BridgeError(
                    "INVALID_REQUEST",
                    "source_path and destination_path must be different",
                )
            self.policy.require_regular_file(source)
            destination_parent = destination.parent
            if not destination_parent.exists() or not destination_parent.is_dir():
                raise BridgeError(
                    "FILE_NOT_FOUND",
                    "destination parent directory does not exist",
                    {"path": normalized_destination},
                )

            async with self._mutation_lock(project_id):
                if destination.exists():
                    raise BridgeError(
                        "CONFLICT",
                        "destination file already exists",
                        {"path": normalized_destination},
                    )

                mutation = await self._begin_mutation(
                    project,
                    session_id=session_id,
                    operation_id=operation_id,
                )
                checkpoint = mutation.checkpoint
                tracked_files = checkpoint.before_data.get("file_hashes", {})
                if not isinstance(tracked_files, dict) or normalized_source not in tracked_files:
                    raise BridgeError(
                        "CONFLICT",
                        "source file must be tracked by the checkpoint HEAD",
                        {"path": normalized_source},
                    )

                _, source, normalized_source = self.registry.resolve_path(project_id, source_path)
                _, destination, normalized_destination = self.registry.resolve_path(
                    project_id, destination_path
                )
                self.policy.require_regular_file(source)
                destination_parent = destination.parent
                if not destination_parent.exists() or not destination_parent.is_dir():
                    raise BridgeError(
                        "FILE_NOT_FOUND",
                        "destination parent directory does not exist",
                        {"path": normalized_destination},
                    )
                if destination.exists():
                    raise BridgeError(
                        "CONFLICT",
                        "destination file appeared after the mutation baseline was recorded",
                        {"path": normalized_destination},
                    )

                new_head = await self.git.move_tracked_file(
                    project.root,
                    source=normalized_source,
                    destination=normalized_destination,
                    expected_head=checkpoint.head,
                    description=description,
                    commit_mode=mutation.commit_mode,
                    session_id=session_id,
                    expected_branch=checkpoint.branch,
                )
                checkpoint_data = await self._finish_mutation(
                    project,
                    checkpoint,
                    expected_after_head=new_head,
                )
            return _Outcome(
                {
                    "source_path": normalized_source,
                    "destination_path": normalized_destination,
                    "head": new_head,
                    "checkpoint": checkpoint_data,
                },
                list(checkpoint_data["after"]["changed_files"]),
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="file_move",
            operation_input={
                "source_path": source_path,
                "destination_path": destination_path,
                "description": description,
            },
            mutation=True,
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
        )

    async def file_delete(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        path: str,
        description: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        async def operation(operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            if not description or len(description) > 500:
                raise BridgeError(
                    "INVALID_REQUEST",
                    "description must be 1-500 characters",
                )

            project, target, normalized = self.registry.resolve_path(project_id, path)
            self.policy.require_regular_file(target)

            async with self._mutation_lock(project_id):
                mutation = await self._begin_mutation(
                    project,
                    session_id=session_id,
                    operation_id=operation_id,
                )
                checkpoint = mutation.checkpoint
                tracked_files = checkpoint.before_data.get("file_hashes", {})
                if not isinstance(tracked_files, dict) or normalized not in tracked_files:
                    raise BridgeError(
                        "CONFLICT",
                        "file must be tracked by the checkpoint HEAD before deletion",
                        {"path": normalized},
                    )

                _, target, normalized = self.registry.resolve_path(project_id, path)
                self.policy.require_regular_file(target)
                new_head = await self.git.delete_tracked_file(
                    project.root,
                    path=normalized,
                    expected_head=checkpoint.head,
                    description=description,
                    commit_mode=mutation.commit_mode,
                    session_id=session_id,
                    expected_branch=checkpoint.branch,
                )
                checkpoint_data = await self._finish_mutation(
                    project,
                    checkpoint,
                    expected_after_head=new_head,
                )
            return _Outcome(
                {
                    "path": normalized,
                    "head": new_head,
                    "checkpoint": checkpoint_data,
                },
                list(checkpoint_data["after"]["changed_files"]),
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="file_delete",
            operation_input={
                "path": path,
                "description": description,
            },
            mutation=True,
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
        )

    async def directory_create(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        path: str,
        description: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        async def operation(operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            if not description or len(description) > 500:
                raise BridgeError(
                    "INVALID_REQUEST",
                    "description must be 1-500 characters",
                )
            project, target, normalized = self.registry.resolve_path(project_id, path)
            parent = target.parent
            if not parent.exists() or not parent.is_dir():
                raise BridgeError(
                    "FILE_NOT_FOUND",
                    "parent directory does not exist",
                    {"path": normalized},
                )

            async with self._mutation_lock(project_id):
                if target.exists():
                    raise BridgeError(
                        "CONFLICT",
                        "path already exists",
                        {"path": normalized},
                    )
                mutation = await self._begin_mutation(
                    project,
                    session_id=session_id,
                    operation_id=operation_id,
                )
                checkpoint = mutation.checkpoint
                if target.exists():
                    raise BridgeError(
                        "CONFLICT",
                        "path appeared after the mutation baseline was recorded",
                        {"path": normalized},
                    )

                try:
                    target.mkdir()
                except FileExistsError as exc:
                    raise BridgeError(
                        "CONFLICT",
                        "path already exists",
                        {"path": normalized},
                    ) from exc
                except OSError as exc:
                    raise BridgeError(
                        "BACKEND_UNAVAILABLE",
                        "directory could not be created",
                        {"path": normalized},
                        status="failed",
                    ) from exc

                marker = target / ".gitkeep"
                marker_path = self.registry.relative_path(project, marker)
                try:
                    new_head = await self.git.commit_file_bytes(
                        project.root,
                        path=marker_path,
                        content=b"",
                        expected_head=checkpoint.head,
                        description=f"{description} (directory marker)",
                        require_exists=False,
                        commit_mode=mutation.commit_mode,
                        session_id=session_id,
                        expected_branch=checkpoint.branch,
                    )
                except asyncio.CancelledError:
                    if not marker.exists():
                        try:
                            target.rmdir()
                        except OSError:
                            pass
                    raise
                except Exception as exc:
                    if not marker.exists():
                        try:
                            target.rmdir()
                        except OSError as cleanup_exc:
                            raise BridgeError(
                                "UNKNOWN_SIDE_EFFECT",
                                "directory creation outcome is unknown and requires reconciliation",
                                {"path": normalized},
                                status="unknown",
                            ) from cleanup_exc
                    raise exc

                checkpoint_data = await self._finish_mutation(
                    project,
                    checkpoint,
                    expected_after_head=new_head,
                )
            return _Outcome(
                {
                    "path": normalized,
                    "marker_path": marker_path,
                    "text": f"Bridge directory create committed at {new_head}",
                    "checkpoint": checkpoint_data,
                },
                list(checkpoint_data["after"]["changed_files"]),
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="directory_create",
            operation_input={
                "path": path,
                "description": description,
            },
            mutation=True,
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
        )

    async def _run_registered_command(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        command_id: str,
        expected_kind: str | None,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        prepared: dict[str, Any] = {}

        def resolve_command(project: ProjectSpec) -> CommandSpec:
            if expected_kind is None:
                return self.policy.registered_command(project, command_id, require_approval=False)
            return self.policy.command(project, command_id, expected_kind, require_approval=False)

        async def approval_check() -> bool:
            await self._require_session(project_id, session_id)
            project = self.registry.get(project_id)
            command = resolve_command(project)
            prepared["project"] = project
            prepared["command"] = command
            return command.approval == "required"

        async def operation(operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            project = prepared.get("project") or self.registry.get(project_id)
            command = prepared.get("command")
            if not isinstance(command, CommandSpec):
                command = resolve_command(project)
            return await self._run_command_body(project, command, session_id, operation_id)

        operation_kind = (
            "registered_command_run" if expected_kind is None else f"{expected_kind}_run"
        )
        operation_input: dict[str, Any] = {"command_id": command_id}
        if expected_kind is not None:
            operation_input["expected_kind"] = expected_kind
        approval_action = (
            f"command:{command_id}" if expected_kind is None else f"{expected_kind}:{command_id}"
        )
        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind=operation_kind,
            operation_input=operation_input,
            mutation=True,
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
            approval_action=approval_action,
            approval_check=approval_check,
        )

    async def _run_command_body(
        self,
        project: ProjectSpec,
        command: CommandSpec,
        session_id: str,
        operation_id: str,
    ) -> _Outcome:
        async with self._mutation_lock(project.project_id):
            mutation = await self._begin_mutation(
                project,
                session_id=session_id,
                operation_id=operation_id,
            )
            checkpoint = mutation.checkpoint
            result = await self.command_runner.run(project, command)
            after_command = await self.git.status(project.root)
            if (
                after_command.branch != checkpoint.branch
                or after_command.head.lower() != checkpoint.head.lower()
            ):
                raise BridgeError(
                    "UNKNOWN_SIDE_EFFECT",
                    "registered command changed Git HEAD or branch",
                    {
                        "command_id": command.command_id,
                        "expected_branch": checkpoint.branch,
                        "actual_branch": after_command.branch,
                        "expected_head": checkpoint.head,
                        "actual_head": after_command.head,
                    },
                    status="unknown",
                )
            if result.is_error:
                raise BridgeError(
                    "BACKEND_UNAVAILABLE",
                    "registered command failed",
                    {
                        "command_id": command.command_id,
                        "output": result.text,
                        "truncated": result.truncated,
                    },
                    status="failed",
                )
            checkpoint_data = await self._finish_mutation(
                project,
                checkpoint,
                expected_after_head=checkpoint.head,
            )
        return _Outcome(
            {
                "command_id": command.command_id,
                "text": result.text,
                "checkpoint": checkpoint_data,
            },
            checkpoint_data["after"]["changed_files"],
            result.truncated,
            "succeeded",
        )

    async def registered_command_run(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        command_id: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        return await self._run_registered_command(
            ctx,
            project_id,
            session_id,
            command_id,
            None,
            client_request_id,
            request_hash,
        )

    async def format_run(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        command_id: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        return await self._run_registered_command(
            ctx,
            project_id,
            session_id,
            command_id,
            "format",
            client_request_id,
            request_hash,
        )

    async def test_run(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        command_id: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        return await self._run_registered_command(
            ctx,
            project_id,
            session_id,
            command_id,
            "test",
            client_request_id,
            request_hash,
        )

    async def git_status(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        async def operation(_operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            project = self.registry.get(project_id)
            status = await self.policy.inspect_project(project)
            return _Outcome(
                {
                    "branch": status.branch,
                    "head": status.head,
                    "dirty": status.dirty,
                    "changed_files": list(status.changed_files),
                },
                list(status.changed_files),
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="git_status",
            operation_input={"project_id": project_id, "session_id": session_id},
        )

    async def git_diff(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        async def operation(_operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            project = self.registry.get(project_id)
            await self.policy.inspect_project(project)
            if checkpoint_id is None:
                diff, truncated = await self.git.diff(project.root)
                return _Outcome({"text": diff}, [], truncated)
            checkpoint = self.checkpoints.get_for_session(
                checkpoint_id,
                project_id=project_id,
                session_id=session_id,
            )
            await self.checkpoints.verify_ref(project, checkpoint)
            changed_files = await self.git.diff_names_from(project.root, checkpoint.ref_name)
            diff, truncated = await self.git.diff_from(project.root, checkpoint.ref_name)
            return _Outcome(
                {
                    "text": diff,
                    "checkpoint": self.checkpoints.summary(checkpoint),
                },
                list(changed_files),
                truncated,
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="git_diff",
            operation_input={
                "project_id": project_id,
                "session_id": session_id,
                "checkpoint_id": checkpoint_id,
            },
        )

    async def _create_checkpoint_for_operation(
        self,
        *,
        project_id: str,
        session_id: str,
        operation_id: str,
    ) -> _Outcome:
        project = self.registry.get(project_id)
        async with self._mutation_lock(project.project_id):
            checkpoint = await self.checkpoints.create(
                project,
                session_id=session_id,
                operation_id=operation_id,
                kind="manual",
            )
        return _Outcome(
            {"checkpoint": self.checkpoints.summary(checkpoint)},
            [],
        )

    async def _run_checkpoint_create_for_operation(self, operation: OperationRecord) -> _Outcome:
        if operation.session_id is None:
            raise BridgeError("SESSION_REQUIRED", "checkpoint operation has no session")
        return await self._create_checkpoint_for_operation(
            project_id=operation.project_id,
            session_id=operation.session_id,
            operation_id=operation.operation_id,
        )

    async def checkpoint_create(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        async def approval_check() -> bool:
            await self._require_session(project_id, session_id)
            project = self.registry.get(project_id)
            await self.policy.require_mutation_preconditions(project)
            return True

        async def operation(operation_id: str) -> _Outcome:
            await self._require_session(project_id, session_id)
            return await self._create_checkpoint_for_operation(
                project_id=project_id,
                session_id=session_id,
                operation_id=operation_id,
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="checkpoint_create",
            operation_input={"project_id": project_id, "session_id": session_id},
            mutation=True,
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
            approval_action="checkpoint_create",
            approval_check=approval_check,
        )

    async def _restore_checkpoint_for_operation(
        self,
        *,
        project_id: str,
        session_id: str,
        checkpoint_id: str,
        expected_head: str,
        operation_id: str,
    ) -> _Outcome:
        self.checkpoints.validate_expected_head(expected_head)
        await self._require_session(project_id, session_id)
        project = self.registry.get(project_id)
        async with self._mutation_lock(project_id):
            checkpoint = self.checkpoints.get_for_session(
                checkpoint_id,
                project_id=project_id,
                session_id=session_id,
            )
            await self.checkpoints.verify_ref(project, checkpoint)
            current = await self.policy.inspect_project(project, enforce_branch=False)
            if current.branch != checkpoint.branch or current.head.lower() != expected_head.lower():
                raise BridgeError(
                    "CHECKPOINT_CONFLICT",
                    "rollback compare-and-swap precondition failed",
                    {
                        "checkpoint_id": checkpoint_id,
                        "expected_branch": checkpoint.branch,
                        "actual_branch": current.branch,
                        "expected_head": expected_head,
                        "actual_head": current.head,
                    },
                )
            self.policy.require_allowed_branch(project, current.branch)
            self.policy.require_clean_workspace(project, current)
            safety = await self.checkpoints.create(
                project,
                session_id=session_id,
                operation_id=operation_id,
                kind="rollback_safety",
            )
            try:
                await self.git.reset_to_checkpoint(project.root, checkpoint.ref_name)
            except BridgeError as exc:
                raise BridgeError(
                    "UNKNOWN_SIDE_EFFECT",
                    "rollback outcome is unknown and requires reconciliation",
                    {
                        "checkpoint_id": checkpoint_id,
                        "safety_checkpoint_id": safety.checkpoint_id,
                        "cause": exc.code,
                    },
                    status="unknown",
                ) from exc
            try:
                after = await self.git.snapshot(project.root)
                if (
                    after.branch != checkpoint.branch
                    or after.head.lower() != checkpoint.head.lower()
                    or after.dirty
                ):
                    raise BridgeError(
                        "UNKNOWN_SIDE_EFFECT",
                        "rollback completed but the restored Git state cannot be confirmed",
                        {
                            "checkpoint_id": checkpoint_id,
                            "expected_branch": checkpoint.branch,
                            "actual_branch": after.branch,
                            "expected_head": checkpoint.head,
                            "actual_head": after.head,
                            "dirty": after.dirty,
                        },
                        status="unknown",
                    )
                safety = await self.checkpoints.finalize(
                    project,
                    safety,
                    expected_after_head=checkpoint.head,
                    expected_after_branch=checkpoint.branch,
                )
            except BridgeError as exc:
                if exc.code == "UNKNOWN_SIDE_EFFECT":
                    raise
                raise BridgeError(
                    "UNKNOWN_SIDE_EFFECT",
                    "rollback completed but its Git result could not be recorded",
                    {
                        "checkpoint_id": checkpoint_id,
                        "safety_checkpoint_id": safety.checkpoint_id,
                        "cause": exc.code,
                    },
                    status="unknown",
                ) from exc
            restored = self.checkpoints.mark_restored(checkpoint_id)
        return _Outcome(
            {
                "checkpoint": self.checkpoints.summary(restored),
                "safety_checkpoint": self.checkpoints.summary(safety),
                "restored_head": checkpoint.head,
                "branch": checkpoint.branch,
            },
            safety.after_data.get("changed_files", []) if safety.after_data else [],
        )

    async def _run_checkpoint_restore_for_operation(self, operation: OperationRecord) -> _Outcome:
        if operation.session_id is None:
            raise BridgeError("SESSION_REQUIRED", "rollback operation has no session")
        input_data = operation.input_data
        checkpoint_id = input_data.get("checkpoint_id")
        expected_head = input_data.get("expected_head")
        if not isinstance(checkpoint_id, str) or not isinstance(expected_head, str):
            raise BridgeError("INVALID_REQUEST", "stored rollback operation is malformed")
        return await self._restore_checkpoint_for_operation(
            project_id=operation.project_id,
            session_id=operation.session_id,
            checkpoint_id=checkpoint_id,
            expected_head=expected_head,
            operation_id=operation.operation_id,
        )

    async def checkpoint_restore(
        self,
        ctx: Context | None,
        project_id: str,
        session_id: str,
        checkpoint_id: str,
        expected_head: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        async def approval_check() -> bool:
            await self._restore_checkpoint_preconditions(
                project_id,
                session_id,
                checkpoint_id,
                expected_head,
            )
            return True

        async def operation(operation_id: str) -> _Outcome:
            return await self._restore_checkpoint_for_operation(
                project_id=project_id,
                session_id=session_id,
                checkpoint_id=checkpoint_id,
                expected_head=expected_head,
                operation_id=operation_id,
            )

        return await self._execute(
            ctx,
            project_id=project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="checkpoint_restore",
            operation_input={
                "project_id": project_id,
                "session_id": session_id,
                "checkpoint_id": checkpoint_id,
                "expected_head": expected_head,
            },
            mutation=True,
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
            approval_action=f"checkpoint_restore:{checkpoint_id}",
            approval_check=approval_check,
        )

    async def _restore_checkpoint_preconditions(
        self,
        project_id: str,
        session_id: str,
        checkpoint_id: str,
        expected_head: str,
    ) -> None:
        self.checkpoints.validate_expected_head(expected_head)
        await self._require_session(project_id, session_id)
        project = self.registry.get(project_id)
        checkpoint = self.checkpoints.get_for_session(
            checkpoint_id,
            project_id=project_id,
            session_id=session_id,
        )
        await self.checkpoints.verify_ref(project, checkpoint)
        current = await self.policy.inspect_project(project, enforce_branch=False)
        if current.branch != checkpoint.branch or current.head.lower() != expected_head.lower():
            raise BridgeError(
                "CHECKPOINT_CONFLICT",
                "rollback compare-and-swap precondition failed",
                {
                    "checkpoint_id": checkpoint_id,
                    "expected_branch": checkpoint.branch,
                    "actual_branch": current.branch,
                    "expected_head": expected_head,
                    "actual_head": current.head,
                },
            )
        self.policy.require_allowed_branch(project, current.branch)
        self.policy.require_clean_workspace(project, current)

    async def operation_status(
        self,
        ctx: Context | None,
        operation_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        await self.start()
        request_id = self._request_id(ctx)
        try:
            record = self.require_operation_for_reconcile(operation_id, session_id)
            return success_payload(
                request_id=request_id,
                session_id=session_id,
                project_id=record.project_id,
                operation_id=record.operation_id,
                data={
                    "state": record.state,
                    "kind": record.kind,
                    "mutation": record.mutation,
                    "client_request_id": record.client_request_id,
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                    "started_at": record.started_at,
                    "finished_at": record.finished_at,
                    "result": record.result_data,
                    "error": record.error_data,
                    "checkpoints": self.checkpoints.for_operation(record.operation_id),
                    "audit_events": self.audit.for_operation(record.operation_id),
                },
            )
        except BridgeError as exc:
            return error_payload(
                request_id=request_id,
                session_id=session_id,
                project_id=None,
                operation_id=operation_id,
                error=exc,
            )

    async def approval_confirm(
        self,
        ctx: Context | None,
        operation_id: str,
        session_id: str,
        approval_token: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        await self.start()
        try:
            original = self.require_operation_for_session(operation_id, session_id)
        except BridgeError as exc:
            return error_payload(
                request_id=self._request_id(ctx),
                session_id=session_id,
                project_id=None,
                operation_id=operation_id,
                error=exc,
            )

        async def operation(_operation_id: str) -> _Outcome:
            self.require_operation_for_session(operation_id, session_id)
            if original.state != "awaiting_approval":
                raise BridgeError(
                    "OPERATION_NOT_CANCELABLE",
                    "operation is not awaiting approval",
                    {"operation_id": operation_id, "state": original.state},
                )
            approved = self.approvals.consume(operation_id, approval_token)
            running = self.operations.dispatch(
                approved.operation_id, from_state="awaiting_approval"
            )
            try:
                outcome = await self._run_approved_operation(running)
            except BridgeError as exc:
                final = error_payload(
                    request_id=self._request_id(ctx),
                    session_id=session_id,
                    project_id=running.project_id,
                    operation_id=running.operation_id,
                    error=exc,
                )
                final_state = "unknown" if exc.status == "unknown" else "failed"
                self.operations.finish(running.operation_id, state=final_state, payload=final)
                return _Outcome(
                    {"approved_operation": final},
                    [],
                    status="failed",
                )
            except Exception:
                logger.exception("unexpected approved mutation failure")
                final = error_payload(
                    request_id=self._request_id(ctx),
                    session_id=session_id,
                    project_id=running.project_id,
                    operation_id=running.operation_id,
                    error=BridgeError(
                        "UNKNOWN_SIDE_EFFECT",
                        "approved mutation outcome is unknown and requires reconciliation",
                        {"operation_id": running.operation_id},
                        status="unknown",
                    ),
                )
                self.operations.finish(running.operation_id, state="unknown", payload=final)
                return _Outcome(
                    {"approved_operation": final},
                    [],
                    status="failed",
                )
            final = success_payload(
                request_id=self._request_id(ctx),
                session_id=session_id,
                project_id=running.project_id,
                operation_id=running.operation_id,
                data=outcome.data,
                changed_files=outcome.changed_files,
                truncated=outcome.truncated,
                status=outcome.status,
            )
            self.operations.finish(running.operation_id, state="succeeded", payload=final)
            return _Outcome(
                {"approved_operation": final},
                outcome.changed_files,
                outcome.truncated,
                outcome.status,
            )

        return await self._execute(
            ctx,
            project_id=original.project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="approval_confirm",
            operation_input={
                "operation_id": operation_id,
                "approval_token_digest": calculate_request_hash(approval_token),
            },
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
        )

    async def _run_approved_operation(self, operation: OperationRecord) -> _Outcome:
        if operation.kind == "checkpoint_create":
            return await self._run_checkpoint_create_for_operation(operation)
        if operation.kind == "checkpoint_restore":
            return await self._run_checkpoint_restore_for_operation(operation)
        return await self._run_command_for_operation(operation)

    async def _run_command_for_operation(self, operation: OperationRecord) -> _Outcome:
        input_data = operation.input_data
        command_id = input_data.get("command_id")
        expected_kind = input_data.get("expected_kind")
        if not isinstance(command_id, str):
            raise BridgeError("INVALID_REQUEST", "stored command operation is malformed")
        project = self.registry.get(operation.project_id)
        if expected_kind is None and operation.kind == "registered_command_run":
            command = self.policy.registered_command(project, command_id, require_approval=False)
        elif isinstance(expected_kind, str):
            command = self.policy.command(
                project, command_id, expected_kind, require_approval=False
            )
        else:
            raise BridgeError("INVALID_REQUEST", "stored command operation is malformed")
        if operation.session_id is None:
            raise BridgeError("SESSION_REQUIRED", "approved operation has no session")
        return await self._run_command_body(
            project,
            command,
            operation.session_id,
            operation.operation_id,
        )

    async def operation_cancel(
        self,
        ctx: Context | None,
        operation_id: str,
        session_id: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        await self.start()
        try:
            original = self.require_operation_for_session(operation_id, session_id)
        except BridgeError as exc:
            return error_payload(
                request_id=self._request_id(ctx),
                session_id=session_id,
                project_id=None,
                operation_id=operation_id,
                error=exc,
            )

        async def operation(_operation_id: str) -> _Outcome:
            self.require_operation_for_session(operation_id, session_id)
            if original.state != "awaiting_approval":
                raise BridgeError(
                    "OPERATION_NOT_CANCELABLE",
                    "only an operation awaiting approval can be cancelled",
                    {"operation_id": operation_id, "state": original.state},
                )
            self.approvals.cancel(operation_id, reason="client_cancelled")
            final = success_payload(
                request_id=self._request_id(ctx),
                session_id=session_id,
                project_id=original.project_id,
                operation_id=operation_id,
                data={"state": "cancelled"},
                status="cancelled",
            )
            self.operations.finish(operation_id, state="cancelled", payload=final)
            return _Outcome({"cancelled_operation": final}, [], status="cancelled")

        return await self._execute(
            ctx,
            project_id=original.project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="operation_cancel",
            operation_input={"operation_id": operation_id},
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
        )

    async def operation_reconcile(
        self,
        ctx: Context | None,
        operation_id: str,
        session_id: str,
        decision: str,
        evidence: str,
        client_request_id: str | None,
        request_hash: str | None,
    ) -> dict[str, Any]:
        await self.start()
        try:
            original = self.require_operation_for_reconcile(operation_id, session_id)
        except BridgeError as exc:
            return error_payload(
                request_id=self._request_id(ctx),
                session_id=session_id,
                project_id=None,
                operation_id=operation_id,
                error=exc,
            )

        async def operation(_operation_id: str) -> _Outcome:
            self.require_operation_for_reconcile(operation_id, session_id)
            if decision not in {"failed", "succeeded"}:
                raise BridgeError(
                    "RECONCILE_REQUIRED",
                    "decision must explicitly confirm whether the unknown mutation "
                    "failed or succeeded",
                )
            if not evidence or len(evidence) > 1000:
                raise BridgeError("INVALID_REQUEST", "evidence must be 1-1000 characters")
            if original.state != "unknown":
                raise BridgeError(
                    "RECONCILE_REQUIRED",
                    "only unknown operations require reconciliation",
                    {"state": original.state},
                )
            if decision == "failed":
                final = error_payload(
                    request_id=self._request_id(ctx),
                    session_id=session_id,
                    project_id=original.project_id,
                    operation_id=operation_id,
                    error=BridgeError(
                        "BRIDGE_RESTARTED",
                        "operation was reconciled as not executed",
                        {"evidence": evidence, "reconciled": True},
                        status="failed",
                    ),
                )
                self.operations.finish(operation_id, state="failed", payload=final)
                return _Outcome({"reconciled_operation": final}, [], status="failed")

            project = self.registry.get(original.project_id)
            async with self._mutation_lock(project.project_id):
                checkpoints = self.database.list_checkpoints(operation_id=operation_id)
                mutation_checkpoints = [
                    checkpoint for checkpoint in checkpoints if checkpoint.kind == "mutation"
                ]
                if len(mutation_checkpoints) != 1:
                    raise BridgeError(
                        "RECONCILE_REQUIRED",
                        "successful reconciliation requires exactly one mutation checkpoint",
                        {"checkpoint_count": len(mutation_checkpoints)},
                    )
                checkpoint = mutation_checkpoints[0]
                if checkpoint.after_data is not None:
                    raise BridgeError(
                        "RECONCILE_REQUIRED",
                        "successful reconciliation requires an unfinished mutation checkpoint",
                        {"checkpoint_id": checkpoint.checkpoint_id},
                    )
                await self.checkpoints.verify_ref(project, checkpoint)
                finalized = await self.checkpoints.finalize(project, checkpoint)
                checkpoint_data = self.checkpoints.summary(finalized)
                changed_files = list(checkpoint_data.get("after", {}).get("changed_files", []))
                final = success_payload(
                    request_id=self._request_id(ctx),
                    session_id=session_id,
                    project_id=original.project_id,
                    operation_id=operation_id,
                    data={
                        "reconciled": True,
                        "evidence": evidence,
                        "checkpoint": checkpoint_data,
                    },
                    changed_files=changed_files,
                )
                self.operations.finish(operation_id, state="succeeded", payload=final)
            return _Outcome({"reconciled_operation": final}, changed_files)

        return await self._execute(
            ctx,
            project_id=original.project_id,
            session_id=session_id,
            operation=operation,
            operation_kind="operation_reconcile",
            operation_input={
                "operation_id": operation_id,
                "decision": decision,
                "evidence_digest": calculate_request_hash(evidence),
            },
            client_request_id=client_request_id,
            supplied_request_hash=request_hash,
        )

    async def health(self) -> dict[str, Any]:
        await self.start()
        self._refresh_project_registry()
        snapshot = self.registry.snapshot()
        return {
            "status": "ok",
            "phase": "5",
            "transport": self.settings.server.transport,
            "endpoint": (
                f"{self.settings.server.host}:{self.settings.server.port}"
                f"{self.settings.server.path}"
            ),
            "worker_mode": self.settings.codemcp.worker_mode,
            "projects_registered": len(snapshot),
            "project_registry": {
                "generation": self.registry.generation,
                "reload_status": self.registry.last_reload_status,
                "last_reload_error": self.registry.last_reload_error_code,
            },
            "model_egress": "deny",
        }

    async def close(self) -> None:
        if not self._started:
            return
        try:
            await self.close_backend()
        finally:
            self.sessions.close_all("bridge_shutdown")
            self.database.close()
            self._started = False

    async def close_backend(self) -> None:
        await self.adapter.close()


def create_server(
    settings: BridgeSettings,
    adapter: AdapterLike | None = None,
    *,
    network_trust: NetworkTrustConfig | None = None,
    network_resource: str | None = None,
) -> tuple[FastMCP, BridgeService]:
    service = BridgeService(settings, adapter)

    @asynccontextmanager
    async def lifespan(_: FastMCP):
        yield

    server = BridgeFastMCP(
        "codemcp-remote-bridge",
        instructions="ChatGPT-only local policy bridge; codemcp is an execution backend.",
        host=settings.server.host,
        port=settings.server.port,
        streamable_http_path=settings.server.path,
        stateless_http=True,
        json_response=True,
        lifespan=lifespan,
        network_trust=network_trust,
        network_resource=network_resource,
    )
    # MCP 1.x closes stateless transports from the HTTP request task. Use the
    # local compatibility manager so responder cancel scopes unwind cleanly.
    server._session_manager = BridgeStreamableHTTPSessionManager(  # noqa: SLF001
        app=server._mcp_server,  # noqa: SLF001
        event_store=server._event_store,  # noqa: SLF001
        retry_interval=server._retry_interval,  # noqa: SLF001
        json_response=server.settings.json_response,
        stateless=server.settings.stateless_http,
        security_settings=server.settings.transport_security,
        max_request_body_size=server.settings.max_request_body_size,
        startup_callback=service.start,
        shutdown_callback=service.close,
    )

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse(await service.health())

    @server.tool(description="Open a registered project and create a persistent session.")
    async def project_open(project_id: str, ctx: Context) -> dict[str, Any]:
        return await service.project_open(ctx, project_id)

    @server.tool(description="Return registered project and worker status.")
    async def project_status(
        project_id: str, session_id: str | None = None, ctx: Context | None = None
    ) -> dict[str, Any]:
        return await service.project_status(ctx, project_id, session_id)

    @server.tool(description="Read one UTF-8 project file through codemcp.")
    async def file_read(
        project_id: str,
        session_id: str,
        path: str,
        offset: int | None = None,
        limit: int | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.file_read(ctx, project_id, session_id, path, offset, limit)

    @server.tool(description="Search project code through codemcp Grep.")
    async def code_search(
        project_id: str,
        session_id: str,
        pattern: str,
        path: str | None = None,
        include: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.code_search(ctx, project_id, session_id, pattern, path, include)

    @server.tool(description="List a registered project directory through codemcp.")
    async def file_list(
        project_id: str,
        session_id: str,
        path: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.file_list(ctx, project_id, session_id, path)

    @server.tool(description="Apply one exact replacement through codemcp EditFile.")
    async def file_edit(
        project_id: str,
        session_id: str,
        path: str,
        old_string: str,
        new_string: str,
        description: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.file_edit(
            ctx,
            project_id,
            session_id,
            path,
            old_string,
            new_string,
            description,
            client_request_id,
            request_hash,
        )

    @server.tool(description="Create one new UTF-8 text file through codemcp WriteFile.")
    async def file_create(
        project_id: str,
        session_id: str,
        path: str,
        content: str,
        description: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.file_create(
            ctx,
            project_id,
            session_id,
            path,
            content,
            description,
            client_request_id,
            request_hash,
        )

    @server.tool(description="Replace one existing UTF-8 text file when its SHA-256 still matches.")
    async def file_write(
        project_id: str,
        session_id: str,
        path: str,
        content: str,
        expected_sha256: str,
        description: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.file_write(
            ctx,
            project_id,
            session_id,
            path,
            content,
            expected_sha256,
            description,
            client_request_id,
            request_hash,
        )

    @server.tool(
        description=(
            "Move one tracked file within the registered project without overwriting "
            "an existing destination."
        ),
    )
    async def file_move(
        project_id: str,
        session_id: str,
        source_path: str,
        destination_path: str,
        description: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.file_move(
            ctx,
            project_id,
            session_id,
            source_path,
            destination_path,
            description,
            client_request_id,
            request_hash,
        )

    @server.tool(
        description="Delete one tracked file from the registered project.",
    )
    async def file_delete(
        project_id: str,
        session_id: str,
        path: str,
        description: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.file_delete(
            ctx,
            project_id,
            session_id,
            path,
            description,
            client_request_id,
            request_hash,
        )

    @server.tool(
        description="Create one new Git-trackable directory with a .gitkeep marker.",
    )
    async def directory_create(
        project_id: str,
        session_id: str,
        path: str,
        description: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.directory_create(
            ctx,
            project_id,
            session_id,
            path,
            description,
            client_request_id,
            request_hash,
        )

    @server.tool(
        description=(
            "Execute one fixed pre-registered project workflow by command ID. "
            "Arbitrary shell, executable paths, argv, and runtime parameters are not accepted."
        )
    )
    async def registered_command_run(
        project_id: str,
        session_id: str,
        command_id: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.registered_command_run(
            ctx,
            project_id,
            session_id,
            command_id,
            client_request_id,
            request_hash,
        )

    @server.tool(description="Run one registered formatting command.")
    async def format_run(
        project_id: str,
        session_id: str,
        command_id: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.format_run(
            ctx,
            project_id,
            session_id,
            command_id,
            client_request_id,
            request_hash,
        )

    @server.tool(description="Run one registered test command.")
    async def test_run(
        project_id: str,
        session_id: str,
        command_id: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.test_run(
            ctx,
            project_id,
            session_id,
            command_id,
            client_request_id,
            request_hash,
        )

    @server.tool(description="Return bounded Git status for a registered project.")
    async def git_status(
        project_id: str, session_id: str, ctx: Context | None = None
    ) -> dict[str, Any]:
        return await service.git_status(ctx, project_id, session_id)

    @server.tool(description="Return a bounded Git diff for a registered project.")
    async def git_diff(
        project_id: str,
        session_id: str,
        checkpoint_id: str | None = None,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.git_diff(ctx, project_id, session_id, checkpoint_id)

    @server.tool(description="Create a Bridge-owned Git checkpoint after explicit approval.")
    async def checkpoint_create(
        project_id: str,
        session_id: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.checkpoint_create(
            ctx,
            project_id,
            session_id,
            client_request_id,
            request_hash,
        )

    @server.tool(description="Restore a Bridge-owned checkpoint with compare-and-swap approval.")
    async def checkpoint_restore(
        project_id: str,
        session_id: str,
        checkpoint_id: str,
        expected_head: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.checkpoint_restore(
            ctx,
            project_id,
            session_id,
            checkpoint_id,
            expected_head,
            client_request_id,
            request_hash,
        )

    @server.tool(description="Return the persistent state and audit trail of one operation.")
    async def operation_status(
        operation_id: str, session_id: str, ctx: Context | None = None
    ) -> dict[str, Any]:
        return await service.operation_status(ctx, operation_id, session_id)

    @server.tool(description="Consume a one-time approval token and run its operation.")
    async def approval_confirm(
        operation_id: str,
        session_id: str,
        approval_token: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.approval_confirm(
            ctx,
            operation_id,
            session_id,
            approval_token,
            client_request_id,
            request_hash,
        )

    @server.tool(description="Cancel an operation that is still awaiting approval.")
    async def operation_cancel(
        operation_id: str,
        session_id: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.operation_cancel(
            ctx,
            operation_id,
            session_id,
            client_request_id,
            request_hash,
        )

    @server.tool(description="Reconcile an unknown mutation as explicitly not executed.")
    async def operation_reconcile(
        operation_id: str,
        session_id: str,
        decision: str,
        evidence: str,
        client_request_id: str,
        request_hash: str,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        return await service.operation_reconcile(
            ctx,
            operation_id,
            session_id,
            decision,
            evidence,
            client_request_id,
            request_hash,
        )

    _validate_public_tool_surface(server)
    return server, service


def install_resource_server_auth(
    server: FastMCP,
    authenticator: OAuthResourceServerAuthenticator,
) -> None:
    """Install MCP authentication and its public RFC 9728 metadata route."""

    if getattr(server, "_codemcp_network_trust_config", None) is not None:  # noqa: SLF001
        raise RuntimeError(
            "network trust and OAuth resource-server authentication are mutually exclusive"
        )

    server._session_manager.install_request_authenticator(authenticator)  # noqa: SLF001
    server._codemcp_resource_auth_installed = True  # noqa: SLF001

    @server.custom_route(
        authenticator.protected_resource_metadata_path,
        methods=["GET"],
        include_in_schema=False,
    )
    async def protected_resource_metadata(_: Request) -> JSONResponse:
        return JSONResponse(authenticator.protected_resource_metadata())


def install_network_trust(
    server: FastMCP,
    config: NetworkTrustConfig,
    *,
    resource: str | None = None,
) -> None:
    """Install the explicit network-trust profile on a Bridge server."""

    if not isinstance(config, NetworkTrustConfig):
        raise TypeError("network trust requires NetworkTrustConfig")
    if not isinstance(server, BridgeFastMCP):
        raise TypeError("network trust requires a BridgeFastMCP server")
    if getattr(server, "_codemcp_resource_auth_installed", False):  # noqa: SLF001
        raise RuntimeError(
            "network trust and OAuth resource-server authentication are mutually exclusive"
        )
    if server._codemcp_network_trust_config is not None:  # noqa: SLF001
        raise RuntimeError("network trust is already installed")
    if getattr(server._session_manager, "_request_authenticator", None) is not None:  # noqa: SLF001
        raise RuntimeError("network trust cannot replace an installed request authenticator")

    server._codemcp_network_trust_config = config  # noqa: SLF001
    server._codemcp_network_resource = resource  # noqa: SLF001
    transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    server.settings.transport_security = transport_security
    server._session_manager.security_settings = transport_security  # noqa: SLF001


def create_app(
    settings: BridgeSettings,
    adapter: AdapterLike | None = None,
    *,
    network_trust: NetworkTrustConfig | None = None,
    network_resource: str | None = None,
) -> tuple[Any, BridgeService]:
    """Create the ASGI app and service for local contract tests."""

    server, service = create_server(
        settings,
        adapter,
        network_trust=network_trust,
        network_resource=network_resource,
    )
    return server.streamable_http_app(), service
