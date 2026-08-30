from __future__ import annotations

from pathlib import Path

import pytest

from codemcp_bridge.errors import BridgeError
from codemcp_bridge.git_guard import GitGuard
from codemcp_bridge.policy_engine import PolicyEngine
from codemcp_bridge.project_readiness import inspect_development_readiness
from codemcp_bridge.project_registry import ProjectRegistry
from codemcp_bridge.settings import load_settings


def _write_bridge_config(config_dir: Path) -> None:
    (config_dir / "bridge.toml").write_text(
        "[policy]\n"
        "allow_arbitrary_paths = false\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n",
        encoding="utf-8",
    )


def _load_project(tmp_path: Path, projects_toml: str):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write_bridge_config(config_dir)
    (config_dir / "projects.toml").write_text(projects_toml, encoding="utf-8")
    return load_settings(config_dir / "bridge.toml", config_dir / "projects.toml").projects["demo"]


def test_detected_maven_profile_populates_fixed_commands(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pom.xml").write_text("<project/>\n", encoding="utf-8")

    spec = _load_project(
        tmp_path,
        f'[projects.demo]\nroot = "{project.as_posix()}"\n',
    )

    assert spec.profile == "java-maven"
    assert spec.profile_source == "detected"
    assert set(spec.commands) == {"doctor", "compile", "test", "verify", "build"}
    assert spec.commands["test"].argv == ("mvn", "test")
    assert spec.commands["test"].timeout_seconds == 900
    assert spec.commands["test"].approval == "not-required"
    assert not (project / "codemcp.toml").exists()


def test_codemcp_remote_repository_resolves_native_development_profile(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]

    spec = _load_project(
        tmp_path,
        f'[projects.demo]\nroot = "{repository.as_posix()}"\n',
    )

    assert spec.profile == "codemcp-remote"
    assert spec.profile_source == "detected"
    assert set(spec.commands) == {"format", "test", "security-audit", "artifact-audit"}
    assert spec.commands["test"].argv == (
        "uv",
        "run",
        "--project",
        "bridge",
        "pytest",
        "-q",
        "bridge/tests",
        "tests/integration",
    )
    readiness = inspect_development_readiness(spec)
    assert readiness.development_ready is True
    assert readiness.matched_commands == ("artifact-audit", "format", "security-audit", "test")
    assert readiness.issues == ()


def test_explicit_generic_profile_suppresses_auto_detection(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pom.xml").write_text("<project/>\n", encoding="utf-8")

    spec = _load_project(
        tmp_path,
        f'[projects.demo]\nroot = "{project.as_posix()}"\nprofile = "generic"\n',
    )

    assert spec.profile == "generic"
    assert spec.profile_source == "explicit"
    assert spec.commands == {}


def test_explicit_profile_wins_over_ambiguous_detection(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (project / "package-lock.json").write_text("{}\n", encoding="utf-8")

    spec = _load_project(
        tmp_path,
        f'[projects.demo]\nroot = "{project.as_posix()}"\nprofile = "python"\n',
    )

    assert spec.profile == "python"
    assert spec.profile_source == "explicit"
    assert spec.commands["doctor"].argv == ("python", "--version")


def test_ambiguous_detection_does_not_grant_profile_commands(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (project / "package-lock.json").write_text("{}\n", encoding="utf-8")

    spec = _load_project(
        tmp_path,
        f"[projects.demo]\n"
        f'root = "{project.as_posix()}"\n'
        "[projects.demo.commands.inspect]\n"
        'kind = "custom"\n'
        'argv = ["inspect-tool"]\n',
    )

    assert spec.profile is None
    assert spec.profile_source == "none"
    assert set(spec.commands) == {"inspect"}
    assert spec.commands["inspect"].approval == "required"


def test_explicit_command_override_wins_over_profile_command(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pom.xml").write_text("<project/>\n", encoding="utf-8")

    spec = _load_project(
        tmp_path,
        f"[projects.demo]\n"
        f'root = "{project.as_posix()}"\n'
        "[projects.demo.commands.test]\n"
        'kind = "test"\n'
        'argv = ["./run_tests.sh"]\n'
        "timeout_seconds = 321\n"
        'approval = "required"\n',
    )

    assert spec.profile == "java-maven"
    assert spec.profile_source == "detected"
    assert set(spec.commands) == {"doctor", "compile", "test", "verify", "build"}
    assert spec.commands["compile"].argv == ("mvn", "-DskipTests", "compile")
    assert spec.commands["test"].argv == ("./run_tests.sh",)
    assert spec.commands["test"].timeout_seconds == 321
    assert spec.commands["test"].approval == "required"


def test_profile_command_uses_generated_config_but_existing_drift_is_rejected(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    _write_bridge_config(config_dir)
    (config_dir / "projects.toml").write_text(
        f'[projects.demo]\nroot = "{project.as_posix()}"\n',
        encoding="utf-8",
    )

    settings = load_settings(config_dir / "bridge.toml", config_dir / "projects.toml")
    spec = settings.projects["demo"]
    assert spec.profile == "java-maven"
    assert spec.commands["test"].argv == ("mvn", "test")

    policy = PolicyEngine(settings, ProjectRegistry(settings), GitGuard())
    assert policy.command(spec, "test", "test").argv == ("mvn", "test")

    (project / "codemcp.toml").write_text(
        '[commands.test]\ncommand = ["mvn", "not-test"]\n',
        encoding="utf-8",
    )
    with pytest.raises(BridgeError) as rejected:
        policy.command(spec, "test", "test")
    assert rejected.value.code == "COMMAND_NOT_ALLOWED"
