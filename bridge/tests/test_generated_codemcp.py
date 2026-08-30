from __future__ import annotations

import os
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

from codemcp_bridge.errors import BridgeError
from codemcp_bridge.generated_codemcp import (
    can_generate_codemcp_config,
    materialize_generated_codemcp_config,
    render_generated_codemcp_config,
)
from codemcp_bridge.settings import CommandSpec, ProjectSpec


def _maven_project(root: Path) -> ProjectSpec:
    commands = {
        "doctor": CommandSpec("doctor", "doctor", ("mvn", "--version"), 60, "not-required"),
        "test": CommandSpec("test", "test", ("mvn", "test"), 900, "not-required"),
    }
    return ProjectSpec(
        project_id="demo",
        root=root,
        allowed_branches=("main",),
        require_clean_workspace=True,
        codemcp_config=root / "codemcp.toml",
        commands=commands,
        profile="java-maven",
        profile_source="detected",
    )


def _test_command(project: ProjectSpec) -> CommandSpec:
    return project.commands["test"]


def test_generated_codemcp_config_is_deterministic_and_parseable(tmp_path: Path) -> None:
    project = _maven_project(tmp_path)

    assert can_generate_codemcp_config(project) is True
    rendered = render_generated_codemcp_config(project)
    parsed = tomllib.loads(rendered)

    assert parsed["commands"]["doctor"]["command"] == ["mvn", "--version"]
    assert parsed["commands"]["test"]["command"] == ["mvn", "test"]
    assert render_generated_codemcp_config(project) == rendered


def test_generated_codemcp_config_escapes_command_kind_in_doc(tmp_path: Path) -> None:
    project = _maven_project(tmp_path)
    dangerous_kind = 'test"\n[commands.injected]\ncommand = ["attacker"]'
    dangerous = replace(project.commands["test"], kind=dangerous_kind)
    project = replace(project, commands={**project.commands, "test": dangerous})

    parsed = tomllib.loads(render_generated_codemcp_config(project))

    assert set(parsed["commands"]) == {"doctor", "test"}
    assert parsed["commands"]["test"]["doc"] == f"Bridge-resolved {dangerous_kind} command"


def test_generated_codemcp_config_exists_only_for_command_lease(tmp_path: Path) -> None:
    project = _maven_project(tmp_path)
    path = project.codemcp_config

    assert path.exists() is False
    with materialize_generated_codemcp_config(project, _test_command(project)) as lease:
        assert lease.generated is True
        assert path.is_file()
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        assert parsed["commands"]["test"]["command"] == ["mvn", "test"]

    assert path.exists() is False


def test_generated_codemcp_config_exists_for_permission_lease_without_command(
    tmp_path: Path,
) -> None:
    project = _maven_project(tmp_path)
    path = project.codemcp_config

    with materialize_generated_codemcp_config(project) as lease:
        assert lease.generated is True
        assert path.is_file()
        assert tomllib.loads(path.read_text(encoding="utf-8"))["commands"]["test"]["command"] == [
            "mvn",
            "test",
        ]

    assert path.exists() is False


def test_matching_existing_project_config_is_never_overwritten(tmp_path: Path) -> None:
    project = _maven_project(tmp_path)
    path = project.codemcp_config
    original = '[commands.test]\ncommand = ["mvn", "test"]\n'
    path.write_text(original, encoding="utf-8")

    with materialize_generated_codemcp_config(project, _test_command(project)) as lease:
        assert lease.generated is False
        assert path.read_text(encoding="utf-8") == original

    assert path.read_text(encoding="utf-8") == original


def test_existing_config_that_appears_with_drift_is_rejected_without_overwrite(
    tmp_path: Path,
) -> None:
    project = _maven_project(tmp_path)
    path = project.codemcp_config
    original = '[commands.test]\ncommand = ["mvn", "not-test"]\n'
    path.write_text(original, encoding="utf-8")

    with pytest.raises(BridgeError) as rejected:
        with materialize_generated_codemcp_config(project, _test_command(project)):
            pytest.fail("mismatched config must not be leased")

    assert rejected.value.code == "COMMAND_NOT_ALLOWED"
    assert path.read_text(encoding="utf-8") == original


