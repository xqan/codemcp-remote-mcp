"""Security-preserving defaults shared by project configuration and profiles."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

DEFAULT_ALLOWED_BRANCHES = ("develop", "develop/*", "codex/*", "feature/*")
DEFAULT_REQUIRE_CLEAN_WORKSPACE = True
DEFAULT_COMMAND_TIMEOUT_SECONDS = 60.0

COMMAND_TIMEOUT_SECONDS: Mapping[str, float] = MappingProxyType(
    {
        "doctor": 60.0,
        "compile": 600.0,
        "format": 300.0,
        "lint": 300.0,
        "test": 900.0,
        "verify": 1200.0,
        "build": 900.0,
        "bootstrap": 1200.0,
        "install": 1200.0,
        "destructive": 300.0,
        "deploy": 1200.0,
    }
)

AUTO_APPROVAL_KINDS = frozenset(
    {
        "doctor",
        "compile",
        "format",
        "lint",
        "test",
        "verify",
        "build",
    }
)


def default_command_timeout_seconds(kind: str) -> float:
    """Return the bounded default timeout for a command kind."""

    return COMMAND_TIMEOUT_SECONDS.get(kind, DEFAULT_COMMAND_TIMEOUT_SECONDS)


def default_command_approval(kind: str) -> str:
    """Fail closed: only known low-risk kinds run without explicit approval."""

    return "not-required" if kind in AUTO_APPROVAL_KINDS else "required"
