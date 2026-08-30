from __future__ import annotations

import base64
import json
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

import codemcp_bridge.resource_auth as resource_auth
from codemcp_bridge.db import Database
from codemcp_bridge.errors import BridgeError
from codemcp_bridge.mcp_server import create_server, install_resource_server_auth
from codemcp_bridge.operation_service import OperationService, request_hash
from codemcp_bridge.resource_auth import (
    AUTH_SCOPE_KEY,
    CONTRACT_VERSION,
    AuthenticatedPrincipal,
    OAuthResourceServerAuthenticator,
    OnlineResourceServerValidator,
    ResourceServerValidationConfig,
    ValidationHTTPResponse,
    VerificationServiceUnavailable,
    bind_auth_context,
    reset_auth_context,
)
from codemcp_bridge.settings import (
    BridgeSettings,
    CodemcpSettings,
    PolicySettings,
    ProjectSpec,
    ServerSettings,
    StorageSettings,
)
from codemcp_bridge.worker_manager import AdapterResult


def _config(
    *,
    resource: str = "https://code.example.com/mcp",
) -> ResourceServerValidationConfig:
    return ResourceServerValidationConfig(
        issuer="https://auth.example.com",
        resource=resource,
        validation_resource_id="resource-code",
        validation_secret="verification-secret-value",
    )


def _principal(
    *,
    subject: str = "subject-a",
    expires_at: int | None = None,
) -> AuthenticatedPrincipal:
    now = int(time.time())
    return AuthenticatedPrincipal(
        contract_version=CONTRACT_VERSION,
        issuer="https://auth.example.com",
        resource="https://code.example.com/mcp",
        subject=subject,
        client_id="chatgpt-client",
        scopes=("file:read", "file:write"),
        issued_at=now - 10,
        expires_at=expires_at or now + 300,
    )


def _active_response(**overrides: Any) -> ValidationHTTPResponse:
    now = int(time.time())
    payload: dict[str, Any] = {
        "contract_version": "1",
        "active": True,
        "issuer": "https://auth.example.com",
        "resource": "https://code.example.com/mcp",
        "subject": "subject-a",
        "client_id": "chatgpt-client",
        "scopes": ["file:read", "file:write"],
        "issued_at": now - 10,
        "expires_at": now + 300,
    }
    payload.update(overrides)
    return ValidationHTTPResponse(
        status_code=200,
        headers={
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        body=json.dumps(payload).encode("utf-8"),
    )


def _git(project: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


class _NoopAdapter:
    async def call(
        self,
        project: ProjectSpec,
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


def _bridge_settings(tmp_path: Path) -> BridgeSettings:
    project = tmp_path / "project"
    project.mkdir()
    (project / "README.md").write_text("phase55 auth\n", encoding="utf-8")
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.name", "Phase 5.5 auth test")
    _git(project, "config", "user.email", "phase55-auth@example.invalid")
    _git(project, "add", ".")
    _git(project, "commit", "-m", "test: auth baseline")
    spec = ProjectSpec(
        project_id="demo",
        root=project,
        allowed_branches=("main",),
        require_clean_workspace=True,
        codemcp_config=project / "codemcp.toml",
        commands={},
    )
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
        projects={"demo": spec},
    )


def _payload(result: Any) -> dict[str, Any]:
    if result.structuredContent:
        return result.structuredContent
    text_blocks = [block.text for block in result.content if hasattr(block, "text")]
    return json.loads("\n".join(text_blocks))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"issuer": "http://auth.example.com"}, "canonical HTTPS origin"),
        ({"issuer": "https://auth.example.com/"}, "canonical HTTPS origin"),
        ({"issuer": "https://auth.example.com/oauth"}, "canonical HTTPS origin"),
        ({"resource": "http://code.example.com/mcp"}, "absolute HTTPS URI"),
        ({"resource": "https://code.example.com/mcp?x=1"}, "absolute HTTPS URI"),
        ({"validation_resource_id": "bad:id"}, "Basic-auth username"),
        ({"timeout_seconds": 1.0}, "2 second timeout"),
        ({"contract_version": "2"}, "unsupported"),
    ],
)
def test_resource_server_config_fails_closed(kwargs: dict[str, Any], message: str) -> None:
    values: dict[str, Any] = {
        "issuer": "https://auth.example.com",
        "resource": "https://code.example.com/mcp",
        "validation_resource_id": "resource-code",
        "validation_secret": "verification-secret-value",
    }
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        ResourceServerValidationConfig(**values)


def test_validation_secret_is_not_exposed_in_repr() -> None:
    assert "verification-secret-value" not in repr(_config())


