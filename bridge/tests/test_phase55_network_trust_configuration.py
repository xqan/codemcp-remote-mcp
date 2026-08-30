from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import codemcp_bridge.lifecycle as lifecycle
from codemcp_bridge.network_trust import (
    NETWORK_TRUST_MODE,
    NetworkTrustConfig,
    NetworkTrustConfigError,
)
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


def _write_remote(
    paths: lifecycle.RuntimePaths,
    *,
    auth: str | None = None,
    network_trust: str | None = None,
) -> None:
    lifecycle.ensure_runtime_dirs(paths)
    text = '[remote]\nversion = 1\ntransport = "cloudflare"\n'
    if auth is not None:
        text += f"\n[auth]\n{auth}"
    if network_trust is not None:
        text += f"\n[network_trust]\n{network_trust}"
    (paths.config_dir / "remote.toml").write_text(text, encoding="utf-8")


def _none_auth() -> str:
    return 'mode = "none"\n'


def _oauth_auth() -> str:
    return (
        "version = 1\n"
        'mode = "oauth-resource-server"\n'
        'verification_contract = "mcp-rs-verification-v1"\n'
        'authorization_server_issuer = "https://auth.example.com"\n'
        'canonical_resource_uri = "https://mcp.example.com/mcp"\n'
        'validation_resource_id = "codemcp-resource"\n'
        "validation_timeout_ms = 2000\n"
    )


def _network_trust(
    *,
    mode: str = NETWORK_TRUST_MODE,
    allowed_hosts: str = '["mcp.example.com"]',
    allowed_origins: str = '["https://chatgpt.com"]',
) -> str:
    return (
        f'mode = "{mode}"\nallowed_hosts = {allowed_hosts}\nallowed_origins = {allowed_origins}\n'
    )


