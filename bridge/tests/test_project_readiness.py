from __future__ import annotations

from pathlib import Path

from codemcp_bridge.project_readiness import inspect_development_readiness
from codemcp_bridge.settings import CommandSpec, ProjectSpec


def _command(command_id: str, kind: str, *argv: str) -> CommandSpec:
    return CommandSpec(
        command_id=command_id,
        kind=kind,
        argv=tuple(argv),
        timeout_seconds=30,
        approval="not-required",
    )


def _project(
    root: Path,
    commands: dict[str, CommandSpec],
    *,
    profile: str | None = None,
    profile_source: str = "none",
) -> ProjectSpec:
    return ProjectSpec(
        project_id="demo",
        root=root,
        allowed_branches=("main",),
        require_clean_workspace=True,
        codemcp_config=root / "codemcp.toml",
        commands=commands,
        profile=profile,
        profile_source=profile_source,
    )


def test_readiness_is_true_when_validation_command_matches_codemcp_config(tmp_path: Path) -> None:
    command = _command("test", "test", "python", "-m", "pytest")
    (tmp_path / "codemcp.toml").write_text(
        '[commands.test]\ncommand = ["python", "-m", "pytest"]\n',
        encoding="utf-8",
    )

    readiness = inspect_development_readiness(_project(tmp_path, {"test": command}))

    assert readiness.available_commands == ("test",)
    assert readiness.matched_commands == ("test",)
    assert readiness.missing_commands == ()
    assert readiness.mismatched_commands == ()
    assert readiness.codemcp_config_ready is True
    assert readiness.development_ready is True
    assert readiness.issues == ()


def test_readiness_reports_missing_codemcp_config(tmp_path: Path) -> None:
    command = _command("test", "test", "python", "-m", "pytest")

    readiness = inspect_development_readiness(_project(tmp_path, {"test": command}))

    assert readiness.codemcp_config_exists is False
    assert readiness.codemcp_config_valid is False
    assert readiness.codemcp_config_ready is False
    assert readiness.development_ready is False
    assert "codemcp configuration file is missing" in readiness.issues


def test_readiness_reports_missing_and_mismatched_commands(tmp_path: Path) -> None:
    commands = {
        "test": _command("test", "test", "python", "-m", "pytest"),
        "build": _command("build", "build", "python", "-m", "build"),
    }
    (tmp_path / "codemcp.toml").write_text(
        '[commands.test]\ncommand = ["python", "-m", "unittest"]\n',
        encoding="utf-8",
    )

    readiness = inspect_development_readiness(_project(tmp_path, commands))

    assert readiness.matched_commands == ()
    assert readiness.missing_commands == ("build",)
    assert readiness.mismatched_commands == ("test",)
    assert readiness.codemcp_config_ready is False
    assert readiness.development_ready is False


def test_ambiguous_detection_blocks_readiness_without_explicit_profile(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}\n", encoding="utf-8")
    command = _command("test", "test", "python", "-m", "pytest")
    (tmp_path / "codemcp.toml").write_text(
        '[commands.test]\ncommand = ["python", "-m", "pytest"]\n',
        encoding="utf-8",
    )

    readiness = inspect_development_readiness(_project(tmp_path, {"test": command}))

    assert readiness.detection_ambiguous is True
    assert readiness.detection_candidates == ("java-maven", "node-npm")
    assert readiness.codemcp_config_ready is True
    assert readiness.development_ready is False
    assert "project metadata is ambiguous; set an explicit profile" in readiness.issues


def test_explicit_profile_resolves_ambiguous_detection_for_readiness(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}\n", encoding="utf-8")
    command = _command("test", "test", "mvn", "test")
    (tmp_path / "codemcp.toml").write_text(
        '[commands.test]\ncommand = ["mvn", "test"]\n',
        encoding="utf-8",
    )

    readiness = inspect_development_readiness(
        _project(
            tmp_path,
            {"test": command},
            profile="java-maven",
            profile_source="explicit",
        )
    )

    assert readiness.detection_ambiguous is True
    assert readiness.profile_id == "java-maven"
    assert readiness.profile_source == "explicit"
    assert readiness.development_ready is True


def test_readiness_requires_test_or_verify_quality_gate(tmp_path: Path) -> None:
    command = _command("build", "build", "python", "-m", "build")
    (tmp_path / "codemcp.toml").write_text(
        '[commands.build]\ncommand = ["python", "-m", "build"]\n',
        encoding="utf-8",
    )

    readiness = inspect_development_readiness(_project(tmp_path, {"build": command}))

    assert readiness.codemcp_config_ready is True
    assert readiness.development_ready is False
    assert "no test or verify command is available" in readiness.issues


def test_readiness_quality_gate_uses_command_kind_not_command_id(tmp_path: Path) -> None:
    command = _command("test", "custom", "custom-tool")
    (tmp_path / "codemcp.toml").write_text(
        '[commands.test]\ncommand = ["custom-tool"]\n',
        encoding="utf-8",
    )

    readiness = inspect_development_readiness(_project(tmp_path, {"test": command}))

    assert readiness.codemcp_config_ready is True
    assert readiness.development_ready is False
    assert "no test or verify command is available" in readiness.issues