def test_protected_resource_metadata_is_derived_from_auth_config() -> None:
    config = ResourceServerValidationConfig(
        issuer="https://auth.example.com",
        resource="https://resource.example.com/mcp",
        validation_resource_id="resource-code",
        validation_secret="verification-secret-value",
    )

    assert config.protected_resource_metadata_url == (
        "https://resource.example.com/.well-known/oauth-protected-resource/mcp"
    )
    assert config.protected_resource_metadata_path == ("/.well-known/oauth-protected-resource/mcp")
    assert config.protected_resource_metadata() == {
        "resource": "https://resource.example.com/mcp",
        "authorization_servers": ["https://auth.example.com"],
        "bearer_methods_supported": ["header"],
    }


def test_protected_resource_metadata_preserves_nested_resource_path() -> None:
    config = ResourceServerValidationConfig(
        issuer="https://auth.example.com",
        resource="https://resource.example.com/foo/bar",
        validation_resource_id="resource-code",
        validation_secret="verification-secret-value",
    )

    assert config.protected_resource_metadata_url == (
        "https://resource.example.com/.well-known/oauth-protected-resource/foo/bar"
    )
    assert config.protected_resource_metadata_path == (
        "/.well-known/oauth-protected-resource/foo/bar"
    )


def test_default_requester_matches_frozen_wire_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status = 200
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        }

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            del args

        def read(self, limit: int) -> bytes:
            captured["read_limit"] = limit
            return b'{"contract_version":"1","active":false}'

    class FakeOpener:
        def open(self, request: Any, timeout: float) -> FakeResponse:
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["headers"] = dict(request.header_items())
            captured["body"] = request.data
            captured["timeout"] = timeout
            return FakeResponse()

    def fake_build_opener(*handlers: Any) -> FakeOpener:
        assert len(handlers) == 1
        assert isinstance(handlers[0], resource_auth._NoRedirectHandler)
        return FakeOpener()

    monkeypatch.setattr(resource_auth.urllib.request, "build_opener", fake_build_opener)
    response = resource_auth._default_requester(
        endpoint="https://auth.example.com/mcp/resource-server/validate",
        resource_id="resource-code",
        verification_secret="verification-secret-value",
        token="opaque-token-value",
        resource="https://code.example.com/mcp",
        timeout_seconds=2.0,
    )

    expected_basic = base64.b64encode(b"resource-code:verification-secret-value").decode("ascii")
    assert captured["url"] == "https://auth.example.com/mcp/resource-server/validate"
    assert captured["method"] == "POST"
    assert captured["timeout"] == 2.0
    assert captured["headers"]["Authorization"] == f"Basic {expected_basic}"
    assert captured["headers"]["Content-type"] == "application/json"
    assert captured["headers"]["Accept"] == "application/json"
    assert json.loads(captured["body"]) == {
        "token": "opaque-token-value",
        "resource": "https://code.example.com/mcp",
    }
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_validator_accepts_only_exact_active_v1_projection() -> None:
    captured: dict[str, Any] = {}

    def requester(**kwargs: Any) -> ValidationHTTPResponse:
        captured.update(kwargs)
        return _active_response()

    validator = OnlineResourceServerValidator(_config(), requester=requester)
    principal = await validator.validate("opaque-token-value")

    assert principal is not None
    assert principal.issuer == "https://auth.example.com"
    assert principal.resource == "https://code.example.com/mcp"
    assert principal.subject == "subject-a"
    assert principal.client_id == "chatgpt-client"
    assert principal.scopes == ("file:read", "file:write")
    assert captured == {
        "endpoint": "https://auth.example.com/mcp/resource-server/validate",
        "resource_id": "resource-code",
        "verification_secret": "verification-secret-value",
        "token": "opaque-token-value",
        "resource": "https://code.example.com/mcp",
        "timeout_seconds": 2.0,
    }


