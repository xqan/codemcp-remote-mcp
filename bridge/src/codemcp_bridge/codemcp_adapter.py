"""Controlled mapping from Bridge operations to codemcp subtools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .project_registry import ProjectRegistry
from .settings import BridgeSettings, ProjectSpec
from .worker_manager import AdapterResult, WorkerManager


def _truncate_utf8(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


class CodemcpAdapter:
    """Expose only the pinned codemcp subtools selected by the Bridge."""

    def __init__(
        self,
        settings: BridgeSettings,
        registry: ProjectRegistry,
        workers: WorkerManager | None = None,
    ):
        self._settings = settings
        self._registry = registry
        self._workers = workers or WorkerManager(settings)

    def _map_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        mapped: dict[str, Any] = {}
        for key, value in arguments.items():
            if key == "path":
                if not isinstance(value, Path):
                    raise TypeError("adapter paths must be pathlib.Path values")
                mapped[key] = self._registry.worker_path(value)
            else:
                mapped[key] = value
        return mapped

    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> AdapterResult:
        mapped = self._map_arguments(arguments)
        result = await self._workers.call(
            project,
            subtool,
            mapped,
            timeout_seconds=timeout_seconds,
            mutation=mutation,
        )
        text, truncated = _truncate_utf8(result.text, self._settings.policy.max_result_bytes)
        return AdapterResult(
            text=text,
            is_error=result.is_error,
            truncated=result.truncated or truncated,
        )

    def is_active(self, project_id: str) -> bool:
        return self._workers.is_active(project_id)

    async def close(self) -> None:
        await self._workers.close()
