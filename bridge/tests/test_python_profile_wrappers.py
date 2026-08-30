from __future__ import annotations

from pathlib import Path

import pytest

from codemcp_bridge.settings import load_settings


def _write_base_config(config_dir: Path, project_root: Path, extra_project: str = "") -> None:
    config_dir.mkdir()
    (config_dir / "bridge.toml").write_text(
        "[policy]\n"
        "allow_arbitrary_paths = false\n"
        "allow_arbitrary_commands = false\n"
        "allow_model_calls = false\n"
        "require_clean_workspace = true\n",
        encoding="utf-8",
    )
    relative_root = project_root.relative_to(config_dir.parent).as_posix()
    (config_dir / "projects.toml").write_text(
        f'[projects.demo]\nroot = "../{relative_root}"\n{extra_project}',
        encoding="utf-8",
    )


def _unittest_wrapper(newline: str = "\n") -> str:
    lines = [
        "#!/usr/bin/env sh",
        "set -eu",
        'HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)',
        'cd "$HERE"',
        'PYTHONPATH="$HERE/src${PYTHONPATH:+:$PYTHONPATH}" python -m unittest discover -s tests -v',
        "",
    ]
    return newline.join(lines)


def test_python_profile_recognizes_unittest_wrapper_as_fixed_argv(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0"\n', encoding="utf-8"
    )
    (project / "run_tests.sh").write_text(_unittest_wrapper(), encoding="utf-8")
    config_dir = tmp_path / "config"
    _write_base_config(config_dir, project)

    spec = load_settings(config_dir / "bridge.toml", config_dir / "projects.toml").projects["demo"]

    assert spec.profile == "python"
    assert spec.profile_source == "detected"
    assert spec.commands["test"].argv == (
        "python",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-v",
    )


def test_python_profile_recognizes_crlf_unittest_wrapper_without_shell_execution(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0"\n', encoding="utf-8"
    )
    (project / "run_tests.sh").write_bytes(_unittest_wrapper("\r\n").encode())
    config_dir = tmp_path / "config"
    _write_base_config(config_dir, project)

    spec = load_settings(config_dir / "bridge.toml", config_dir / "projects.toml").projects["demo"]

    assert spec.commands["test"].argv == (
        "python",
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-v",
    )


def test_python_profile_keeps_regular_noncanonical_wrapper(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0"\n', encoding="utf-8"
    )
    (project / "run_tests.sh").write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    config_dir = tmp_path / "config"
    _write_base_config(config_dir, project)

    spec = load_settings(config_dir / "bridge.toml", config_dir / "projects.toml").projects["demo"]

    assert spec.commands["test"].argv == ("/bin/sh", "./run_tests.sh")


def test_python_profile_does_not_select_symlinked_run_tests_script(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0"\n', encoding="utf-8"
    )
    target = tmp_path / "outside-tests.sh"
    target.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
    try:
        (project / "run_tests.sh").symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable on this platform")
    config_dir = tmp_path / "config"
    _write_base_config(config_dir, project)

    spec = load_settings(config_dir / "bridge.toml", config_dir / "projects.toml").projects["demo"]

    assert spec.commands["test"].argv == ("python", "-m", "pytest")


def test_explicit_python_test_command_overrides_detected_run_tests_script(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0"\n', encoding="utf-8"
    )
    (project / "run_tests.sh").write_text(_unittest_wrapper(), encoding="utf-8")
    config_dir = tmp_path / "config"
    _write_base_config(
        config_dir,
        project,
        '[projects.demo.commands.test]\nkind = "test"\nargv = ["python", "-m", "unittest"]\n',
    )

    spec = load_settings(config_dir / "bridge.toml", config_dir / "projects.toml").projects["demo"]

    assert spec.commands["test"].argv == ("python", "-m", "unittest")