@pytest.mark.asyncio
async def test_validator_maps_inactive_credential_to_none() -> None:
    response = ValidationHTTPResponse(
        status_code=200,
        headers={"content-type": "application/json", "cache-control": "no-store"},
        body=b'{"contract_version":"1","active":false}',
    )
    validator = OnlineResourceServerValidator(_config(), requester=lambda **_: response)
    assert await validator.validate("opaque-token-value") is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        ValidationHTTPResponse(401, {"content-type": "application/json"}, b"{}"),
        ValidationHTTPResponse(
            200,
            {"content-type": "text/plain", "cache-control": "no-store"},
            b"{}",
        ),
        ValidationHTTPResponse(
            200,
            {"content-type": "application/json"},
            b'{"contract_version":"1","active":false}',
        ),
        ValidationHTTPResponse(
            200,
            {"content-type": "application/json", "cache-control": "no-store"},
            b"{",
        ),
        ValidationHTTPResponse(
            200,
            {"content-type": "application/json", "cache-control": "no-store"},
            b'{"contract_version":"2","active":false}',
        ),
    ],
)
async def test_validator_maps_protocol_and_service_failures_to_unavailable(
    response: ValidationHTTPResponse,
) -> None:
    validator = OnlineResourceServerValidator(_config(), requester=lambda **_: response)
    with pytest.raises(VerificationServiceUnavailable):
        await validator.validate("opaque-token-value")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"issuer": "https://other.example.com"},
        {"resource": "https://other.example.com/mcp"},
        {"subject": ""},
        {"client_id": ""},
        {"scopes": ["file:read", "file:read"]},
        {"scopes": "file:read"},
        {"expires_at": 1},
    ],
)
async def test_validator_rechecks_active_response_locally(overrides: dict[str, Any]) -> None:
    validator = OnlineResourceServerValidator(
        _config(),
        requester=lambda **_: _active_response(**overrides),
    )
    with pytest.raises(VerificationServiceUnavailable):
        await validator.validate("opaque-token-value")


@pytest.mark.asyncio
async def test_authenticator_maps_bearer_and_service_outcomes() -> None:
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    class StubValidator:
        def __init__(self, result: Any):
            self.result = result
            self.config = _config()

        async def validate(self, token: str) -> AuthenticatedPrincipal | None:
            assert token == "opaque-token"
            if isinstance(self.result, Exception):
                raise self.result
            return self.result

    missing = OAuthResourceServerAuthenticator(StubValidator(_principal()))  # type: ignore[arg-type]
    assert not await missing({"type": "http", "headers": []}, send)  # type: ignore[arg-type]
    assert messages[0]["status"] == 401
    expected_challenge = (
        b'Bearer resource_metadata="https://code.example.com/'
        b'.well-known/oauth-protected-resource/mcp"'
    )
    assert (b"www-authenticate", expected_challenge) in messages[0]["headers"]

    messages.clear()
    inactive = OAuthResourceServerAuthenticator(StubValidator(None))  # type: ignore[arg-type]
    scope = {"type": "http", "headers": [(b"authorization", b"Bearer opaque-token")]}
    assert not await inactive(scope, send)  # type: ignore[arg-type]
    assert messages[0]["status"] == 401
    assert (b"www-authenticate", expected_challenge) in messages[0]["headers"]
    serialized_inactive = repr(messages)
    assert "opaque-token" not in serialized_inactive
    assert "resource-code" not in serialized_inactive
    assert "verification-secret-value" not in serialized_inactive

    messages.clear()
    unavailable = OAuthResourceServerAuthenticator(  # type: ignore[arg-type]
        StubValidator(VerificationServiceUnavailable("unavailable"))
    )
    assert not await unavailable(scope, send)  # type: ignore[arg-type]
    assert messages[0]["status"] == 503
    assert not any(name == b"www-authenticate" for name, _ in messages[0]["headers"])

    messages.clear()
    principal = _principal()
    active = OAuthResourceServerAuthenticator(StubValidator(principal))  # type: ignore[arg-type]
    active_scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer opaque-token")],
    }
    assert await active(active_scope, send)  # type: ignore[arg-type]
    assert active_scope[AUTH_SCOPE_KEY] == principal
    assert messages == []


@pytest.mark.asyncio
async def test_cloudflare_identity_headers_do_not_authenticate_without_bearer() -> None:
    messages: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)

    class RejectingValidator:
        config = _config()

        async def validate(self, token: str) -> AuthenticatedPrincipal | None:
            pytest.fail(
                f"validator must not receive Cloudflare identity headers as bearer: {token}"
            )

    authenticator = OAuthResourceServerAuthenticator(RejectingValidator())  # type: ignore[arg-type]
    scope = {
        "type": "http",
        "headers": [
            (b"cf-access-authenticated-user-email", b"user@example.com"),
            (b"cf-access-jwt-assertion", b"cloudflare-access-token"),
            (b"x-forwarded-user", b"subject-a"),
        ],
    }

    assert not await authenticator(scope, send)  # type: ignore[arg-type]
    assert messages[0]["status"] == 401
    assert AUTH_SCOPE_KEY not in scope


