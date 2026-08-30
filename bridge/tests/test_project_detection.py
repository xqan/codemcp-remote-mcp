from __future__ import annotations

from pathlib import Path

import pytest

from codemcp_bridge.project_detection import detect_project_profile


@pytest.mark.parametrize(
    ("marker", "profile_id"),
    [
        ("pom.xml", "java-maven"),
        ("gradlew", "java-gradle"),
        ("gradlew.bat", "java-gradle"),
        ("build.gradle", "java-gradle"),
        ("build.gradle.kts", "java-gradle"),
        ("package-lock.json", "node-npm"),
        ("pnpm-lock.yaml", "node-pnpm"),
        ("pyproject.toml", "python"),
        ("go.mod", "go"),
        ("Cargo.toml", "rust"),
    ],
)
def test_detect_project_profile_from_known_root_marker(
    tmp_path: Path, marker: str, profile_id: str
) -> None:
    (tmp_path / marker).write_text("marker\n", encoding="utf-8")

    result = detect_project_profile(tmp_path)

    assert result.detected is True
    assert result.ambiguous is False
    assert result.profile_id == profile_id
    assert result.candidates == (profile_id,)
    assert result.evidence[profile_id] == (marker,)


def test_multiple_markers_for_same_profile_are_not_ambiguous(tmp_path: Path) -> None:
    (tmp_path / "gradlew").write_text("wrapper\n", encoding="utf-8")
    (tmp_path / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")

    result = detect_project_profile(tmp_path)

    assert result.profile_id == "java-gradle"
    assert result.ambiguous is False
    assert result.evidence["java-gradle"] == ("gradlew", "build.gradle.kts")


def test_multi_stack_detection_fails_closed_without_priority(tmp_path: Path) -> None:
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}\n", encoding="utf-8")

    result = detect_project_profile(tmp_path)

    assert result.detected is False
    assert result.ambiguous is True
    assert result.profile_id is None
    assert result.candidates == ("java-maven", "node-npm")
    assert result.evidence["java-maven"] == ("pom.xml",)
    assert result.evidence["node-npm"] == ("package-lock.json",)


def test_unknown_project_returns_no_profile(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("unknown stack\n", encoding="utf-8")

    result = detect_project_profile(tmp_path)

    assert result.detected is False
    assert result.ambiguous is False
    assert result.profile_id is None
    assert result.candidates == ()
    assert dict(result.evidence) == {}


def test_detect_codemcp_remote_from_strict_repository_markers(tmp_path: Path) -> None:
    (tmp_path / "bridge").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "bridge" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "scripts" / "windows_entrypoint.py").write_text("# entrypoint\n", encoding="utf-8")
    (tmp_path / "codemcp.toml").write_text("[commands.test]\n", encoding="utf-8")

    result = detect_project_profile(tmp_path)

    assert result.detected is True
    assert result.ambiguous is False
    assert result.profile_id == "codemcp-remote"
    assert result.candidates == ("codemcp-remote",)
    assert result.evidence["codemcp-remote"] == (
        "bridge/pyproject.toml",
        "scripts/windows_entrypoint.py",
        "codemcp.toml",
    )


def test_partial_codemcp_remote_markers_do_not_select_profile(tmp_path: Path) -> None:
    (tmp_path / "bridge").mkdir()
    (tmp_path / "bridge" / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (tmp_path / "codemcp.toml").write_text("[commands.test]\n", encoding="utf-8")

    result = detect_project_profile(tmp_path)

    assert result.detected is False
    assert result.profile_id is None
    assert result.candidates == ()


def test_symlink_marker_cannot_select_profile(tmp_path: Path) -> None:
    target = tmp_path / "real-pom.xml"
    target.write_text("<project/>\n", encoding="utf-8")
    marker = tmp_path / "pom.xml"
    try:
        marker.symlink_to(target)
    except OSError:
        pytest.skip("symlinks are unavailable in this environment")

    result = detect_project_profile(tmp_path)

    assert result.detected is False
    assert result.profile_id is None
    assert result.candidates == ()
