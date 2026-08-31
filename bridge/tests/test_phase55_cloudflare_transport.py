from __future__ import annotations

import json
import os
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

import codemcp_bridge.lifecycle as lifecycle
import codemcp_bridge.main as main_module
import codemcp_bridge.transports.cloudflare as cloudflare
from codemcp_bridge.transports import (
    CLOUDFLARE_TUNNEL_PROVIDER,
    OPENAI_TUNNEL_PROVIDER,
    LifecycleError,
    TransportContext,
    get_transport_provider,
)


def _context(tmp_path: Path) -> TransportContext:
    runtime_root = tmp_path / "runtime"
    app_root = tmp_path / "app"
    config_dir = app_root / "config"
    log_dir = app_root / "logs"
    tunnel_dir = app_root / "tunnel"
    secret_dir = app_root / "secrets"
    for path in (runtime_root, app_root, config_dir, log_dir, tunnel_dir, secret_dir):
        path.mkdir(parents=True, exist_ok=True)
    return TransportContext(
        runtime_root=runtime_root,
        bundled_runtime_root=runtime_root / ".codemcp-runtime",
        app_root=app_root,
        config_dir=config_dir,
        log_dir=log_dir,
        tunnel_dir=tunnel_dir,
        secret_file=secret_dir / "cloudflare-tunnel-token.dpapi",
        tunnel_env=config_dir / "cloudflare.env",
    )


def _settings(context: TransportContext):
    CLOUDFLARE_TUNNEL_PROVIDER.initialize_config(
        context,
        public_url="https://mcp.example.com/mcp",
        origin_url="http://127.0.0.1:46200/mcp",
        metrics_addr="127.0.0.1:46202",
    )
    return CLOUDFLARE_TUNNEL_PROVIDER.load_settings(context)


def test_transport_registry_contains_openai_and_cloudflare() -> None:
    assert get_transport_provider("openai-tunnel") is OPENAI_TUNNEL_PROVIDER
    assert get_transport_provider("cloudflare") is CLOUDFLARE_TUNNEL_PROVIDER
    with pytest.raises(LifecycleError, match="unsupported remote transport"):
        get_transport_provider("unknown")


def test_cloudflare_config_is_non_secret_and_fail_closed(tmp_path: Path) -> None:
    context = _context(tmp_path)
    settings = _settings(context)

    assert settings.public_url == "https://mcp.example.com/mcp"
    assert settings.origin_url == "http://127.0.0.1:46200/mcp"
    assert settings.metrics_addr == "127.0.0.1:46202"
    assert CLOUDFLARE_TUNNEL_PROVIDER.ready_url(settings) == "http://127.0.0.1:46202/ready"
    text = context.tunnel_env.read_text(encoding="utf-8")
    assert "TUNNEL_TOKEN" not in text

    with pytest.raises(LifecycleError, match="HTTPS /mcp"):
        CLOUDFLARE_TUNNEL_PROVIDER.initialize_config(
            context,
            public_url="http://mcp.example.com/mcp",
            force=True,
        )
    with pytest.raises(LifecycleError, match="127.0.0.1"):
        CLOUDFLARE_TUNNEL_PROVIDER.initialize_config(
            context,
            public_url="https://mcp.example.com/mcp",
            origin_url="http://192.168.1.10:46200/mcp",
            force=True,
        )
    with pytest.raises(LifecycleError, match="metrics address"):
        CLOUDFLARE_TUNNEL_PROVIDER.initialize_config(
            context,
            public_url="https://mcp.example.com/mcp",
            metrics_addr="0.0.0.0:46202",
            force=True,
        )


