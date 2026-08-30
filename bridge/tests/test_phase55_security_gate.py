from __future__ import annotations

import ast
import compileall
import subprocess
from pathlib import Path

import codemcp_bridge.resource_auth as resource_auth

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_phase556_compileall_source_tree() -> None:
    assert compileall.compile_dir(
        _REPOSITORY_ROOT / "bridge" / "src",
        quiet=1,
        force=True,
    )


def test_phase556_git_diff_check() -> None:
    completed = subprocess.run(
        ["git", "diff", "--check"],
        cwd=_REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stdout or completed.stderr


def test_resource_auth_keeps_protocol_only_runtime_dependencies() -> None:
    source = Path(resource_auth.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    import_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            import_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            import_roots.add(node.module.split(".", 1)[0])

    forbidden = {
        "cloudflare",
        "cryptography",
        "jwt",
        "mcp_auth_server",
        "workers_oauth_provider",
    }
    assert import_roots.isdisjoint(forbidden)
