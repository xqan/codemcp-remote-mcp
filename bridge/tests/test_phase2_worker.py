from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

import codemcp_bridge.native_codemcp_worker as native_worker_module
import codemcp_bridge.worker_manager as worker_manager_module
from codemcp_bridge.errors import BridgeError
from codemcp_bridge.settings import (
    BridgeSettings,
    CodemcpSettings,
    PolicySettings,
    ProjectSpec,
    ServerSettings,
    StorageSettings,
)
from codemcp_bridge.worker_manager import WorkerManager, _CodemcpWorker


def _settings(
    project: Path,
    data_dir: Path,
    *,
    worker_mode: str = "local",
) -> BridgeSettings:
    spec = ProjectSpec(
        project_id="demo",
        root=project,
        allowed_branches=("main",),
        require_clean_workspace=True,
        codemcp_config=project / "codemcp.toml",
        commands={},
    )
    return BridgeSettings(
        repository_root=project.parent,
        bridge_config_path=project.parent / "bridge.toml",
        projects_config_path=project.parent / "projects.toml",
        server=ServerSettings("127.0.0.1", 46200, "/mcp", "streamable-http"),
        storage=StorageSettings(data_dir, data_dir / "bridge.sqlite3", data_dir / "logs"),
        policy=PolicySettings(False, False, False, True, 1024, 4096, "per-project"),
        codemcp=CodemcpSettings(worker_mode, "Ubuntu", None, 10, 10, 5),
        projects={"demo": spec},
    )


def test_wsl_worker_git_environment_aligns_autocrlf_with_windows(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _settings(project, tmp_path / "data", worker_mode="wsl2")
    worker = _CodemcpWorker(settings, settings.projects["demo"])

    parameters = worker._parameters(os_name="nt")

    assert parameters.command == "wsl.exe"
    assert parameters.env is not None
    assert parameters.env["GIT_CONFIG_COUNT"] == "2"
    assert parameters.env["GIT_CONFIG_KEY_0"] == "core.excludesfile"
    assert parameters.env["GIT_CONFIG_KEY_1"] == "core.autocrlf"
    assert parameters.env["GIT_CONFIG_VALUE_1"] == "true"


def test_local_worker_uses_native_windows_entrypoint(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _settings(project, tmp_path / "data", worker_mode="local")
    worker = _CodemcpWorker(settings, settings.projects["demo"])

    parameters = worker._parameters(os_name="nt")

    assert parameters.command == sys.executable
    assert parameters.args == ["-m", "codemcp_bridge.native_codemcp_worker"]
    assert parameters.env is not None
    assert parameters.env["GIT_CONFIG_COUNT"] == "1"
    assert "GIT_CONFIG_KEY_1" not in parameters.env


def test_frozen_local_worker_reuses_executable_internal_entrypoint(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _settings(project, tmp_path / "data", worker_mode="local")
    worker = _CodemcpWorker(settings, settings.projects["demo"])

    parameters = worker._parameters(os_name="nt", frozen=True)

    assert parameters.command == sys.executable
    assert parameters.args == ["_worker"]
    assert parameters.cwd == str(project)


@pytest.mark.asyncio
async def test_native_worker_subprocess_patch_supplies_devnull_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, tuple[str, ...], dict[str, Any]]] = []

    async def fake_create_subprocess_exec(
        program: str,
        *args: str,
        **kwargs: Any,
    ) -> object:
        calls.append((program, args, kwargs))
        return object()

    monkeypatch.setattr(
        native_worker_module,
        "_ORIGINAL_CREATE_SUBPROCESS_EXEC",
        fake_create_subprocess_exec,
    )

    await native_worker_module._create_subprocess_exec_with_devnull_stdin("git", "--version")
    sentinel = object()
    await native_worker_module._create_subprocess_exec_with_devnull_stdin(
        "git",
        "status",
        stdin=sentinel,
    )

    assert calls[0][2]["stdin"] == asyncio.subprocess.DEVNULL
    assert calls[1][2]["stdin"] is sentinel


def test_native_worker_file_write_preserves_pre_normalized_crlf(tmp_path: Path) -> None:
    target = tmp_path / "crlf.txt"
    native_worker_module._write_file_sync_without_newline_translation(
        str(target),
        "first\r\nsecond\r\n",
    )

    assert target.read_bytes() == b"first\r\nsecond\r\n"


def test_native_worker_uses_stdlib_tomllib_as_tomli_compatibility() -> None:
    modules: dict[str, Any] = {}

    native_worker_module.install_tomli_compatibility(modules=modules)

    assert modules["tomli"] is native_worker_module.tomllib


def test_native_worker_patch_is_windows_only() -> None:
    from codemcp.tools import file_utils

    original_subprocess = asyncio.create_subprocess_exec
    original_write_file_sync = file_utils.write_file_sync
    try:
        assert native_worker_module.install_windows_compatibility(os_name="posix") is False
        assert asyncio.create_subprocess_exec is original_subprocess
        assert file_utils.write_file_sync is original_write_file_sync
        assert native_worker_module.install_windows_compatibility(os_name="nt") is True
        assert (
            asyncio.create_subprocess_exec
            is native_worker_module._create_subprocess_exec_with_devnull_stdin
        )
        assert (
            file_utils.write_file_sync
            is native_worker_module._write_file_sync_without_newline_translation
        )
    finally:
        asyncio.create_subprocess_exec = original_subprocess
        file_utils.write_file_sync = original_write_file_sync


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [(False, "BACKEND_UNAVAILABLE"), (True, "UNKNOWN_SIDE_EFFECT")],
)
async def test_worker_timeout_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: bool,
    expected_code: str,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _settings(project, tmp_path / "data")

    async def timeout(
        self: _CodemcpWorker,
        subtool: str,
        arguments: dict[str, object],
        timeout_seconds: float,
    ) -> object:
        del self, subtool, arguments, timeout_seconds
        raise TimeoutError("test timeout")

    monkeypatch.setattr(_CodemcpWorker, "call", timeout)
    manager = WorkerManager(settings)

    with pytest.raises(BridgeError) as raised:
        await manager.call(
            settings.projects["demo"],
            "EditFile" if mutation else "ReadFile",
            {},
            mutation=mutation,
        )
    assert raised.value.code == expected_code
    assert manager.is_active("demo") is False
    await manager.close()


