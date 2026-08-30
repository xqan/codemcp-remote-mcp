from __future__ import annotations

import json
import sqlite3
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from codemcp_bridge.db import Database, PersistenceError
from codemcp_bridge.errors import BridgeError
from codemcp_bridge.mcp_server import (
    create_server,
    install_network_trust,
    install_resource_server_auth,
)
from codemcp_bridge.network_trust import (
    NetworkTrustConfig,
    NetworkTrustMiddleware,
)
from codemcp_bridge.operation_service import OperationService, request_hash
from codemcp_bridge.resource_auth import (
    AUTH_SCOPE_KEY,
    CONTRACT_VERSION,
    AuthenticatedPrincipal,
    NetworkTrustedPrincipal,
    ResourceServerValidationConfig,
    auth_audit_details,
    auth_context_identity,
    bind_auth_context,
    reset_auth_context,
)
from codemcp_bridge.session_service import SessionService
from codemcp_bridge.settings import (
    BridgeSettings,
    CodemcpSettings,
    PolicySettings,
    ServerSettings,
    StorageSettings,
)
from codemcp_bridge.worker_manager import AdapterResult


class _NoopAdapter:
    async def call(
        self,
        project: Any,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> AdapterResult:
        del project, subtool, arguments, timeout_seconds, mutation
        return AdapterResult("unused", False)

    def is_active(self, project_id: str) -> bool:
        del project_id
        return False

    async def close(self) -> None:
        return None


def _settings(tmp_path: Path) -> BridgeSettings:
    return BridgeSettings(
        repository_root=tmp_path,
        bridge_config_path=tmp_path / "bridge.toml",
        projects_config_path=tmp_path / "projects.toml",
        server=ServerSettings("127.0.0.1", 46200, "/mcp", "streamable-http"),
        storage=StorageSettings(
            tmp_path / ".local",
            tmp_path / ".local" / "bridge.sqlite3",
            tmp_path / ".local" / "logs",
        ),
        policy=PolicySettings(False, False, False, True, 1024, 4096, "per-project"),
        codemcp=CodemcpSettings("local", "Ubuntu", None, 10, 10, 5),
        projects={},
    )


def _network_config(
    *, allowed_origins: tuple[str, ...] = ("https://chatgpt.com",)
) -> NetworkTrustConfig:
    return NetworkTrustConfig(
        mode="cloudflare-chatgpt",
        allowed_hosts=("mcp.example.com",),
        allowed_origins=allowed_origins,
    )


async def _receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


async def _invoke(
    app: Any, headers: list[tuple[bytes, bytes]]
) -> tuple[int, dict[str, str], bytes]:
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/healthz",
        "raw_path": b"/healthz",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 46200),
        "root_path": "",
    }
    await app(scope, _receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    response_headers = {
        name.decode("latin-1"): value.decode("latin-1") for name, value in start["headers"]
    }
    return start["status"], response_headers, body


@asynccontextmanager
async def _network_app(
    tmp_path: Path, *, allowed_origins: tuple[str, ...] = ("https://chatgpt.com",)
):
    server, _ = create_server(
        _settings(tmp_path),
        adapter=_NoopAdapter(),
        network_trust=_network_config(allowed_origins=allowed_origins),
        network_resource="https://mcp.example.com/mcp",
    )
    app = server.streamable_http_app()
    async with app.router.lifespan_context(app):
        yield app


def _headers(*pairs: tuple[str, str]) -> list[tuple[bytes, bytes]]:
    return [(name.encode("latin-1"), value.encode("latin-1")) for name, value in pairs]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "127.0.0.1",
        "foo.mcp.example.com",
        "mcp.example.com.attacker.com",
        "example.com",
        "mcp.example.com:444",
        "https://mcp.example.com",
        "mcp.example.com/mcp",
        "user@mcp.example.com",
    ],
)
async def test_network_trust_rejects_non_exact_hosts(tmp_path: Path, host: str) -> None:
    async with _network_app(tmp_path) as app:
        status, response_headers, body = await _invoke(app, _headers(("Host", host)))

    assert status == 403
    assert response_headers["cache-control"] == "no-store"
    assert json.loads(body) == {"error": "host_not_allowed"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers",
    [
        [],
        _headers(("Host", "mcp.example.com"), ("Host", "mcp.example.com")),
        _headers(("Host", "mcp.example.com"), ("Host", "evil.example.com")),
        _headers((":authority", "evil.example.com")),
        _headers(("Host", "mcp.example.com"), (":authority", "evil.example.com")),
        _headers(("Host", "evil.example.com"), ("X-Forwarded-Host", "mcp.example.com")),
    ],
)
async def test_network_trust_rejects_missing_conflicting_or_forwarded_host(
    tmp_path: Path,
    headers: list[tuple[bytes, bytes]],
) -> None:
    async with _network_app(tmp_path) as app:
        status, _, body = await _invoke(app, headers)

    assert status == 403
    assert json.loads(body) == {"error": "host_not_allowed"}