def test_cloudflare_config_rejects_plaintext_token(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.tunnel_env.write_text(
        "\n".join(
            [
                "CLOUDFLARE_PUBLIC_URL=https://mcp.example.com/mcp",
                "CLOUDFLARE_ORIGIN_URL=http://127.0.0.1:46200/mcp",
                "CLOUDFLARE_METRICS_ADDR=127.0.0.1:46202",
                "TUNNEL_TOKEN=must-not-be-here",
                "",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(LifecycleError, match="must never be stored"):
        CLOUDFLARE_TUNNEL_PROVIDER.load_settings(context)


def test_cloudflared_discovery_prefers_bundled_binary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    executable_name = "cloudflared.exe" if os.name == "nt" else "cloudflared"
    bundled = context.bundled_runtime_root / "bin" / executable_name
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"placeholder")
    legacy = context.runtime_root / executable_name
    legacy.write_bytes(b"legacy")
    monkeypatch.setattr(cloudflare.shutil, "which", lambda _name: str(tmp_path / "other.exe"))

    assert CLOUDFLARE_TUNNEL_PROVIDER.find_client(context) == bundled.resolve()


@pytest.mark.skipif(os.name != "nt", reason="bundled Windows cloudflared pin is Windows-specific")
def test_bundled_cloudflared_sha256_mismatch_fails_closed(tmp_path: Path) -> None:
    context = _context(tmp_path)
    bundled = context.runtime_root / "cloudflared.exe"
    bundled.write_bytes(b"tampered-cloudflared")

    with pytest.raises(LifecycleError, match="SHA-256"):
        CLOUDFLARE_TUNNEL_PROVIDER.client_version(context)


def test_bundled_macos_cloudflared_version_is_pinned(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    bundled = context.bundled_runtime_root / "bin" / "cloudflared"
    monkeypatch.setattr(cloudflare.sys, "platform", "darwin")
    monkeypatch.setattr(CLOUDFLARE_TUNNEL_PROVIDER, "find_client", lambda _context: bundled)
    monkeypatch.setattr(
        cloudflare.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="cloudflared version 2026.7.3 (built 2026-08-01)\n",
            stderr="",
        ),
    )

    assert CLOUDFLARE_TUNNEL_PROVIDER.client_version(context) == "2026.7.3"

    monkeypatch.setattr(
        cloudflare.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="cloudflared version 2026.8.0 (built 2026-08-30)\n",
            stderr="",
        ),
    )
    with pytest.raises(LifecycleError, match="pinned release"):
        CLOUDFLARE_TUNNEL_PROVIDER.client_version(context)


def test_cloudflared_missing_and_bad_version_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    monkeypatch.setattr(cloudflare.shutil, "which", lambda _name: None)
    with pytest.raises(LifecycleError, match="cloudflared was not found"):
        CLOUDFLARE_TUNNEL_PROVIDER.find_client(context)

    fake_client = tmp_path / "cloudflared.exe"
    monkeypatch.setattr(CLOUDFLARE_TUNNEL_PROVIDER, "find_client", lambda _context: fake_client)
    monkeypatch.setattr(
        cloudflare.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="unexpected version output",
            stderr="",
        ),
    )
    with pytest.raises(LifecycleError, match="version output is not recognized"):
        CLOUDFLARE_TUNNEL_PROVIDER.client_version(context)


def test_cloudflared_run_uses_environment_token_and_fixed_argv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    settings = _settings(context)
    fake_client = tmp_path / "cloudflared.exe"
    captured: dict[str, object] = {}

    monkeypatch.setattr(CLOUDFLARE_TUNNEL_PROVIDER, "find_client", lambda _context: fake_client)
    monkeypatch.setattr(
        CLOUDFLARE_TUNNEL_PROVIDER,
        "client_version",
        lambda _context: "2026.8.0",
    )

    fake_process = SimpleNamespace(
        stdout=["TUNNEL_TOKEN=supersecret\n", "connected\n"],
        wait=lambda: 0,
    )

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return fake_process

    def fake_attach(process):
        captured["job_process"] = process
        return 12345

    def fake_close(handle):
        captured["closed_job_handle"] = handle

    monkeypatch.setattr(cloudflare.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(cloudflare, "_attach_kill_on_close_job", fake_attach)
    monkeypatch.setattr(cloudflare, "_close_windows_handle", fake_close)
    rotated: list[Path] = []

    result = CLOUDFLARE_TUNNEL_PROVIDER.run(
        context,
        settings,
        secret="supersecret",
        rotate_log=rotated.append,
    )

    assert result == 0
    assert captured["args"] == [
        str(fake_client),
        "tunnel",
        "--no-autoupdate",
        "--loglevel",
        "info",
        "--metrics",
        "127.0.0.1:46202",
        "run",
    ]
    assert "supersecret" not in " ".join(captured["args"])
    assert captured["env"]["TUNNEL_TOKEN"] == "supersecret"
    assert captured["job_process"] is fake_process
    assert captured["closed_job_handle"] == 12345
    assert rotated == [context.log_dir / "cloudflared.log"]
    log_text = (context.log_dir / "cloudflared.log").read_text(encoding="utf-8")
    assert "supersecret" not in log_text
    assert "TUNNEL_TOKEN=<redacted>" in log_text


def test_cloudflare_doctor_reports_version_without_secret_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    _settings(context)
    fake_client = tmp_path / "cloudflared.exe"
    monkeypatch.setattr(CLOUDFLARE_TUNNEL_PROVIDER, "find_client", lambda _context: fake_client)
    monkeypatch.setattr(
        CLOUDFLARE_TUNNEL_PROVIDER,
        "client_version",
        lambda _context: "2026.8.0",
    )

    checks = CLOUDFLARE_TUNNEL_PROVIDER.doctor(
        context,
        env_file=None,
        secret_available=True,
        secret_source="windows-dpapi",
    )

    assert checks["cloudflare_settings"]["status"] == "ok"
    assert checks["cloudflared"] == {
        "status": "ok",
        "path": str(fake_client),
        "version": "2026.8.0",
    }
    assert checks["tunnel_token"] == {
        "status": "ok",
        "source": "windows-dpapi",
    }


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI acceptance runs on Windows")
def test_cloudflare_tunnel_token_uses_provider_specific_dpapi_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    paths = lifecycle.runtime_paths(runtime, app_root=tmp_path / "app")
    monkeypatch.setenv("TUNNEL_TOKEN", "phase55-cloudflare-secret")
    monkeypatch.setattr(lifecycle, "_dpapi_protect", lambda value: b"encrypted-token")
    monkeypatch.setattr(
        lifecycle,
        "_dpapi_unprotect",
        lambda value: b"phase55-cloudflare-secret",
    )

    assert lifecycle.store_transport_secret_from_environment(
        paths,
        provider=CLOUDFLARE_TUNNEL_PROVIDER,
    )
    secret_path = paths.secret_dir / "cloudflare-tunnel-token.dpapi"
    assert secret_path.read_bytes() == b"encrypted-token"
    assert not paths.secret_file.exists()

    monkeypatch.delenv("TUNNEL_TOKEN")
    assert (
        lifecycle._secret_from_runtime(
            paths,
            provider=CLOUDFLARE_TUNNEL_PROVIDER,
        )
        == "phase55-cloudflare-secret"
    )


def _lifecycle_paths(tmp_path: Path) -> lifecycle.RuntimePaths:
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


def test_versioned_transport_config_selects_cloudflare_and_preserves_legacy_default(
    tmp_path: Path,
) -> None:
    paths = _lifecycle_paths(tmp_path)
    provider, source = lifecycle.load_transport_provider(paths)
    assert provider is OPENAI_TUNNEL_PROVIDER
    assert source == "legacy-default"

    result = lifecycle.initialize_runtime(
        paths,
        tunnel_id="",
        transport="cloudflare",
        public_url="https://mcp.example.com/mcp",
        origin_url="http://127.0.0.1:46200/mcp",
        metrics_addr="127.0.0.1:46202",
    )

    assert result["transport"] == "cloudflare"
    remote = (paths.config_dir / "remote.toml").read_text(encoding="utf-8")
    assert "version = 1" in remote
    assert 'transport = "cloudflare"' in remote
    assert "TUNNEL_TOKEN" not in paths.tunnel_env.read_text(encoding="utf-8")
    provider, source = lifecycle.load_transport_provider(paths)
    assert provider is CLOUDFLARE_TUNNEL_PROVIDER
    assert source == "config"
    status = lifecycle.status_services(paths)
    assert status["status"] == "stopped"
    assert status["transport"] == "cloudflare"
    assert status["transport_source"] == "config"

    with pytest.raises(LifecycleError, match="requires --force"):
        lifecycle.initialize_runtime(
            paths,
            tunnel_id="tunnel_12345678",
            transport="openai-tunnel",
        )


def test_initialize_runtime_keeps_cloudflare_origin_and_bridge_endpoint_aligned(
    tmp_path: Path,
) -> None:
    paths = _lifecycle_paths(tmp_path)
    (paths.runtime_root / "config" / "bridge.example.toml").write_text(
        "[server]\n"
        'host = "127.0.0.1"\n'
        "port = 46200\n"
        'path = "/mcp"\n'
        'transport = "streamable-http"\n'
        "\n"
        "[storage]\n"
        'data_dir = ".local"\n'
        'sqlite_file = ".local/bridge.sqlite3"\n'
        'log_dir = ".local/logs"\n',
        encoding="utf-8",
    )

    lifecycle.initialize_runtime(
        paths,
        tunnel_id="",
        transport="cloudflare",
        public_url="https://mcp.example.com/mcp",
        origin_url="http://127.0.0.1:47210/mcp",
        metrics_addr="127.0.0.1:47212",
    )

    bridge = tomllib.loads(paths.bridge_config.read_text(encoding="utf-8"))
    cloudflare_settings = CLOUDFLARE_TUNNEL_PROVIDER.load_settings(
        lifecycle._transport_context(paths)
    )
    assert bridge["server"] == {
        "host": "127.0.0.1",
        "port": 47210,
        "path": "/mcp",
        "transport": "streamable-http",
    }
    assert cloudflare_settings.origin_url == "http://127.0.0.1:47210/mcp"


def test_cloudflare_status_requires_owned_and_healthy_provider_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _lifecycle_paths(tmp_path)
    lifecycle.initialize_runtime(
        paths,
        tunnel_id="",
        transport="cloudflare",
        public_url="https://mcp.example.com/mcp",
        origin_url="http://127.0.0.1:46200/mcp",
        metrics_addr="127.0.0.1:46202",
    )
    lifecycle.configure_network_trust(
        paths,
        mode="cloudflare-chatgpt",
        allowed_hosts=["mcp.example.com"],
    )
    lifecycle.configure_resource_auth(paths, mode="none")
    paths.state_file.parent.mkdir(parents=True, exist_ok=True)
    paths.state_file.write_text(
        json.dumps(
            {
                "version": 1,
                "transport": "cloudflare",
                "bridge_pid": 700,
                "tunnel_pid": 701,
                "bridge_process_marker": "bridge-marker",
                "tunnel_process_marker": "tunnel-marker",
                "bridge_health_url": "http://127.0.0.1:46200/healthz",
                "tunnel_ready_url": "http://127.0.0.1:46202/ready",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(lifecycle, "_matches_process_marker", lambda _pid, _marker: True)

    http_checks: list[tuple[str, object]] = []

    def fake_http_check(
        url: str,
        timeout: float = 2.0,
        headers=None,
    ) -> dict[str, object]:
        del timeout
        http_checks.append((url, headers))
        return {
            "status": "ok" if url.endswith("/healthz") else "unreachable",
            "url": url,
        }

    monkeypatch.setattr(lifecycle, "_http_check", fake_http_check)

    status = lifecycle.status_services(paths)

    assert status["status"] == "degraded"
    assert status["transport"] == "cloudflare"
    assert status["bridge"]["owned"] is True
    assert status["bridge"]["health"]["status"] == "ok"
    assert status["tunnel"]["owned"] is True
    assert status["tunnel"]["health"]["status"] == "unreachable"
    assert http_checks[0] == (
        "http://127.0.0.1:46200/healthz",
        {"Host": "mcp.example.com"},
    )


def test_cloudflare_start_replaces_degraded_supervisor_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _lifecycle_paths(tmp_path)
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
    monkeypatch.setenv(lifecycle.RESOURCE_AUTH_SECRET_ENV_NAME, "auth-secret")
    monkeypatch.setattr(
        lifecycle,
        "_secret_from_runtime",
        lambda _paths, _provider=None: "transport-secret",
    )
    paths.state_file.parent.mkdir(parents=True, exist_ok=True)
    paths.state_file.write_text(
        json.dumps(
            {
                "version": 1,
                "transport": "cloudflare",
                "bridge_pid": 100,
                "tunnel_pid": 101,
                "bridge_process_marker": "stale-bridge",
                "tunnel_process_marker": "stale-tunnel",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(lifecycle, "status_services", lambda _paths: {"status": "degraded"})
    monkeypatch.setattr(lifecycle, "load_settings", lambda _bridge, _projects: object())
    monkeypatch.setattr(
        lifecycle,
        "_http_check",
        lambda url, timeout=2.0: {"status": "unreachable", "url": url},
    )

    processes: list[SimpleNamespace] = []

    def fake_popen(args, *, cwd, log_path, env):
        del cwd, log_path, env
        process = SimpleNamespace(
            pid=700 + len(processes),
            args=args,
            poll=lambda: None,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(lifecycle, "_popen_background", fake_popen)
    monkeypatch.setattr(
        lifecycle,
        "_wait_endpoint",
        lambda url, process, timeout: {"status": "ok", "status_code": 200, "url": url},
    )
    monkeypatch.setattr(lifecycle, "_process_marker", lambda pid: f"marker-{pid}")

    result = lifecycle.start_services(paths)

    assert result["status"] == "ok"
    state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    assert state["transport"] == "cloudflare"
    assert state["bridge_pid"] == 700
    assert state["tunnel_pid"] == 701
    assert state["bridge_process_marker"] == "marker-700"
    assert state["tunnel_process_marker"] == "marker-701"
    assert processes[1].args[-2:] == ["--app-root", str(paths.app_root)]


def test_cli_accepts_cloudflare_transport_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "codemcp-remote",
            "init",
            "--transport",
            "cloudflare",
            "--public-url",
            "https://mcp.example.com/mcp",
            "--origin-url",
            "http://127.0.0.1:46200/mcp",
            "--metrics-addr",
            "127.0.0.1:46202",
            "--store-transport-secret",
        ],
    )

    args = main_module._parse_args()

    assert args.transport == "cloudflare"
    assert args.public_url == "https://mcp.example.com/mcp"
    assert args.origin_url == "http://127.0.0.1:46200/mcp"
    assert args.metrics_addr == "127.0.0.1:46202"
    assert args.store_transport_secret is True
