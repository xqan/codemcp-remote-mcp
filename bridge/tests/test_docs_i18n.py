from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _default_markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "bridge" / "README.md"]
    files.extend(
        path
        for path in (ROOT / "docs").rglob("*.md")
        if "zh-CN" not in path.relative_to(ROOT / "docs").parts
    )
    return sorted(set(files))


def test_default_documentation_is_english() -> None:
    offenders = []
    for path in _default_markdown_files():
        if HAN.search(path.read_text(encoding="utf-8")):
            offenders.append(path.relative_to(ROOT).as_posix())

    assert offenders == [], (
        "Default documentation must remain English; move Simplified Chinese "
        f"content into dedicated zh-CN documentation: {offenders}"
    )


def test_simplified_chinese_documentation_is_independent() -> None:
    expected = [
        ROOT / "README.zh-CN.md",
        ROOT / "bridge" / "README.zh-CN.md",
        ROOT / "docs" / "zh-CN" / "README.md",
        ROOT / "docs" / "zh-CN" / "getting-started.md",
        ROOT / "docs" / "zh-CN" / "architecture-and-security.md",
        ROOT / "docs" / "zh-CN" / "operations.md",
        ROOT / "docs" / "zh-CN" / "macos.md",
        ROOT / "docs" / "zh-CN" / "implementation-plan.md",
        ROOT / "docs" / "zh-CN" / "open-source-readiness-plan.md",
        ROOT / "docs" / "zh-CN" / "codemcp-compatibility-matrix.md",
    ]

    for path in expected:
        assert path.is_file(), f"missing Simplified Chinese document: {path}"
        assert HAN.search(path.read_text(encoding="utf-8")), (
            f"Simplified Chinese document contains no Han text: {path}"
        )


def test_language_entrypoints_are_linked() -> None:
    assert "README.zh-CN.md" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert "README.md" in (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
    assert "zh-CN/README.md" in (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "../README.md" in (ROOT / "docs" / "zh-CN" / "README.md").read_text(encoding="utf-8")
    assert "README.zh-CN.md" in (ROOT / "bridge" / "README.md").read_text(encoding="utf-8")
    assert "README.md" in (ROOT / "bridge" / "README.zh-CN.md").read_text(encoding="utf-8")


def test_chinese_deep_documents_link_to_english_canonical() -> None:
    expected_links = {
        ROOT / "docs" / "zh-CN" / "implementation-plan.md": "../implementation-plan.md",
        ROOT
        / "docs"
        / "zh-CN"
        / "open-source-readiness-plan.md": "../plans/v0.1.0/open-source-readiness-plan.md",
        ROOT
        / "docs"
        / "zh-CN"
        / "codemcp-compatibility-matrix.md": "../reports/compatibility/codemcp-compatibility-matrix.md",
    }

    for path, expected_link in expected_links.items():
        assert expected_link in path.read_text(encoding="utf-8")
