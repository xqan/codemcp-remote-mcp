from __future__ import annotations

import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
MANIFEST_PATH = SCRIPTS / "release-assets" / "macos-v0.1.0.json"
ASSET_HELPER_PATH = SCRIPTS / "prepare-verified-release-asset.py"


def _load_asset_helper():
    spec = importlib.util.spec_from_file_location(
        "prepare_verified_release_asset", ASSET_HELPER_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _write_tgz(path: Path, entries: list[tuple[str, bytes, str]]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(content)
                info.mode = 0o755
                archive.addfile(info, io.BytesIO(content))
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = "target"
                archive.addfile(info)
            else:
                raise AssertionError(f"unknown fixture kind: {kind}")


def test_release_manifest_is_unblocked_with_verified_cloudflared_pins() -> None:
    helper = _load_asset_helper()
    manifest = _manifest()

    assert helper._validate_manifest(manifest) == []
    assert manifest["integrity_blockers"] == []


def test_release_manifest_rejects_github_asset_digest_drift() -> None:
    helper = _load_asset_helper()
    manifest = _manifest()
    manifest["assets"]["cloudflared-arm64"]["sha256"] = "0" * 64

    with pytest.raises(helper.AssetError, match="must match the GitHub asset digest"):
        helper._validate_manifest(manifest)


def test_release_manifest_has_expected_build_tool_pins_and_no_tunnel_client() -> None:
    manifest = _manifest()
    assets = manifest["assets"]

    expected = {
        "pyinstaller": "ebd1b1ca932d7cf25d7366ce691aaf79a5ff9425811ed7328b5116e4471b6d6d",
        "pyinstaller-hooks-contrib": "fd13b8ac126b35361175edacd41a0d97080b75dd5f4b594ecefefff969509dd3",
        "altgraph": "f3a22400bce1b0c701683820ac4f3b159cd301acab067c51c653e06961600597",
        "macholib": "da1a3fa8266e30f0ce7e97c6a54eefaae8edd1e5f86f3eb8b95457cae90265ea",
        "packaging": "d7193f7c8e4e93f444fde0262bf90af30e16fa0ad0ad44cb553c87339b23cd1c",
        "setuptools": "51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670",
    }
    for asset_id, digest in expected.items():
        assert assets[asset_id]["sha256"] == digest

    assert "tunnel-client" not in json.dumps(manifest, ensure_ascii=False).lower()


def test_cloudflared_asset_digest_is_current_and_release_notes_mismatch_is_retained() -> None:
    manifest = _manifest()
    assets = manifest["assets"]

    expected = {
        "cloudflared-x86_64": {
            "asset": "70d1c8684fa6d14b5843787ec8d1ea8e18b23650e424f4ea43d849a506487c3b",
            "release_notes": "e88fe5874d42a94f49a7ea59cabc3722d2962d0449232b0f3b1a426a712e275c",
        },
        "cloudflared-arm64": {
            "asset": "90c5a4f914d705fd70c135dba6d80b1791d254b08d6d4136301941f88330dd09",
            "release_notes": "f35c50089cd25f77a4cb5a2152036bc26db15aa31fbe11f7995d2e42a4ed6257",
        },
    }
    for asset_id, digests in expected.items():
        assert assets[asset_id]["sha256"] == digests["asset"]
        assert assets[asset_id]["github_asset_digest_sha256"] == digests["asset"]
        assert assets[asset_id]["upstream_release_notes_sha256"] == digests["release_notes"]
        assert digests["asset"] != digests["release_notes"]

    assert manifest["cloudflared_license"]["sha256"] == (
        "58d1e17ffe5109a7ae296caafcadfdbe6a7d176f0bc4ab01e12a689b0499d8bd"
    )


def test_safe_cloudflared_tgz_extracts_exactly_one_regular_file(tmp_path: Path) -> None:
    helper = _load_asset_helper()
    archive = tmp_path / "cloudflared.tgz"
    destination = tmp_path / "bin" / "cloudflared"
    _write_tgz(archive, [("cloudflared", b"binary", "file")])

    result = helper._extract_single_tgz(archive, "cloudflared", destination)

    assert result == destination
    assert destination.read_bytes() == b"binary"


@pytest.mark.parametrize(
    "entries",
    [
        [("../cloudflared", b"binary", "file")],
        [("/cloudflared", b"binary", "file")],
        [("cloudflared", b"", "symlink")],
        [
            ("cloudflared", b"binary", "file"),
            ("extra", b"unexpected", "file"),
        ],
    ],
)
def test_safe_cloudflared_tgz_rejects_unsafe_or_ambiguous_archives(
    tmp_path: Path,
    entries: list[tuple[str, bytes, str]],
) -> None:
    helper = _load_asset_helper()
    archive = tmp_path / "cloudflared.tgz"
    _write_tgz(archive, entries)

    with pytest.raises(helper.AssetError):
        helper._extract_single_tgz(archive, "cloudflared", tmp_path / "cloudflared")


def test_install_script_keeps_secret_out_of_argv_and_checks_distribution_first() -> None:
    script = (SCRIPTS / "codemcp-install.sh").read_text(encoding="utf-8")

    checksum_index = script.index("shasum -a 256 -c SHA256SUMS.txt")
    secret_prompt_index = script.index("Cloudflare remotely-managed Tunnel token")
    init_index = script.index('"$CODEMCP" init')

    assert checksum_index < secret_prompt_index < init_index
    assert "stty -echo" in script
    assert "trap on_int 2" in script
    assert "trap on_term 15" in script
    assert "export TUNNEL_TOKEN" in script
    assert "--store-transport-secret" in script
    assert "--transport cloudflare" in script
    assert "--auth-mode none" in script
    assert "--network-trust cloudflare-chatgpt" in script
    assert "--force" not in script
    assert "eval " not in script
    assert "--tunnel-token" not in script
    assert '"$TUNNEL_TOKEN"' not in script[init_index:]
    assert '"$CODEMCP" start' not in script


def test_start_stop_scripts_are_relocatable_and_do_not_override_home() -> None:
    start = (SCRIPTS / "codemcp-start.sh").read_text(encoding="utf-8")
    stop = (SCRIPTS / "codemcp-stop.sh").read_text(encoding="utf-8")

    assert 'exec "$SCRIPT_DIR/codemcp-remote" start' in start
    assert 'exec "$SCRIPT_DIR/codemcp-remote" stop' in stop
    assert "--home" not in start
    assert "--home" not in stop
    assert "eval " not in start
    assert "eval " not in stop


def test_macos_build_script_uses_native_onedir_adhoc_contract() -> None:
    script = (SCRIPTS / "build-macos-release.sh").read_text(encoding="utf-8")

    required = (
        "--onedir",
        "--target-arch",
        "--contents-directory .codemcp-runtime",
        "--hidden-import keyring.backends.macOS",
        "codesign --force --sign -",
        "codemcp-remote-build-provenance-v2",
        '"status": "not_performed"',
        '"reason": "no_certificate"',
        '"external_inputs"',
        '"github_release_asset_sha256"',
        '"upstream_release_notes_sha256"',
        '"extracted_pre_sign_sha256"',
        '"expected_release_tag": "v0.1.0"',
        'SOURCE_TAG=$(git -C "$ROOT" describe --tags --exact-match HEAD',
        "UV_VERSION=$(printf '%s\\n' \"$UV_VERSION_OUTPUT\" | awk '{print $2}')",
        'getattr(sys, "_stdlib_dir", "")',
        "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2",
        "cloudflared-license",
        "printf '%s\\n' \"$OTOOL_OUTPUT\" | sed '1d' |",
        "SHA256SUMS.txt",
    )
    for value in required:
        assert value in script
    assert "lipo -create" not in script
    assert "notarytool" not in script
    assert "tunnel-client" not in script
    assert "HEAD must be exactly tagged" not in script


def test_macos_release_workflow_uses_native_dual_arch_and_immutable_actions() -> None:
    workflow = (ROOT / ".github" / "workflows" / "macos-release.yml").read_text(encoding="utf-8")

    required = (
        "runner: macos-15",
        "arch: arm64",
        "label: macos-arm64",
        "runner: macos-15-intel",
        "arch: x86_64",
        "label: macos-intel64",
        "version: ${{ env.UV_VERSION }}",
        "bash ./scripts/build-macos-release.sh",
        "bash ./scripts/validate-macos-release.sh",
        "--expect-spctl-rejection",
        '--source-commit "$GITHUB_SHA"',
        "macos-candidate-convergence",
        "codemcp-remote-macos-convergence-v1",
        "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
        "actions/download-artifact@018cc2cf5baa6db3ef3c5f8a56943fffe632ef53",
    )
    for value in required:
        assert value in workflow

    assert "actions/upload-artifact@v" not in workflow
    assert "actions/download-artifact@v" not in workflow
    assert "notarytool" not in workflow
    assert "lipo -create" not in workflow
    assert "chmod +x scripts/" not in workflow


def test_macos_release_validator_enforces_layout_signing_and_gatekeeper_limit() -> None:
    script = (SCRIPTS / "validate-macos-release.sh").read_text(encoding="utf-8")

    required = (
        "unexpected top-level archive entry",
        "hardlink is not allowed",
        "symlink escapes release root",
        "directory mode must be 0755",
        "shasum -a 256 -c SHA256SUMS.txt",
        "foreign or universal Mach-O",
        "codesign --verify --strict --all-architectures",
        "Signature=adhoc",
        "TeamIdentifier=not set",
        "/opt/homebrew/",
        "printf '%s\\n' \"$OTOOL_OUTPUT\" | sed '1d' |",
        "codemcp-remote-build-provenance-v2",
        "spctl --assess --type execute",
        "Gatekeeper unexpectedly accepted",
        "codemcp-remote-macos-validation-v1",
    )
    for value in required:
        assert value in script

    assert "notarytool" not in script
    assert "tunnel-client must not be present" in script


def test_core_ci_has_native_arm64_macos_source_runtime_gate() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    required = (
        "macos-source:",
        "runs-on: macos-15",
        'python-version: "3.12.10"',
        'version: "0.12.7"',
        "keyring.backends.macOS",
        "bridge/tests/test_phase1_macos_runtime_security.py",
        "bridge/tests/test_phase3_lifecycle.py",
        "bridge/tests/test_phase55_cloudflare_transport.py",
        "bridge/tests/test_macos_packaging.py",
    )
    for value in required:
        assert value in workflow


def test_clean_macos_harness_keeps_secret_interactive_and_records_native_evidence() -> None:
    script = (SCRIPTS / "validate-clean-macos-release.sh").read_text(encoding="utf-8")

    required = (
        "prepare|verify|secret-scan|lifecycle|cleanup",
        'env -i HOME="$SANDBOX_HOME" PATH="$SAFE_PATH"',
        "codemcp-remote-macos-clean-host-v1",
        "archive hash differs from CI evidence",
        "candidate architecture differs from host",
        "signing.mode",
        "notarization.status",
        "spctl --assess --type execute",
        'xattr -dr com.apple.quarantine "$DIST"',
        "checks.tunnel_token.source",
        "macos-keychain",
        "grep -R -l -F -f -",
        "lifecycle_cycles_passed",
        "security delete-generic-password",
    )
    for value in required:
        assert value in script

    assert "--tunnel-token" not in script
    assert "--token " not in script
    assert "CODEMCP_HOME=" not in script
