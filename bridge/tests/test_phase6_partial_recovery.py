from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import codemcp_bridge.lifecycle as lifecycle
from codemcp_bridge.lifecycle import (
    initialize_runtime,
    load_tunnel_settings,
    runtime_paths,
    start_services,
)


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


def test_start_services_recovers_dead_tunnel_without_restarting_owned_healthy_bridge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "config").mkdir(parents=True)
    (runtime / "config" / "bridge.example.toml").write_text(_bridge_template(), encoding="utf-8")
    paths = runtime_paths(runtime, app_root=tmp_path / "app")
    initialize_runtime(paths, tunnel_id="tunnel_12345678")
    settings = load_tunnel_settings(paths)
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    (settings.profile_dir / "codemcp-bridge.yaml").write_text(
        """\
tunnel_id: tunnel_12345678
control_plane:
  base_url: https://api.openai.com
  api_key: env:CONTROL_PLANE_API_KEY
server_urls:
  - url: http://127.0.0.1:46200/mcp
""",
        encoding="utf-8",
    )
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.state_file.write_text(
        json.dumps(
            {
                "version": 1,
                "transport": "openai-tunnel",
                "bridge_pid": 123,
                "tunnel_pid": 456,
                "bridge_process_marker": "bridge-marker",
                "tunnel_process_marker": "dead-tunnel-marker",
                "bridge_config": str(paths.bridge_config),
                "projects_config": str(paths.projects_config),
                "env_file": str(settings.env_file),
                "bridge_health_url": "http://127.0.0.1:46200/healthz",
                "tunnel_ready_url": "http://127.0.0.1:46201/readyz",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        lifecycle,
        "status_services",
        lambda runtime: {
            "status": "degraded",
            "bridge": {
                "pid": 123,
                "owned": True,
                "health": {"status": "ok", "url": "http://127.0.0.1:46200/healthz"},
            },
            "tunnel": {
                "pid": 456,
                "owned": False,
                "health": {"status": "unreachable", "url": "http://127.0.0.1:46201/readyz"},
            },
        },
    )
    monkeypatch.setattr(
        lifecycle,
        "_http_check",
        lambda url, timeout=2.0, headers=None: {"status": "unreachable", "url": url},
    )
    monkeypatch.setattr(
        lifecycle,
        "_secret_from_runtime",
        lambda runtime, provider=None: "secret",
    )

    processes: list[SimpleNamespace] = []

    def fake_popen(args, *, cwd, log_path, env):
        process = SimpleNamespace(pid=789, args=args, poll=lambda: None)
        processes.append(process)
        return process

    monkeypatch.setattr(lifecycle, "_popen_background", fake_popen)
    monkeypatch.setattr(
        lifecycle,
        "_wait_endpoint",
        lambda url, process, timeout: {"status": "ok", "status_code": 200, "url": url},
    )
    monkeypatch.setattr(lifecycle, "_process_marker", lambda pid: f"marker-{pid}")

    result = start_services(paths)

    assert result["status"] == "ok"
    assert result["services"]["bridge"]["status"] == "reused"
    assert result["services"]["bridge"]["pid"] == 123
    assert len(processes) == 1
    assert "_tunnel" in processes[0].args
    state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    assert state["bridge_pid"] == 123
    assert state["bridge_process_marker"] == "bridge-marker"
    assert state["tunnel_pid"] == 789
    assert state["tunnel_process_marker"] == "marker-789"


def test_start_services_recovers_dead_bridge_without_restarting_owned_healthy_tunnel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    (runtime / "config").mkdir(parents=True)
    (runtime / "config" / "bridge.example.toml").write_text(_bridge_template(), encoding="utf-8")
    paths = runtime_paths(runtime, app_root=tmp_path / "app")
    initialize_runtime(paths, tunnel_id="tunnel_12345678")
    settings = load_tunnel_settings(paths)
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    (settings.profile_dir / "codemcp-bridge.yaml").write_text(
        """\
tunnel_id: tunnel_12345678
control_plane:
  base_url: https://api.openai.com
  api_key: env:CONTROL_PLANE_API_KEY
server_urls:
  - url: http://127.0.0.1:46200/mcp
""",
        encoding="utf-8",
    )
    paths.run_dir.mkdir(parents=True, exist_ok=True)
    paths.state_file.write_text(
        json.dumps(
            {
                "version": 1,
                "transport": "openai-tunnel",
                "bridge_pid": 123,
                "tunnel_pid": 456,
                "bridge_process_marker": "dead-bridge-marker",
                "tunnel_process_marker": "tunnel-marker",
                "bridge_config": str(paths.bridge_config),
                "projects_config": str(paths.projects_config),
                "env_file": str(settings.env_file),
                "bridge_health_url": "http://127.0.0.1:46200/healthz",
                "tunnel_ready_url": "http://127.0.0.1:46201/readyz",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        lifecycle,
        "status_services",
        lambda runtime: {
            "status": "degraded",
            "bridge": {
                "pid": 123,
                "owned": False,
                "health": {
                    "status": "unreachable",
                    "url": "http://127.0.0.1:46200/healthz",
                },
            },
            "tunnel": {
                "pid": 456,
                "owned": True,
                "health": {"status": "ok", "url": "http://127.0.0.1:46201/readyz"},
            },
        },
    )
    monkeypatch.setattr(
        lifecycle,
        "_http_check",
        lambda url, timeout=2.0, headers=None: {"status": "unreachable", "url": url},
    )
    monkeypatch.setattr(
        lifecycle,
        "_secret_from_runtime",
        lambda runtime, provider=None: "secret",
    )

    processes: list[SimpleNamespace] = []

    def fake_popen(args, *, cwd, log_path, env):
        process = SimpleNamespace(pid=789, args=args, poll=lambda: None)
        processes.append(process)
        return process

    monkeypatch.setattr(lifecycle, "_popen_background", fake_popen)
    monkeypatch.setattr(
        lifecycle,
        "_wait_endpoint",
        lambda url, process, timeout: {"status": "ok", "status_code": 200, "url": url},
    )
    monkeypatch.setattr(lifecycle, "_process_marker", lambda pid: f"marker-{pid}")

    result = start_services(paths)

    assert result["status"] == "ok"
    assert result["services"]["bridge"]["status"] == "started"
    assert result["services"]["bridge"]["pid"] == 789
    assert result["services"]["tunnel"]["status"] == "reused"
    assert result["services"]["tunnel"]["pid"] == 456
    assert len(processes) == 1
    assert "serve" in processes[0].args
    assert "_tunnel" not in processes[0].args
    state = json.loads(paths.state_file.read_text(encoding="utf-8"))
    assert state["bridge_pid"] == 789
    assert state["bridge_process_marker"] == "marker-789"
    assert state["tunnel_pid"] == 456
    assert state["tunnel_process_marker"] == "tunnel-marker"
