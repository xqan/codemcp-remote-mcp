"""CLI entry point for the local loopback Bridge server."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .lifecycle import (
    APP_NAME,
    DEFAULT_BRIDGE_URL,
    DEFAULT_HEALTH_LISTEN_ADDR,
    DEFAULT_PROFILE,
    DEFAULT_TUNNEL_HEALTH_URL,
    LifecycleError,
    add_project,
    configure_network_trust,
    configure_resource_auth,
    doctor_report,
    initialize_runtime,
    initialize_tunnel_profile,
    load_remote_security_settings,
    load_request_authenticator,
    load_transport_provider,
    load_tunnel_settings,
    remove_project,
    run_tunnel_proxy,
    runtime_paths,
    start_services,
    status_services,
    stop_services,
    store_api_key_from_environment,
    store_resource_auth_secret_from_environment,
    store_transport_secret_from_environment,
)
from .logging_utils import configure_logging
from .mcp_server import create_server, install_resource_server_auth
from .native_codemcp_worker import main as native_worker_main
from .settings import SettingsError, load_settings


def is_frozen_runtime(*, frozen: bool | None = None) -> bool:
    """Return whether the current process is a packaged executable."""

    return bool(getattr(sys, "frozen", False)) if frozen is None else frozen


def distribution_root(
    *,
    frozen: bool | None = None,
    executable: Path | None = None,
) -> Path:
    """Return the read-only distribution root for source or packaged execution."""

    if is_frozen_runtime(frozen=frozen):
        executable_path = Path(sys.executable) if executable is None else executable
        return executable_path.resolve().parent
    return Path(__file__).resolve().parents[3]


def runtime_root(
    *,
    frozen: bool | None = None,
    executable: Path | None = None,
) -> Path:
    """Compatibility alias for the distribution root."""

    return distribution_root(frozen=frozen, executable=executable)


def bundled_runtime_root(distribution_root_path: Path) -> Path:
    """Return the fixed hidden root for packaged runtime dependencies."""

    return distribution_root_path.resolve() / ".codemcp-runtime"


def default_cli_command(*, frozen: bool | None = None) -> str:
    """Start the managed lifecycle when a packaged executable is launched with no arguments."""

    return "start" if is_frozen_runtime(frozen=frozen) else "serve"


def default_runtime_home(
    runtime_root_path: Path,
    *,
    frozen: bool | None = None,
    platform: str | None = None,
    user_home: Path | None = None,
) -> Path | None:
    """Return the platform-specific packaged writable home without migrating source mode."""

    if not is_frozen_runtime(frozen=frozen):
        return None
    effective_platform = sys.platform if platform is None else platform
    if effective_platform == "darwin":
        base = Path.home() if user_home is None else user_home
        return (
            base.expanduser().resolve(strict=False) / "Library" / "Application Support" / APP_NAME
        )
    return runtime_root_path.resolve()


RUNTIME_ROOT = runtime_root()
DEFAULT_BRIDGE_CONFIG = RUNTIME_ROOT / "config" / "bridge.example.toml"
DEFAULT_PROJECTS_CONFIG = RUNTIME_ROOT / "config" / "projects.toml"
if not DEFAULT_PROJECTS_CONFIG.is_file():
    DEFAULT_PROJECTS_CONFIG = RUNTIME_ROOT / "config" / "projects.example.toml"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run and manage codemcp-remote")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "command",
        choices=(
            "serve",
            "check",
            "init",
            "configure",
            "project",
            "start",
            "status",
            "stop",
            "doctor",
            "_worker",
            "_tunnel",
        ),
        nargs="?",
        default=default_cli_command(),
    )
    parser.add_argument("subcommand", nargs="?")
    parser.add_argument("project_id", nargs="?")
    parser.add_argument("project_root", nargs="?")
    parser.add_argument("--expected-root", type=Path)
    parser.add_argument("--bridge-config", type=Path)
    parser.add_argument("--projects-config", type=Path)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument(
        "--home",
        type=Path,
        help=(
            "writable runtime home (overrides CODEMCP_HOME; packaged default is platform-specific)"
        ),
    )
    parser.add_argument("--app-root", type=Path)
    parser.add_argument("--transport", choices=("openai-tunnel", "cloudflare"))
    parser.add_argument("--tunnel-id")
    parser.add_argument("--profile-name", default=DEFAULT_PROFILE)
    parser.add_argument("--bridge-url", default=DEFAULT_BRIDGE_URL)
    parser.add_argument("--tunnel-health-url", default=DEFAULT_TUNNEL_HEALTH_URL)
    parser.add_argument("--health-listen-addr", default=DEFAULT_HEALTH_LISTEN_ADDR)
    parser.add_argument("--public-url")
    parser.add_argument("--origin-url", default=DEFAULT_BRIDGE_URL)
    parser.add_argument("--metrics-addr", default="127.0.0.1:46202")
    parser.add_argument("--startup-timeout", type=float, default=45)
    parser.add_argument("--store-api-key", action="store_true")
    parser.add_argument("--store-transport-secret", action="store_true")
    parser.add_argument("--auth-mode", choices=("none", "oauth-resource-server"))
    parser.add_argument("--network-trust", choices=("cloudflare-chatgpt",))
    parser.add_argument("--allowed-host", dest="allowed_hosts", action="append")
    parser.add_argument("--allowed-origin", dest="allowed_origins", action="append")
    parser.add_argument("--authorization-server-issuer")
    parser.add_argument("--canonical-resource-uri")
    parser.add_argument("--validation-resource-id")
    parser.add_argument("--store-auth-secret", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main() -> int:
    args = _parse_args()

    if args.command == "_worker":
        sys.argv = [sys.argv[0]]
        native_worker_main()
        return 0

    try:
        paths = runtime_paths(
            RUNTIME_ROOT,
            home=args.home,
            app_root=args.app_root,
            default_home=default_runtime_home(RUNTIME_ROOT),
        )
    except LifecycleError as exc:
        _json({"status": "failed", "error": str(exc)})
        return 1

    if args.command == "_tunnel":
        try:
            tunnel = load_tunnel_settings(paths, env_file=args.env_file)
            return run_tunnel_proxy(paths, tunnel)
        except LifecycleError as exc:
            _json({"status": "failed", "error": str(exc)})
            return 1

    if args.command in {"init", "configure", "project", "start", "status", "stop", "doctor"}:
        try:
            if args.command == "init":
                tunnel_id = args.tunnel_id or os.environ.get("CONTROL_PLANE_TUNNEL_ID", "")
                result = initialize_runtime(
                    paths,
                    tunnel_id=tunnel_id,
                    profile_name=args.profile_name,
                    bridge_url=args.bridge_url,
                    tunnel_health_url=args.tunnel_health_url,
                    health_listen_addr=args.health_listen_addr,
                    transport=args.transport,
                    public_url=args.public_url or "",
                    origin_url=args.origin_url,
                    metrics_addr=args.metrics_addr,
                    force=args.force,
                )
                network_fields = (args.network_trust, args.allowed_hosts, args.allowed_origins)
                if args.network_trust is None and any(
                    value is not None for value in network_fields[1:]
                ):
                    raise LifecycleError(
                        "--network-trust is required when "
                        "--allowed-host/--allowed-origin is supplied"
                    )
                if args.network_trust is not None:
                    if not args.allowed_hosts:
                        raise LifecycleError("--network-trust requires at least one --allowed-host")
                    result.update(
                        configure_network_trust(
                            paths,
                            mode=args.network_trust,
                            allowed_hosts=args.allowed_hosts,
                            allowed_origins=args.allowed_origins or (),
                        )
                    )
                auth_fields = (
                    args.authorization_server_issuer,
                    args.canonical_resource_uri,
                    args.validation_resource_id,
                )
                if args.auth_mode is None and any(value is not None for value in auth_fields):
                    raise LifecycleError(
                        "--auth-mode is required when OAuth auth fields are supplied"
                    )
                if args.auth_mode is not None:
                    result.update(
                        configure_resource_auth(
                            paths,
                            mode=args.auth_mode,
                            issuer=args.authorization_server_issuer,
                            resource=args.canonical_resource_uri,
                            validation_resource_id=args.validation_resource_id,
                        )
                    )
                provider, _ = load_transport_provider(paths)
                if args.store_api_key:
                    if provider.provider_id != "openai-tunnel":
                        raise LifecycleError(
                            "--store-api-key is only valid for the openai-tunnel transport; "
                            "use --store-transport-secret"
                        )
                    result["api_key"] = store_api_key_from_environment(paths)
                if args.store_transport_secret:
                    result["transport_secret"] = store_transport_secret_from_environment(
                        paths,
                        provider=provider,
                    )
                if args.store_auth_secret:
                    result["auth_secret"] = store_resource_auth_secret_from_environment(paths)
                tunnel = load_tunnel_settings(paths)
                config = initialize_tunnel_profile(paths, tunnel, force=args.force)
                result["transport_config"] = str(config)
                if provider.provider_id == "openai-tunnel":
                    result["tunnel_profile"] = str(config)
            elif args.command == "configure":
                result = {}
                if args.network_trust is None and any(
                    value is not None for value in (args.allowed_hosts, args.allowed_origins)
                ):
                    raise LifecycleError(
                        "--network-trust is required when "
                        "--allowed-host/--allowed-origin is supplied"
                    )
                if args.network_trust is not None:
                    if not args.allowed_hosts:
                        raise LifecycleError("--network-trust requires at least one --allowed-host")
                    result.update(
                        configure_network_trust(
                            paths,
                            mode=args.network_trust,
                            allowed_hosts=args.allowed_hosts,
                            allowed_origins=args.allowed_origins or (),
                        )
                    )
                auth_fields = (
                    args.authorization_server_issuer,
                    args.canonical_resource_uri,
                    args.validation_resource_id,
                )
                if args.auth_mode is None and any(value is not None for value in auth_fields):
                    raise LifecycleError(
                        "--auth-mode is required when OAuth auth fields are supplied"
                    )
                if args.auth_mode is not None:
                    result.update(
                        configure_resource_auth(
                            paths,
                            mode=args.auth_mode,
                            issuer=args.authorization_server_issuer,
                            resource=args.canonical_resource_uri,
                            validation_resource_id=args.validation_resource_id,
                        )
                    )
                if not result:
                    raise LifecycleError("configure requires --network-trust or --auth-mode")
            elif args.command == "project":
                if args.subcommand == "add":
                    if not args.project_id or not args.project_root:
                        raise LifecycleError(
                            "usage: codemcp-remote project add <project-id> <project-root>"
                        )
                    result = add_project(
                        paths,
                        project_id=args.project_id,
                        root=Path(args.project_root),
                    )
                elif args.subcommand == "remove":
                    if not args.project_id or args.expected_root is None:
                        raise LifecycleError(
                            "usage: codemcp-remote project remove <project-id> "
                            "--expected-root <project-root>"
                        )
                    result = remove_project(
                        paths,
                        project_id=args.project_id,
                        expected_root=args.expected_root,
                    )
                else:
                    raise LifecycleError(
                        "usage: codemcp-remote project add <project-id> <project-root> "
                        "or project remove <project-id> --expected-root <project-root>"
                    )
            elif args.command == "start":
                result = start_services(
                    paths,
                    bridge_config=args.bridge_config,
                    projects_config=args.projects_config,
                    env_file=args.env_file,
                    startup_timeout_seconds=args.startup_timeout,
                )
            elif args.command == "status":
                result = status_services(paths)
            elif args.command == "stop":
                result = stop_services(paths)
            else:
                result = doctor_report(
                    paths,
                    bridge_config=args.bridge_config,
                    projects_config=args.projects_config,
                    env_file=args.env_file,
                )
            _json(result)
            return (
                1 if result.get("status") in {"failed", "attention", "unknown", "degraded"} else 0
            )
        except LifecycleError as exc:
            _json({"status": "failed", "error": str(exc)})
            return 1

    bridge_config = args.bridge_config or DEFAULT_BRIDGE_CONFIG
    projects_config = args.projects_config or DEFAULT_PROJECTS_CONFIG
    try:
        settings = load_settings(bridge_config, projects_config)
    except SettingsError as exc:
        print(f"configuration_error={exc}")
        return 1

    if args.command == "check":
        _json(
            {
                "status": "ok",
                "phase": "5",
                "host": settings.server.host,
                "port": settings.server.port,
                "path": settings.server.path,
                "worker_mode": settings.codemcp.worker_mode,
                "projects_registered": len(settings.projects),
                "model_egress": "deny",
            }
        )
        return 0

    try:
        security = load_remote_security_settings(paths)
        request_authenticator = load_request_authenticator(paths)
        network_trust = security.network_trust if security.auth_mode == "none" else None
        network_resource = None
        if network_trust is not None:
            provider, _ = load_transport_provider(paths)
            if provider.provider_id == "cloudflare":
                network_resource = load_tunnel_settings(paths).public_url
    except LifecycleError as exc:
        print(f"configuration_error={exc}")
        return 1

    configure_logging(settings.storage.log_dir)
    logging.getLogger(__name__).info("Bridge logging initialized")
    server, service = create_server(
        settings,
        network_trust=network_trust,
        network_resource=network_resource,
    )
    if request_authenticator is not None:
        install_resource_server_auth(server, request_authenticator)
    try:
        server.run(transport=settings.server.transport)
    finally:
        asyncio.run(service.close())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
