from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import codemcp_bridge.lifecycle as lifecycle
import codemcp_bridge.main as main_module
from codemcp_bridge.transports import LifecycleError


def _bridge_template() -> str:
    return """\
[server]
host = "127.0.0.1"
port = 46200
path = "/mcp"
transport = "streamable-http"

[storage]
data_dir = ".local"
sqlite_file = ".local/bridge.sqlite3"
log_dir = ".local/logs"

[policy]
allow_arbitrary_paths = false
allow_arbitrary_commands = false
allow_model_calls = false
require_clean_workspace = true
max_file_bytes = 1048576
max_result_bytes = 262144
mutation_lock = "per-project"

[codemcp]
worker_mode = "local"
wsl_distribution = "Ubuntu"
wsl_python = ""
startup_timeout_seconds = 30
worker_timeout_seconds = 60
shutdown_timeout_seconds = 5
"""


def _paths(tmp_path: Path, *, home: Path | None = None) -> lifecycle.RuntimePaths:
    runtime = tmp_path / "runtime"
    (runtime / "config").mkdir(parents=True)
    (runtime / "config" / "bridge.example.toml").write_text(
        _bridge_template(),
        encoding="utf-8",
    )
    return lifecycle.runtime_paths(
        runtime,
        home=tmp_path / "home" if home is None else home,
    )


def _configure_network_profile(paths: lifecycle.RuntimePaths) -> None:
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
        allowed_hosts=["MCP.EXAMPLE.COM"],
        allowed_origins=["https://chatgpt.com:443"],
    )
    lifecycle.configure_resource_auth(paths, mode="none")


