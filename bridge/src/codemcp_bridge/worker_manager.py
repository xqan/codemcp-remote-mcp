"""Lifecycle management for isolated codemcp stdio workers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from .errors import BridgeError
from .logging_utils import open_worker_stderr
from .settings import BridgeSettings, ProjectSpec, to_wsl_path

logger = logging.getLogger(__name__)
CODEMCP_TOOL = "codemcp"
SAFE_ENVIRONMENT = (
    "PATH",
    "PATHEXT",
    "SYSTEMDRIVE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "USER",
    "LANG",
    "LC_ALL",
)


@dataclass(frozen=True, slots=True)
class AdapterResult:
    text: str
    is_error: bool
    truncated: bool = False


def _result_text(result: Any) -> str:
    chunks = [
        block.text
        for block in getattr(result, "content", [])
        if isinstance(getattr(block, "text", None), str)
    ]
    if chunks:
        return "\n".join(chunks)
    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return json.dumps(structured, ensure_ascii=False, separators=(",", ":"))
    return ""


class _CodemcpWorker:
    def __init__(self, settings: BridgeSettings, project: ProjectSpec):
        self._settings = settings
        self._project = project
        self._call_lock = asyncio.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._owner_task: asyncio.Task[None] | None = None
        self._startup_future: asyncio.Future[ClientSession] | None = None
        self._shutdown_event: asyncio.Event | None = None
        self._session: ClientSession | None = None
        self._stderr = None

    def _parameters(
        self,
        *,
        os_name: str | None = None,
        frozen: bool | None = None,
    ) -> StdioServerParameters:
        host_os = os.name if os_name is None else os_name
        effective_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
        worker_home = self._settings.storage.data_dir / "workers" / self._project.project_id
        worker_home.mkdir(parents=True, exist_ok=True)
        git_excludes = worker_home / "git-excludes"
        git_excludes.write_text("codemcp.toml\n", encoding="utf-8")
        environment = {
            key: os.environ[key] for key in SAFE_ENVIRONMENT if os.environ.get(key) is not None
        }
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["GIT_CONFIG_COUNT"] = "1"
        environment["GIT_CONFIG_KEY_0"] = "core.excludesfile"
        normalize_stderr = self._settings.codemcp.worker_mode == "wsl2" and host_os == "nt"
        self._stderr = open_worker_stderr(
            self._settings.storage.log_dir,
            self._project.project_id,
            normalize_subprocess_output=normalize_stderr,
        )

        if self._settings.codemcp.worker_mode == "wsl2" and host_os == "nt":
            python = self._settings.codemcp.wsl_python or to_wsl_path(
                self._settings.repository_root / ".local" / "bridge-venv-wsl" / "bin" / "python"
            )
            command = "wsl.exe"
            args = [
                "--distribution",
                self._settings.codemcp.wsl_distribution,
                "--cd",
                to_wsl_path(self._project.root),
                "--",
                python,
                "-m",
                "codemcp",
            ]
            environment["HOME"] = to_wsl_path(worker_home)
            environment["USERPROFILE"] = str(worker_home)
            environment["GIT_CONFIG_VALUE_0"] = to_wsl_path(git_excludes)
            environment["GIT_CONFIG_COUNT"] = "2"
            environment["GIT_CONFIG_KEY_1"] = "core.autocrlf"
            environment["GIT_CONFIG_VALUE_1"] = "true"
            cwd = None
        else:
            command = sys.executable
            args = (
                ["_worker"] if effective_frozen else ["-m", "codemcp_bridge.native_codemcp_worker"]
            )
            environment["HOME"] = str(worker_home)
            environment["USERPROFILE"] = str(worker_home)
            environment["GIT_CONFIG_VALUE_0"] = str(git_excludes)
            cwd = str(self._project.root)

        return StdioServerParameters(command=command, args=args, env=environment, cwd=cwd)

    async def _run_owner(
        self,
        startup_future: asyncio.Future[ClientSession],
        shutdown_event: asyncio.Event,
    ) -> None:
        try:
            parameters = self._parameters()
            async with stdio_client(parameters, errlog=self._stderr) as (
                read_stream,
                write_stream,
            ):
                async with ClientSession(read_stream, write_stream) as session:
                    await asyncio.wait_for(
                        session.initialize(),
                        timeout=self._settings.codemcp.startup_timeout_seconds,
                    )
                    self._session = session
                    if not startup_future.done():
                        startup_future.set_result(session)
                    await shutdown_event.wait()
        except BaseException as exc:
            if not startup_future.done():
                startup_future.set_exception(exc)
            elif not isinstance(exc, asyncio.CancelledError):
                logger.error(
                    "codemcp worker owner task failed",
                    exc_info=(type(exc), exc, exc.__traceback__),
                )
        finally:
            self._session = None
            if self._stderr is not None:
                self._stderr.close()
                self._stderr = None

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._session is not None:
                return
            if self._owner_task is None or self._owner_task.done():
                loop = asyncio.get_running_loop()
                startup_future: asyncio.Future[ClientSession] = loop.create_future()
                shutdown_event = asyncio.Event()
                self._startup_future = startup_future
                self._shutdown_event = shutdown_event
                self._owner_task = asyncio.create_task(
                    self._run_owner(startup_future, shutdown_event),
                    name=f"codemcp-worker-{self._project.project_id}",
                )
            else:
                startup_future = self._startup_future
                if startup_future is None:
                    raise RuntimeError("codemcp worker owner has no startup future")

        await asyncio.shield(startup_future)

    def is_active(self) -> bool:
        owner_task = self._owner_task
        return self._session is not None and owner_task is not None and not owner_task.done()

    def matches_project(self, project: ProjectSpec) -> bool:
        return self._project == project

    async def call(
        self,
        subtool: str,
        arguments: dict[str, Any],
        timeout_seconds: float,
    ) -> AdapterResult:
        async with self._call_lock:
            if self._session is None:
                await self.start()
            session = self._session
            if session is None:
                raise RuntimeError("codemcp worker session failed to start")
            result = await asyncio.wait_for(
                session.call_tool(CODEMCP_TOOL, {"subtool": subtool, **arguments}),
                timeout=timeout_seconds,
            )
            return AdapterResult(text=_result_text(result), is_error=bool(result.isError))

    async def close(self) -> None:
        async with self._call_lock:
            async with self._lifecycle_lock:
                owner_task = self._owner_task
                shutdown_event = self._shutdown_event
                if shutdown_event is not None:
                    shutdown_event.set()

            if owner_task is not None:
                await owner_task

            async with self._lifecycle_lock:
                if self._owner_task is owner_task:
                    self._owner_task = None
                    self._startup_future = None
                    self._shutdown_event = None
                    self._session = None


class WorkerManager:
    """Maintain at most one serialized worker per registered project."""

    def __init__(self, settings: BridgeSettings):
        self._settings = settings
        self._workers: dict[str, _CodemcpWorker] = {}
        self._workers_lock = asyncio.Lock()

    async def _get_worker(self, project: ProjectSpec) -> _CodemcpWorker:
        stale_worker: _CodemcpWorker | None = None
        async with self._workers_lock:
            worker = self._workers.get(project.project_id)
            if worker is not None and not worker.matches_project(project):
                stale_worker = self._workers.pop(project.project_id)
                worker = None
            if worker is None:
                worker = _CodemcpWorker(self._settings, project)
                self._workers[project.project_id] = worker
        if stale_worker is not None:
            await stale_worker.close()
        return worker

    async def _discard(self, project_id: str, worker: _CodemcpWorker) -> None:
        async with self._workers_lock:
            if self._workers.get(project_id) is worker:
                self._workers.pop(project_id, None)
        await worker.close()

    async def call(
        self,
        project: ProjectSpec,
        subtool: str,
        arguments: dict[str, Any],
        *,
        timeout_seconds: float | None = None,
        mutation: bool = False,
    ) -> AdapterResult:
        worker = await self._get_worker(project)
        timeout = timeout_seconds or self._settings.codemcp.worker_timeout_seconds
        try:
            return await worker.call(subtool, arguments, timeout)
        except asyncio.CancelledError:
            await self._discard(project.project_id, worker)
            raise
        except TimeoutError as exc:
            await self._discard(project.project_id, worker)
            code = "UNKNOWN_SIDE_EFFECT" if mutation else "BACKEND_UNAVAILABLE"
            raise BridgeError(
                code,
                "codemcp worker timed out",
                {"subtool": subtool, "project_id": project.project_id},
                retryable=not mutation,
                status="unknown" if mutation else "failed",
            ) from exc
        except BridgeError:
            await self._discard(project.project_id, worker)
            raise
        except Exception as exc:
            await self._discard(project.project_id, worker)
            raise BridgeError(
                "BACKEND_UNAVAILABLE",
                "codemcp worker is unavailable",
                {"project_id": project.project_id, "subtool": subtool},
                retryable=True,
                status="failed",
            ) from exc

    def is_active(self, project_id: str) -> bool:
        worker = self._workers.get(project_id)
        return worker is not None and worker.is_active()

    async def close(self) -> None:
        async with self._workers_lock:
            workers = list(self._workers.values())
            self._workers.clear()
        await asyncio.gather(*(worker.close() for worker in workers), return_exceptions=True)