def test_config_appearing_between_lstat_and_create_is_rejected(tmp_path: Path, monkeypatch) -> None:
    project = _maven_project(tmp_path)
    path = project.codemcp_config
    real_open = os.open

    def racing_open(target, flags, mode=0o777):
        path.write_text('[commands.test]\ncommand = ["attacker"]\n', encoding="utf-8")
        return real_open(target, flags, mode)

    monkeypatch.setattr("codemcp_bridge.generated_codemcp.os.open", racing_open)

    with pytest.raises(BridgeError) as rejected:
        with materialize_generated_codemcp_config(project, _test_command(project)):
            pytest.fail("racing config must not be leased")

    assert rejected.value.code == "COMMAND_NOT_ALLOWED"
    assert path.read_text(encoding="utf-8").endswith('command = ["attacker"]\n')


def test_existing_symlink_config_is_rejected(tmp_path: Path) -> None:
    project = _maven_project(tmp_path)
    target = tmp_path / "outside.toml"
    target.write_text('[commands.test]\ncommand = ["mvn", "test"]\n', encoding="utf-8")
    try:
        project.codemcp_config.symlink_to(target)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("symlink creation is not permitted by this Windows account")
        raise

    with pytest.raises(BridgeError) as rejected:
        with materialize_generated_codemcp_config(project, _test_command(project)):
            pytest.fail("symlink config must not be leased")

    assert rejected.value.code == "COMMAND_NOT_ALLOWED"


def test_generated_config_tamper_becomes_unknown_and_is_not_deleted(tmp_path: Path) -> None:
    project = _maven_project(tmp_path)
    path = project.codemcp_config

    with pytest.raises(BridgeError) as raised:
        with materialize_generated_codemcp_config(project, _test_command(project)):
            path.write_text('[commands.test]\ncommand = ["tampered"]\n', encoding="utf-8")

    assert raised.value.code == "UNKNOWN_SIDE_EFFECT"
    assert raised.value.status == "unknown"
    assert path.exists()
    assert "tampered" in path.read_text(encoding="utf-8")


def test_generated_config_disappearance_becomes_unknown(tmp_path: Path) -> None:
    project = _maven_project(tmp_path)
    path = project.codemcp_config

    with pytest.raises(BridgeError) as raised:
        with materialize_generated_codemcp_config(project, _test_command(project)):
            path.unlink()

    assert raised.value.code == "UNKNOWN_SIDE_EFFECT"
    assert raised.value.status == "unknown"
    assert path.exists() is False


def test_generated_config_cleanup_failure_becomes_unknown(tmp_path: Path, monkeypatch) -> None:
    project = _maven_project(tmp_path)
    path = project.codemcp_config
    original_unlink = Path.unlink

    def denied_unlink(self: Path, *args, **kwargs):
        if self == path:
            raise PermissionError("simulated cleanup denial")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", denied_unlink)

    with pytest.raises(BridgeError) as raised:
        with materialize_generated_codemcp_config(project, _test_command(project)):
            pass

    assert raised.value.code == "UNKNOWN_SIDE_EFFECT"
    assert raised.value.status == "unknown"
    assert path.exists()
    original_unlink(path)


def test_ambiguous_or_generic_project_cannot_generate_missing_config(tmp_path: Path) -> None:
    project = _maven_project(tmp_path)
    ambiguous = replace(project, profile=None, profile_source="none")
    generic = replace(project, profile="generic", profile_source="explicit")

    assert can_generate_codemcp_config(ambiguous) is False
    assert can_generate_codemcp_config(generic) is False

    for candidate in (ambiguous, generic):
        with pytest.raises(BridgeError) as rejected:
            with materialize_generated_codemcp_config(candidate, _test_command(candidate)):
                pytest.fail("ineligible project must not generate config")
        assert rejected.value.code == "COMMAND_NOT_ALLOWED"
