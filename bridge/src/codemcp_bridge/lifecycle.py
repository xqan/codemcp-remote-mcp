"""Native Windows lifecycle, configuration, and tunnel orchestration."""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .network_trust import (
    NETWORK_TRUST_MODE,
    NetworkTrustConfig,
    NetworkTrustConfigError,
)
from .resource_auth import (
    CONTRACT_ID,
    CONTRACT_VERSION,
    OAuthResourceServerAuthenticator,
    OnlineResourceServerValidator,
    ResourceServerValidationConfig,
)
from .secret_store import (
    MacOSKeychainSecretStore,
    SecretStore,
    SecretStoreError,
    SecretValue,
    WindowsDpapiSecretStore,
)
from .settings import PROJECT_ID_PATTERN, SettingsError, load_settings
from .transports import (
    OPENAI_TUNNEL_PROVIDER,
    LifecycleError,
    OpenAITunnelSettings,
    RemoteTransportProvider,
    TransportContext,
    get_transport_provider,
)
from .transports.openai_tunnel import (
    DEFAULT_BRIDGE_URL,
    DEFAULT_HEALTH_LISTEN_ADDR,
    DEFAULT_PROFILE,
    DEFAULT_TUNNEL_HEALTH_URL,
)

APP_NAME = "codemcp-remote"
CODEMCP_HOME_ENV_NAME = "CODEMCP_HOME"
TunnelSettings = OpenAITunnelSettings
_REMOTE_TRANSPORT = OPENAI_TUNNEL_PROVIDER
_SECRET_NAME = _REMOTE_TRANSPORT.secret_env_name
_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUP_COUNT = 3
_POSIX_SIGTERM = getattr(signal, "SIGTERM", 15)
_POSIX_SIGKILL = getattr(signal, "SIGKILL", 9)


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    runtime_root: Path
    bundled_runtime_root: Path
    app_root: Path
    config_dir: Path
    data_dir: Path
    log_dir: Path
    run_dir: Path
    tunnel_dir: Path
    secret_dir: Path
    bridge_config: Path
    projects_config: Path
    tunnel_env: Path
    state_file: Path
    secret_file: Path
    layout: str = "legacy"

    @property
    def distribution_root(self) -> Path:
        """The read-only distribution root containing templates and notices."""

        return self.runtime_root

    @property
    def home(self) -> Path:
        """The operator-selected writable runtime home."""

        return self.app_root

    @property
    def checkpoint_dir(self) -> Path:
        """The conventional checkpoint subtree for the selected runtime home."""

        return self.data_dir / "checkpoints"

    @property
    def home_option(self) -> str:
        """Return the CLI option that preserves this path layout across child processes."""

        return "--home" if self.layout == "home" else "--app-root"


def _absolute_path(value: Path | str, *, label: str) -> Path:
    if not isinstance(value, (Path, str)):
        raise LifecycleError(f"{label} must be an absolute path")
    text = os.fspath(value)
    if not text or not text.strip() or "\x00" in text:
        raise LifecycleError(f"{label} must be a non-empty absolute path")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        raise LifecycleError(f"{label} must be absolute; relative paths are not allowed")
    try:
        return candidate.resolve(strict=False)
    except OSError as exc:
        raise LifecycleError(f"{label} cannot be resolved: {exc}") from exc


def resolve_runtime_home(
    *,
    explicit_home: Path | str | None = None,
    legacy_app_root: Path | str | None = None,
    default_home: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> tuple[Path, str]:
    """Resolve the writable runtime home and identify its compatibility layout.

    Explicit ``--home`` wins over ``CODEMCP_HOME``. ``--app-root`` remains a
    compatibility escape hatch for existing installations and intentionally
    keeps the historical directory layout. Packaged callers may supply
    ``default_home`` so a frozen executable uses its installation directory
    when no explicit or environment override is present. Source-mode callers
    that omit ``default_home`` keep the historical per-user fallback.
    """

    if explicit_home is not None and legacy_app_root is not None:
        raise LifecycleError("--home and --app-root cannot be used together")
    env = os.environ if environ is None else environ
    if explicit_home is not None:
        return _absolute_path(explicit_home, label="--home"), "home"
    if legacy_app_root is not None:
        return _absolute_path(legacy_app_root, label="--app-root"), "legacy-app-root"

    configured_home = env.get(CODEMCP_HOME_ENV_NAME)
    if configured_home is not None and configured_home.strip():
        return _absolute_path(configured_home, label=CODEMCP_HOME_ENV_NAME), "home"
    if default_home is not None:
        return _absolute_path(default_home, label="runtime default home"), "home"
    return app_data_root(environ=dict(env)), "legacy-default"


def app_data_root(*, environ: dict[str, str] | None = None, home: Path | None = None) -> Path:
    env = os.environ if environ is None else environ
    local_appdata = env.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata).expanduser().resolve(strict=False) / APP_NAME
    base = Path.home() if home is None else home
    return base.resolve(strict=False) / f".{APP_NAME}"


