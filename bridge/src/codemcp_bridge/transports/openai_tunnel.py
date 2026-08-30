"""OpenAI Secure MCP Tunnel transport provider."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .base import LifecycleError, TransportContext

DEFAULT_PROFILE = "codemcp-bridge"
DEFAULT_BRIDGE_URL = "http://127.0.0.1:46200/mcp"
DEFAULT_TUNNEL_HEALTH_URL = "http://127.0.0.1:46201"
DEFAULT_HEALTH_LISTEN_ADDR = "127.0.0.1:46201"
TUNNEL_ID_PATTERN = re.compile(r"^tunnel_[A-Za-z0-9_-]{8,}$")
PROFILE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
ALLOWED_ENV_NAMES = {
    "CONTROL_PLANE_TUNNEL_ID",
    "TUNNEL_CLIENT_PROFILE",
    "TUNNEL_CLIENT_PROFILE_DIR",
    "BRIDGE_MCP_URL",
    "HEALTH_LISTEN_ADDR",
    "TUNNEL_HEALTH_URL",
    "CONTROL_PLANE_ORGANIZATION_ID",
}
SECRET_ENV_NAME = "CONTROL_PLANE_API_KEY"
SECRET_FILE_NAME = "control-plane-api-key.dpapi"
_REDACT_KEY = re.compile(
    r"(?i)((?:CONTROL_PLANE_API_KEY|OPENAI_API_KEY|API_KEY|AUTHORIZATION|"
    r"ACCESS_TOKEN|REFRESH_TOKEN|TOKEN)\s*[:=]\s*)([^\s,;]+)"
)
_REDACT_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_REDACT_SK = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}")


@dataclass(frozen=True, slots=True)
class OpenAITunnelSettings:
    tunnel_id: str
    profile_name: str
    profile_dir: Path
    bridge_url: str
    tunnel_health_url: str
    health_listen_addr: str
    env_file: Path


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        raise LifecycleError(f"tunnel environment file not found: {path}")
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise LifecycleError(f"invalid environment assignment at line {line_number}")
        name, value = stripped.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name == SECRET_ENV_NAME:
            raise LifecycleError(f"{SECRET_ENV_NAME} must never be stored in {path}")
        if name not in ALLOWED_ENV_NAMES:
            raise LifecycleError(f"{name} is not an allowed tunnel setting")
        if len(value) >= 2 and value[:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        elif "'" in value or '"' in value:
            raise LifecycleError(f"unterminated quoted value at line {line_number}")
        if value:
            values[name] = os.path.expandvars(value)
    return values


def _validate_tunnel_id(value: str) -> None:
    if (
        not value
        or not TUNNEL_ID_PATTERN.fullmatch(value)
        or re.search(r"(?i)replace|example|placeholder", value)
    ):
        raise LifecycleError("CONTROL_PLANE_TUNNEL_ID must be a real OpenAI tunnel_id")


def _validate_profile_name(value: str) -> None:
    if not PROFILE_PATTERN.fullmatch(value):
        raise LifecycleError("profile name contains unsupported characters")


def _validate_bridge_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname != "127.0.0.1"
        or parsed.path.rstrip("/") != "/mcp"
        or parsed.query
        or parsed.fragment
    ):
        raise LifecycleError("Bridge MCP URL must be an HTTP(S) /mcp endpoint on 127.0.0.1")


def _validate_health_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname != "127.0.0.1"
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise LifecycleError("Tunnel health URL must be a loopback HTTP(S) base URL")


def _validate_health_listen_addr(value: str) -> None:
    match = re.fullmatch(r"127\.0\.0\.1:(\d+)", value)
    if match is None or not 0 <= int(match.group(1)) <= 65535:
        raise LifecycleError("HEALTH_LISTEN_ADDR must bind to 127.0.0.1:<port>")


def _profile_path(settings: OpenAITunnelSettings) -> Path:
    yaml_path = settings.profile_dir / f"{settings.profile_name}.yaml"
    if yaml_path.is_file():
        return yaml_path
    yml_path = settings.profile_dir / f"{settings.profile_name}.yml"
    return yml_path if yml_path.is_file() else yaml_path


class OpenAITunnelProvider:
    """Existing OpenAI Secure MCP Tunnel lifecycle behind the provider boundary."""

    provider_id = "openai-tunnel"
    secret_env_name = SECRET_ENV_NAME
    secret_file_name = SECRET_FILE_NAME

    def initialize_config(
        self,
        context: TransportContext,
        **kwargs: Any,
    ) -> list[str]:
        tunnel_id = str(kwargs["tunnel_id"])
        profile_name = str(kwargs.get("profile_name", DEFAULT_PROFILE))
        bridge_url = str(kwargs.get("bridge_url", DEFAULT_BRIDGE_URL))
        tunnel_health_url = str(kwargs.get("tunnel_health_url", DEFAULT_TUNNEL_HEALTH_URL))
        health_listen_addr = str(kwargs.get("health_listen_addr", DEFAULT_HEALTH_LISTEN_ADDR))
        force = bool(kwargs.get("force", False))

        _validate_tunnel_id(tunnel_id)
        _validate_profile_name(profile_name)
        _validate_bridge_url(bridge_url)
        _validate_health_url(tunnel_health_url)
        _validate_health_listen_addr(health_listen_addr)

        env_lines = [
            f"CONTROL_PLANE_TUNNEL_ID={tunnel_id}",
            f"TUNNEL_CLIENT_PROFILE={profile_name}",
            f"TUNNEL_CLIENT_PROFILE_DIR={context.tunnel_dir}",
            f"BRIDGE_MCP_URL={bridge_url}",
            f"HEALTH_LISTEN_ADDR={health_listen_addr}",
            f"TUNNEL_HEALTH_URL={tunnel_health_url}",
            "",
        ]
        if force or not context.tunnel_env.exists():
            context.tunnel_env.write_text("\n".join(env_lines), encoding="utf-8")
            return [str(context.tunnel_env)]
        return []

    def load_settings(
        self,
        context: TransportContext,
        *,
        env_file: Path | None = None,
    ) -> OpenAITunnelSettings:
        source = (
            context.tunnel_env if env_file is None else env_file.expanduser().resolve(strict=False)
        )
        values = _parse_env_file(source)
        tunnel_id = values.get("CONTROL_PLANE_TUNNEL_ID") or os.environ.get(
            "CONTROL_PLANE_TUNNEL_ID", ""
        )
        profile_name = values.get("TUNNEL_CLIENT_PROFILE", DEFAULT_PROFILE)
        profile_dir_value = values.get("TUNNEL_CLIENT_PROFILE_DIR")
        if profile_dir_value:
            profile_dir_path = Path(profile_dir_value).expanduser()
            if not profile_dir_path.is_absolute():
                profile_dir_path = source.parent.parent / profile_dir_path
            profile_dir = profile_dir_path.resolve(strict=False)
        else:
            profile_dir = context.tunnel_dir
        bridge_url = values.get("BRIDGE_MCP_URL", DEFAULT_BRIDGE_URL)
        tunnel_health_url = values.get("TUNNEL_HEALTH_URL", DEFAULT_TUNNEL_HEALTH_URL)
        health_listen_addr = values.get("HEALTH_LISTEN_ADDR", DEFAULT_HEALTH_LISTEN_ADDR)

        _validate_tunnel_id(tunnel_id)
        _validate_profile_name(profile_name)
        _validate_bridge_url(bridge_url)
        _validate_health_url(tunnel_health_url)
        _validate_health_listen_addr(health_listen_addr)
        return OpenAITunnelSettings(
            tunnel_id=tunnel_id,
            profile_name=profile_name,
            profile_dir=profile_dir,
            bridge_url=bridge_url,
            tunnel_health_url=tunnel_health_url,
            health_listen_addr=health_listen_addr,
            env_file=source,
        )

    def find_client(self, context: TransportContext) -> Path:
        candidates = [
            context.runtime_root / "tunnel-client.exe",
            context.runtime_root / "tunnel-client",
        ]
        discovered = shutil.which("tunnel-client")
        if discovered:
            candidates.append(Path(discovered))
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve(strict=False)
        raise LifecycleError("tunnel-client was not found beside the executable or on PATH")

    def validate_config(self, settings: OpenAITunnelSettings) -> Path:
        profile = _profile_path(settings)
        if not profile.is_file():
            raise LifecycleError(f"Tunnel profile not found: {profile}; run 'codemcp-remote init'")
        content = profile.read_text(encoding="utf-8", errors="replace")
        tunnel_pattern = re.escape(settings.tunnel_id)
        if not re.search(
            rf"(?m)^\s*tunnel_id:\s*[\"']?{tunnel_pattern}[\"']?\s*$",
            content,
        ):
            raise LifecycleError("Tunnel profile tunnel_id does not match configured tunnel_id")
        if not re.search(
            r'(?m)^\s*base_url:\s*["\']?https://(api|mtls)\.openai\.com["\']?\s*$',
            content,
        ):
            raise LifecycleError("Tunnel profile control plane is not an allowed OpenAI endpoint")
        if not re.search(
            r'(?m)^\s*api_key:\s*["\']?env:CONTROL_PLANE_API_KEY["\']?\s*$',
            content,
        ):
            raise LifecycleError("Tunnel profile must reference env:CONTROL_PLANE_API_KEY")
        if re.search(r"(?m)^\s*commands:\s*$", content):
            raise LifecycleError("stdio tunnel commands are not allowed")
        urls = re.findall(
            r'(?m)^\s*(?:-\s*)?url:\s*["\']?([^\r\n"\'\s]+)["\']?\s*$',
            content,
        )
        if urls != [settings.bridge_url]:
            raise LifecycleError(
                "Tunnel profile must contain exactly the configured Bridge MCP URL"
            )
        return profile

    def redact(self, value: str) -> str:
        redacted = _REDACT_BEARER.sub("Bearer <redacted>", value)
        redacted = _REDACT_KEY.sub(r"\1<redacted>", redacted)
        return _REDACT_SK.sub("<redacted-api-key>", redacted)

    def initialize(
        self,
        context: TransportContext,
        settings: OpenAITunnelSettings,
        *,
        secret: str,
        force: bool = False,
    ) -> Path:
        tunnel_client = self.find_client(context)
        settings.profile_dir.mkdir(parents=True, exist_ok=True)
        args = [
            str(tunnel_client),
            "init",
            "--sample",
            "sample_mcp_remote_no_auth",
            "--profile",
            settings.profile_name,
            "--profile-dir",
            str(settings.profile_dir),
            "--tunnel-id",
            settings.tunnel_id,
            "--mcp-server-url",
            settings.bridge_url,
            "--health-listen-addr",
            settings.health_listen_addr,
            "--control-plane-api-key-ref",
            f"env:{SECRET_ENV_NAME}",
        ]
        if force:
            args.append("--force")
        environment = os.environ.copy()
        environment[SECRET_ENV_NAME] = secret
        completed = subprocess.run(
            args,
            cwd=context.app_root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        if completed.returncode != 0:
            detail = self.redact((completed.stdout or "") + "\n" + (completed.stderr or ""))
            raise LifecycleError(f"tunnel-client init failed: {detail.strip()}")
        return self.validate_config(settings)

    def run(
        self,
        context: TransportContext,
        settings: OpenAITunnelSettings,
        *,
        secret: str,
        rotate_log: Callable[[Path], None],
    ) -> int:
        self.validate_config(settings)
        tunnel_client = self.find_client(context)
        environment = os.environ.copy()
        environment[SECRET_ENV_NAME] = secret
        context.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = context.log_dir / "tunnel-client.log"
        rotate_log(log_path)
        process = subprocess.Popen(
            [
                str(tunnel_client),
                "run",
                "--profile",
                settings.profile_name,
                "--profile-dir",
                str(settings.profile_dir),
                "--health.listen-addr",
                settings.health_listen_addr,
            ],
            cwd=context.app_root,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None
        with log_path.open("a", encoding="utf-8", newline="\n") as handle:
            for line in process.stdout:
                timestamp = time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    time.gmtime(),
                )
                handle.write(f"{timestamp} {self.redact(line.rstrip())}\n")
                handle.flush()
        return int(process.wait())

    def bridge_url(self, settings: OpenAITunnelSettings) -> str:
        return settings.bridge_url

    def ready_url(self, settings: OpenAITunnelSettings) -> str:
        return f"{settings.tunnel_health_url.rstrip('/')}/readyz"

    def doctor(
        self,
        context: TransportContext,
        *,
        env_file: Path | None,
        secret_available: bool,
        secret_source: str,
    ) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        try:
            settings = self.load_settings(context, env_file=env_file)
            checks["tunnel_settings"] = {
                "status": "ok",
                "profile": settings.profile_name,
            }
            try:
                profile = self.validate_config(settings)
                checks["tunnel_profile"] = {
                    "status": "ok",
                    "path": str(profile),
                }
            except LifecycleError as exc:
                checks["tunnel_profile"] = {
                    "status": "failed",
                    "error": str(exc),
                }
        except LifecycleError as exc:
            checks["tunnel_settings"] = {
                "status": "failed",
                "error": str(exc),
            }
        try:
            client = self.find_client(context)
            checks["tunnel_client"] = {
                "status": "ok",
                "path": str(client),
            }
        except LifecycleError as exc:
            checks["tunnel_client"] = {
                "status": "failed",
                "error": str(exc),
            }
        checks["api_key"] = {
            "status": "ok" if secret_available else "missing",
            "source": secret_source,
        }
        return checks


OPENAI_TUNNEL_PROVIDER = OpenAITunnelProvider()
