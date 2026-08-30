"""Environment and configuration diagnostics for Phase 0."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
BRIDGE_CONFIG = REPOSITORY_ROOT / "config" / "bridge.example.toml"
PROJECT_CONFIG = REPOSITORY_ROOT / "config" / "projects.toml"
if not PROJECT_CONFIG.is_file():
    PROJECT_CONFIG = REPOSITORY_ROOT / "config" / "projects.example.toml"
CODEMCP_BASELINE = REPOSITORY_ROOT / "config" / "codemcp-baseline.toml"


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _command_version(command: str) -> dict[str, Any]:
    executable = shutil.which(command)
    if executable is None:
        return {"installed": False, "executable": None, "version": None}

    try:
        completed = subprocess.run(
            [executable, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "installed": True,
            "executable": executable,
            "version": None,
            "error": str(exc),
        }

    output = (completed.stdout or completed.stderr).strip()
    return {
        "installed": completed.returncode == 0,
        "executable": executable,
        "version": output or None,
        "returncode": completed.returncode,
    }


def _package_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _codemcp_diagnostics() -> dict[str, Any]:
    """Inspect codemcp metadata without starting its long-lived MCP server."""

    executable = shutil.which("codemcp")
    package_version = _package_version("codemcp")
    return {
        "installed": executable is not None and package_version is not None,
        "executable": executable,
        "version": f"codemcp {package_version}" if package_version else None,
        "package_version": package_version,
        "probe": "distribution-metadata",
    }


def _validate_configuration() -> list[str]:
    errors: list[str] = []

    for path in (BRIDGE_CONFIG, PROJECT_CONFIG, CODEMCP_BASELINE):
        if not path.is_file():
            errors.append(f"missing configuration: {path}")

    if errors:
        return errors

    bridge = _load_toml(BRIDGE_CONFIG)
    server = bridge.get("server", {})
    policy = bridge.get("policy", {})
    network = bridge.get("network", {})

    if server.get("host") != "127.0.0.1":
        errors.append("server.host must be 127.0.0.1")
    if server.get("port") != 46200:
        errors.append("server.port must be 46200 for the Phase 0 baseline")
    if server.get("path") != "/mcp":
        errors.append("server.path must be /mcp")
    for key in ("allow_arbitrary_paths", "allow_arbitrary_commands", "allow_model_calls"):
        if policy.get(key) is not False:
            errors.append(f"policy.{key} must be false")
    if policy.get("require_clean_workspace") is not True:
        errors.append("policy.require_clean_workspace must be true")
    if network.get("model_egress") != "deny":
        errors.append("network.model_egress must be deny")
    if network.get("remote_transport") != "provider-selected":
        errors.append("network.remote_transport must be provider-selected")

    projects = _load_toml(PROJECT_CONFIG)
    registered_projects = projects.get("projects", {})
    if not isinstance(registered_projects, dict) or not registered_projects:
        errors.append(f"{PROJECT_CONFIG.name} must define at least one project")
    else:
        has_project_root = any(
            isinstance(project, dict) and project.get("root")
            for project in registered_projects.values()
        )
        if not has_project_root:
            errors.append(f"{PROJECT_CONFIG.name} must define a project root")
        has_test_command = any(
            isinstance(project, dict)
            and isinstance(project.get("commands"), dict)
            and project["commands"].get("test", {}).get("argv")
            for project in registered_projects.values()
        )
        if not has_test_command:
            errors.append(f"{PROJECT_CONFIG.name} must define a test command")

    baseline = _load_toml(CODEMCP_BASELINE).get("upstream", {})
    if baseline.get("release") != "0.3.0":
        errors.append("codemcp baseline release must be 0.3.0")
    if baseline.get("commit") != "683e6ec29b15b91ec12430afabf5a45ed57d2489":
        errors.append("codemcp baseline commit does not match release 0.3.0")
    if baseline.get("model_calls_allowed") is not False:
        errors.append("codemcp baseline must explicitly deny model calls")

    return errors


def collect_diagnostics() -> dict[str, Any]:
    configuration_errors = _validate_configuration()
    codemcp_command = _codemcp_diagnostics()

    return {
        "phase": "0",
        "status": "ok" if not configuration_errors else "configuration_error",
        "repository_root": str(REPOSITORY_ROOT),
        "runtime": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "uv": _command_version("uv"),
            "git": _command_version("git"),
        },
        "packages": {
            "mcp": _package_version("mcp"),
            "pydantic": _package_version("pydantic"),
            "pydantic-settings": _package_version("pydantic-settings"),
        },
        "codemcp": {
            "expected_release": "0.3.0",
            "expected_commit": "683e6ec29b15b91ec12430afabf5a45ed57d2489",
            "installed_command": codemcp_command,
            "required_by_phase": "Phase 1",
        },
        "network_policy": {
            "model_egress": "deny",
            "bridge_listener": "loopback-only",
            "remote_transport": "provider-selected",
            "recommended_remote_transport": "cloudflare",
            "secure_mcp_tunnel_optional": True,
        },
        "configuration_errors": configuration_errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Check the Phase 0 runtime baseline.")
    parser.add_argument(
        "command",
        nargs="?",
        choices=("doctor",),
        default="doctor",
        help="diagnostic command; omitted for backward compatibility",
    )
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also require the codemcp command to be installed; intended for Phase 1",
    )
    args = parser.parse_args()

    diagnostics = collect_diagnostics()
    if args.strict:
        codemcp = diagnostics["codemcp"]["installed_command"]
        if not codemcp["installed"]:
            diagnostics["status"] = "codemcp_not_installed"
        elif codemcp["package_version"] != diagnostics["codemcp"]["expected_release"]:
            diagnostics["status"] = "codemcp_version_mismatch"

    if args.json:
        print(json.dumps(diagnostics, ensure_ascii=False, indent=2))
    else:
        print(f"phase={diagnostics['phase']} status={diagnostics['status']}")
        print(f"repository_root={diagnostics['repository_root']}")
        print(f"python={diagnostics['runtime']['python']}")
        print(f"uv={diagnostics['runtime']['uv']['version']}")
        print(f"git={diagnostics['runtime']['git']['version']}")
        print(f"mcp={diagnostics['packages']['mcp'] or 'not-installed'}")
        print(
            "codemcp="
            + (
                diagnostics["codemcp"]["installed_command"]["version"]
                or "not-installed; expected in Phase 1"
            )
        )
        for error in diagnostics["configuration_errors"]:
            print(f"configuration_error={error}")

    return 0 if diagnostics["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
