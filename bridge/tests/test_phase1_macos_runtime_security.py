from __future__ import annotations

from pathlib import Path

import pytest

import codemcp_bridge.lifecycle as lifecycle
import codemcp_bridge.secret_store as secret_store
from codemcp_bridge.secret_store import (
    KEYCHAIN_SERVICE,
    MacOSKeychainSecretStore,
    SecretStoreError,
    keychain_account,
)
from codemcp_bridge.transports import OPENAI_TUNNEL_PROVIDER


class _FakeKeychain:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.get_calls: list[tuple[str, str]] = []
        self.set_calls: list[tuple[str, str, str]] = []

    def get_password(self, service: str, account: str) -> str | None:
        self.get_calls.append((service, account))
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.set_calls.append((service, account, value))
        self.values[(service, account)] = value


class _FailingKeychain:
    def __init__(self, message: str) -> None:
        self.message = message

    def get_password(self, service: str, account: str) -> str | None:
        del service, account
        raise RuntimeError(self.message)

    def set_password(self, service: str, account: str, value: str) -> None:
        del service, account, value
        raise RuntimeError(self.message)


def _paths(tmp_path: Path) -> lifecycle.RuntimePaths:
    return lifecycle.runtime_paths(
        tmp_path / "distribution",
        home=tmp_path / "home",
    )


def _logical_transport_secret_id() -> str:
    provider = OPENAI_TUNNEL_PROVIDER
    return f"transport:{provider.provider_id}:{provider.secret_env_name}"


def test_keychain_account_is_stable_and_scoped_to_home_and_secret(tmp_path: Path) -> None:
    home = tmp_path / "home"

    first = keychain_account(home, "logical-a")
    assert first == keychain_account(home / ".", "logical-a")
    assert first != keychain_account(tmp_path / "other-home", "logical-a")
    assert first != keychain_account(home, "logical-b")


def test_macos_keychain_store_uses_fixed_service_and_stable_account(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeKeychain()
    monkeypatch.setattr(secret_store, "_macos_keyring_backend", lambda: backend)
    store = MacOSKeychainSecretStore(tmp_path / "home", "logical-secret")

    assert store.write("secret-value") == "macos-keychain"
    value = store.read()

    assert value.value == "secret-value"
    assert value.source == "macos-keychain"
    expected_account = keychain_account(tmp_path / "home", "logical-secret")
    assert backend.set_calls == [(KEYCHAIN_SERVICE, expected_account, "secret-value")]
    assert backend.get_calls == [(KEYCHAIN_SERVICE, expected_account)]


def test_macos_keychain_missing_secret_reports_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeKeychain()
    monkeypatch.setattr(secret_store, "_macos_keyring_backend", lambda: backend)

    value = MacOSKeychainSecretStore(tmp_path / "home", "logical-secret").read()

    assert value.value is None
    assert value.source == "none"


@pytest.mark.parametrize("message", ["keychain locked", "backend unavailable"])
def test_macos_keychain_errors_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
) -> None:
    monkeypatch.setattr(
        secret_store,
        "_macos_keyring_backend",
        lambda: _FailingKeychain(message),
    )
    store = MacOSKeychainSecretStore(tmp_path / "home", "logical-secret")

    with pytest.raises(SecretStoreError, match=message):
        store.read()
    with pytest.raises(SecretStoreError, match=message):
        store.write("secret-value")


def test_environment_secret_always_wins_over_macos_keychain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    provider = OPENAI_TUNNEL_PROVIDER
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")
    monkeypatch.setenv(provider.secret_env_name, "environment-secret")
    monkeypatch.setattr(
        secret_store,
        "_macos_keyring_backend",
        lambda: pytest.fail("Keychain must not be consulted when the environment has the secret"),
    )

    value = lifecycle._transport_secret_value(paths, provider)

    assert value.value == "environment-secret"
    assert value.source == "environment"


def test_lifecycle_reads_macos_keychain_and_reports_real_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    provider = OPENAI_TUNNEL_PROVIDER
    backend = _FakeKeychain()
    logical_id = _logical_transport_secret_id()
    backend.values[(KEYCHAIN_SERVICE, keychain_account(paths.home, logical_id))] = "stored-secret"
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")
    monkeypatch.delenv(provider.secret_env_name, raising=False)
    monkeypatch.setattr(secret_store, "_macos_keyring_backend", lambda: backend)

    value = lifecycle._transport_secret_value(paths, provider)

    assert value.value == "stored-secret"
    assert value.source == "macos-keychain"


def test_lifecycle_keychain_failure_does_not_fall_back_to_plaintext(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    provider = OPENAI_TUNNEL_PROVIDER
    paths.secret_dir.mkdir(parents=True)
    plaintext_path = paths.secret_dir / provider.secret_file_name
    plaintext_path.write_text("must-not-be-read", encoding="utf-8")
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")
    monkeypatch.delenv(provider.secret_env_name, raising=False)
    monkeypatch.setattr(
        secret_store,
        "_macos_keyring_backend",
        lambda: _FailingKeychain("keychain locked"),
    )

    with pytest.raises(lifecycle.LifecycleError, match="keychain locked"):
        lifecycle._transport_secret_value(paths, provider)

    assert plaintext_path.read_text(encoding="utf-8") == "must-not-be-read"


def test_lifecycle_stores_macos_secret_without_creating_plaintext_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = _paths(tmp_path)
    provider = OPENAI_TUNNEL_PROVIDER
    backend = _FakeKeychain()
    monkeypatch.setattr(lifecycle.sys, "platform", "darwin")
    monkeypatch.setenv(provider.secret_env_name, "environment-secret")
    monkeypatch.setattr(secret_store, "_macos_keyring_backend", lambda: backend)

    source = lifecycle.store_transport_secret_from_environment(paths, provider=provider)

    assert source == "macos-keychain"
    assert not (paths.secret_dir / provider.secret_file_name).exists()