def resolve_runtime_paths(
    runtime_root: Path,
    *,
    home: Path | str | None = None,
    app_root: Path | str | None = None,
    default_home: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimePaths:
    """Build all writable paths from one validated runtime-home decision."""

    runtime = _absolute_path(runtime_root, label="runtime root")
    root, layout = resolve_runtime_home(
        explicit_home=home,
        legacy_app_root=app_root,
        default_home=default_home,
        environ=environ,
    )
    config_dir = root / "config"
    data_dir = root / "data"
    if layout == "home":
        log_dir = data_dir / "logs"
        run_dir = data_dir / "runtime"
        tunnel_dir = run_dir / "tunnel"
    else:
        # Preserve the historical default/--app-root layout for existing
        # installations.  A new --home or CODEMCP_HOME opts into the explicit
        # modern layout above without an implicit migration.
        log_dir = root / "logs"
        run_dir = root / "run"
        tunnel_dir = root / "tunnel"
    secret_dir = root / "secrets"
    return RuntimePaths(
        runtime_root=runtime,
        bundled_runtime_root=runtime / ".codemcp-runtime",
        app_root=root,
        config_dir=config_dir,
        data_dir=data_dir,
        log_dir=log_dir,
        run_dir=run_dir,
        tunnel_dir=tunnel_dir,
        secret_dir=secret_dir,
        bridge_config=config_dir / "bridge.toml",
        projects_config=config_dir / "projects.toml",
        tunnel_env=config_dir / "tunnel.env",
        state_file=run_dir / "state.json",
        secret_file=secret_dir / "control-plane-api-key.dpapi",
        layout="home" if layout == "home" else "legacy",
    )


def runtime_paths(
    runtime_root: Path,
    *,
    app_root: Path | str | None = None,
    home: Path | str | None = None,
    default_home: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimePaths:
    """Compatibility wrapper for the central runtime-path resolver."""

    return resolve_runtime_paths(
        runtime_root,
        home=home,
        app_root=app_root,
        default_home=default_home,
        environ=environ,
    )


def _transport_context(paths: RuntimePaths) -> TransportContext:
    return TransportContext(
        runtime_root=paths.distribution_root,
        bundled_runtime_root=paths.bundled_runtime_root,
        app_root=paths.app_root,
        config_dir=paths.config_dir,
        log_dir=paths.log_dir,
        tunnel_dir=paths.tunnel_dir,
        secret_file=paths.secret_file,
        tunnel_env=paths.tunnel_env,
    )


REMOTE_CONFIG_VERSION = 1
RESOURCE_AUTH_CONFIG_VERSION = 1
AUTH_MODE_NONE = "none"
RESOURCE_AUTH_MODE = "oauth-resource-server"
RESOURCE_AUTH_SECRET_ENV_NAME = "CODEMCP_RS_VERIFICATION_SECRET"
RESOURCE_AUTH_SECRET_FILE_NAME = "mcp-rs-verification-secret.dpapi"
PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST = "PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST"


@dataclass(frozen=True, slots=True)
class ResourceAuthSettings:
    issuer: str
    resource: str
    validation_resource_id: str
    mode: str = RESOURCE_AUTH_MODE
    contract_id: str = CONTRACT_ID
    timeout_seconds: float = 2.0

    def validation_config(self, secret: str) -> ResourceServerValidationConfig:
        return ResourceServerValidationConfig(
            issuer=self.issuer,
            resource=self.resource,
            validation_resource_id=self.validation_resource_id,
            validation_secret=secret,
            timeout_seconds=self.timeout_seconds,
            contract_version=CONTRACT_VERSION,
        )


@dataclass(frozen=True, slots=True)
class RemoteSecuritySettings:
    """The independently configured authentication and network-trust profiles."""

    auth_mode: str | None
    resource_auth: ResourceAuthSettings | None
    network_trust: NetworkTrustConfig | None
    auth_source: str
    network_trust_source: str


def _remote_config_path(paths: RuntimePaths) -> Path:
    return paths.config_dir / "remote.toml"


def _read_remote_config(paths: RuntimePaths) -> dict[str, Any] | None:
    path = _remote_config_path(paths)
    if not path.is_file():
        return None

    import tomllib

    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LifecycleError(f"remote transport configuration is invalid: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LifecycleError("remote transport configuration must be a TOML document")
    return parsed


def _write_remote_config(
    paths: RuntimePaths,
    provider: RemoteTransportProvider,
    auth: ResourceAuthSettings | None = None,
    *,
    auth_mode: str | None = None,
    network_trust: NetworkTrustConfig | None = None,
) -> Path:
    path = _remote_config_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".toml.tmp")
    text = (
        "[remote]\n"
        f"version = {REMOTE_CONFIG_VERSION}\n"
        f"transport = {_toml_quote(provider.provider_id)}\n"
    )
    selected_auth_mode = auth.mode if auth is not None else auth_mode
    if selected_auth_mode is not None:
        if selected_auth_mode not in {AUTH_MODE_NONE, RESOURCE_AUTH_MODE}:
            raise LifecycleError(f"unsupported remote auth mode: {selected_auth_mode!r}")
        if selected_auth_mode == RESOURCE_AUTH_MODE and auth is None:
            raise LifecycleError("OAuth Resource Server configuration is incomplete")
        text += (
            "\n[auth]\n"
            f"version = {RESOURCE_AUTH_CONFIG_VERSION}\n"
            f"mode = {_toml_quote(selected_auth_mode)}\n"
        )
        if auth is not None:
            text += (
                f"verification_contract = {_toml_quote(auth.contract_id)}\n"
                f"authorization_server_issuer = {_toml_quote(auth.issuer)}\n"
                f"canonical_resource_uri = {_toml_quote(auth.resource)}\n"
                f"validation_resource_id = {_toml_quote(auth.validation_resource_id)}\n"
                f"validation_timeout_ms = {int(auth.timeout_seconds * 1000)}\n"
            )
    if network_trust is not None:
        text += (
            "\n[network_trust]\n"
            f"mode = {_toml_quote(network_trust.mode)}\n"
            f"allowed_hosts = {_toml_string_array(network_trust.allowed_hosts)}\n"
            f"allowed_origins = {_toml_string_array(network_trust.allowed_origins)}\n"
        )
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)
    return path


def load_transport_provider(paths: RuntimePaths) -> tuple[RemoteTransportProvider, str]:
    parsed = _read_remote_config(paths)
    if parsed is None:
        return _REMOTE_TRANSPORT, "legacy-default"
    remote = parsed.get("remote")
    if not isinstance(remote, dict):
        raise LifecycleError("remote transport configuration must contain [remote]")
    if remote.get("version") != REMOTE_CONFIG_VERSION:
        raise LifecycleError(
            f"unsupported remote transport configuration version: {remote.get('version')!r}"
        )
    transport = remote.get("transport")
    if not isinstance(transport, str) or not transport:
        raise LifecycleError("remote.transport must be a non-empty string")
    return get_transport_provider(transport), "config"


def _build_resource_auth_settings(
    *,
    issuer: str,
    resource: str,
    validation_resource_id: str,
) -> ResourceAuthSettings:
    try:
        settings = ResourceAuthSettings(
            issuer=issuer,
            resource=resource,
            validation_resource_id=validation_resource_id,
        )
        settings.validation_config("structural-validation-only")
    except ValueError as exc:
        raise LifecycleError(f"OAuth Resource Server configuration is invalid: {exc}") from exc
    return settings


def _parse_auth_configuration(
    parsed: dict[str, Any],
) -> tuple[str | None, ResourceAuthSettings | None, str]:
    auth = parsed.get("auth")
    if auth is None:
        return None, None, "disabled"
    if not isinstance(auth, dict):
        raise LifecycleError("remote auth configuration must be a TOML table")

    mode = auth.get("mode")
    if mode == AUTH_MODE_NONE:
        unexpected = sorted(set(auth) - {"version", "mode"})
        if unexpected:
            raise LifecycleError(
                "remote auth configuration contains unsupported fields: " + ", ".join(unexpected)
            )
        if "version" in auth and auth["version"] != RESOURCE_AUTH_CONFIG_VERSION:
            raise LifecycleError(
                f"unsupported remote auth configuration version: {auth.get('version')!r}"
            )
        return AUTH_MODE_NONE, None, "config"

    if mode != RESOURCE_AUTH_MODE:
        raise LifecycleError(f"unsupported remote auth mode: {mode!r}")

    allowed = {
        "version",
        "mode",
        "verification_contract",
        "authorization_server_issuer",
        "canonical_resource_uri",
        "validation_resource_id",
        "validation_timeout_ms",
    }
    unexpected = sorted(set(auth) - allowed)
    if unexpected:
        raise LifecycleError(
            "remote auth configuration contains unsupported fields: " + ", ".join(unexpected)
        )
    if auth.get("version") != RESOURCE_AUTH_CONFIG_VERSION:
        raise LifecycleError(
            f"unsupported remote auth configuration version: {auth.get('version')!r}"
        )
    if auth.get("verification_contract") != CONTRACT_ID:
        raise LifecycleError("remote auth verification_contract must be mcp-rs-verification-v1")
    if auth.get("validation_timeout_ms") != 2000:
        raise LifecycleError("remote auth validation_timeout_ms must be exactly 2000")

    issuer = auth.get("authorization_server_issuer")
    resource = auth.get("canonical_resource_uri")
    validation_resource_id = auth.get("validation_resource_id")
    if not all(
        isinstance(value, str) and value for value in (issuer, resource, validation_resource_id)
    ):
        raise LifecycleError(
            "remote auth issuer, canonical resource URI, and validation resource id are required"
        )
    return (
        RESOURCE_AUTH_MODE,
        _build_resource_auth_settings(
            issuer=issuer,
            resource=resource,
            validation_resource_id=validation_resource_id,
        ),
        "config",
    )


def _parse_network_trust_configuration(
    parsed: dict[str, Any],
) -> tuple[NetworkTrustConfig | None, str]:
    network_trust = parsed.get("network_trust")
    if network_trust is None:
        return None, "disabled"
    if not isinstance(network_trust, dict):
        raise LifecycleError("remote network_trust configuration must be a TOML table")
    try:
        settings = NetworkTrustConfig.from_mapping(network_trust)
    except NetworkTrustConfigError as exc:
        raise LifecycleError(f"network trust configuration is invalid: {exc}") from exc
    return settings, "config"


def load_remote_security_settings(paths: RuntimePaths) -> RemoteSecuritySettings:
    """Load both independent security profiles and validate their legal combination."""

    parsed = _read_remote_config(paths)
    if parsed is None:
        return RemoteSecuritySettings(
            auth_mode=None,
            resource_auth=None,
            network_trust=None,
            auth_source="disabled",
            network_trust_source="disabled",
        )

    auth_mode, resource_auth, auth_source = _parse_auth_configuration(parsed)
    network_trust, network_trust_source = _parse_network_trust_configuration(parsed)
    if auth_mode == AUTH_MODE_NONE and network_trust is None:
        raise LifecycleError(
            f"auth.mode = none requires network_trust.mode = {NETWORK_TRUST_MODE} "
            "with non-empty allowed_hosts"
        )
    return RemoteSecuritySettings(
        auth_mode=auth_mode,
        resource_auth=resource_auth,
        network_trust=network_trust,
        auth_source=auth_source,
        network_trust_source=network_trust_source,
    )


def load_resource_auth_settings(paths: RuntimePaths) -> tuple[ResourceAuthSettings | None, str]:
    security = load_remote_security_settings(paths)
    return security.resource_auth, security.auth_source


def load_network_trust_settings(paths: RuntimePaths) -> tuple[NetworkTrustConfig | None, str]:
    security = load_remote_security_settings(paths)
    return security.network_trust, security.network_trust_source


def load_auth_mode(paths: RuntimePaths) -> tuple[str | None, str]:
    security = load_remote_security_settings(paths)
    return security.auth_mode, security.auth_source


def configure_resource_auth(
    paths: RuntimePaths,
    *,
    mode: str,
    issuer: str | None = None,
    resource: str | None = None,
    validation_resource_id: str | None = None,
) -> dict[str, Any]:
    ensure_runtime_dirs(paths)
    provider, _ = load_transport_provider(paths)
    parsed = _read_remote_config(paths) or {}
    existing_network_trust, _ = _parse_network_trust_configuration(parsed)
    if mode == AUTH_MODE_NONE:
        if any(value is not None for value in (issuer, resource, validation_resource_id)):
            raise LifecycleError(
                "auth fields must not be supplied when --auth-mode none is selected"
            )
        if existing_network_trust is None:
            raise LifecycleError(
                f"auth.mode = none requires network_trust.mode = {NETWORK_TRUST_MODE} "
                "with non-empty allowed_hosts"
            )
        auth = None
        auth_mode = AUTH_MODE_NONE
    elif mode == RESOURCE_AUTH_MODE:
        if not all(
            isinstance(value, str) and value for value in (issuer, resource, validation_resource_id)
        ):
            raise LifecycleError(
                "OAuth Resource Server auth requires issuer, canonical resource URI, "
                "and validation resource id"
            )
        assert issuer is not None and resource is not None and validation_resource_id is not None
        auth = _build_resource_auth_settings(
            issuer=issuer,
            resource=resource,
            validation_resource_id=validation_resource_id,
        )
        auth_mode = RESOURCE_AUTH_MODE
    else:
        raise LifecycleError(f"unsupported remote auth mode: {mode!r}")
    _write_remote_config(
        paths,
        provider,
        auth,
        auth_mode=auth_mode,
        network_trust=existing_network_trust,
    )
    return {
        "status": "ok",
        "auth_mode": auth.mode if auth is not None else AUTH_MODE_NONE,
        "auth_config": str(_remote_config_path(paths)),
    }


def configure_network_trust(
    paths: RuntimePaths,
    *,
    mode: str,
    allowed_hosts: list[str] | tuple[str, ...],
    allowed_origins: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Persist a validated network-trust policy while preserving auth settings."""

    ensure_runtime_dirs(paths)
    provider, _ = load_transport_provider(paths)
    parsed = _read_remote_config(paths) or {}
    auth_mode, auth, _ = _parse_auth_configuration(parsed)
    try:
        network_trust = NetworkTrustConfig(
            mode=mode,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins,
        )
    except NetworkTrustConfigError as exc:
        raise LifecycleError(f"network trust configuration is invalid: {exc}") from exc
    _write_remote_config(
        paths,
        provider,
        auth,
        auth_mode=auth_mode,
        network_trust=network_trust,
    )
    return {
        "status": "ok",
        "network_trust_mode": network_trust.mode,
        "network_trust_config": str(_remote_config_path(paths)),
    }


def ensure_runtime_dirs(paths: RuntimePaths) -> None:
    for path in (
        paths.config_dir,
        paths.data_dir,
        paths.log_dir,
        paths.run_dir,
        paths.tunnel_dir,
        paths.secret_dir,
    ):
        path.mkdir(parents=True, exist_ok=True)


def _toml_quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_string_array(values: tuple[str, ...]) -> str:
    return "[" + ", ".join(_toml_quote(value) for value in values) + "]"


def _rewrite_bridge_storage(template: str, paths: RuntimePaths) -> str:
    replacements = {
        r'(?m)^data_dir\s*=\s*["\'][^"\']*["\']\s*$': (
            f"data_dir = {_toml_quote(str(paths.data_dir))}"
        ),
        r'(?m)^sqlite_file\s*=\s*["\'][^"\']*["\']\s*$': (
            f"sqlite_file = {_toml_quote(str(paths.data_dir / 'bridge.sqlite3'))}"
        ),
        r'(?m)^log_dir\s*=\s*["\'][^"\']*["\']\s*$': f"log_dir = {_toml_quote(str(paths.log_dir))}",
    }
    updated = template
    for pattern, replacement in replacements.items():
        updated = re.sub(pattern, lambda _match, value=replacement: value, updated)
    return updated


def _rewrite_bridge_endpoint(template: str, endpoint_url: str) -> str:
    """Keep the generated Bridge listener aligned with the selected transport endpoint."""

    try:
        parsed = urllib.parse.urlsplit(endpoint_url)
        port = parsed.port
    except ValueError as exc:
        raise LifecycleError(f"Bridge MCP endpoint is invalid: {exc}") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path.rstrip("/") != "/mcp"
        or parsed.query
        or parsed.fragment
    ):
        raise LifecycleError("Bridge MCP endpoint must be an HTTP(S) /mcp endpoint on 127.0.0.1")
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    server_match = re.search(
        r"(?ms)^\[server\]\s*\n(?P<body>.*?)(?=^\[|\Z)",
        template,
    )
    if server_match is None:
        if endpoint_url == DEFAULT_BRIDGE_URL:
            return template
        raise LifecycleError("bridge template is missing the [server] table")

    body = server_match.group("body")
    replacements = {
        "host": f"host = {_toml_quote(parsed.hostname)}",
        "port": f"port = {port}",
        "path": f"path = {_toml_quote(parsed.path.rstrip('/') or '/')}",
    }
    for key, replacement in replacements.items():
        body, count = re.subn(
            rf"(?m)^{re.escape(key)}\s*=.*$",
            replacement,
            body,
            count=1,
        )
        if count != 1:
            raise LifecycleError(f"bridge template [server] is missing {key}")

    return template[: server_match.start("body")] + body + template[server_match.end("body") :]


def initialize_runtime(
    paths: RuntimePaths,
    *,
    tunnel_id: str,
    profile_name: str = DEFAULT_PROFILE,
    bridge_url: str = DEFAULT_BRIDGE_URL,
    tunnel_health_url: str = DEFAULT_TUNNEL_HEALTH_URL,
    health_listen_addr: str = DEFAULT_HEALTH_LISTEN_ADDR,
    transport: str | None = None,
    public_url: str = "",
    origin_url: str = DEFAULT_BRIDGE_URL,
    metrics_addr: str = "127.0.0.1:46202",
    force: bool = False,
) -> dict[str, Any]:
    ensure_runtime_dirs(paths)
    existing_provider, existing_source = load_transport_provider(paths)
    # Parse the independent tables here so an explicitly incomplete
    # ``auth.mode = none`` installation can be repaired by adding its trust
    # policy.  The combined loader remains the fail-closed gate for use.
    parsed = _read_remote_config(paths) or {}
    existing_auth_mode, existing_auth, _ = _parse_auth_configuration(parsed)
    existing_network_trust, _ = _parse_network_trust_configuration(parsed)
    provider = existing_provider if transport is None else get_transport_provider(transport)
    if existing_provider.provider_id != provider.provider_id and not force:
        if existing_source == "config" or paths.tunnel_env.exists():
            raise LifecycleError(
                "remote transport change requires --force so provider configuration is "
                "replaced safely"
            )

    template = paths.runtime_root / "config" / "bridge.example.toml"
    if not template.is_file():
        raise LifecycleError(f"bridge template not found: {template}")

    created: list[str] = []
    if force or not paths.bridge_config.exists():
        endpoint_url = origin_url if provider.provider_id == "cloudflare" else bridge_url
        bridge_text = _rewrite_bridge_storage(template.read_text(encoding="utf-8"), paths)
        bridge_text = _rewrite_bridge_endpoint(bridge_text, endpoint_url)
        paths.bridge_config.write_text(bridge_text, encoding="utf-8")
        created.append(str(paths.bridge_config))

    if force or not paths.projects_config.exists():
        paths.projects_config.write_text("# Managed by codemcp-remote\n", encoding="utf-8")
        created.append(str(paths.projects_config))

    created.extend(
        provider.initialize_config(
            _transport_context(paths),
            tunnel_id=tunnel_id,
            profile_name=profile_name,
            bridge_url=bridge_url,
            tunnel_health_url=tunnel_health_url,
            health_listen_addr=health_listen_addr,
            public_url=public_url,
            origin_url=origin_url,
            metrics_addr=metrics_addr,
            force=force,
        )
    )
    remote_config = _remote_config_path(paths)
    if (
        force
        or not remote_config.is_file()
        or existing_provider.provider_id != provider.provider_id
    ):
        created.append(
            str(
                _write_remote_config(
                    paths,
                    provider,
                    existing_auth,
                    auth_mode=existing_auth_mode,
                    network_trust=existing_network_trust,
                )
            )
        )

    return {
        "status": "ok",
        "app_root": str(paths.app_root),
        "home": str(paths.home),
        "config_dir": str(paths.config_dir),
        "data_dir": str(paths.data_dir),
        "secret_dir": str(paths.secret_dir),
        "transport": provider.provider_id,
        "created": created,
    }


def add_project(paths: RuntimePaths, *, project_id: str, root: Path) -> dict[str, Any]:
    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise LifecycleError("project id must contain only letters, digits, '.', '_' or '-'")
    project_root = root.expanduser().resolve(strict=False)
    if not project_root.is_dir():
        raise LifecycleError(f"project root is not a directory: {project_root}")
    if not paths.projects_config.is_file():
        raise LifecycleError("projects.toml is missing; run 'codemcp-remote init' first")

    import tomllib

    raw = paths.projects_config.read_text(encoding="utf-8")
    try:
        parsed = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise LifecycleError(f"projects.toml is invalid: {exc}") from exc
    projects = parsed.get("projects", {})
    if isinstance(projects, dict) and project_id in projects:
        raise LifecycleError(f"project already exists: {project_id}")

    entry = (
        f"\n[projects.{project_id}]\n"
        f"root = {_toml_quote(str(project_root))}\n"
        'codemcp_config = "codemcp.toml"\n'
    )
    candidate = raw + entry
    temporary = paths.projects_config.with_suffix(".toml.tmp")
    temporary.write_text(candidate, encoding="utf-8", newline="\n")
    try:
        load_settings(paths.bridge_config, temporary)
    except SettingsError as exc:
        temporary.unlink(missing_ok=True)
        raise LifecycleError(f"project configuration is invalid: {exc}") from exc
    os.replace(temporary, paths.projects_config)
    return {
        "status": "ok",
        "project_id": project_id,
        "root": str(project_root),
        "reload": "automatic",
        "restart_required": False,
    }


def remove_project(
    paths: RuntimePaths,
    *,
    project_id: str,
    expected_root: Path,
) -> dict[str, Any]:
    """Unregister one project only when its configured root matches exactly.

    The expected-root check is intentionally mandatory for callers such as the
    clean-machine acceptance harness.  It prevents a disposable project ID from
    being used to remove an unrelated user-owned registration.
    """

    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise LifecycleError("project id must contain only letters, digits, '.', '_' or '-'")
    if not paths.projects_config.is_file():
        raise LifecycleError("projects.toml is missing; run 'codemcp-remote init' first")

    import tomllib

    raw = paths.projects_config.read_text(encoding="utf-8")
    try:
        parsed = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise LifecycleError(f"projects.toml is invalid: {exc}") from exc
    projects = parsed.get("projects", {})
    if not isinstance(projects, dict):
        raise LifecycleError("projects.toml [projects] must be a table")
    if project_id not in projects:
        return {
            "status": "not-found",
            "project_id": project_id,
            "reload": "automatic",
            "restart_required": False,
        }

    raw_project = projects[project_id]
    if not isinstance(raw_project, dict):
        raise LifecycleError(f"project registration is invalid: {project_id}")
    registered_root_value = raw_project.get("root")
    if not isinstance(registered_root_value, str) or not registered_root_value.strip():
        raise LifecycleError(f"project registration has no valid root: {project_id}")
    registered_root = Path(registered_root_value).expanduser()
    if not registered_root.is_absolute():
        registered_root = paths.projects_config.parent / registered_root
    registered_root = registered_root.resolve(strict=False)
    expected = expected_root.expanduser().resolve(strict=False)
    if os.path.normcase(str(registered_root)) != os.path.normcase(str(expected)):
        raise LifecycleError(
            f"project root ownership mismatch for {project_id}: "
            f"registered={registered_root} expected={expected}"
        )

    base_table = f"projects.{project_id}"
    lines = raw.splitlines(keepends=True)
    section_start: int | None = None
    section_end: int | None = None
    for index, line in enumerate(lines):
        match = re.match(r"^\s*\[([^\]]+)\]", line)
        if match is None:
            continue
        table_name = match.group(1).strip()
        if section_start is None:
            if table_name == base_table:
                section_start = index
            continue
        if table_name != base_table and not table_name.startswith(f"{base_table}."):
            section_end = index
            break

    if section_start is None:
        raise LifecycleError(
            f"project registration section not found for {project_id}; refusing removal"
        )
    if section_end is None:
        section_end = len(lines)

    candidate = "".join(lines[:section_start] + lines[section_end:])
    temporary = paths.projects_config.with_suffix(".toml.tmp")
    temporary.write_text(candidate, encoding="utf-8", newline="\n")
    try:
        load_settings(paths.bridge_config, temporary)
    except SettingsError as exc:
        temporary.unlink(missing_ok=True)
        raise LifecycleError(f"project configuration is invalid after removal: {exc}") from exc
    os.replace(temporary, paths.projects_config)
    return {
        "status": "ok",
        "project_id": project_id,
        "root": str(registered_root),
        "removed": True,
        "reload": "automatic",
        "restart_required": False,
    }


def load_tunnel_settings(paths: RuntimePaths, *, env_file: Path | None = None) -> Any:
    provider, _ = load_transport_provider(paths)
    return provider.load_settings(
        _transport_context(paths),
        env_file=env_file,
    )


def find_tunnel_client(paths: RuntimePaths) -> Path:
    provider, _ = load_transport_provider(paths)
    return provider.find_client(_transport_context(paths))


def validate_tunnel_profile(settings: TunnelSettings) -> Path:
    profile = _REMOTE_TRANSPORT.validate_config(settings)
    if not isinstance(profile, Path):
        raise LifecycleError("OpenAI tunnel provider did not return a profile path")
    return profile


def redact_log_text(value: str) -> str:
    return _REMOTE_TRANSPORT.redact(value)


def _rotate_log(path: Path) -> None:
    if not path.is_file() or path.stat().st_size < _LOG_MAX_BYTES:
        return
    for index in range(_LOG_BACKUP_COUNT - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        destination = path.with_name(f"{path.name}.{index + 1}")
        if source.exists():
            os.replace(source, destination)
    os.replace(path, path.with_name(f"{path.name}.1"))


def _self_command() -> list[str]:
    if bool(getattr(sys, "frozen", False)):
        return [sys.executable]
    return [sys.executable, "-m", "codemcp_bridge.main"]


def _http_check(
    url: str,
    *,
    timeout: float = 2.0,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    try:
        request = urllib.request.Request(url, headers=dict(headers or {}))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"status": "ok", "status_code": int(response.status), "url": url}
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        return {"status": "unreachable", "status_code": None, "url": url, "error": str(exc)}


def _http_json_check(
    url: str,
    *,
    timeout: float = 2.0,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Read one bounded JSON object from a local lifecycle endpoint."""

    try:
        request = urllib.request.Request(url, headers=dict(headers or {}))
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(64 * 1024 + 1)
            if len(payload) > 64 * 1024:
                raise ValueError("response exceeds 64 KiB")
            decoded = json.loads(payload.decode("utf-8"))
            if not isinstance(decoded, dict):
                raise ValueError("response is not a JSON object")
            return {
                "status": "ok",
                "status_code": int(response.status),
                "url": url,
                "data": decoded,
            }
    except (
        UnicodeDecodeError,
        ValueError,
        json.JSONDecodeError,
        urllib.error.URLError,
        OSError,
        TimeoutError,
    ) as exc:
        return {"status": "unavailable", "status_code": None, "url": url, "error": str(exc)}


def _wait_endpoint(
    url: str,
    process: subprocess.Popen[Any],
    timeout_seconds: float,
    *,
    headers: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        code = process.poll()
        if code is not None:
            return {
                "status": "failed",
                "url": url,
                "error": "process exited before endpoint became healthy",
                "exit_code": code,
                "last_check": last,
            }
        if headers is None:
            last = _http_check(url)
        else:
            last = _http_check(url, headers=headers)
        if last["status"] == "ok":
            return last
        time.sleep(0.25)
    return {"status": "timeout", "url": url, "last_check": last}


def _bridge_health_url(bridge_url: str) -> str:
    from urllib.parse import urlsplit

    parsed = urlsplit(bridge_url)
    return f"{parsed.scheme}://{parsed.netloc}/healthz"


def _bridge_probe_headers(
    allowed_hosts: list[str] | tuple[str, ...] | None,
) -> dict[str, str] | None:
    """Return the configured Host authority for an internal Bridge probe."""

    if not allowed_hosts:
        return None
    host = allowed_hosts[0]
    if not isinstance(host, str) or not host:
        return None
    return {"Host": host}


def _provider_secret_path(
    paths: RuntimePaths,
    provider: RemoteTransportProvider | None = None,
) -> Path:
    effective = load_transport_provider(paths)[0] if provider is None else provider
    filename = effective.secret_file_name
    if not filename or Path(filename).name != filename or filename in {".", ".."}:
        raise LifecycleError("transport provider secret file name is invalid")
    return paths.secret_dir / filename


def _platform_secret_store(
    paths: RuntimePaths,
    *,
    logical_secret_id: str,
    windows_secret_path: Path,
) -> SecretStore | None:
    if sys.platform == "darwin":
        return MacOSKeychainSecretStore(paths.home, logical_secret_id)
    if os.name == "nt":
        return WindowsDpapiSecretStore(
            windows_secret_path,
            protect=_dpapi_protect,
            unprotect=_dpapi_unprotect,
        )
    return None


def _read_secret(
    paths: RuntimePaths,
    *,
    env_name: str,
    logical_secret_id: str,
    windows_secret_path: Path,
) -> SecretValue:
    value = os.environ.get(env_name)
    if value:
        return SecretValue(value, "environment")
    store = _platform_secret_store(
        paths,
        logical_secret_id=logical_secret_id,
        windows_secret_path=windows_secret_path,
    )
    if store is None:
        return SecretValue(None, "none")
    try:
        return store.read()
    except SecretStoreError as exc:
        raise LifecycleError(str(exc)) from exc


def _store_secret_from_environment(
    paths: RuntimePaths,
    *,
    env_name: str,
    logical_secret_id: str,
    windows_secret_path: Path,
) -> str:
    value = os.environ.get(env_name)
    if not value:
        raise LifecycleError(f"{env_name} is not set in the current process")
    store = _platform_secret_store(
        paths,
        logical_secret_id=logical_secret_id,
        windows_secret_path=windows_secret_path,
    )
    if store is None:
        raise LifecycleError("secure native secret storage is unavailable on this platform")
    ensure_runtime_dirs(paths)
    try:
        return store.write(value)
    except SecretStoreError as exc:
        raise LifecycleError(str(exc)) from exc


def _transport_secret_value(
    paths: RuntimePaths,
    provider: RemoteTransportProvider | None = None,
) -> SecretValue:
    effective = load_transport_provider(paths)[0] if provider is None else provider
    return _read_secret(
        paths,
        env_name=effective.secret_env_name,
        logical_secret_id=f"transport:{effective.provider_id}:{effective.secret_env_name}",
        windows_secret_path=_provider_secret_path(paths, effective),
    )


def _secret_from_runtime(
    paths: RuntimePaths,
    provider: RemoteTransportProvider | None = None,
) -> str | None:
    return _transport_secret_value(paths, provider).value


def store_transport_secret_from_environment(
    paths: RuntimePaths,
    *,
    provider: RemoteTransportProvider | None = None,
) -> str:
    effective = load_transport_provider(paths)[0] if provider is None else provider
    return _store_secret_from_environment(
        paths,
        env_name=effective.secret_env_name,
        logical_secret_id=f"transport:{effective.provider_id}:{effective.secret_env_name}",
        windows_secret_path=_provider_secret_path(paths, effective),
    )


def store_api_key_from_environment(paths: RuntimePaths) -> str:
    return store_transport_secret_from_environment(
        paths,
        provider=OPENAI_TUNNEL_PROVIDER,
    )


def _resource_auth_secret_path(paths: RuntimePaths) -> Path:
    return paths.secret_dir / RESOURCE_AUTH_SECRET_FILE_NAME


def _resource_auth_secret_value(paths: RuntimePaths) -> SecretValue:
    return _read_secret(
        paths,
        env_name=RESOURCE_AUTH_SECRET_ENV_NAME,
        logical_secret_id="resource-auth:verification-secret",
        windows_secret_path=_resource_auth_secret_path(paths),
    )


def _resource_auth_secret_from_runtime(paths: RuntimePaths) -> str | None:
    return _resource_auth_secret_value(paths).value


def store_resource_auth_secret_from_environment(paths: RuntimePaths) -> str:
    auth, _ = load_resource_auth_settings(paths)
    if auth is None:
        raise LifecycleError("OAuth Resource Server auth is not configured")
    return _store_secret_from_environment(
        paths,
        env_name=RESOURCE_AUTH_SECRET_ENV_NAME,
        logical_secret_id="resource-auth:verification-secret",
        windows_secret_path=_resource_auth_secret_path(paths),
    )


def load_request_authenticator(paths: RuntimePaths) -> OAuthResourceServerAuthenticator | None:
    auth, _ = load_resource_auth_settings(paths)
    if auth is None:
        return None
    secret = _resource_auth_secret_from_runtime(paths)
    if not secret:
        raise LifecycleError(
            f"{RESOURCE_AUTH_SECRET_ENV_NAME} is unavailable; set it in the environment or "
            "store it securely"
        )
    try:
        validator = OnlineResourceServerValidator(auth.validation_config(secret))
    except ValueError as exc:
        raise LifecycleError(f"OAuth Resource Server configuration is invalid: {exc}") from exc
    return OAuthResourceServerAuthenticator(validator)


def resource_auth_status(paths: RuntimePaths) -> dict[str, Any]:
    try:
        auth, source = load_resource_auth_settings(paths)
    except LifecycleError as exc:
        return {"status": "invalid", "mode": "unknown", "error": str(exc)}
    if auth is None:
        return {"status": "disabled", "mode": "none", "source": source}

    secret_source = "none"
    try:
        secret_value = _resource_auth_secret_value(paths)
        secret_available = bool(secret_value.value)
        secret_source = secret_value.source
    except (LifecycleError, OSError, UnicodeDecodeError) as exc:
        return {
            "status": "invalid",
            "mode": auth.mode,
            "source": source,
            "verification_contract": auth.contract_id,
            "issuer": auth.issuer,
            "resource": auth.resource,
            "validation_endpoint": auth.validation_config("status-only").validation_endpoint,
            "secret_source": secret_source,
            "error": str(exc),
        }
    return {
        "status": "ready" if secret_available else "missing",
        "mode": auth.mode,
        "source": source,
        "verification_contract": auth.contract_id,
        "issuer": auth.issuer,
        "resource": auth.resource,
        "validation_endpoint": auth.validation_config("status-only").validation_endpoint,
        "validation_resource_id": auth.validation_resource_id,
        "validation_timeout_ms": int(auth.timeout_seconds * 1000),
        "secret_available": secret_available,
        "secret_source": secret_source,
        "oauth_secret_required": True,
    }


def _network_trust_status(
    settings: NetworkTrustConfig | None,
    source: str,
) -> dict[str, Any]:
    if settings is None:
        return {
            "status": "disabled",
            "mode": None,
            "source": source,
            "boundary": "network trust boundary",
        }
    return {
        "status": "ready",
        "mode": settings.mode,
        "source": source,
        "allowed_hosts": list(settings.allowed_hosts),
        "allowed_origins": list(settings.allowed_origins),
        "boundary": "network trust boundary",
    }


def security_profile_status(paths: RuntimePaths) -> dict[str, Any]:
    """Return a redacted, profile-aware status for doctor and lifecycle output."""

    try:
        security = load_remote_security_settings(paths)
    except LifecycleError as exc:
        auth_mode: str | None = None
        network_settings: NetworkTrustConfig | None = None
        network_source = "invalid"
        try:
            parsed = _read_remote_config(paths) or {}
            try:
                auth_mode, _, _ = _parse_auth_configuration(parsed)
            except LifecycleError:
                auth_mode = None
            try:
                network_settings, network_source = _parse_network_trust_configuration(parsed)
            except LifecycleError:
                network_source = "invalid"
        except LifecycleError:
            pass
        return {
            "status": "invalid",
            "profile": "invalid",
            "auth_mode": auth_mode,
            "auth": {
                "status": "failed",
                "mode": auth_mode or "unknown",
                "source": "invalid",
                "oauth_secret_required": auth_mode == RESOURCE_AUTH_MODE,
                "error": str(exc),
            },
            "network_trust": (
                _network_trust_status(network_settings, network_source)
                if network_settings is not None
                else {
                    "status": "failed",
                    "mode": None,
                    "source": network_source,
                    "boundary": "network trust boundary",
                    "error": str(exc),
                }
            ),
            "identity_level": "unknown",
            "error": str(exc),
        }

    network_status = _network_trust_status(
        security.network_trust,
        security.network_trust_source,
    )
    if security.auth_mode == AUTH_MODE_NONE:
        auth_status = {
            "status": "ready",
            "mode": AUTH_MODE_NONE,
            "source": security.auth_source,
            "oauth_secret_required": False,
            "identity_level": "network-only",
        }
        profile = "network-trusted"
        identity_level = "network-only"
    elif security.resource_auth is not None:
        auth_status = {**resource_auth_status(paths), "oauth_secret_required": True}
        profile = "oauth-resource-server"
        identity_level = "subject-client-scopes"
    else:
        auth_status = {
            "status": "disabled",
            "mode": None,
            "source": security.auth_source,
            "oauth_secret_required": False,
        }
        profile = "legacy"
        identity_level = "local-only"

    return {
        "status": "ready",
        "profile": profile,
        "auth_mode": security.auth_mode,
        "auth": auth_status,
        "network_trust": network_status,
        "identity_level": identity_level,
    }


def runtime_path_status(paths: RuntimePaths) -> dict[str, str]:
    """Return non-secret paths exposed by doctor/status diagnostics."""

    return {
        "home": str(paths.home),
        "config": str(paths.config_dir),
        "bridge_config": str(paths.bridge_config),
        "projects_config": str(paths.projects_config),
        "data": str(paths.data_dir),
        "checkpoints": str(paths.checkpoint_dir),
        "logs": str(paths.log_dir),
        "runtime": str(paths.run_dir),
        "secrets": str(paths.secret_dir),
        "layout": paths.layout,
    }


def initialize_tunnel_profile(
    paths: RuntimePaths,
    settings: Any,
    *,
    force: bool = False,
) -> Path:
    provider, _ = load_transport_provider(paths)
    secret = _secret_from_runtime(paths, provider)
    if not secret:
        raise LifecycleError(
            f"{provider.secret_env_name} is not available; set it for init or store it securely"
        )
    config = provider.initialize(
        _transport_context(paths),
        settings,
        secret=secret,
        force=force,
    )
    if not isinstance(config, Path):
        raise LifecycleError(
            f"{provider.provider_id} transport did not return a configuration path"
        )
    return config


def run_tunnel_proxy(paths: RuntimePaths, settings: Any) -> int:
    provider, _ = load_transport_provider(paths)
    secret = _secret_from_runtime(paths, provider)
    if not secret:
        raise LifecycleError(f"{provider.secret_env_name} is unavailable")
    return provider.run(
        _transport_context(paths),
        settings,
        secret=secret,
        rotate_log=_rotate_log,
    )


def _popen_background(
    args: list[str],
    *,
    cwd: Path,
    log_path: Path,
    env: dict[str, str],
) -> subprocess.Popen[Any]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
            subprocess, "CREATE_NO_WINDOW", 0
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = subprocess.Popen(args, **kwargs)
    finally:
        log_handle.close()
    return process


def start_services(
    paths: RuntimePaths,
    *,
    bridge_config: Path | None = None,
    projects_config: Path | None = None,
    env_file: Path | None = None,
    startup_timeout_seconds: float = 45,
) -> dict[str, Any]:
    ensure_runtime_dirs(paths)
    bridge_path = (
        paths.bridge_config if bridge_config is None else bridge_config.resolve(strict=False)
    )
    projects_path = (
        paths.projects_config if projects_config is None else projects_config.resolve(strict=False)
    )
    try:
        load_settings(bridge_path, projects_path)
    except SettingsError as exc:
        raise LifecycleError(f"Bridge configuration is invalid: {exc}") from exc
    provider, _ = load_transport_provider(paths)
    tunnel = provider.load_settings(_transport_context(paths), env_file=env_file)
    provider.validate_config(tunnel)
    try:
        security = load_remote_security_settings(paths)
    except LifecycleError as exc:
        if provider.provider_id == "cloudflare" and "auth.mode = none requires" in str(exc):
            raise LifecycleError(PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST) from exc
        raise

    if security.auth_mode == RESOURCE_AUTH_MODE:
        load_request_authenticator(paths)
    elif provider.provider_id == "cloudflare" and security.auth_mode != AUTH_MODE_NONE:
        raise LifecycleError(PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST)
    if provider.provider_id == "cloudflare" and security.network_trust is None:
        if security.auth_mode == AUTH_MODE_NONE:
            raise LifecycleError(PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST)
        if security.auth_mode is None:
            raise LifecycleError(PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST)
    secret = _secret_from_runtime(paths, provider)
    if not secret:
        raise LifecycleError(
            f"{provider.secret_env_name} is unavailable; set it in the environment or "
            "store it securely"
        )
    bridge_health = _bridge_health_url(provider.bridge_url(tunnel))
    tunnel_ready = provider.ready_url(tunnel)
    if paths.state_file.exists():
        existing = status_services(paths)
        if existing["status"] == "running":
            return existing

        existing_bridge = existing.get("bridge")
        existing_tunnel = existing.get("tunnel")
        bridge_reusable = (
            isinstance(existing_bridge, Mapping)
            and existing_bridge.get("owned") is True
            and isinstance(existing_bridge.get("health"), Mapping)
            and existing_bridge["health"].get("status") == "ok"
        )
        if bridge_reusable:
            if not isinstance(existing_tunnel, Mapping):
                raise LifecycleError("degraded lifecycle state has no tunnel ownership metadata")
            tunnel_owned = existing_tunnel.get("owned") is True
            tunnel_health = existing_tunnel.get("health")
            tunnel_healthy = (
                isinstance(tunnel_health, Mapping) and tunnel_health.get("status") == "ok"
            )
            if not tunnel_owned and tunnel_healthy:
                raise LifecycleError(
                    "Tunnel health endpoint is occupied by an unowned process; "
                    "refusing unsafe takeover"
                )
            if tunnel_owned and tunnel_healthy:
                raise LifecycleError(
                    "Runtime services are owned and healthy but lifecycle status is degraded; "
                    "refusing unsafe recovery"
                )

            state = json.loads(paths.state_file.read_text(encoding="utf-8"))
            old_tunnel_pid = int(state.get("tunnel_pid", 0))
            if tunnel_owned and old_tunnel_pid > 0:
                _terminate_state_process(state, "tunnel")
            if _http_check(tunnel_ready)["status"] == "ok":
                raise LifecycleError(
                    "Tunnel health endpoint is already occupied; refusing unsafe takeover"
                )

            environment = os.environ.copy()
            tunnel_process: subprocess.Popen[Any] | None = None
            try:
                tunnel_process = _popen_background(
                    [
                        *_self_command(),
                        "_tunnel",
                        "--env-file",
                        str(tunnel.env_file),
                        paths.home_option,
                        str(paths.app_root),
                    ],
                    cwd=paths.app_root,
                    log_path=paths.log_dir / "tunnel-supervisor.log",
                    env=environment,
                )
                tunnel_wait = _wait_endpoint(
                    tunnel_ready,
                    tunnel_process,
                    startup_timeout_seconds,
                )
                if tunnel_wait["status"] != "ok":
                    raise LifecycleError(
                        f"Tunnel startup failed: {json.dumps(tunnel_wait, ensure_ascii=False)}"
                    )
                state["transport"] = provider.provider_id
                _capture_process_state(state, "tunnel", tunnel_process.pid)
                state["tunnel_ready_url"] = tunnel_ready
                _write_json_atomic(paths.state_file, state)
                return {
                    "status": "ok",
                    "services": {
                        "bridge": {
                            "status": "reused",
                            "pid": existing_bridge.get("pid"),
                            "health": existing_bridge.get("health"),
                        },
                        "tunnel": {
                            "status": "started",
                            "pid": tunnel_process.pid,
                            "health": tunnel_wait,
                        },
                    },
                }
            except Exception:
                if tunnel_process is not None and tunnel_process.poll() is None:
                    _terminate_tree(tunnel_process.pid)
                raise

        tunnel_reusable = (
            isinstance(existing_tunnel, Mapping)
            and existing_tunnel.get("owned") is True
            and isinstance(existing_tunnel.get("health"), Mapping)
            and existing_tunnel["health"].get("status") == "ok"
        )
        if tunnel_reusable:
            if not isinstance(existing_bridge, Mapping):
                raise LifecycleError("degraded lifecycle state has no bridge ownership metadata")
            bridge_owned = existing_bridge.get("owned") is True
            existing_bridge_health = existing_bridge.get("health")
            bridge_healthy = (
                isinstance(existing_bridge_health, Mapping)
                and existing_bridge_health.get("status") == "ok"
            )
            if not bridge_owned and bridge_healthy:
                raise LifecycleError(
                    "Bridge health endpoint is occupied by an unowned process; "
                    "refusing unsafe takeover"
                )

            state = json.loads(paths.state_file.read_text(encoding="utf-8"))
            old_bridge_pid = int(state.get("bridge_pid", 0))
            if bridge_owned and old_bridge_pid > 0:
                _terminate_state_process(state, "bridge")

            bridge_health_headers = _bridge_probe_headers(
                security.network_trust.allowed_hosts
                if security.auth_mode == AUTH_MODE_NONE and security.network_trust is not None
                else None
            )
            bridge_occupied = (
                _http_check(bridge_health)
                if bridge_health_headers is None
                else _http_check(bridge_health, headers=bridge_health_headers)
            )
            if bridge_occupied["status"] == "ok":
                raise LifecycleError(
                    "Bridge health endpoint is already occupied; refusing unsafe takeover"
                )

            environment = os.environ.copy()
            bridge_process: subprocess.Popen[Any] | None = None
            try:
                bridge_process = _popen_background(
                    [
                        *_self_command(),
                        "serve",
                        "--bridge-config",
                        str(bridge_path),
                        "--projects-config",
                        str(projects_path),
                        paths.home_option,
                        str(paths.app_root),
                    ],
                    cwd=paths.app_root,
                    log_path=paths.log_dir / "bridge-supervisor.log",
                    env=environment,
                )
                if bridge_health_headers is None:
                    bridge_wait = _wait_endpoint(
                        bridge_health,
                        bridge_process,
                        startup_timeout_seconds,
                    )
                else:
                    bridge_wait = _wait_endpoint(
                        bridge_health,
                        bridge_process,
                        startup_timeout_seconds,
                        headers=bridge_health_headers,
                    )
                if bridge_wait["status"] != "ok":
                    raise LifecycleError(
                        f"Bridge startup failed: {json.dumps(bridge_wait, ensure_ascii=False)}"
                    )
                _capture_process_state(state, "bridge", bridge_process.pid)
                state["bridge_health_url"] = bridge_health
                _write_json_atomic(paths.state_file, state)
                return {
                    "status": "ok",
                    "services": {
                        "bridge": {
                            "status": "started",
                            "pid": bridge_process.pid,
                            "health": bridge_wait,
                        },
                        "tunnel": {
                            "status": "reused",
                            "pid": existing_tunnel.get("pid"),
                            "health": existing_tunnel.get("health"),
                        },
                    },
                }
            except Exception:
                if bridge_process is not None and bridge_process.poll() is None:
                    _terminate_tree(bridge_process.pid)
                raise

        state = json.loads(paths.state_file.read_text(encoding="utf-8"))
        if isinstance(existing_bridge, Mapping):
            bridge_owned = existing_bridge.get("owned") is True
            existing_bridge_health = existing_bridge.get("health")
            bridge_healthy = (
                isinstance(existing_bridge_health, Mapping)
                and existing_bridge_health.get("status") == "ok"
            )
            if not bridge_owned and bridge_healthy:
                raise LifecycleError(
                    "Bridge health endpoint is occupied by an unowned process; "
                    "refusing unsafe takeover"
                )
            old_bridge_pid = int(state.get("bridge_pid", 0))
            if bridge_owned and old_bridge_pid > 0:
                _terminate_state_process(state, "bridge")
        if isinstance(existing_tunnel, Mapping):
            tunnel_owned = existing_tunnel.get("owned") is True
            existing_tunnel_health = existing_tunnel.get("health")
            tunnel_healthy = (
                isinstance(existing_tunnel_health, Mapping)
                and existing_tunnel_health.get("status") == "ok"
            )
            if not tunnel_owned and tunnel_healthy:
                raise LifecycleError(
                    "Tunnel health endpoint is occupied by an unowned process; "
                    "refusing unsafe takeover"
                )
            old_tunnel_pid = int(state.get("tunnel_pid", 0))
            if tunnel_owned and old_tunnel_pid > 0:
                _terminate_state_process(state, "tunnel")

        paths.state_file.unlink(missing_ok=True)
    bridge_health_headers = _bridge_probe_headers(
        security.network_trust.allowed_hosts
        if security.auth_mode == AUTH_MODE_NONE and security.network_trust is not None
        else None
    )
    bridge_occupied = (
        _http_check(bridge_health)
        if bridge_health_headers is None
        else _http_check(bridge_health, headers=bridge_health_headers)
    )
    if bridge_occupied["status"] == "ok":
        raise LifecycleError("Bridge health endpoint is already occupied; refusing unsafe takeover")
    if _http_check(tunnel_ready)["status"] == "ok":
        raise LifecycleError("Tunnel health endpoint is already occupied; refusing unsafe takeover")

    environment = os.environ.copy()
    bridge_process: subprocess.Popen[Any] | None = None
    tunnel_process: subprocess.Popen[Any] | None = None
    try:
        bridge_process = _popen_background(
            [
                *_self_command(),
                "serve",
                "--bridge-config",
                str(bridge_path),
                "--projects-config",
                str(projects_path),
                paths.home_option,
                str(paths.app_root),
            ],
            cwd=paths.app_root,
            log_path=paths.log_dir / "bridge-supervisor.log",
            env=environment,
        )
        if bridge_health_headers is None:
            bridge_wait = _wait_endpoint(bridge_health, bridge_process, startup_timeout_seconds)
        else:
            bridge_wait = _wait_endpoint(
                bridge_health,
                bridge_process,
                startup_timeout_seconds,
                headers=bridge_health_headers,
            )
        if bridge_wait["status"] != "ok":
            raise LifecycleError(
                f"Bridge startup failed: {json.dumps(bridge_wait, ensure_ascii=False)}"
            )

        tunnel_process = _popen_background(
            [
                *_self_command(),
                "_tunnel",
                "--env-file",
                str(tunnel.env_file),
                paths.home_option,
                str(paths.app_root),
            ],
            cwd=paths.app_root,
            log_path=paths.log_dir / "tunnel-supervisor.log",
            env=environment,
        )
        tunnel_wait = _wait_endpoint(tunnel_ready, tunnel_process, startup_timeout_seconds)
        if tunnel_wait["status"] != "ok":
            raise LifecycleError(
                f"Tunnel startup failed: {json.dumps(tunnel_wait, ensure_ascii=False)}"
            )

        state = {
            "version": 1 if os.name == "nt" else 2,
            "transport": provider.provider_id,
            "bridge_config": str(bridge_path),
            "projects_config": str(projects_path),
            "env_file": str(tunnel.env_file),
            "bridge_health_url": bridge_health,
            "tunnel_ready_url": tunnel_ready,
        }
        _capture_process_state(state, "bridge", bridge_process.pid)
        _capture_process_state(state, "tunnel", tunnel_process.pid)
        _write_json_atomic(paths.state_file, state)
        return {
            "status": "ok",
            "services": {
                "bridge": {"status": "started", "pid": bridge_process.pid, "health": bridge_wait},
                "tunnel": {"status": "started", "pid": tunnel_process.pid, "health": tunnel_wait},
            },
        }
    except Exception:
        for process in (tunnel_process, bridge_process):
            if process is not None and process.poll() is None:
                _terminate_tree(process.pid)
        raise


def status_services(paths: RuntimePaths) -> dict[str, Any]:
    security_status = security_profile_status(paths)
    try:
        provider, transport_source = load_transport_provider(paths)
    except LifecycleError as exc:
        return {
            "status": "unknown",
            "error": str(exc),
            "state_file": str(paths.state_file),
            "paths": runtime_path_status(paths),
        }
    if not paths.state_file.is_file():
        try:
            configured_project_count: int | None = len(
                load_settings(paths.bridge_config, paths.projects_config).projects
            )
        except SettingsError:
            configured_project_count = None
        return {
            "status": "stopped",
            "transport": provider.provider_id,
            "transport_source": transport_source,
            "auth": security_status["auth"],
            "network_trust": security_status["network_trust"],
            "identity_level": security_status["identity_level"],
            "security_profile": security_status["profile"],
            "project_registry": {
                "status": "stopped",
                "generation": None,
                "reload_status": "stopped",
                "last_reload_error": None,
                "projects_registered": configured_project_count,
            },
            "state_file": str(paths.state_file),
            "paths": runtime_path_status(paths),
        }
    try:
        state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "unknown", "error": f"cannot read lifecycle state: {exc}"}

    bridge_owned = _state_process_owned(state, "bridge")
    tunnel_owned = _state_process_owned(state, "tunnel")
    network_status = security_status.get("network_trust")
    allowed_hosts = None
    if security_status.get("identity_level") == "network-only" and isinstance(
        network_status, Mapping
    ):
        configured_hosts = network_status.get("allowed_hosts")
        if isinstance(configured_hosts, (list, tuple)):
            allowed_hosts = configured_hosts
    bridge_health_headers = _bridge_probe_headers(allowed_hosts)
    bridge_health_url = str(state.get("bridge_health_url", DEFAULT_BRIDGE_URL))
    bridge_health = (
        _http_check(bridge_health_url)
        if bridge_health_headers is None
        else _http_check(bridge_health_url, headers=bridge_health_headers)
    )
    project_registry: dict[str, Any] = {
        "status": "unavailable",
        "generation": None,
        "reload_status": None,
        "last_reload_error": None,
        "projects_registered": None,
    }
    if bridge_owned and bridge_health["status"] == "ok":
        live_health = (
            _http_json_check(bridge_health_url)
            if bridge_health_headers is None
            else _http_json_check(bridge_health_url, headers=bridge_health_headers)
        )
        live_data = live_health.get("data")
        if isinstance(live_data, Mapping):
            registry_data = live_data.get("project_registry")
            registered = live_data.get("projects_registered")
            if isinstance(registry_data, Mapping):
                project_registry = {
                    "status": "ok",
                    "generation": registry_data.get("generation"),
                    "reload_status": registry_data.get("reload_status"),
                    "last_reload_error": registry_data.get("last_reload_error"),
                    "projects_registered": (
                        registered
                        if isinstance(registered, int) and not isinstance(registered, bool)
                        else None
                    ),
                }
    tunnel_health = _http_check(
        str(state.get("tunnel_ready_url", f"{DEFAULT_TUNNEL_HEALTH_URL}/readyz"))
    )
    running = (
        bridge_owned
        and tunnel_owned
        and bridge_health["status"] == "ok"
        and tunnel_health["status"] == "ok"
        and security_status["status"] != "invalid"
    )
    return {
        "status": "running" if running else "degraded",
        "transport": state.get("transport", provider.provider_id),
        "transport_source": transport_source,
        "auth": security_status["auth"],
        "network_trust": security_status["network_trust"],
        "identity_level": security_status["identity_level"],
        "security_profile": security_status["profile"],
        "project_registry": project_registry,
        "bridge": {
            "pid": state.get("bridge_pid"),
            "owned": bridge_owned,
            "health": bridge_health,
        },
        "tunnel": {
            "pid": state.get("tunnel_pid"),
            "owned": tunnel_owned,
            "health": tunnel_health,
        },
        "state_file": str(paths.state_file),
        "paths": runtime_path_status(paths),
    }


def stop_services(paths: RuntimePaths) -> dict[str, Any]:
    if not paths.state_file.is_file():
        return {"status": "ok", "actions": [], "note": "already stopped"}
    try:
        state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"cannot read lifecycle state: {exc}") from exc

    actions: list[dict[str, Any]] = []
    for category in ("tunnel", "bridge"):
        pid = int(state.get(f"{category}_pid", 0))
        if pid <= 0 or not _state_process_owned(state, category):
            actions.append({"service": category, "pid": pid or None, "status": "not_owned"})
            continue
        if not _terminate_state_process(state, category):
            actions.append({"service": category, "pid": pid, "status": "not_owned"})
            continue
        actions.append({"service": category, "pid": pid, "status": "stopped"})
    paths.state_file.unlink(missing_ok=True)
    return {"status": "ok", "actions": actions}


def doctor_report(
    paths: RuntimePaths,
    *,
    bridge_config: Path | None = None,
    projects_config: Path | None = None,
    env_file: Path | None = None,
) -> dict[str, Any]:
    bridge_path = (
        paths.bridge_config if bridge_config is None else bridge_config.resolve(strict=False)
    )
    projects_path = (
        paths.projects_config if projects_config is None else projects_config.resolve(strict=False)
    )
    checks: dict[str, Any] = {}
    try:
        settings = load_settings(bridge_path, projects_path)
        checks["configuration"] = {
            "status": "ok",
            "projects": len(settings.projects),
            "worker_mode": settings.codemcp.worker_mode,
            "bridge_url": (
                f"http://{settings.server.host}:{settings.server.port}{settings.server.path}"
            ),
        }
    except SettingsError as exc:
        checks["configuration"] = {"status": "failed", "error": str(exc)}
    provider, transport_source = load_transport_provider(paths)
    checks["transport"] = {
        "status": "ok",
        "provider": provider.provider_id,
        "source": transport_source,
        "config": str(_remote_config_path(paths)),
    }
    security_status = security_profile_status(paths)
    checks["paths"] = runtime_path_status(paths)
    checks["network_trust"] = security_status["network_trust"]
    checks["identity_level"] = security_status["identity_level"]
    auth_status = security_status["auth"]
    if provider.provider_id == "cloudflare":
        if security_status["auth_mode"] == RESOURCE_AUTH_MODE:
            if auth_status["status"] == "ready":
                checks["auth"] = {**auth_status, "status": "ok"}
            else:
                error = auth_status.get("error")
                if auth_status["status"] == "missing":
                    error = f"{RESOURCE_AUTH_SECRET_ENV_NAME} is unavailable"
                checks["auth"] = {**auth_status, "status": "failed", "error": error}
        elif security_status["profile"] == "network-trusted":
            checks["auth"] = auth_status
        else:
            checks["auth"] = {
                **auth_status,
                "status": "failed",
                "mode": auth_status.get("mode") or "none",
                "error": PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST,
            }
    elif auth_status["status"] == "ready":
        checks["auth"] = {**auth_status, "status": "ok"}
    else:
        checks["auth"] = auth_status
    secret_source = "none"
    try:
        secret_value = _transport_secret_value(paths, provider)
        secret = secret_value.value
        secret_source = secret_value.source
    except LifecycleError as exc:
        secret = None
        checks["secret_store"] = {
            "status": "failed",
            "source": "none",
            "error": str(exc),
        }
    checks.update(
        provider.doctor(
            _transport_context(paths),
            env_file=env_file,
            secret_available=bool(secret),
            secret_source=secret_source,
        )
    )
    checks["git"] = {
        "status": "ok" if shutil.which("git") else "failed",
        "path": shutil.which("git"),
    }
    services_status = status_services(paths)
    checks["services"] = services_status
    registry_status = services_status.get("project_registry")
    if isinstance(registry_status, Mapping):
        checks["project_registry"] = dict(registry_status)
    failed = [
        name
        for name, value in checks.items()
        if isinstance(value, dict) and value.get("status") in {"failed", "missing", "unknown"}
    ]
    return {
        "status": "ok" if not failed else "attention",
        "home": str(paths.home),
        "config": str(paths.config_dir),
        "checks": checks,
    }


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _ps_value(pid: int, field: str) -> str | None:
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", f"{field}="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = " ".join((completed.stdout or "").split())
    return value or None


def _process_marker(pid: int) -> str | None:
    if os.name != "nt":
        if pid <= 0:
            return None
        if sys.platform == "darwin":
            started = _ps_value(pid, "lstart")
            return f"darwin-lstart:{started}" if started else None
        stat_path = Path(f"/proc/{pid}/stat")
        try:
            stat_text = stat_path.read_text(encoding="utf-8")
            remainder = stat_text.rsplit(")", 1)[1].split()
            if len(remainder) > 19:
                return f"proc-start:{remainder[19]}"
        except (OSError, IndexError):
            pass
        started = _ps_value(pid, "lstart")
        return f"posix-lstart:{started}" if started else None

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_time = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not ctypes.windll.kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        value = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return str(value)
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _process_executable_identity(pid: int) -> str | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        return None
    proc_executable = Path(f"/proc/{pid}/exe")
    try:
        if proc_executable.exists():
            return str(proc_executable.resolve(strict=True))
    except OSError:
        pass
    executable = _ps_value(pid, "comm")
    if executable is None:
        return None
    candidate = Path(executable)
    if candidate.is_absolute():
        return str(candidate.resolve(strict=False))
    return executable


def _process_group_id(pid: int) -> int | None:
    if os.name == "nt" or pid <= 0:
        return None
    try:
        return int(os.getpgid(pid))
    except (OSError, ProcessLookupError):
        return None


def _capture_process_state(state: dict[str, Any], category: str, pid: int) -> None:
    marker = _process_marker(pid)
    if marker is None:
        raise LifecycleError(f"cannot establish {category} process start marker for PID {pid}")
    state[f"{category}_pid"] = pid
    state[f"{category}_process_marker"] = marker
    if os.name == "nt":
        return
    pgid = _process_group_id(pid)
    executable_identity = _process_executable_identity(pid)
    if pgid is None or pgid <= 1 or pgid == os.getpgrp():
        raise LifecycleError(f"cannot establish a safe {category} process group for PID {pid}")
    if executable_identity is None:
        raise LifecycleError(f"cannot establish {category} executable identity for PID {pid}")
    state[f"{category}_pgid"] = pgid
    state[f"{category}_executable_identity"] = executable_identity


def _matches_process_marker(pid: int, marker: Any) -> bool:
    if pid <= 0 or marker is None:
        return False
    current = _process_marker(pid)
    return current is not None and str(current) == str(marker)


def _state_process_owned(state: Mapping[str, Any], category: str) -> bool:
    pid = int(state.get(f"{category}_pid", 0))
    marker = state.get(f"{category}_process_marker")
    if pid <= 0 or not _matches_process_marker(pid, marker):
        return False
    if os.name == "nt":
        return True
    try:
        version = int(state.get("version", 0))
        pgid = int(state.get(f"{category}_pgid", 0))
    except (TypeError, ValueError):
        return False
    if version != 2:
        return False
    if pgid <= 1 or pgid == os.getpgrp():
        return False
    actual_pgid = _process_group_id(pid)
    if actual_pgid is None or actual_pgid != pgid:
        return False
    expected_executable = state.get(f"{category}_executable_identity")
    if not isinstance(expected_executable, str) or not expected_executable:
        return False
    return _process_executable_identity(pid) == expected_executable


def _terminate_state_process(
    state: Mapping[str, Any],
    category: str,
    *,
    timeout_seconds: float = 5.0,
) -> bool:
    pid = int(state.get(f"{category}_pid", 0))
    if not _state_process_owned(state, category):
        return False
    if os.name == "nt":
        completed = subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        if completed.returncode not in {0, 128}:
            raise LifecycleError(f"failed to stop PID {pid}: {completed.stderr.strip()}")
        return True

    pgid = int(state[f"{category}_pgid"])
    if not _state_process_owned(state, category):
        return False
    try:
        os.killpg(pgid, _POSIX_SIGTERM)
    except ProcessLookupError:
        return True

    deadline = time.monotonic() + max(timeout_seconds, 0)
    while time.monotonic() < deadline:
        if not _state_process_owned(state, category):
            return True
        time.sleep(0.05)

    if not _state_process_owned(state, category):
        return True
    try:
        os.killpg(pgid, _POSIX_SIGKILL)
    except ProcessLookupError:
        return True
    return True


def _terminate_tree(pid: int) -> None:
    if os.name == "nt":
        state: dict[str, Any] = {"version": 1}
    else:
        state = {"version": 2}
    try:
        _capture_process_state(state, "process", pid)
    except LifecycleError:
        return
    _terminate_state_process(state, "process")


def _pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(value: bytes) -> tuple[_DATA_BLOB, Any]:
    buffer = ctypes.create_string_buffer(value)
    blob = _DATA_BLOB(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def _dpapi_protect(value: bytes) -> bytes:
    if os.name != "nt":
        raise LifecycleError("DPAPI is available only on Windows")
    source, source_buffer = _blob_from_bytes(value)
    destination = _DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(destination),
    )
    _ = source_buffer
    if not ok:
        raise LifecycleError("Windows DPAPI failed to protect the API key")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(destination.pbData)


def _dpapi_unprotect(value: bytes) -> bytes:
    if os.name != "nt":
        raise LifecycleError("DPAPI is available only on Windows")
    source, source_buffer = _blob_from_bytes(value)
    destination = _DATA_BLOB()
    CRYPTPROTECT_UI_FORBIDDEN = 0x1
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(destination),
    )
    _ = source_buffer
    if not ok:
        raise LifecycleError("Windows DPAPI failed to decrypt the API key")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(destination.pbData)
