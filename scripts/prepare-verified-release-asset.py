#!/usr/bin/env python3
"""Fetch release inputs only when repository pins and archive structure are safe."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA = "codemcp-remote-release-assets-v1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_HOSTS = {
    "files.pythonhosted.org",
    "github.com",
    "raw.githubusercontent.com",
}
USER_AGENT = "codemcp-remote-release-builder/0.1.0"


class AssetError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetError(f"cannot read release asset manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise AssetError("release asset manifest must contain a JSON object")
    return data


def _validate_https_url(url: object) -> None:
    if not isinstance(url, str):
        raise AssetError("asset URL must be a string")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.hostname not in ALLOWED_HOSTS:
        raise AssetError(f"release asset URL is not allowed: {url}")
    if parsed.username or parsed.password or parsed.fragment:
        raise AssetError(f"release asset URL contains forbidden components: {url}")


def _validate_manifest(data: dict[str, Any]) -> list[dict[str, str]]:
    if data.get("schema") != SCHEMA:
        raise AssetError("unsupported release asset manifest schema")
    if data.get("release_version") != "0.1.0":
        raise AssetError("release asset manifest version must be 0.1.0")

    serialized = json.dumps(data, ensure_ascii=False).lower()
    if "tunnel-client" in serialized:
        raise AssetError("macOS release asset manifest must not contain tunnel-client")

    assets = data.get("assets")
    if not isinstance(assets, dict) or not assets:
        raise AssetError("release asset manifest has no assets")

    for asset_id, value in assets.items():
        if not isinstance(asset_id, str) or not isinstance(value, dict):
            raise AssetError("release asset entries must be named objects")
        filename = value.get("filename")
        if (
            not isinstance(filename, str)
            or not filename
            or filename != Path(filename).name
            or "/" in filename
            or "\\" in filename
        ):
            raise AssetError(f"invalid filename for release asset {asset_id}")
        _validate_https_url(value.get("url"))
        expected = value.get("sha256")
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            raise AssetError(f"invalid SHA-256 pin for release asset {asset_id}")
        github_digest = value.get("github_asset_digest_sha256")
        if github_digest is not None:
            if not isinstance(github_digest, str) or not SHA256_RE.fullmatch(github_digest):
                raise AssetError(f"invalid GitHub asset digest for {asset_id}")
            if github_digest != expected:
                raise AssetError(
                    f"release asset SHA-256 must match the GitHub asset digest for {asset_id}"
                )
        release_notes_digest = value.get("upstream_release_notes_sha256")
        if release_notes_digest is not None and (
            not isinstance(release_notes_digest, str)
            or not SHA256_RE.fullmatch(release_notes_digest)
        ):
            raise AssetError(f"invalid upstream release-notes SHA-256 for {asset_id}")

    blockers = data.get("integrity_blockers", [])
    if not isinstance(blockers, list):
        raise AssetError("integrity_blockers must be a list")
    normalized: list[dict[str, str]] = []
    for blocker in blockers:
        if not isinstance(blocker, dict):
            raise AssetError("integrity blocker entries must be objects")
        blocker_id = blocker.get("id")
        reason = blocker.get("reason")
        if not isinstance(blocker_id, str) or not blocker_id:
            raise AssetError("integrity blocker id is required")
        if not isinstance(reason, str) or not reason:
            raise AssetError("integrity blocker reason is required")
        normalized.append({"id": blocker_id, "reason": reason})

    license_input = data.get("cloudflared_license")
    if not isinstance(license_input, dict):
        raise AssetError("cloudflared license evidence is missing")
    _validate_https_url(license_input.get("url"))
    license_sha = license_input.get("sha256")
    if license_sha is not None and (
        not isinstance(license_sha, str) or not SHA256_RE.fullmatch(license_sha)
    ):
        raise AssetError("cloudflared license SHA-256 must be null or a lowercase digest")
    if license_sha is None and not any(
        item["id"] == "cloudflared-license-sha256-not-recorded" for item in normalized
    ):
        raise AssetError("cloudflared license SHA-256 is required when the manifest is unblocked")

    return normalized


def _download(url: str, destination: Path, expected_sha256: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        actual = _sha256(destination)
        if actual != expected_sha256:
            raise AssetError(f"cached asset checksum mismatch: {destination.name}")
        return destination

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{destination.name}.",
            suffix=".download",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            with urllib.request.urlopen(request, timeout=60) as response:
                final_url = urllib.parse.urlsplit(response.geturl())
                if final_url.scheme != "https":
                    raise AssetError("release asset download redirected away from HTTPS")
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    handle.write(block)
            handle.flush()
            os.fsync(handle.fileno())

        actual = _sha256(temporary)
        if actual != expected_sha256:
            raise AssetError(
                f"downloaded asset checksum mismatch for {destination.name}: "
                f"expected {expected_sha256}, got {actual}"
            )
        os.replace(temporary, destination)
        temporary = None
        return destination
    except (OSError, urllib.error.URLError) as exc:
        if isinstance(exc, AssetError):
            raise
        raise AssetError(f"release asset download failed: {destination.name}: {exc}") from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_tar_name(name: str) -> PurePosixPath:
    normalized = PurePosixPath(name)
    if normalized.is_absolute() or not normalized.parts:
        raise AssetError(f"unsafe archive member path: {name}")
    if any(part in {"", ".", ".."} for part in normalized.parts):
        raise AssetError(f"unsafe archive member path: {name}")
    return normalized


def _extract_single_tgz(archive: Path, member_basename: str, destination: Path) -> Path:
    if not member_basename or member_basename != Path(member_basename).name:
        raise AssetError("archive member basename is invalid")
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            regular = []
            for member in bundle.getmembers():
                _safe_tar_name(member.name)
                if member.issym() or member.islnk():
                    raise AssetError(f"archive links are forbidden: {member.name}")
                if member.ischr() or member.isblk() or member.isfifo():
                    raise AssetError(f"special archive members are forbidden: {member.name}")
                if member.isfile():
                    regular.append(member)
            candidates = [
                member for member in regular if PurePosixPath(member.name).name == member_basename
            ]
            if len(candidates) != 1 or len(regular) != 1:
                raise AssetError(
                    "verified cloudflared archive must contain exactly one regular executable"
                )
            source = bundle.extractfile(candidates[0])
            if source is None:
                raise AssetError("cloudflared archive member could not be opened")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}.extracting")
            try:
                with temporary.open("wb") as target:
                    while True:
                        block = source.read(1024 * 1024)
                        if not block:
                            break
                        target.write(block)
                    target.flush()
                    os.fsync(target.fileno())
                temporary.chmod(0o755)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
    except (OSError, tarfile.TarError) as exc:
        if isinstance(exc, AssetError):
            raise
        raise AssetError(f"cannot safely extract {archive.name}: {exc}") from exc
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--asset")
    parser.add_argument("--destination-dir", type=Path)
    parser.add_argument("--extract-to", type=Path)
    args = parser.parse_args()

    try:
        manifest = _load_manifest(args.manifest.resolve())
        blockers = _validate_manifest(manifest)
        if blockers:
            raise AssetError(
                "release asset manifest is blocked: "
                + "; ".join(f"{item['id']}: {item['reason']}" for item in blockers)
            )
        if args.check:
            print(json.dumps({"status": "ok", "schema": SCHEMA}, sort_keys=True))
            return 0
        if not args.asset or args.destination_dir is None:
            raise AssetError("--asset and --destination-dir are required unless --check is used")

        assets = manifest["assets"]
        if args.asset == "cloudflared-license":
            license_input = manifest["cloudflared_license"]
            license_sha = license_input.get("sha256")
            if not isinstance(license_sha, str):
                raise AssetError("cloudflared license SHA-256 is not pinned")
            asset = {
                "kind": "file",
                "filename": f"cloudflared-{license_input['version']}-LICENSE",
                "url": license_input["url"],
                "sha256": license_sha,
            }
        else:
            asset = assets.get(args.asset)
        if not isinstance(asset, dict):
            raise AssetError(f"unknown release asset id: {args.asset}")
        destination = args.destination_dir.resolve() / str(asset["filename"])
        fetched = _download(str(asset["url"]), destination, str(asset["sha256"]))
        result: dict[str, object] = {
            "status": "ok",
            "asset": args.asset,
            "artifact": str(fetched),
            "sha256": _sha256(fetched),
        }
        if args.extract_to is not None:
            if asset.get("kind") != "tgz":
                raise AssetError("--extract-to is only supported for tgz assets")
            member = asset.get("archive_member")
            if not isinstance(member, str):
                raise AssetError("tgz asset does not define archive_member")
            extracted = _extract_single_tgz(fetched, member, args.extract_to.resolve())
            result["extracted"] = str(extracted)
            result["extracted_sha256"] = _sha256(extracted)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except AssetError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
