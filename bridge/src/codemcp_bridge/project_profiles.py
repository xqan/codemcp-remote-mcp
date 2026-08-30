"""Built-in project profiles for fixed, policy-safe command catalogs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .security_defaults import default_command_approval, default_command_timeout_seconds


@dataclass(frozen=True, slots=True)
class ProfileCommand:
    """One fixed command candidate supplied by a built-in project profile."""

    command_id: str
    kind: str
    argv: tuple[str, ...]
    timeout_seconds: float
    approval: str


@dataclass(frozen=True, slots=True)
class ProjectProfile:
    """A named, immutable collection of fixed command candidates."""

    profile_id: str
    commands: Mapping[str, ProfileCommand]


def _command(command_id: str, kind: str, *argv: str) -> ProfileCommand:
    return ProfileCommand(
        command_id=command_id,
        kind=kind,
        argv=tuple(argv),
        timeout_seconds=default_command_timeout_seconds(kind),
        approval=default_command_approval(kind),
    )


def _profile(profile_id: str, *commands: ProfileCommand) -> ProjectProfile:
    return ProjectProfile(
        profile_id=profile_id,
        commands=MappingProxyType({command.command_id: command for command in commands}),
    )


_BUILTIN_PROFILES = MappingProxyType(
    {
        "generic": _profile("generic"),
        "java-maven": _profile(
            "java-maven",
            _command("doctor", "doctor", "mvn", "--version"),
            _command("compile", "compile", "mvn", "-DskipTests", "compile"),
            _command("test", "test", "mvn", "test"),
            _command("verify", "verify", "mvn", "verify"),
            _command("build", "build", "mvn", "-DskipTests", "package"),
        ),
        "java-gradle": _profile(
            "java-gradle",
            _command("doctor", "doctor", "./gradlew", "--version"),
            _command("compile", "compile", "./gradlew", "classes"),
            _command("test", "test", "./gradlew", "test"),
            _command("verify", "verify", "./gradlew", "check"),
            _command("build", "build", "./gradlew", "build"),
        ),
        "node-npm": _profile(
            "node-npm",
            _command("doctor", "doctor", "npm", "--version"),
            _command("test", "test", "npm", "test"),
            _command("build", "build", "npm", "run", "build"),
        ),
        "node-pnpm": _profile(
            "node-pnpm",
            _command("doctor", "doctor", "pnpm", "--version"),
            _command("test", "test", "pnpm", "test"),
            _command("build", "build", "pnpm", "build"),
        ),
        "python": _profile(
            "python",
            _command("doctor", "doctor", "python", "--version"),
            _command("compile", "compile", "python", "-m", "compileall", "."),
            _command("test", "test", "python", "-m", "pytest"),
            _command("build", "build", "python", "-m", "build"),
        ),
        "codemcp-remote": _profile(
            "codemcp-remote",
            _command(
                "format",
                "format",
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
            ),
            _command(
                "test",
                "test",
                "uv",
                "run",
                "--project",
                "bridge",
                "pytest",
                "-q",
                "bridge/tests",
                "tests/integration",
            ),
            _command(
                "security-audit",
                "verify",
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                "scripts/validate-open-source-security.ps1",
            ),
            _command(
                "artifact-audit",
                "verify",
                "pwsh",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                "scripts/validate-open-source-security.ps1",
                "-RequireArtifact",
            ),
        ),
        "go": _profile(
            "go",
            _command("doctor", "doctor", "go", "version"),
            _command("compile", "compile", "go", "test", "-run", "^$", "./..."),
            _command("test", "test", "go", "test", "./..."),
            _command("verify", "verify", "go", "vet", "./..."),
            _command("build", "build", "go", "build", "./..."),
        ),
        "rust": _profile(
            "rust",
            _command("doctor", "doctor", "cargo", "--version"),
            _command("compile", "compile", "cargo", "check"),
            _command("test", "test", "cargo", "test"),
            _command("verify", "verify", "cargo", "check", "--all-targets"),
            _command("build", "build", "cargo", "build"),
        ),
    }
)

SUPPORTED_PROFILE_IDS = frozenset(_BUILTIN_PROFILES)


def get_builtin_profile(profile_id: str) -> ProjectProfile | None:
    """Return one immutable built-in profile without activating it."""

    return _BUILTIN_PROFILES.get(profile_id)


def builtin_profiles() -> Mapping[str, ProjectProfile]:
    """Return the immutable built-in profile registry."""

    return _BUILTIN_PROFILES
