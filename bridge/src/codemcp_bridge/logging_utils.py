"""Centralized, bounded and redacted runtime logging for the Bridge."""

from __future__ import annotations

import logging
import os
import re
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import BinaryIO, TextIO

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_KEY_VALUE_PATTERN = re.compile(
    r"(?i)((?:[\"']?\b(?:CONTROL_PLANE_API_KEY|OPENAI_API_KEY|API_KEY|"
    r"AUTHORIZATION|ACCESS_TOKEN|REFRESH_TOKEN|TOKEN)\b[\"']?)\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY_PATTERN = re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}")


def redact_text(value: str) -> str:
    """Remove common API-key and authorization forms from diagnostic text."""

    redacted = _BEARER_PATTERN.sub("Bearer <redacted>", value)
    redacted = _KEY_VALUE_PATTERN.sub(r"\1<redacted>", redacted)
    return _OPENAI_KEY_PATTERN.sub("<redacted-api-key>", redacted)


class RedactingFormatter(logging.Formatter):
    """Format records and redact the final rendered message and traceback."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def configure_logging(log_dir: Path) -> Path:
    """Attach the bounded Bridge file handler and return its path."""

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = (log_dir / "bridge.log").resolve()
    root_logger = logging.getLogger()

    for handler in list(root_logger.handlers):
        existing_path = getattr(handler, "baseFilename", None)
        if existing_path and Path(existing_path).resolve() == log_path:
            root_logger.removeHandler(handler)
            handler.close()

    handler = RotatingFileHandler(
        log_path,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
        delay=True,
    )
    handler.setFormatter(RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    return log_path


def _rotate_worker_log(path: Path, *, force: bool = False) -> None:
    if not path.is_file() or (not force and path.stat().st_size < LOG_MAX_BYTES):
        return

    for index in range(LOG_BACKUP_COUNT - 1, 0, -1):
        source = Path(f"{path}.{index}")
        destination = Path(f"{path}.{index + 1}")
        if source.is_file():
            if destination.exists():
                destination.unlink()
            source.replace(destination)
    first_backup = Path(f"{path}.1")
    if first_backup.exists():
        first_backup.unlink()
    path.replace(first_backup)


def _contains_utf16le_output(path: Path) -> bool:
    """Identify a legacy mixed-encoding worker log before appending to it."""

    try:
        sample = path.read_bytes()[: 64 * 1024]
    except OSError:
        return False
    return b"\x00" in sample and (b"\n\x00" in sample or b"\r\x00" in sample)


def _looks_like_utf16le(value: bytes) -> bool:
    """Detect the UTF-16LE diagnostics emitted by wsl.exe without a BOM."""

    return len(value) >= 4 and value.count(b"\x00") >= max(1, len(value) // 8)


def _decode_worker_stderr_line(value: bytes) -> tuple[str, bool]:
    """Decode one worker stderr line and report whether it was UTF-16LE."""

    if _looks_like_utf16le(value):
        # A UTF-16LE newline leaves its second byte at the start of the next
        # byte-oriented line after splitting on b"\n".
        value = value.lstrip(b"\x00")
        return value.decode("utf-16-le", errors="replace").rstrip("\r"), True
    return value.decode("utf-8", errors="replace").rstrip("\r"), False


class _WorkerStderrForwarder:
    """Expose a pipe handle to subprocesses and write normalized UTF-8 logs."""

    def __init__(self, log_path: Path):
        self._reader_fd, writer_fd = os.pipe()
        self._reader: BinaryIO = os.fdopen(self._reader_fd, "rb", closefd=True)
        self._writer: BinaryIO = os.fdopen(writer_fd, "wb", closefd=True)
        self._log = log_path.open("a", encoding="utf-8", newline="")
        self._thread = threading.Thread(
            target=self._forward,
            name="codemcp-worker-stderr",
            daemon=True,
        )
        self._closed = False
        self._thread.start()

    def fileno(self) -> int:
        """Return the OS handle used as the child process stderr target."""

        return self._writer.fileno()

    def flush(self) -> None:
        self._writer.flush()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._writer.close()
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            self._reader.close()
            self._thread.join(timeout=2)
        self._log.close()

    def _forward(self) -> None:
        pending = bytearray()
        try:
            while True:
                chunk = self._reader.read(4096)
                if not chunk:
                    break
                pending.extend(chunk)
                while True:
                    newline = pending.find(b"\n")
                    if newline < 0:
                        break
                    raw_line = bytes(pending[:newline])
                    del pending[: newline + 1]
                    text, is_utf16le = _decode_worker_stderr_line(raw_line)
                    self._log.write(text + "\n")
                    self._log.flush()
                    if is_utf16le and pending.startswith(b"\x00"):
                        del pending[0]
            if pending:
                text, _ = _decode_worker_stderr_line(bytes(pending))
                self._log.write(text + "\n")
                self._log.flush()
        except (OSError, ValueError):
            # The worker can be terminated while its stderr pipe is closing.
            pass
        finally:
            self._reader.close()


def open_worker_stderr(
    log_dir: Path,
    project_id: str,
    *,
    normalize_subprocess_output: bool = False,
) -> TextIO | _WorkerStderrForwarder:
    """Open an append-only, rotated stderr file for one codemcp worker.

    WSL's Windows launcher writes diagnostics as UTF-16LE while the Linux
    Python worker writes UTF-8.  When requested, capture stderr through a
    pipe and normalize both streams before writing the project log.
    """

    if not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ValueError("invalid project_id for worker log")
    worker_log_dir = log_dir / "workers"
    worker_log_dir.mkdir(parents=True, exist_ok=True)
    log_path = worker_log_dir / f"{project_id}.stderr.log"
    _rotate_worker_log(log_path)
    if normalize_subprocess_output:
        if _contains_utf16le_output(log_path):
            _rotate_worker_log(log_path, force=True)
        return _WorkerStderrForwarder(log_path)
    return log_path.open("a+", encoding="utf-8")
