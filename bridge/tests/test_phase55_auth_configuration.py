from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import codemcp_bridge.lifecycle as lifecycle
import codemcp_bridge.main as main_module
from codemcp_bridge.resource_auth import OAuthResourceServerAuthenticator
from codemcp_bridge.transports import LifecycleError


def _paths(tmp_path: Path) -> lifecycle.RuntimePaths:
    runtime = tmp_path / "runtime"
    (runtime / "config").mkdir(parents=True)
    (runtime / "config" / "bridge.example.toml").write_text(
        "[storage]\n"
        'data_dir = ".local"\n'
        'sqlite_file = ".local/bridge.sqlite3"\n'
        'log_dir = ".local/logs"\n',
        encoding="utf-8",
    )
    return lifecycle.runtime_paths(runtime, app_root=tmp_path / "app")


def _configure_cloudflare_auth(
    paths: lifecycle.RuntimePaths,
) -> lifecycle.ResourceAuthSettings:
    lifecycle.initialize_runtime(
        paths,
        tunnel_id="",
        transport="cloudflare",
        public_url="https://mcp.example.com/mcp",
        origin_url="http://127.0.0.1:46200/mcp",
        metrics_addr="127.0.0.1:46202",
    )
    lifecycle.configure_resource_auth(
        paths,
        mode="oauth-resource-server",
        issuer="https://auth.example.com",
        resource="https://mcp.example.com/mcp",
        validation_resource_id="codemcp-resource",
    )
    settings, source = lifecycle.load_resource_auth_settings(paths)
    assert source == "config"
    assert settings is not None
    return settings


def test_versioned_auth_config_contains_no_verification_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    settings = _configure_cloudflare_auth(paths)
    monkeypatch.setenv(lifecycle.RESOURCE_AUTH_SECRET_ENV_NAME, "runtime-only-secret")

    text = (paths.config_dir / "remote.toml").read_text(encoding="utf-8")
    assert "[auth]" in text
    assert "version = 1" in text
    assert 'mode = "oauth-resource-server"' in text
    assert 'verification_contract = "mcp-rs-verification-v1"' in text
    assert 'authorization_server_issuer = "https://auth.example.com"' in text
    assert 'canonical_resource_uri = "https://mcp.example.com/mcp"' in text
    assert 'validation_resource_id = "codemcp-resource"' in text
    assert "validation_timeout_ms = 2000" in text
    assert "runtime-only-secret" not in text
    assert "validation_secret" not in text

    assert settings.issuer == "https://auth.example.com"
    status = lifecycle.status_services(paths)
    assert status["status"] == "stopped"
    assert status["auth"]["status"] == "ready"
    assert status["auth"]["secret_source"] == "environment"
    assert "runtime-only-secret" not in repr(status)


