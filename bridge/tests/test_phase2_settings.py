from __future__ import annotations

from pathlib import Path

import pytest

from codemcp_bridge.settings import SettingsError, load_settings


def test_settings_reject_unsafe_phase2_flags(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "bridge.toml").write_text(
        "[server]\n"
        'host = "127.0.0.1"\n'
        "port = 46200\n"
        'path = "/mcp"\n'
        'transport = "streamable-http"\n'
        "\n"
        "[storage]\n"
        'data_dir = ".local"\n'
        'sqlite_file = ".local/bridge.sqlite3"\n'
        'log_dir = ".local/logs"\n'
        "\n"
        "[policy]\n"
        "allow_arbitrary_paths = true\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n",
        encoding="utf-8",
    )
    (config_dir / "projects.toml").write_text(
        '[projects.demo]\nroot = "../project"\n', encoding="utf-8"
    )

    with pytest.raises(SettingsError, match="must remain false"):
        load_settings(config_dir / "bridge.toml", config_dir / "projects.toml")


def test_project_profile_is_optional_and_backward_compatible(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "bridge.toml").write_text(
        "[policy]\n"
        "allow_arbitrary_paths = false\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n",
        encoding="utf-8",
    )
    (config_dir / "projects.toml").write_text(
        '[projects.demo]\nroot = "../project"\n',
        encoding="utf-8",
    )

    settings = load_settings(config_dir / "bridge.toml", config_dir / "projects.toml")
    legacy = settings.projects["demo"]
    assert legacy.profile is None
    assert legacy.commands == {}
    assert legacy.require_clean_workspace is True

    (config_dir / "projects.toml").write_text(
        '[projects.demo]\nroot = "../project"\nprofile = "java-maven"\n',
        encoding="utf-8",
    )
    settings = load_settings(config_dir / "bridge.toml", config_dir / "projects.toml")
    assert settings.projects["demo"].profile == "java-maven"


@pytest.mark.parametrize("profile", ["", "Java-Maven", "java_maven", "java maven"])
def test_project_profile_rejects_noncanonical_identifiers(tmp_path: Path, profile: str) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "bridge.toml").write_text(
        "[policy]\n"
        "allow_arbitrary_paths = false\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n",
        encoding="utf-8",
    )
    (config_dir / "projects.toml").write_text(
        f'[projects.demo]\nroot = "../project"\nprofile = "{profile}"\n',
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match="lowercase profile identifier"):
        load_settings(config_dir / "bridge.toml", config_dir / "projects.toml")


def test_project_profile_rejects_unknown_builtin_profile(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "bridge.toml").write_text(
        "[policy]\n"
        "allow_arbitrary_paths = false\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n",
        encoding="utf-8",
    )
    (config_dir / "projects.toml").write_text(
        '[projects.demo]\nroot = "../project"\nprofile = "unknown-profile"\n',
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match="not a supported built-in profile"):
        load_settings(config_dir / "bridge.toml", config_dir / "projects.toml")


def test_minimal_project_uses_conservative_security_defaults(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "bridge.toml").write_text(
        "[policy]\n"
        "allow_arbitrary_paths = false\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n",
        encoding="utf-8",
    )
    (config_dir / "projects.toml").write_text(
        '[projects.demo]\nroot = "../project"\n',
        encoding="utf-8",
    )

    project = load_settings(config_dir / "bridge.toml", config_dir / "projects.toml").projects[
        "demo"
    ]

    assert project.allowed_branches == ("develop", "develop/*", "codex/*", "feature/*")
    assert project.require_clean_workspace is True


def test_command_defaults_are_kind_aware_and_fail_closed(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "bridge.toml").write_text(
        "[policy]\n"
        "allow_arbitrary_paths = false\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n",
        encoding="utf-8",
    )
    (config_dir / "projects.toml").write_text(
        "[projects.demo]\n"
        'root = "../project"\n'
        "[projects.demo.commands.test]\n"
        'kind = "test"\n'
        'argv = ["python", "-m", "pytest"]\n'
        "[projects.demo.commands.deploy]\n"
        'kind = "deploy"\n'
        'argv = ["deploy-tool"]\n'
        "[projects.demo.commands.custom]\n"
        'kind = "custom"\n'
        'argv = ["custom-tool"]\n',
        encoding="utf-8",
    )

    commands = (
        load_settings(config_dir / "bridge.toml", config_dir / "projects.toml")
        .projects["demo"]
        .commands
    )

    assert commands["test"].timeout_seconds == 900
    assert commands["test"].approval == "not-required"
    assert commands["deploy"].timeout_seconds == 1200
    assert commands["deploy"].approval == "required"
    assert commands["custom"].timeout_seconds == 60
    assert commands["custom"].approval == "required"


def test_high_risk_command_cannot_disable_required_approval(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "bridge.toml").write_text(
        "[policy]\n"
        "allow_arbitrary_paths = false\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n",
        encoding="utf-8",
    )
    (config_dir / "projects.toml").write_text(
        "[projects.demo]\n"
        'root = "../project"\n'
        "[projects.demo.commands.deploy]\n"
        'kind = "deploy"\n'
        'argv = ["deploy-tool"]\n'
        'approval = "not-required"\n',
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match="cannot disable required approval"):
        load_settings(config_dir / "bridge.toml", config_dir / "projects.toml")


def test_project_require_clean_workspace_must_be_boolean(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "bridge.toml").write_text(
        "[policy]\n"
        "allow_arbitrary_paths = false\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n",
        encoding="utf-8",
    )
    (config_dir / "projects.toml").write_text(
        '[projects.demo]\nroot = "../project"\nrequire_clean_workspace = 0\n',
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match="require_clean_workspace must be boolean"):
        load_settings(config_dir / "bridge.toml", config_dir / "projects.toml")


def test_command_timeout_rejects_boolean_values(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "bridge.toml").write_text(
        "[policy]\n"
        "allow_arbitrary_paths = false\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n",
        encoding="utf-8",
    )
    (config_dir / "projects.toml").write_text(
        "[projects.demo]\n"
        'root = "../project"\n'
        "[projects.demo.commands.test]\n"
        'kind = "test"\n'
        'argv = ["python", "-m", "pytest"]\n'
        "timeout_seconds = true\n",
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match="timeout_seconds must be positive"):
        load_settings(config_dir / "bridge.toml", config_dir / "projects.toml")