@pytest.mark.asyncio
async def test_network_trust_accepts_authority_when_it_is_the_actual_request_authority(
    tmp_path: Path,
) -> None:
    async with _network_app(tmp_path) as app:
        status, _, body = await _invoke(
            app,
            _headers((":authority", "mcp.example.com:443")),
        )

    assert status == 200
    assert json.loads(body)["status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("host", ["mcp.example.com", "mcp.example.com:443", "MCP.EXAMPLE.COM"])
async def test_network_trust_accepts_exact_canonical_hosts(tmp_path: Path, host: str) -> None:
    async with _network_app(tmp_path) as app:
        status, _, body = await _invoke(app, _headers(("Host", host)))

    assert status == 200
    assert json.loads(body)["status"] == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "http://chatgpt.com",
        "https://foo.chatgpt.com",
        "https://chatgpt.com.attacker.com",
        "https://chatgpt.com/path",
        "https://chatgpt.com?q=1",
        "https://user@chatgpt.com",
        "null",
        "malformed-origin",
    ],
)
async def test_network_trust_rejects_invalid_present_origins(tmp_path: Path, origin: str) -> None:
    async with _network_app(tmp_path) as app:
        status, response_headers, body = await _invoke(
            app,
            _headers(("Host", "mcp.example.com"), ("Origin", origin)),
        )

    assert status == 403
    assert response_headers["cache-control"] == "no-store"
    assert json.loads(body) == {"error": "origin_not_allowed"}


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["https://chatgpt.com", "https://chatgpt.com:443"])
async def test_network_trust_accepts_exact_origin_and_default_port(
    tmp_path: Path,
    origin: str,
) -> None:
    async with _network_app(tmp_path) as app:
        status, _, body = await _invoke(
            app,
            _headers(("Host", "mcp.example.com"), ("Origin", origin)),
        )

    assert status == 200
    assert json.loads(body)["status"] == "ok"


@pytest.mark.asyncio
async def test_network_trust_origin_is_if_present_and_empty_allowlist_is_fail_closed(
    tmp_path: Path,
) -> None:
    async with _network_app(tmp_path, allowed_origins=()) as app:
        missing_status, _, _ = await _invoke(app, _headers(("Host", "mcp.example.com")))
        present_status, _, body = await _invoke(
            app,
            _headers(("Host", "mcp.example.com"), ("Origin", "https://chatgpt.com")),
        )

    assert missing_status == 200
    assert present_status == 403
    assert json.loads(body) == {"error": "origin_not_allowed"}


@pytest.mark.asyncio
async def test_network_trust_rejects_multiple_origins_without_echoing_headers(
    tmp_path: Path,
) -> None:
    malicious = "https://attacker.example/?token=secret-value"
    async with _network_app(tmp_path) as app:
        status, _, body = await _invoke(
            app,
            _headers(
                ("Host", "mcp.example.com"),
                ("Origin", "https://chatgpt.com"),
                ("Origin", malicious),
            ),
        )

    assert status == 403
    assert json.loads(body) == {"error": "origin_not_allowed"}
    assert malicious not in body.decode("utf-8")


@pytest.mark.asyncio
async def test_network_trust_injects_explicit_principal_and_health_is_host_protected(
    tmp_path: Path,
) -> None:
    async with _network_app(tmp_path) as app:
        status, _, body = await _invoke(app, _headers(("Host", "mcp.example.com")))
        denied_status, _, denied_body = await _invoke(app, _headers(("Host", "evil.example.com")))

    assert status == 200
    assert denied_status == 403
    assert json.loads(denied_body) == {"error": "host_not_allowed"}