@pytest.mark.asyncio
async def test_worker_crash_maps_to_backend_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _settings(project, tmp_path / "data")

    async def crash(
        self: _CodemcpWorker,
        subtool: str,
        arguments: dict[str, object],
        timeout_seconds: float,
    ) -> object:
        del self, subtool, arguments, timeout_seconds
        raise RuntimeError("test worker crash")

    monkeypatch.setattr(_CodemcpWorker, "call", crash)
    manager = WorkerManager(settings)

    with pytest.raises(BridgeError) as raised:
        await manager.call(settings.projects["demo"], "ReadFile", {})
    assert raised.value.code == "BACKEND_UNAVAILABLE"
    assert raised.value.retryable is True
    assert manager.is_active("demo") is False
    await manager.close()


@pytest.mark.asyncio
async def test_worker_contexts_are_owned_and_closed_by_same_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _settings(project, tmp_path / "data")
    events: list[tuple[str, asyncio.Task[Any] | None]] = []
    caller_task = asyncio.current_task()

    @asynccontextmanager
    async def fake_stdio_client(*args: Any, **kwargs: Any):
        del args, kwargs
        async with anyio.create_task_group():
            events.append(("stdio-enter", asyncio.current_task()))
            try:
                yield object(), object()
            finally:
                events.append(("stdio-exit", asyncio.current_task()))

    class FakeClientSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs
            self._task_group: Any = None

        async def __aenter__(self) -> FakeClientSession:
            self._task_group = anyio.create_task_group()
            await self._task_group.__aenter__()
            events.append(("session-enter", asyncio.current_task()))
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: object,
        ) -> bool | None:
            events.append(("session-exit", asyncio.current_task()))
            self._task_group.cancel_scope.cancel()
            return await self._task_group.__aexit__(exc_type, exc_val, exc_tb)

        async def initialize(self) -> None:
            return None

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            assert name == "codemcp"
            assert arguments["subtool"] == "ReadFile"
            return SimpleNamespace(
                content=[SimpleNamespace(text="ok")],
                structuredContent=None,
                isError=False,
            )

    monkeypatch.setattr(worker_manager_module, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(worker_manager_module, "ClientSession", FakeClientSession)

    worker = _CodemcpWorker(settings, settings.projects["demo"])
    first = await worker.call("ReadFile", {}, 1)
    second = await worker.call("ReadFile", {}, 1)

    assert first.text == "ok"
    assert second.text == "ok"
    assert worker.is_active()
    stdio_enter_tasks = [task for name, task in events if name == "stdio-enter"]
    session_enter_tasks = [task for name, task in events if name == "session-enter"]
    assert len(stdio_enter_tasks) == 1
    assert len(session_enter_tasks) == 1
    owner_task = stdio_enter_tasks[0]
    assert owner_task is not None
    assert owner_task is not caller_task
    assert session_enter_tasks[0] is owner_task
    assert not any(name.endswith("-exit") for name, _ in events)

    await asyncio.create_task(worker.close())

    session_exit_tasks = [task for name, task in events if name == "session-exit"]
    stdio_exit_tasks = [task for name, task in events if name == "stdio-exit"]
    assert session_exit_tasks == [owner_task]
    assert stdio_exit_tasks == [owner_task]
    assert worker.is_active() is False


@pytest.mark.asyncio
async def test_cancelled_worker_call_discards_and_closes_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _settings(project, tmp_path / "data")
    call_started = asyncio.Event()
    stdio_exited = asyncio.Event()

    @asynccontextmanager
    async def fake_stdio_client(*args: Any, **kwargs: Any):
        del args, kwargs
        try:
            yield object(), object()
        finally:
            stdio_exited.set()

    class FakeClientSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        async def __aenter__(self) -> FakeClientSession:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: object,
        ) -> None:
            del exc_type, exc_val, exc_tb

        async def initialize(self) -> None:
            return None

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            del name, arguments
            call_started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(worker_manager_module, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(worker_manager_module, "ClientSession", FakeClientSession)

    manager = WorkerManager(settings)
    call_task = asyncio.create_task(manager.call(settings.projects["demo"], "ReadFile", {}))
    await asyncio.wait_for(call_started.wait(), timeout=1)

    call_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call_task

    await asyncio.wait_for(stdio_exited.wait(), timeout=1)
    assert manager.is_active("demo") is False
    await manager.close()


@pytest.mark.asyncio
async def test_cancelled_startup_does_not_cancel_shared_startup_future(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _settings(project, tmp_path / "data")
    initialize_started = asyncio.Event()
    release_initialize = asyncio.Event()
    stdio_exited = asyncio.Event()

    @asynccontextmanager
    async def fake_stdio_client(*args: Any, **kwargs: Any):
        del args, kwargs
        try:
            yield object(), object()
        finally:
            stdio_exited.set()

    class FakeClientSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        async def __aenter__(self) -> FakeClientSession:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: object,
        ) -> None:
            del exc_type, exc_val, exc_tb

        async def initialize(self) -> None:
            initialize_started.set()
            await release_initialize.wait()

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            raise AssertionError(f"unexpected call after startup cancellation: {name} {arguments}")

    monkeypatch.setattr(worker_manager_module, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(worker_manager_module, "ClientSession", FakeClientSession)

    manager = WorkerManager(settings)
    call_task = asyncio.create_task(manager.call(settings.projects["demo"], "ReadFile", {}))
    await asyncio.wait_for(initialize_started.wait(), timeout=1)
    worker = manager._workers["demo"]

    call_task.cancel()
    await asyncio.sleep(0)
    assert worker._startup_future is not None
    assert worker._startup_future.cancelled() is False

    release_initialize.set()
    with pytest.raises(asyncio.CancelledError):
        await call_task

    await asyncio.wait_for(stdio_exited.wait(), timeout=1)
    assert manager.is_active("demo") is False
    await manager.close()


@pytest.mark.asyncio
async def test_manager_close_waits_for_inflight_call_then_closes_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    settings = _settings(project, tmp_path / "data")
    call_started = asyncio.Event()
    release_call = asyncio.Event()
    stdio_exited = asyncio.Event()

    @asynccontextmanager
    async def fake_stdio_client(*args: Any, **kwargs: Any):
        del args, kwargs
        try:
            yield object(), object()
        finally:
            stdio_exited.set()

    class FakeClientSession:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            del args, kwargs

        async def __aenter__(self) -> FakeClientSession:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: object,
        ) -> None:
            del exc_type, exc_val, exc_tb

        async def initialize(self) -> None:
            return None

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
            del name, arguments
            call_started.set()
            await release_call.wait()
            return SimpleNamespace(
                content=[SimpleNamespace(text="ok")],
                structuredContent=None,
                isError=False,
            )

    monkeypatch.setattr(worker_manager_module, "stdio_client", fake_stdio_client)
    monkeypatch.setattr(worker_manager_module, "ClientSession", FakeClientSession)

    manager = WorkerManager(settings)
    call_task = asyncio.create_task(manager.call(settings.projects["demo"], "ReadFile", {}))
    await asyncio.wait_for(call_started.wait(), timeout=1)

    close_task = asyncio.create_task(manager.close())
    await asyncio.sleep(0)
    assert close_task.done() is False

    release_call.set()
    result = await asyncio.wait_for(call_task, timeout=1)
    await asyncio.wait_for(close_task, timeout=1)

    assert result.text == "ok"
    assert stdio_exited.is_set()
    assert manager.is_active("demo") is False