def test_runtime_home_precedence_and_modern_layout(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    env_home = tmp_path / "env-home"
    cli_home = tmp_path / "cli-home"
    legacy_root = tmp_path / "legacy-root"
    environ = {
        "CODEMCP_HOME": str(env_home),
        "LOCALAPPDATA": str(tmp_path / "localappdata"),
    }

    from_environment = lifecycle.resolve_runtime_paths(
        runtime,
        default_home=runtime,
        environ=environ,
    )
    assert from_environment.home == env_home.resolve()
    assert from_environment.layout == "home"
    assert from_environment.config_dir == env_home.resolve() / "config"
    assert from_environment.data_dir == env_home.resolve() / "data"
    assert from_environment.log_dir == env_home.resolve() / "data" / "logs"
    assert from_environment.run_dir == env_home.resolve() / "data" / "runtime"
    assert from_environment.checkpoint_dir == env_home.resolve() / "data" / "checkpoints"
    assert from_environment.secret_dir == env_home.resolve() / "secrets"

    explicit = lifecycle.resolve_runtime_paths(
        runtime,
        home=cli_home,
        default_home=runtime,
        environ=environ,
    )
    assert explicit.home == cli_home.resolve()
    assert explicit.layout == "home"

    legacy = lifecycle.resolve_runtime_paths(
        runtime,
        app_root=legacy_root,
        environ=environ,
    )
    assert legacy.home == legacy_root.resolve()
    assert legacy.layout == "legacy"
    assert legacy.log_dir == legacy_root.resolve() / "logs"
    assert legacy.run_dir == legacy_root.resolve() / "run"


def test_runtime_home_uses_packaged_default_when_no_override(tmp_path: Path) -> None:
    runtime = tmp_path / "installed"

    resolved = lifecycle.resolve_runtime_paths(
        runtime,
        default_home=runtime,
        environ={},
    )

    assert resolved.home == runtime.resolve()
    assert resolved.layout == "home"
    assert resolved.config_dir == runtime.resolve() / "config"
    assert resolved.data_dir == runtime.resolve() / "data"
    assert resolved.secret_dir == runtime.resolve() / "secrets"


def test_runtime_home_rejects_relative_overrides(tmp_path: Path) -> None:
    with pytest.raises(LifecycleError, match="absolute"):
        lifecycle.resolve_runtime_home(explicit_home="relative-home")
    with pytest.raises(LifecycleError, match="absolute"):
        lifecycle.resolve_runtime_home(environ={lifecycle.CODEMCP_HOME_ENV_NAME: "relative-home"})


def test_cloudflare_public_start_requires_network_trust(tmp_path: Path, monkeypatch) -> None:
    paths = _paths(tmp_path)
    lifecycle.initialize_runtime(
        paths,
        tunnel_id="",
        transport="cloudflare",
        public_url="https://mcp.example.com/mcp",
        origin_url="http://127.0.0.1:46200/mcp",
        metrics_addr="127.0.0.1:46202",
    )
    monkeypatch.setattr(lifecycle, "load_settings", lambda *_args: object())

    with pytest.raises(LifecycleError, match=lifecycle.PUBLIC_NO_AUTH_REQUIRES_NETWORK_TRUST):
        lifecycle.start_services(paths)


def test_cloudflare_network_profile_starts_without_oauth_secret(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    _configure_network_profile(paths)
    monkeypatch.setattr(lifecycle, "load_settings", lambda *_args: object())
    monkeypatch.setattr(
        lifecycle,
        "_secret_from_runtime",
        lambda _paths, _provider=None: "transport-secret",
    )

    http_checks: list[tuple[str, object]] = []

    def fake_http_check(url: str, timeout: float = 2.0, headers=None) -> dict[str, object]:
        del timeout
        http_checks.append((url, headers))
        return {"status": "unreachable", "url": url}

    monkeypatch.setattr(
        lifecycle,
        "_http_check",
        fake_http_check,
    )

    endpoint_checks: list[tuple[str, object]] = []

    def fake_wait_endpoint(url: str, process, timeout: float, headers=None) -> dict[str, object]:
        del process, timeout
        endpoint_checks.append((url, headers))
        return {"status": "ok", "status_code": 200, "url": url}

    monkeypatch.setattr(
        lifecycle,
        "_wait_endpoint",
        fake_wait_endpoint,
    )
    monkeypatch.setattr(lifecycle, "_process_marker", lambda pid: f"marker-{pid}")
    processes: list[SimpleNamespace] = []

    def fake_popen(args, *, cwd, log_path, env):
        del cwd, log_path, env
        process = SimpleNamespace(
            pid=800 + len(processes),
            args=args,
            poll=lambda: None,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(lifecycle, "_popen_background", fake_popen)

    result = lifecycle.start_services(paths)

    assert result["status"] == "ok"
    assert processes[0].args[-2:] == ["--home", str(paths.home)]
    assert processes[1].args[-2:] == ["--home", str(paths.home)]
    assert lifecycle.RESOURCE_AUTH_SECRET_ENV_NAME not in repr(result)
    bridge_checks = [item for item in http_checks if item[0].endswith("/healthz")]
    assert bridge_checks == [("http://127.0.0.1:46200/healthz", {"Host": "mcp.example.com"})]
    assert endpoint_checks[0] == (
        "http://127.0.0.1:46200/healthz",
        {"Host": "mcp.example.com"},
    )


def test_serve_wires_network_profile_without_installing_oauth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    paths = _paths(tmp_path)
    _configure_network_profile(paths)
    captured: dict[str, object] = {}

    settings = SimpleNamespace(
        storage=SimpleNamespace(log_dir=tmp_path / "logs"),
        server=SimpleNamespace(transport="streamable-http"),
    )
    args = SimpleNamespace(
        command="serve",
        home=paths.home,
        app_root=None,
        bridge_config=None,
        projects_config=None,
        env_file=None,
    )

    class FakeService:
        async def close(self) -> None:
            return None

    class FakeServer:
        def run(self, *, transport: str) -> None:
            captured["transport"] = transport

    def fake_create_server(settings_arg, *, network_trust=None, network_resource=None):
        captured["settings"] = settings_arg
        captured["network_trust"] = network_trust
        captured["network_resource"] = network_resource
        return FakeServer(), FakeService()

    monkeypatch.setattr(main_module, "_parse_args", lambda: args)
    monkeypatch.setattr(main_module, "load_settings", lambda *_args: settings)
    monkeypatch.setattr(main_module, "configure_logging", lambda _path: None)
    monkeypatch.setattr(main_module, "create_server", fake_create_server)

    assert main_module.main() == 0
    assert captured["settings"] is settings
    assert captured["network_trust"].mode == "cloudflare-chatgpt"
    assert captured["network_resource"] == "https://mcp.example.com/mcp"
    assert captured["transport"] == "streamable-http"