def test_operation_idempotency_is_bound_to_stable_authenticated_identity(tmp_path: Path) -> None:
    database = Database(tmp_path / "bridge.sqlite3")
    database.initialize()
    operations = OperationService(database)
    input_data = {"project_id": "demo"}
    digest = request_hash(input_data)
    first = _principal(subject="subject-a")

    auth_token = bind_auth_context(first)
    try:
        started = operations.start(
            operation_id="operation-a",
            project_id="demo",
            session_id=None,
            kind="project_open",
            mutation=False,
            client_request_id="request-a",
            supplied_request_hash=digest,
            input_data=input_data,
        )
    finally:
        reset_auth_context(auth_token)

    events = database.list_audit_events(operation_id=started.record.operation_id)
    auth_events = [event for event in events if event["event_type"] == "auth.context"]
    assert len(auth_events) == 1
    assert auth_events[0]["details"]["subject"] == "subject-a"
    assert "opaque-token" not in json.dumps(auth_events)
    assert "verification-secret-value" not in json.dumps(auth_events)

    different = _principal(subject="subject-b")
    auth_token = bind_auth_context(different)
    try:
        with pytest.raises(BridgeError, match="different authenticated identity") as error:
            operations.start(
                operation_id="operation-b",
                project_id="demo",
                session_id=None,
                kind="project_open",
                mutation=False,
                client_request_id="request-a",
                supplied_request_hash=digest,
                input_data=input_data,
            )
        assert error.value.code == "IDEMPOTENCY_CONFLICT"
    finally:
        reset_auth_context(auth_token)
        database.close()


@pytest.mark.asyncio
async def test_mcp_transport_enforces_auth_and_propagates_safe_audit_identity(
    tmp_path: Path,
) -> None:
    settings = _bridge_settings(tmp_path)
    config = _config(resource="https://resource.example.com/mcp")
    calls: list[str] = []

    def requester(**kwargs: Any) -> ValidationHTTPResponse:
        calls.append(kwargs["token"])
        if kwargs["token"] == "inactive-token-value":
            return ValidationHTTPResponse(
                status_code=200,
                headers={
                    "content-type": "application/json",
                    "cache-control": "no-store",
                },
                body=b'{"contract_version":"1","active":false}',
            )
        return _active_response(resource=config.resource)

    authenticator = OAuthResourceServerAuthenticator(
        OnlineResourceServerValidator(config, requester=requester)
    )
    server, service = create_server(settings, adapter=_NoopAdapter())
    install_resource_server_auth(server, authenticator)
    app = server.streamable_http_app()

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:46200",
        ) as anonymous:
            health = await anonymous.get("/healthz")
            assert health.status_code == 200
            metadata = await anonymous.get("/.well-known/oauth-protected-resource/mcp")
            assert metadata.status_code == 200
            assert metadata.headers["content-type"].split(";", 1)[0] == "application/json"
            assert metadata.json() == {
                "resource": "https://resource.example.com/mcp",
                "authorization_servers": ["https://auth.example.com"],
                "bearer_methods_supported": ["header"],
            }

            denied = await anonymous.get("/mcp")
            assert denied.status_code == 401
            assert "no-store" in denied.headers["cache-control"]
            expected_challenge = (
                'Bearer resource_metadata="https://resource.example.com/'
                '.well-known/oauth-protected-resource/mcp"'
            )
            assert denied.headers["www-authenticate"] == expected_challenge

            inactive = await anonymous.get(
                "/mcp",
                headers={"Authorization": "Bearer inactive-token-value"},
            )
            assert inactive.status_code == 401
            assert inactive.headers["www-authenticate"] == expected_challenge

            public_surface = "\n".join(
                [
                    str(metadata.headers),
                    metadata.text,
                    str(denied.headers),
                    denied.text,
                    str(inactive.headers),
                    inactive.text,
                ]
            )
            assert "resource-code" not in public_surface
            assert "verification-secret-value" not in public_surface
            assert "inactive-token-value" not in public_surface
            assert "127.0.0.1:46200" not in public_surface

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:46200",
            headers={"Authorization": "Bearer opaque-token-value"},
        ) as http:
            async with streamable_http_client(
                "http://127.0.0.1:46200/mcp",
                http_client=http,
            ) as (read_stream, write_stream, _):
                async with ClientSession(read_stream, write_stream) as client:
                    await client.initialize()
                    opened = _payload(
                        await client.call_tool("project_open", {"project_id": "demo"})
                    )

        assert opened["status"] == "succeeded"
        assert calls
        assert set(calls) == {"inactive-token-value", "opaque-token-value"}
        events = service.audit.for_operation(opened["operation_id"])
        auth_events = [event for event in events if event["event_type"] == "auth.context"]
        assert len(auth_events) == 1
        details = auth_events[0]["details"]
        assert details["issuer"] == "https://auth.example.com"
        assert details["resource"] == "https://resource.example.com/mcp"
        assert details["subject"] == "subject-a"
        assert details["client_id"] == "chatgpt-client"
        serialized = json.dumps(events)
        assert "opaque-token-value" not in serialized
        assert "verification-secret-value" not in serialized
