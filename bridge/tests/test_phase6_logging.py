from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

from codemcp_bridge.logging_utils import (
    LOG_BACKUP_COUNT,
    LOG_MAX_BYTES,
    configure_logging,
    open_worker_stderr,
    redact_text,
)


def test_redact_text_removes_key_and_authorization_forms() -> None:
    source = (
        "CONTROL_PLANE_API_KEY=sk-test-secret-value "
        "authorization: Bearer bearer-secret-value "
        'payload={"api_key":"another-secret"}'
    )

    redacted = redact_text(source)

    assert "sk-test-secret-value" not in redacted
    assert "bearer-secret-value" not in redacted
    assert "another-secret" not in redacted
    assert redacted.count("<redacted") >= 3


def test_configure_logging_writes_bounded_redacted_file(tmp_path: Path) -> None:
    root_logger = logging.getLogger()
    previous_level = root_logger.level
    handler = configure_logging(tmp_path)
    try:
        assert isinstance(handler, RotatingFileHandler) or any(
            isinstance(item, RotatingFileHandler) and item.baseFilename.endswith("bridge.log")
            for item in root_logger.handlers
        )
        file_handler = next(
            item
            for item in root_logger.handlers
            if isinstance(item, RotatingFileHandler) and item.baseFilename.endswith("bridge.log")
        )
        assert file_handler.maxBytes == LOG_MAX_BYTES
        assert file_handler.backupCount == LOG_BACKUP_COUNT

        logging.getLogger("phase6-test").warning("API_KEY=%s", "sk-test-file-secret")
        file_handler.flush()
        content = (tmp_path / "bridge.log").read_text(encoding="utf-8")
        assert "sk-test-file-secret" not in content
        assert "<redacted>" in content or "<redacted-api-key>" in content
    finally:
        for item in list(root_logger.handlers):
            if getattr(item, "baseFilename", "") == str((tmp_path / "bridge.log").resolve()):
                root_logger.removeHandler(item)
                item.close()
        root_logger.setLevel(previous_level)


def test_worker_stderr_is_unified_and_project_scoped(tmp_path: Path) -> None:
    stream = open_worker_stderr(tmp_path, "sample_project")
    try:
        stream.write("worker diagnostic\n")
        stream.flush()
    finally:
        stream.close()

    assert (tmp_path / "workers" / "sample_project.stderr.log").read_text(
        encoding="utf-8"
    ) == "worker diagnostic\n"


def test_worker_stderr_normalizes_wsl_utf16_and_python_utf8(
    tmp_path: Path,
) -> None:
    legacy_log = tmp_path / "workers" / "sample_project.stderr.log"
    legacy_log.parent.mkdir(parents=True)
    legacy_log.write_bytes("wsl: legacy warning\r\n".encode("utf-16-le"))

    stream = open_worker_stderr(
        tmp_path,
        "sample_project",
        normalize_subprocess_output=True,
    )
    try:
        os.write(stream.fileno(), "wsl: localhost proxy warning\r\n".encode("utf-16-le"))
        os.write(stream.fileno(), b"worker diagnostic\n")
    finally:
        stream.close()

    assert (tmp_path / "workers" / "sample_project.stderr.log").read_text(
        encoding="utf-8"
    ) == "wsl: localhost proxy warning\nworker diagnostic\n"
    assert (tmp_path / "workers" / "sample_project.stderr.log.1").read_bytes() == (
        "wsl: legacy warning\r\n".encode("utf-16-le")
    )


def test_worker_stderr_rotates_when_size_limit_is_reached(tmp_path: Path) -> None:
    worker_dir = tmp_path / "workers"
    worker_dir.mkdir()
    current_log = worker_dir / "sample_project.stderr.log"
    current_log.write_bytes(b"x" * (LOG_MAX_BYTES + 1))

    stream = open_worker_stderr(tmp_path, "sample_project")
    stream.close()

    rotated_log = worker_dir / "sample_project.stderr.log.1"
    assert rotated_log.is_file()
    assert rotated_log.stat().st_size == LOG_MAX_BYTES + 1
    assert current_log.stat().st_size == 0
