"""Platform secret storage with fail-closed native backends."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

KEYCHAIN_SERVICE = "codemcp-remote"


class SecretStoreError(RuntimeError):
    """Raised when a native secret backend cannot be used safely."""


@dataclass(frozen=True, slots=True)
class SecretValue:
    value: str | None
    source: str


class SecretStore(Protocol):
    """Minimal native secret storage contract."""

    source: str

    def read(self) -> SecretValue:
        """Read a secret without falling back to plaintext storage."""

    def write(self, value: str) -> str:
        """Persist a secret and return the backend source label."""


def canonical_home_identity(home: Path) -> str:
    """Return a stable, non-secret identity for one writable runtime home."""

    canonical = home.expanduser().resolve(strict=False)
    normalized = os.path.normcase(str(canonical))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def keychain_account(home: Path, logical_secret_id: str) -> str:
    """Derive a stable Keychain account from home identity and logical secret id."""

    if not logical_secret_id or any(character in logical_secret_id for character in "\r\n\x00"):
        raise SecretStoreError("logical secret id is invalid")
    return f"{logical_secret_id}:{canonical_home_identity(home)}"


@dataclass(slots=True)
class WindowsDpapiSecretStore:
    secret_path: Path
    protect: Callable[[bytes], bytes]
    unprotect: Callable[[bytes], bytes]
    source: str = "windows-dpapi"

    def read(self) -> SecretValue:
        if not self.secret_path.is_file():
            return SecretValue(None, "none")
        try:
            value = self.unprotect(self.secret_path.read_bytes()).decode("utf-8")
        except (OSError, UnicodeDecodeError, RuntimeError) as exc:
            raise SecretStoreError(f"Windows DPAPI secret read failed: {exc}") from exc
        return SecretValue(value, self.source)

    def write(self, value: str) -> str:
        if not value:
            raise SecretStoreError("secret value must not be empty")
        try:
            self.secret_path.parent.mkdir(parents=True, exist_ok=True)
            self.secret_path.write_bytes(self.protect(value.encode("utf-8")))
        except (OSError, RuntimeError) as exc:
            raise SecretStoreError(f"Windows DPAPI secret write failed: {exc}") from exc
        return self.source


def _macos_keyring_backend():
    try:
        from keyring.backends.macOS import Keyring
    except (ImportError, ModuleNotFoundError) as exc:
        raise SecretStoreError("macOS Keychain backend is unavailable") from exc
    try:
        return Keyring()
    except Exception as exc:
        raise SecretStoreError(f"macOS Keychain backend initialization failed: {exc}") from exc


@dataclass(slots=True)
class MacOSKeychainSecretStore:
    home: Path
    logical_secret_id: str
    source: str = "macos-keychain"

    @property
    def account(self) -> str:
        return keychain_account(self.home, self.logical_secret_id)

    def read(self) -> SecretValue:
        backend = _macos_keyring_backend()
        try:
            value = backend.get_password(KEYCHAIN_SERVICE, self.account)
        except Exception as exc:
            raise SecretStoreError(f"macOS Keychain read failed: {exc}") from exc
        if value is None:
            return SecretValue(None, "none")
        if not isinstance(value, str):
            raise SecretStoreError("macOS Keychain returned an invalid secret value")
        return SecretValue(value, self.source)

    def write(self, value: str) -> str:
        if not value:
            raise SecretStoreError("secret value must not be empty")
        backend = _macos_keyring_backend()
        try:
            backend.set_password(KEYCHAIN_SERVICE, self.account, value)
        except Exception as exc:
            raise SecretStoreError(f"macOS Keychain write failed: {exc}") from exc
        return self.source
