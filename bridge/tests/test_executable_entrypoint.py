from __future__ import annotations

import sys
from pathlib import Path

import codemcp_bridge.main as main_module


def test_runtime_root_uses_executable_directory_when_frozen(tmp_path: Path) -> None:
    executable = tmp_path / "dist" / "codemcp-remote.exe"

    assert (
        main_module.runtime_root(frozen=True, executable=executable) == executable.parent.resolve()
    )


def test_packaged_executable_defaults_to_start_and_install_directory(tmp_path: Path) -> None:
    runtime = tmp_path / "installed"

    assert main_module.default_cli_command(frozen=True) == "start"
    assert main_module.default_cli_command(frozen=False) == "serve"
    assert (
        main_module.default_runtime_home(runtime, frozen=True, platform="win32")
        == runtime.resolve()
    )
    assert main_module.default_runtime_home(runtime, frozen=False) is None


def test_macos_packaged_home_is_application_support(tmp_path: Path) -> None:
    runtime = tmp_path / "distribution"
    user_home = tmp_path / "user"

    assert (
        main_module.default_runtime_home(
            runtime,
            frozen=True,
            platform="darwin",
            user_home=user_home,
        )
        == (user_home / "Library" / "Application Support" / "codemcp-remote").resolve()
    )


def test_bundled_runtime_root_is_separate_from_distribution(tmp_path: Path) -> None:
    distribution = tmp_path / "distribution"

    assert (
        main_module.bundled_runtime_root(distribution)
        == (distribution / ".codemcp-runtime").resolve()
    )


def test_internal_worker_dispatch_skips_bridge_configuration(
    monkeypatch,
) -> None:
    calls: list[str] = []

    monkeypatch.setattr(sys, "argv", ["codemcp-remote.exe", "_worker"])
    monkeypatch.setattr(main_module, "native_worker_main", lambda: calls.append("worker"))

    assert main_module.main() == 0
    assert calls == ["worker"]
    assert sys.argv == ["codemcp-remote.exe"]
