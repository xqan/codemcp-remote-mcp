"""Cloudflare Tunnel remote transport provider."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .base import LifecycleError, TransportContext

DEFAULT_PUBLIC_URL = ""
DEFAULT_ORIGIN_URL = "http://127.0.0.1:46200/mcp"
DEFAULT_METRICS_ADDR = "127.0.0.1:46202"
BUNDLED_WINDOWS_AMD64_VERSION = "2026.7.3"
BUNDLED_WINDOWS_AMD64_SHA256 = "8635da433b6df8194746e88ed9d2589566c20e38bfc2a80e431a348b7c765841"
BUNDLED_MACOS_VERSION = "2026.7.3"
SECRET_ENV_NAME = "TUNNEL_TOKEN"
SECRET_FILE_NAME = "cloudflare-tunnel-token.dpapi"
ALLOWED_ENV_NAMES = {
    "CLOUDFLARE_PUBLIC_URL",
    "CLOUDFLARE_ORIGIN_URL",
    "CLOUDFLARE_METRICS_ADDR",
}
_VERSION_PATTERN = re.compile(r"\bcloudflared version (\d{4}\.\d+\.\d+)\b", re.IGNORECASE)
_REDACT_ASSIGNMENT = re.compile(
    r"(?i)((?:TUNNEL_TOKEN|CLOUDFLARE_TUNNEL_TOKEN|AUTHORIZATION|"
    r"ACCESS_TOKEN|REFRESH_TOKEN|TOKEN)\s*[:=]\s*)([^\s,;]+)"
)
_REDACT_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")

_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS = 9


class _JobObjectBasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("per_process_user_time_limit", ctypes.c_longlong),
        ("per_job_user_time_limit", ctypes.c_longlong),
        ("limit_flags", wintypes.DWORD),
        ("minimum_working_set_size", ctypes.c_size_t),
        ("maximum_working_set_size", ctypes.c_size_t),
        ("active_process_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority_class", wintypes.DWORD),
        ("scheduling_class", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [
        ("read_operation_count", ctypes.c_ulonglong),
        ("write_operation_count", ctypes.c_ulonglong),
        ("other_operation_count", ctypes.c_ulonglong),
        ("read_transfer_count", ctypes.c_ulonglong),
        ("write_transfer_count", ctypes.c_ulonglong),
        ("other_transfer_count", ctypes.c_ulonglong),
    ]


class _JobObjectExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("basic_limit_information", _JobObjectBasicLimitInformation),
        ("io_info", _IoCounters),
        ("process_memory_limit", ctypes.c_size_t),
        ("job_memory_limit", ctypes.c_size_t),
        ("peak_process_memory_used", ctypes.c_size_t),
        ("peak_job_memory_used", ctypes.c_size_t),
    ]


def _attach_kill_on_close_job(process: subprocess.Popen[Any]) -> int | None:
    """Bind a Windows child tree to the supervisor lifetime."""

    if os.name != "nt":
        return None

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    job_handle = kernel32.CreateJobObjectW(None, None)
    if not job_handle:
        raise LifecycleError(
            f"failed to create Windows Job Object: winerror={ctypes.get_last_error()}"
        )

    information = _JobObjectExtendedLimitInformation()
    information.basic_limit_information.limit_flags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(
        job_handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION_CLASS,
        ctypes.byref(information),
        ctypes.sizeof(information),
    ):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job_handle)
        raise LifecycleError(f"failed to configure Windows Job Object: winerror={error}")

    process_handle = getattr(process, "_handle", None)
    if process_handle is None or not kernel32.AssignProcessToJobObject(job_handle, process_handle):
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job_handle)
        raise LifecycleError(
            f"failed to bind cloudflared to the supervisor Job Object: winerror={error}"
        )
    return int(job_handle)


def _close_windows_handle(handle: int | None) -> None:
    if handle is None or os.name != "nt":
        return
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle(handle)


@dataclass(frozen=True, slots=True)
class CloudflareTunnelSettings:
    public_url: str
    origin_url: str
    metrics_addr: str
    env_file: Path


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        raise LifecycleError(f"Cloudflare transport environment file not found: {path}")
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
            raise LifecycleError(f"{name} is not an allowed Cloudflare transport setting")
        if len(value) >= 2 and value[:1] == value[-1:] and value.startswith(("'", '"')):
            value = value[1:-1]
        elif "'" in value or '"' in value:
            raise LifecycleError(f"unterminated quoted value at line {line_number}")
        if value:
            values[name] = os.path.expandvars(value)
    return values


def _validate_public_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path.rstrip("/") != "/mcp"
        or parsed.query
        or parsed.fragment
    ):
        raise LifecycleError("Cloudflare public MCP URL must be an HTTPS /mcp endpoint")


def _validate_origin_url(value: str) -> None:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path.rstrip("/") != "/mcp"
        or parsed.query
        or parsed.fragment
    ):
        raise LifecycleError("Cloudflare origin URL must be an HTTP(S) /mcp endpoint on 127.0.0.1")


def _validate_metrics_addr(value: str) -> None:
    match = re.fullmatch(r"127\.0\.0\.1:(\d+)", value)
    if match is None or not 1 <= int(match.group(1)) <= 65535:
        raise LifecycleError("Cloudflare metrics address must bind to 127.0.0.1:<port>")


class CloudflareTunnelProvider:
    """Remotely managed Cloudflare Tunnel provider."""

    provider_id = "cloudflare"
    secret_env_name = SECRET_ENV_NAME
    secret_file_name = SECRET_FILE_NAME

    def initialize_config(
        self,
        context: TransportContext,
        **kwargs: Any,
    ) -> list[str]:
        public_url = str(kwargs.get("public_url", DEFAULT_PUBLIC_URL))
        origin_url = str(kwargs.get("origin_url", DEFAULT_ORIGIN_URL))
        metrics_addr = str(kwargs.get("metrics_addr", DEFAULT_METRICS_ADDR))
        force = bool(kwargs.get("force", False))

        _validate_public_url(public_url)
        _validate_origin_url(origin_url)
        _validate_metrics_addr(metrics_addr)

        env_lines = [
            f"CLOUDFLARE_PUBLIC_URL={public_url}",
            f"CLOUDFLARE_ORIGIN_URL={origin_url}",
            f"CLOUDFLARE_METRICS_ADDR={metrics_addr}",
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
    ) -> CloudflareTunnelSettings:
        source = (
            context.tunnel_env if env_file is None else env_file.expanduser().resolve(strict=False)
        )
        values = _parse_env_file(source)
        public_url = values.get("CLOUDFLARE_PUBLIC_URL", DEFAULT_PUBLIC_URL)
        origin_url = values.get("CLOUDFLARE_ORIGIN_URL", DEFAULT_ORIGIN_URL)
        metrics_addr = values.get("CLOUDFLARE_METRICS_ADDR", DEFAULT_METRICS_ADDR)

        _validate_public_url(public_url)
        _validate_origin_url(origin_url)
        _validate_metrics_addr(metrics_addr)
        return CloudflareTunnelSettings(
            public_url=public_url,
            origin_url=origin_url,
            metrics_addr=metrics_addr,
            env_file=source,
        )

    def validate_config(self, settings: CloudflareTunnelSettings) -> Path:
        _validate_public_url(settings.public_url)
        _validate_origin_url(settings.origin_url)
        _validate_metrics_addr(settings.metrics_addr)
        if not settings.env_file.is_file():
            raise LifecycleError(
                f"Cloudflare transport environment file not found: {settings.env_file}"
            )
        content = settings.env_file.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(?mi)^\s*TUNNEL_TOKEN\s*=", content):
            raise LifecycleError(f"{SECRET_ENV_NAME} must never be stored in {settings.env_file}")
        return settings.env_file

    def find_client(self, context: TransportContext) -> Path:
        executable_name = "cloudflared.exe" if os.name == "nt" else "cloudflared"
        candidates = [
            context.bundled_runtime_root / "bin" / executable_name,
            context.runtime_root / executable_name,
        ]
        if os.name == "nt":
            candidates.append(context.runtime_root / "cloudflared")
        discovered = shutil.which("cloudflared")
        if discovered:
            candidates.append(Path(discovered))
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve(strict=False)
        raise LifecycleError(
            "cloudflared was not found in the bundled runtime, beside the executable, or on PATH"
        )

    def client_version(self, context: TransportContext) -> str:
        client = self.find_client(context)
        bundled_windows = os.name == "nt" and client == (
            context.bundled_runtime_root / "bin" / "cloudflared.exe"
        ).resolve(strict=False)
        legacy_windows = os.name == "nt" and client == (
            context.runtime_root / "cloudflared.exe"
        ).resolve(strict=False)
        bundled_macos = sys.platform == "darwin" and client == (
            context.bundled_runtime_root / "bin" / "cloudflared"
        ).resolve(strict=False)
        pinned_windows = bundled_windows or legacy_windows
        if pinned_windows:
            digest = hashlib.sha256(client.read_bytes()).hexdigest()
            if digest.lower() != BUNDLED_WINDOWS_AMD64_SHA256:
                raise LifecycleError(
                    "bundled cloudflared SHA-256 does not match the pinned release"
                )
        try:
            completed = subprocess.run(
                [str(client), "--version"],
                cwd=context.app_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LifecycleError(f"cloudflared version check failed: {exc}") from exc
        if completed.returncode != 0:
            detail = self.redact((completed.stdout or "") + "\n" + (completed.stderr or ""))
            raise LifecycleError(
                f"cloudflared version check failed with exit code {completed.returncode}: "
                f"{detail.strip()}"
            )
        output = (completed.stdout or "") + "\n" + (completed.stderr or "")
        match = _VERSION_PATTERN.search(output)
        if match is None:
            raise LifecycleError("cloudflared version output is not recognized")
        version = match.group(1)
        if pinned_windows and version != BUNDLED_WINDOWS_AMD64_VERSION:
            raise LifecycleError("bundled cloudflared version does not match the pinned release")
        if bundled_macos and version != BUNDLED_MACOS_VERSION:
            raise LifecycleError("bundled cloudflared version does not match the pinned release")
        return version

    def redact(self, value: str) -> str:
        redacted = _REDACT_BEARER.sub("Bearer <redacted>", value)
        return _REDACT_ASSIGNMENT.sub(r"\1<redacted>", redacted)

    def initialize(
        self,
        context: TransportContext,
        settings: CloudflareTunnelSettings,
        *,
        secret: str,
        force: bool = False,
    ) -> Path:
        del force
        if not secret:
            raise LifecycleError(f"{SECRET_ENV_NAME} is unavailable")
        config = self.validate_config(settings)
        self.client_version(context)
        return config

    def run(
        self,
        context: TransportContext,
        settings: CloudflareTunnelSettings,
        *,
        secret: str,
        rotate_log: Callable[[Path], None],
    ) -> int:
        if not secret:
            raise LifecycleError(f"{SECRET_ENV_NAME} is unavailable")
        self.validate_config(settings)
        client = self.find_client(context)
        self.client_version(context)
        environment = os.environ.copy()
        environment[SECRET_ENV_NAME] = secret
        context.log_dir.mkdir(parents=True, exist_ok=True)
        log_path = context.log_dir / "cloudflared.log"
        rotate_log(log_path)
        process = subprocess.Popen(
            [
                str(client),
                "tunnel",
                "--no-autoupdate",
                "--loglevel",
                "info",
                "--metrics",
                settings.metrics_addr,
                "run",
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
        job_handle: int | None = None
        try:
            job_handle = _attach_kill_on_close_job(process)
            assert process.stdout is not None
            with log_path.open("a", encoding="utf-8", newline="\n") as handle:
                for line in process.stdout:
                    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    handle.write(f"{timestamp} {self.redact(line.rstrip())}\n")
                    handle.flush()
            return int(process.wait())
        except BaseException:
            if process.poll() is None:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            raise
        finally:
            _close_windows_handle(job_handle)

    def bridge_url(self, settings: CloudflareTunnelSettings) -> str:
        return settings.origin_url

    def ready_url(self, settings: CloudflareTunnelSettings) -> str:
        return f"http://{settings.metrics_addr}/ready"

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
            config = self.validate_config(settings)
            checks["cloudflare_settings"] = {
                "status": "ok",
                "path": str(config),
                "public_url": settings.public_url,
                "origin_url": settings.origin_url,
                "metrics_addr": settings.metrics_addr,
            }
        except LifecycleError as exc:
            checks["cloudflare_settings"] = {"status": "failed", "error": str(exc)}
        try:
            client = self.find_client(context)
            version = self.client_version(context)
            checks["cloudflared"] = {
                "status": "ok",
                "path": str(client),
                "version": version,
            }
        except LifecycleError as exc:
            checks["cloudflared"] = {"status": "failed", "error": str(exc)}
        checks["tunnel_token"] = {
            "status": "ok" if secret_available else "missing",
            "source": secret_source,
        }
        return checks


CLOUDFLARE_TUNNEL_PROVIDER = CloudflareTunnelProvider()
