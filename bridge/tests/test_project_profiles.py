from __future__ import annotations

import pytest

from codemcp_bridge.project_profiles import (
    SUPPORTED_PROFILE_IDS,
    builtin_profiles,
    get_builtin_profile,
)


def test_builtin_profile_registry_has_expected_profiles() -> None:
    assert SUPPORTED_PROFILE_IDS == {
        "generic",
        "java-maven",
        "java-gradle",
        "node-npm",
        "node-pnpm",
        "python",
        "codemcp-remote",
        "go",
        "rust",
    }
    assert set(builtin_profiles()) == SUPPORTED_PROFILE_IDS
    assert get_builtin_profile("missing") is None


def test_java_maven_profile_has_fixed_core_commands() -> None:
    profile = get_builtin_profile("java-maven")
    assert profile is not None
    assert profile.profile_id == "java-maven"
    assert set(profile.commands) == {"doctor", "compile", "test", "verify", "build"}
    assert profile.commands["doctor"].argv == ("mvn", "--version")
    assert profile.commands["compile"].argv == ("mvn", "-DskipTests", "compile")
    assert profile.commands["test"].argv == ("mvn", "test")
    assert profile.commands["verify"].argv == ("mvn", "verify")
    assert profile.commands["build"].argv == ("mvn", "-DskipTests", "package")


@pytest.mark.parametrize(
    ("profile_id", "executable"),
    [
        ("java-gradle", "./gradlew"),
        ("node-npm", "npm"),
        ("node-pnpm", "pnpm"),
        ("python", "python"),
        ("go", "go"),
        ("rust", "cargo"),
    ],
)
def test_builtin_profile_commands_use_fixed_argv(profile_id: str, executable: str) -> None:
    profile = get_builtin_profile(profile_id)
    assert profile is not None
    assert "doctor" in profile.commands
    assert all(command.argv for command in profile.commands.values())
    assert all(command.argv[0] == executable for command in profile.commands.values())
    assert all(command.approval == "not-required" for command in profile.commands.values())
    assert all(command.timeout_seconds > 0 for command in profile.commands.values())


def test_generic_profile_has_no_implicit_execution_capability() -> None:
    profile = get_builtin_profile("generic")
    assert profile is not None
    assert dict(profile.commands) == {}


def test_codemcp_remote_profile_has_native_source_commands() -> None:
    profile = get_builtin_profile("codemcp-remote")
    assert profile is not None
    assert set(profile.commands) == {"format", "test", "security-audit", "artifact-audit"}
    assert profile.commands["format"].argv == (
        "uv",
        "run",
        "--project",
        "bridge",
        "ruff",
        "format",
        "--check",
        "bridge/src",
        "bridge/tests",
        "tests/integration",
    )
    assert profile.commands["test"].argv == (
        "uv",
        "run",
        "--project",
        "bridge",
        "pytest",
        "-q",
        "bridge/tests",
        "tests/integration",
    )
    assert all(command.approval == "not-required" for command in profile.commands.values())


def test_builtin_profile_registry_is_immutable() -> None:
    profiles = builtin_profiles()
    with pytest.raises(TypeError):
        profiles["other"] = profiles["generic"]  # type: ignore[index]
    with pytest.raises(TypeError):
        profiles["java-maven"].commands["other"] = profiles["java-maven"].commands["test"]  # type: ignore[index]
