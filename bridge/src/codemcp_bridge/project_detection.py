"""Conservative project metadata detection without executing project code."""

from __future__ import annotations

import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

_PROFILE_MARKERS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "java-maven": ("pom.xml",),
        "java-gradle": ("gradlew", "gradlew.bat", "build.gradle", "build.gradle.kts"),
        "node-npm": ("package-lock.json",),
        "node-pnpm": ("pnpm-lock.yaml",),
        "python": ("pyproject.toml",),
        "go": ("go.mod",),
        "rust": ("Cargo.toml",),
    }
)

# The Bridge repository is a small monorepo rather than a single root-level
# language project. Keep its detection strict so an unrelated project with a
# similarly named directory cannot inherit the repository command catalog.
_REQUIRED_PROFILE_MARKERS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "codemcp-remote": (
            "bridge/pyproject.toml",
            "scripts/windows_entrypoint.py",
            "codemcp.toml",
        ),
    }
)


@dataclass(frozen=True, slots=True)
class ProjectDetection:
    """A deterministic profile detection result derived only from repository metadata."""

    profile_id: str | None
    candidates: tuple[str, ...]
    evidence: Mapping[str, tuple[str, ...]]
    ambiguous: bool

    @property
    def detected(self) -> bool:
        return self.profile_id is not None


def _is_regular_marker(path: Path) -> bool:
    """Accept a real regular file only; symlinked markers cannot select a profile."""

    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return False


def detect_project_profile(root: Path) -> ProjectDetection:
    """Detect one built-in profile from known repository metadata.

    Detection never executes project code and never resolves ambiguous multi-stack
    repositories by priority. Multiple distinct candidates therefore fail closed.
    """

    found: dict[str, tuple[str, ...]] = {}
    for profile_id, markers in _PROFILE_MARKERS.items():
        matches = tuple(marker for marker in markers if _is_regular_marker(root / marker))
        if matches:
            found[profile_id] = matches
    for profile_id, markers in _REQUIRED_PROFILE_MARKERS.items():
        matches = tuple(marker for marker in markers if _is_regular_marker(root / marker))
        if len(matches) == len(markers):
            found[profile_id] = matches

    candidates = tuple(sorted(found))
    profile_id = candidates[0] if len(candidates) == 1 else None
    evidence = MappingProxyType({candidate: found[candidate] for candidate in candidates})
    return ProjectDetection(
        profile_id=profile_id,
        candidates=candidates,
        evidence=evidence,
        ambiguous=len(candidates) > 1,
    )
