from __future__ import annotations

import subprocess
from pathlib import Path

HISTORICAL_EVIDENCE_PREFIXES = (
    "docs/releases/",
    "docs/reports/",
)

FORBIDDEN_OPERATOR_MARKERS = (
    b"quick" + b"clip.cc",
    b"d:" + b"\\documents\\codexproject",
    b"d:" + b"/documents/codexproject",
)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _tracked_paths() -> list[str]:
    root = _repository_root()
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True,
        capture_output=True,
    )
    return [item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def _is_historical_evidence(path: str) -> bool:
    return path.startswith(HISTORICAL_EVIDENCE_PREFIXES)


def test_current_public_tree_contains_no_operator_specific_markers() -> None:
    root = _repository_root()
    violations: list[str] = []

    for relative_path in _tracked_paths():
        if _is_historical_evidence(relative_path):
            continue
        raw = (root / relative_path).read_bytes().lower()
        for marker in FORBIDDEN_OPERATOR_MARKERS:
            if marker in raw:
                violations.append(f"{relative_path}: {marker.decode('ascii')}")

    assert violations == []


def test_runtime_state_and_secret_material_are_not_tracked() -> None:
    violations: list[str] = []

    for relative_path in _tracked_paths():
        normalized = relative_path.replace("\\", "/")
        parts = normalized.split("/")
        name = parts[-1].lower()

        forbidden = (
            ".local" in parts
            or normalized == "config/projects.toml"
            or normalized == "config/tunnel-profile.local.env"
            or name.endswith((".sqlite3", ".sqlite3-shm", ".sqlite3-wal", ".log"))
            or name == ".env"
            or (name.startswith(".env.") and name != ".env.example")
            or "secrets" in parts
        )
        if forbidden:
            violations.append(relative_path)

    assert violations == []
