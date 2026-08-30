"""Resolve explicit or detected project profiles without executing project code."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .project_detection import ProjectDetection, detect_project_profile
from .project_profiles import ProjectProfile, get_builtin_profile


@dataclass(frozen=True, slots=True)
class ProfileResolution:
    """One deterministic profile selection result."""

    profile: ProjectProfile | None
    source: str
    detection: ProjectDetection

    @property
    def profile_id(self) -> str | None:
        return self.profile.profile_id if self.profile is not None else None


def resolve_project_profile(root: Path, explicit_profile: str | None) -> ProfileResolution:
    """Resolve explicit profile first, otherwise use one unambiguous detection result."""

    detection = detect_project_profile(root)
    if explicit_profile is not None:
        profile = get_builtin_profile(explicit_profile)
        if profile is None:
            raise ValueError(f"unsupported built-in profile: {explicit_profile}")
        return ProfileResolution(profile=profile, source="explicit", detection=detection)

    if detection.profile_id is None:
        return ProfileResolution(profile=None, source="none", detection=detection)

    profile = get_builtin_profile(detection.profile_id)
    if profile is None:
        raise ValueError(f"detected unsupported built-in profile: {detection.profile_id}")
    return ProfileResolution(profile=profile, source="detected", detection=detection)