def test_auth_config_rejects_plaintext_or_unknown_secret_fields(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    lifecycle.ensure_runtime_dirs(paths)
    (paths.config_dir / "remote.toml").write_text(
        "[remote]\n"
        "version = 1\n"
        'transport = "cloudflare"\n'
        "\n[auth]\n"
        "version = 1\n"
        'mode = "oauth-resource-server"\n'
        'verification_contract = "mcp-rs-verification-v1"\n'
        'authorization_server_issuer = "https://auth.example.com"\n'
        'canonical_resource_uri = "https://mcp.example.com/mcp"\n'
        'validation_resource_id = "codemcp-resource"\n'
        "validation_timeout_ms = 2000\n"
        'validation_secret = "must-not-be-plaintext"\n',
        encoding="utf-8",
    )

    with pytest.raises(LifecycleError, match="unsupported fields"):
        lifecycle.load_resource_auth_settings(paths)


def test_auth_config_structural_validation_is_fail_closed(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    lifecycle.initialize_runtime(
        paths,
        tunnel_id="",
        transport="cloudflare",
        public_url="https://mcp.example.com/mcp",
        origin_url="http://127.0.0.1:46200/mcp",
        metrics_addr="127.0.0.1:46202",
    )

    with pytest.raises(LifecycleError, match="canonical HTTPS origin"):
        lifecycle.configure_resource_auth(
            paths,
            mode="oauth-resource-server",
            issuer="http://auth.example.com",
            resource="https://mcp.example.com/mcp",
            validation_resource_id="codemcp-resource",
        )
    with pytest.raises(LifecycleError, match="Basic-auth username"):
        lifecycle.configure_resource_auth(
            paths,
            mode="oauth-resource-server",
            issuer="https://auth.example.com",
            resource="https://mcp.example.com/mcp",
            validation_resource_id="bad:id",
        )


def test_cloudflare_doctor_supports_network_trust_without_oauth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    lifecycle.initialize_runtime(
        paths,
        tunnel_id="",
        transport="cloudflare",
        public_url="https://mcp.example.com/mcp",
        origin_url="http://127.0.0.1:46200/mcp",
        metrics_addr="127.0.0.1:46202",
    )
    monkeypatch.setattr(
        lifecycle,
        "_secret_from_runtime",
        lambda _paths, _provider=None: "transport-secret",
    )

    report = lifecycle.doctor_report(paths)
    assert report["checks"]["auth"]["status"] == "failed"
    assert report["checks"]["auth"]["error"] == lifecycle.PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST
    assert report["checks"]["network_trust"]["status"] == "disabled"

    lifecycle.configure_network_trust(
        paths,
        mode="cloudflare-chatgpt",
        allowed_hosts=["mcp.example.com"],
    )
    lifecycle.configure_resource_auth(paths, mode="none")
    report = lifecycle.doctor_report(paths)
    assert report["checks"]["auth"]["status"] == "ready"
    assert report["checks"]["auth"]["mode"] == "none"
    assert report["checks"]["auth"]["oauth_secret_required"] is False
    assert report["checks"]["network_trust"]["status"] == "ready"
    assert report["checks"]["network_trust"]["allowed_hosts"] == ["mcp.example.com"]
    assert report["checks"]["identity_level"] == "network-only"
    assert lifecycle.RESOURCE_AUTH_SECRET_ENV_NAME not in repr(report)

    _configure_cloudflare_auth(paths)
    report = lifecycle.doctor_report(paths)
    assert report["checks"]["auth"]["status"] == "failed"
    assert lifecycle.RESOURCE_AUTH_SECRET_ENV_NAME in report["checks"]["auth"]["error"]

    monkeypatch.setenv(lifecycle.RESOURCE_AUTH_SECRET_ENV_NAME, "runtime-only-secret")
    report = lifecycle.doctor_report(paths)
    assert report["checks"]["auth"]["status"] == "ok"
    assert report["checks"]["auth"]["secret_available"] is True
    assert "runtime-only-secret" not in repr(report)


def test_load_request_authenticator_uses_persisted_public_config_and_runtime_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    _configure_cloudflare_auth(paths)
    monkeypatch.setenv(lifecycle.RESOURCE_AUTH_SECRET_ENV_NAME, "runtime-only-secret")

    authenticator = lifecycle.load_request_authenticator(paths)

    assert isinstance(authenticator, OAuthResourceServerAuthenticator)
    config = authenticator.validator.config
    assert config.issuer == "https://auth.example.com"
    assert config.resource == "https://mcp.example.com/mcp"
    assert config.validation_resource_id == "codemcp-resource"
    assert config.validation_endpoint == "https://auth.example.com/mcp/resource-server/validate"
    assert "runtime-only-secret" not in repr(config)


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI acceptance runs on Windows")
def test_resource_auth_secret_uses_dedicated_dpapi_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    _configure_cloudflare_auth(paths)
    monkeypatch.setenv(lifecycle.RESOURCE_AUTH_SECRET_ENV_NAME, "verification-secret")
    monkeypatch.setattr(lifecycle, "_dpapi_protect", lambda value: b"encrypted-auth-secret")
    monkeypatch.setattr(lifecycle, "_dpapi_unprotect", lambda value: b"verification-secret")

    assert lifecycle.store_resource_auth_secret_from_environment(paths)
    secret_path = paths.secret_dir / lifecycle.RESOURCE_AUTH_SECRET_FILE_NAME
    assert secret_path.read_bytes() == b"encrypted-auth-secret"

    monkeypatch.delenv(lifecycle.RESOURCE_AUTH_SECRET_ENV_NAME)
    assert lifecycle._resource_auth_secret_from_runtime(paths) == "verification-secret"
    status = lifecycle.resource_auth_status(paths)
    assert status["status"] == "ready"
    assert status["secret_source"] == "windows-dpapi"


def test_auth_cli_options_are_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codemcp-remote",
            "init",
            "--transport",
            "cloudflare",
            "--auth-mode",
            "oauth-resource-server",
            "--authorization-server-issuer",
            "https://auth.example.com",
            "--canonical-resource-uri",
            "https://mcp.example.com/mcp",
            "--validation-resource-id",
            "codemcp-resource",
            "--store-auth-secret",
        ],
    )

    args = main_module._parse_args()

    assert args.auth_mode == "oauth-resource-server"
    assert args.authorization_server_issuer == "https://auth.example.com"
    assert args.canonical_resource_uri == "https://mcp.example.com/mcp"
    assert args.validation_resource_id == "codemcp-resource"
    assert args.store_auth_secret is True