def test_oauth_existing_config_passes_without_network_trust(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_remote(paths, auth=_oauth_auth())

    settings = lifecycle.load_remote_security_settings(paths)

    assert settings.auth_mode == lifecycle.RESOURCE_AUTH_MODE
    assert settings.resource_auth is not None
    assert settings.resource_auth.issuer == "https://auth.example.com"
    assert settings.network_trust is None
    assert lifecycle.load_resource_auth_settings(paths)[0] is not None


def test_none_with_valid_cloudflare_network_trust_passes(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_remote(paths, auth=_none_auth(), network_trust=_network_trust())

    settings = lifecycle.load_remote_security_settings(paths)

    assert settings.auth_mode == lifecycle.AUTH_MODE_NONE
    assert settings.resource_auth is None
    assert settings.network_trust is not None
    assert settings.network_trust.allowed_hosts == ("mcp.example.com",)
    assert settings.network_trust.allowed_origins == ("https://chatgpt.com",)


def test_none_without_network_trust_fails_closed(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_remote(paths, auth=_none_auth())

    with pytest.raises(LifecycleError, match="requires network_trust"):
        lifecycle.load_remote_security_settings(paths)


def test_none_with_empty_allowed_hosts_fails_closed(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_remote(
        paths,
        auth=_none_auth(),
        network_trust=_network_trust(allowed_hosts="[]"),
    )

    with pytest.raises(LifecycleError, match="allowed_hosts.*at least one"):
        lifecycle.load_remote_security_settings(paths)


def test_unknown_network_trust_mode_fails_closed(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_remote(
        paths,
        auth=_none_auth(),
        network_trust=_network_trust(mode="unrestricted"),
    )

    with pytest.raises(LifecycleError, match="unsupported network trust mode"):
        lifecycle.load_remote_security_settings(paths)


def test_unknown_auth_mode_fails_closed(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_remote(paths, auth='mode = "unrestricted"\n')

    with pytest.raises(LifecycleError, match="unsupported remote auth mode"):
        lifecycle.load_remote_security_settings(paths)


@pytest.mark.parametrize(
    "value",
    [
        "https://mcp.example.com",
        "mcp.example.com/mcp",
        "*.example.com",
        "mcp.example.com:443",
        "user@mcp.example.com",
        "mcp.example.com?query=1",
        "mcp.example.com#fragment",
        "",
    ],
)
def test_allowed_hosts_accepts_only_hostname(value: str) -> None:
    with pytest.raises(NetworkTrustConfigError):
        NetworkTrustConfig(mode=NETWORK_TRUST_MODE, allowed_hosts=[value])


def test_allowed_hosts_canonicalizes_uppercase_hostname() -> None:
    config = NetworkTrustConfig(
        mode=NETWORK_TRUST_MODE,
        allowed_hosts=["MCP.EXAMPLE.COM"],
    )

    assert config.allowed_hosts == ("mcp.example.com",)


def test_valid_https_origin_passes() -> None:
    config = NetworkTrustConfig(
        mode=NETWORK_TRUST_MODE,
        allowed_hosts=["mcp.example.com"],
        allowed_origins=["https://chatgpt.com"],
    )

    assert config.allowed_origins == ("https://chatgpt.com",)


def test_https_origin_default_port_is_canonicalized() -> None:
    config = NetworkTrustConfig(
        mode=NETWORK_TRUST_MODE,
        allowed_hosts=["mcp.example.com"],
        allowed_origins=["https://CHATGPT.COM:443"],
    )

    assert config.allowed_origins == ("https://chatgpt.com",)


@pytest.mark.parametrize(
    "value",
    [
        "http://chatgpt.com",
        "https://chatgpt.com/path",
        "https://chatgpt.com?q=1",
        "https://chatgpt.com?",
        "https://chatgpt.com#",
        "https://user@chatgpt.com",
        "null",
        "https://*.chatgpt.com",
        "malformed origin",
    ],
)
def test_allowed_origins_accepts_only_https_origins(value: str) -> None:
    with pytest.raises(NetworkTrustConfigError):
        NetworkTrustConfig(
            mode=NETWORK_TRUST_MODE,
            allowed_hosts=["mcp.example.com"],
            allowed_origins=[value],
        )


def test_empty_allowed_origins_is_valid() -> None:
    config = NetworkTrustConfig(
        mode=NETWORK_TRUST_MODE,
        allowed_hosts=["mcp.example.com"],
        allowed_origins=[],
    )

    assert config.allowed_origins == ()


def test_network_trust_config_rejects_unknown_fields() -> None:
    with pytest.raises(NetworkTrustConfigError, match="unsupported fields"):
        NetworkTrustConfig.from_mapping(
            {
                "mode": NETWORK_TRUST_MODE,
                "allowed_hosts": ["mcp.example.com"],
                "unexpected": True,
            }
        )


def test_configure_network_trust_preserves_existing_oauth_config(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_remote(paths, auth=_oauth_auth())

    result = lifecycle.configure_network_trust(
        paths,
        mode=NETWORK_TRUST_MODE,
        allowed_hosts=["MCP.EXAMPLE.COM"],
        allowed_origins=["https://chatgpt.com:443"],
    )
    settings = lifecycle.load_remote_security_settings(paths)

    assert result["status"] == "ok"
    assert settings.auth_mode == lifecycle.RESOURCE_AUTH_MODE
    assert settings.resource_auth is not None
    assert settings.network_trust is not None
    assert settings.network_trust.allowed_hosts == ("mcp.example.com",)
    assert settings.network_trust.allowed_origins == ("https://chatgpt.com",)


def test_configure_none_requires_an_existing_network_trust_policy(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_remote(paths)

    with pytest.raises(LifecycleError, match="requires network_trust"):
        lifecycle.configure_resource_auth(paths, mode="none")


def test_config_round_trip_writes_explicit_none_and_canonical_values(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _write_remote(paths)

    lifecycle.configure_network_trust(
        paths,
        mode=NETWORK_TRUST_MODE,
        allowed_hosts=["MCP.EXAMPLE.COM"],
        allowed_origins=["https://chatgpt.com:443"],
    )
    lifecycle.configure_resource_auth(paths, mode="none")

    parsed = tomllib.loads((paths.config_dir / "remote.toml").read_text(encoding="utf-8"))
    settings = lifecycle.load_remote_security_settings(paths)

    assert parsed["auth"]["mode"] == "none"
    assert parsed["network_trust"] == {
        "mode": NETWORK_TRUST_MODE,
        "allowed_hosts": ["mcp.example.com"],
        "allowed_origins": ["https://chatgpt.com"],
    }
    assert settings.auth_mode == lifecycle.AUTH_MODE_NONE
    assert settings.network_trust is not None
