"""Remote transport provider contracts shared by lifecycle implementations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class LifecycleError(RuntimeError):
    """Raised when lifecycle or remote transport orchestration cannot safely continue."""


@dataclass(frozen=True, slots=True)
class TransportContext:
    """Provider-visible runtime paths without coupling providers to lifecycle.RuntimePaths."""

    runtime_root: Path
    bundled_runtime_root: Path
    app_root: Path
    config_dir: Path
    log_dir: Path
    tunnel_dir: Path
    secret_file: Path
    tunnel_env: Path


class RemoteTransportProvider(Protocol):
    """Lifecycle boundary for one remote transport implementation."""

    provider_id: str
    secret_env_name: str
    secret_file_name: str

    def initialize_config(self, context: TransportContext, **kwargs: Any) -> list[str]:
        """Create or update provider-owned runtime configuration."""

    def load_settings(
        self,
        context: TransportContext,
        *,
        env_file: Path | None = None,
    ) -> Any:
        """Load and validate provider settings."""

    def validate_config(self, settings: Any) -> Path | None:
        """Validate provider configuration without starting the process."""

    def find_client(self, context: TransportContext) -> Path:
        """Resolve the provider executable from approved locations."""

    def redact(self, value: str) -> str:
        """Redact provider secrets from diagnostic text."""

    def initialize(
        self,
        context: TransportContext,
        settings: Any,
        *,
        secret: str,
        force: bool = False,
    ) -> Path | None:
        """Initialize provider-specific remote state."""

    def run(
        self,
        context: TransportContext,
        settings: Any,
        *,
        secret: str,
        rotate_log: Callable[[Path], None],
    ) -> int:
        """Run the provider proxy until it exits."""

    def bridge_url(self, settings: Any) -> str:
        """Return the loopback MCP origin used by the provider."""

    def ready_url(self, settings: Any) -> str:
        """Return the local provider readiness endpoint."""

    def doctor(
        self,
        context: TransportContext,
        *,
        env_file: Path | None,
        secret_available: bool,
        secret_source: str,
    ) -> dict[str, Any]:
        """Return provider-specific doctor checks."""