@pytest.mark.asyncio
async def test_network_trust_mcp_codemcp_557_exposes_complete_public_tool_surface(
    tmp_path: Path,
) -> None:
    server, _ = create_server(
        _settings(tmp_path),
        adapter=_NoopAdapter(),
        network_trust=_network_config(),
    )
    app = server.streamable_http_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://mcp.example.com",
            headers={"Authorization": "Bearer must-not-invoke-oauth"},
        ) as http:
            async with streamable_http_client(
                "http://mcp.example.com/mcp",
                http_client=http,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()

    exposed_tools = {tool.name for tool in tools.tools}
    assert exposed_tools == {
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
    assert {
        "file_edit",
        "file_create",
        "file_write",
        "checkpoint_create",
        "checkpoint_restore",
        "operation_status",
        "approval_confirm",
    } <= exposed_tools


def _oauth_principal(*, subject: str = "subject-a") -> AuthenticatedPrincipal:
    now = int(time.time())
    return AuthenticatedPrincipal(
        contract_version=CONTRACT_VERSION,
        issuer="https://auth.example.com",
        resource="https://mcp.example.com/mcp",
        subject=subject,
        client_id="oauth-client",
        scopes=("file:read",),
        issued_at=now - 10,
        expires_at=now + 300,
    )


def test_network_principal_is_deterministic_and_not_user_or_waf_identity() -> None:
    principal = NetworkTrustedPrincipal(resource="https://mcp.example.com/mcp")
    details = auth_audit_details(principal)

    assert principal == NetworkTrustedPrincipal(resource="https://mcp.example.com/mcp")
    assert details == {
        "auth_kind": "network-trusted",
        "auth_type": "network-trusted",
        "trust_profile": "cloudflare-chatgpt",
        "identity_level": "network-only",
        "principal": "network-chatgpt-v1",
        "issuer": "network-trust://cloudflare-chatgpt",
        "subject": "network-chatgpt-v1",
        "replay_namespace": "network-chatgpt-v1",
        "resource": "https://mcp.example.com/mcp",
    }
    assert "client_id" not in details
    assert auth_context_identity(details) == ("network-trusted", "network-chatgpt-v1")
    assert "waf_verified" not in details
    assert "user" not in json.dumps(details).lower()


@pytest.mark.asyncio
async def test_network_middleware_propagates_principal_to_downstream_scope() -> None:
    seen: dict[str, Any] = {}

    async def downstream(scope: Any, receive: Any, send: Any) -> None:
        del receive
        seen["principal"] = scope[AUTH_SCOPE_KEY]
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = NetworkTrustMiddleware(
        downstream,
        config=_network_config(),
        resource="https://mcp.example.com/mcp",
    )
    status, _, _ = await _invoke(middleware, _headers(("Host", "mcp.example.com")))

    assert status == 204
    assert isinstance(seen["principal"], NetworkTrustedPrincipal)
    assert seen["principal"].replay_namespace == "network-chatgpt-v1"


def test_oauth_and_network_replay_namespaces_are_isolated(tmp_path: Path) -> None:
    database = Database(tmp_path / "bridge.sqlite3")
    database.initialize()
    operations = OperationService(database)
    input_data = {"project_id": "demo"}
    digest = request_hash(input_data)

    network_token = bind_auth_context(NetworkTrustedPrincipal())
    try:
        network = operations.start(
            operation_id="network-operation",
            project_id="demo",
            session_id=None,
            kind="project_status",
            mutation=False,
            client_request_id="shared-request",
            supplied_request_hash=digest,
            input_data=input_data,
        ).record
    finally:
        reset_auth_context(network_token)

    oauth_token = bind_auth_context(_oauth_principal())
    try:
        oauth = operations.start(
            operation_id="oauth-operation",
            project_id="demo",
            session_id=None,
            kind="project_status",
            mutation=False,
            client_request_id="shared-request",
            supplied_request_hash=digest,
            input_data=input_data,
        ).record
    finally:
        reset_auth_context(oauth_token)

    assert network.operation_id != oauth.operation_id
    assert network.client_request_id == "shared-request"
    assert oauth.client_request_id == "shared-request"
    with sqlite3.connect(database.path) as connection:
        stored_keys = [
            row[0]
            for row in connection.execute(
                "SELECT client_request_id FROM operations ORDER BY operation_id"
            )
        ]
    assert len(stored_keys) == 2
    assert all("\x1f" in key for key in stored_keys)
    assert database.get_operation_auth_context(network.operation_id)["auth_kind"] == (
        "network-trusted"
    )
    assert database.get_operation_auth_context(oauth.operation_id)["auth_kind"] == (
        "oauth-resource-server"
    )
    database.close()


def test_legacy_oauth_replay_key_remains_readable(tmp_path: Path) -> None:
    database = Database(tmp_path / "bridge.sqlite3")
    database.initialize()
    operations = OperationService(database)
    input_data = {"project_id": "demo"}
    digest = request_hash(input_data)
    legacy, existing = database.create_operation(
        operation_id="legacy-operation",
        project_id="demo",
        session_id=None,
        owner_id="local-policy",
        client_request_id="legacy-request",
        request_hash=digest,
        kind="project_status",
        mutation=False,
        input_data=input_data,
    )
    assert not existing
    database.record_operation_auth_context(
        legacy.operation_id,
        details={
            "contract_version": "1",
            "issuer": "https://auth.example.com",
            "resource": "https://mcp.example.com/mcp",
            "subject": "subject-a",
            "client_id": "oauth-client",
            "scopes": ["file:read"],
            "issued_at": int(time.time()) - 10,
            "expires_at": int(time.time()) + 300,
        },
    )
    database.transition_operation(legacy.operation_id, "validated")
    database.transition_operation(legacy.operation_id, "dispatched")
    database.transition_operation(legacy.operation_id, "running")
    database.transition_operation(
        legacy.operation_id,
        "succeeded",
        result_data={"status": "succeeded", "operation_id": legacy.operation_id},
    )

    token = bind_auth_context(_oauth_principal())
    try:
        replay = operations.start(
            operation_id="new-operation",
            project_id="demo",
            session_id=None,
            kind="project_status",
            mutation=False,
            client_request_id="legacy-request",
            supplied_request_hash=digest,
            input_data=input_data,
        )
    finally:
        reset_auth_context(token)

    assert replay.is_replay
    assert replay.record.operation_id == legacy.operation_id
    assert replay.record.client_request_id == "legacy-request"
    database.close()


def test_session_security_context_cannot_cross_oauth_and_network(tmp_path: Path) -> None:
    database = Database(tmp_path / "bridge.sqlite3")
    database.initialize()
    sessions = SessionService(database)

    network_token = bind_auth_context(NetworkTrustedPrincipal())
    try:
        network_session = sessions.create("demo")
        assert sessions.require_active("demo", network_session.session_id) == network_session
    finally:
        reset_auth_context(network_token)

    oauth_token = bind_auth_context(_oauth_principal())
    try:
        with pytest.raises(BridgeError, match="security context"):
            sessions.require_active("demo", network_session.session_id)
        oauth_session = sessions.create("demo")
    finally:
        reset_auth_context(oauth_token)

    assert not sessions.auth_contexts_match(network_session.session_id, oauth_session.session_id)
    assert network_session.owner_id == oauth_session.owner_id == "local-policy"
    database.close()


def test_malformed_persisted_auth_context_fails_closed(tmp_path: Path) -> None:
    database = Database(tmp_path / "bridge.sqlite3")
    database.initialize()
    sessions = SessionService(database)

    token = bind_auth_context(NetworkTrustedPrincipal())
    try:
        session = sessions.create("demo")
    finally:
        reset_auth_context(token)

    with sqlite3.connect(database.path) as connection:
        connection.execute(
            "UPDATE audit_events SET details_json=? WHERE session_id=? "
            "AND event_type='auth.session.context'",
            ("[]", session.session_id),
        )
        connection.commit()

    with pytest.raises(PersistenceError, match="session auth context"):
        sessions.require_active("demo", session.session_id)
    database.close()


def test_network_and_oauth_installation_are_mutually_exclusive(tmp_path: Path) -> None:
    server, _ = create_server(_settings(tmp_path), adapter=_NoopAdapter())
    install_network_trust(server, _network_config())

    with pytest.raises(RuntimeError, match="mutually exclusive"):
        install_resource_server_auth(server, object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_network_trust_can_be_installed_before_app_creation(tmp_path: Path) -> None:
    server, _ = create_server(_settings(tmp_path), adapter=_NoopAdapter())
    install_network_trust(
        server,
        _network_config(),
        resource="https://mcp.example.com/mcp",
    )
    app = server.streamable_http_app()
    async with app.router.lifespan_context(app):
        status, _, body = await _invoke(app, _headers(("Host", "mcp.example.com")))

    assert status == 200
    assert json.loads(body)["status"] == "ok"


def test_network_principal_resource_is_optional_but_validated() -> None:
    assert NetworkTrustedPrincipal().resource is None
    with pytest.raises(ValueError, match="absolute HTTPS URI"):
        NetworkTrustedPrincipal(resource="http://mcp.example.com/mcp")


def test_oauth_audit_projection_keeps_stable_identity_and_adds_namespace() -> None:
    details = auth_audit_details(_oauth_principal())
    assert details["auth_kind"] == "oauth-resource-server"
    assert details["auth_type"] == "oauth"
    assert details["replay_namespace"].startswith("oauth-")
    assert auth_context_identity(details) == (
        "oauth-resource-server",
        details["replay_namespace"],
    )


def test_resource_auth_config_import_remains_available() -> None:
    config = ResourceServerValidationConfig(
        issuer="https://auth.example.com",
        resource="https://mcp.example.com/mcp",
        validation_resource_id="resource-code",
        validation_secret="secret",
    )
    assert config.contract_version == CONTRACT_VERSION


def test_network_principal_does_not_use_anonymous_namespace() -> None:
    details = auth_audit_details(NetworkTrustedPrincipal())
    serialized = json.dumps(details)
    assert "anonymous" not in serialized.lower()
    assert "network-chatgpt-v1" in serialized
