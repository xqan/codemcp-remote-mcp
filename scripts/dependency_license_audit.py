from __future__ import annotations

import argparse
import hashlib
import json
import sys
from importlib import metadata
from pathlib import Path

import tomllib

FIRST_PARTY = {"codemcp-remote-bridge"}
CODEMCP_VERSION = "0.3.0"
CODEMCP_LICENSE_FILE_SHA256 = (
    "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30"
)
EMPTY_LICENSE_VALUES = {"", "unknown", "n/a", "none"}


def _normalize(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _license_files(dist: metadata.Distribution) -> list[dict[str, str]]:
    evidence: list[dict[str, str]] = []
    for relative in dist.files or ():
        normalized = str(relative).replace("\\", "/")
        leaf = Path(normalized).name.lower()
        if not leaf.startswith(("license", "copying", "notice")):
            continue
        target = Path(dist.locate_file(relative))
        if not target.is_file():
            continue
        try:
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            continue
        evidence.append({"path": normalized, "sha256": digest})
    return sorted(evidence, key=lambda item: item["path"].lower())


def _license_metadata(dist: metadata.Distribution) -> dict[str, object]:
    meta = dist.metadata
    expressions = [
        value.strip()
        for value in (meta.get_all("License-Expression") or [])
        if value and value.strip()
    ]
    license_field = (meta.get("License") or "").strip()
    classifiers = sorted(
        value.strip()
        for value in (meta.get_all("Classifier") or [])
        if value.startswith("License ::")
    )
    files = _license_files(dist)
    return {
        "license_expressions": expressions,
        "license_field": license_field,
        "license_classifiers": classifiers,
        "license_files": files,
    }


def _has_license_evidence(item: dict[str, object]) -> bool:
    expressions = item["license_expressions"]
    license_field = str(item["license_field"]).strip()
    classifiers = item["license_classifiers"]
    files = item["license_files"]
    return bool(
        expressions
        or (license_field.lower() not in EMPTY_LICENSE_VALUES)
        or classifiers
        or files
    )


def _load_locked_names(lock_path: Path) -> set[str]:
    data = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    return {
        _normalize(str(package["name"]))
        for package in data.get("package", [])
        if _normalize(str(package["name"])) not in FIRST_PARTY
    }


def _validate_codemcp(item: dict[str, object]) -> list[str]:
    errors: list[str] = []
    if item["version"] != CODEMCP_VERSION:
        errors.append(f"codemcp version changed: {item['version']}")
    if str(item["license_field"]).strip() != "MIT":
        errors.append(
            "codemcp METADATA License field no longer matches audited value MIT"
        )
    hashes = {
        str(entry["sha256"])
        for entry in item["license_files"]
        if isinstance(entry, dict) and "sha256" in entry
    }
    if CODEMCP_LICENSE_FILE_SHA256 not in hashes:
        errors.append(
            "codemcp bundled audited Apache-2.0 license file hash is missing or changed"
        )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inventory license evidence for installed dependencies from bridge/uv.lock."
    )
    parser.add_argument("--lock", type=Path, default=Path("bridge/uv.lock"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    lock_path = args.lock.resolve()
    locked_names = _load_locked_names(lock_path)
    installed = {
        _normalize(dist.metadata.get("Name") or ""): dist
        for dist in metadata.distributions()
        if dist.metadata.get("Name")
    }

    packages: list[dict[str, object]] = []
    missing_license_evidence: list[str] = []
    codemcp_errors: list[str] = []

    for name in sorted(locked_names):
        dist = installed.get(name)
        if dist is None:
            continue
        item: dict[str, object] = {
            "name": name,
            "version": dist.version,
            **_license_metadata(dist),
        }
        item["license_evidence_present"] = _has_license_evidence(item)
        if not item["license_evidence_present"]:
            missing_license_evidence.append(name)
        if name == "codemcp":
            codemcp_errors.extend(_validate_codemcp(item))
            item["known_metadata_discrepancy"] = (
                "METADATA says MIT while bundled audited License-File is Apache-2.0"
            )
        packages.append(item)

    installed_locked_names = {str(item["name"]) for item in packages}
    report = {
        "status": "ok"
        if not missing_license_evidence and not codemcp_errors
        else "failed",
        "lockfile": str(lock_path),
        "installed_locked_package_count": len(packages),
        "not_installed_locked_packages": sorted(locked_names - installed_locked_names),
        "missing_license_evidence": missing_license_evidence,
        "codemcp_validation_errors": codemcp_errors,
        "manual_compatibility_review_required": True,
        "packages": packages,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    if report["status"] != "ok":
        print(json.dumps(report, ensure_ascii=False), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "status": "ok",
                "installed_locked_package_count": len(packages),
                "report": str(args.output.resolve()),
                "manual_compatibility_review_required": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
