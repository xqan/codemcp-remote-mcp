#!/bin/bash
set -euo pipefail

VERSION=""
ARCH=""
MODE="candidate"

fail() { printf 'build-macos-release: %s\n' "$*" >&2; exit 1; }
usage() {
  printf '%s\n' 'usage: build-macos-release.sh --version 0.1.0 --arch <arm64|x86_64> [--mode candidate]' >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version) [ "$#" -ge 2 ] || usage; VERSION=$2; shift 2 ;;
    --arch) [ "$#" -ge 2 ] || usage; ARCH=$2; shift 2 ;;
    --mode) [ "$#" -ge 2 ] || usage; MODE=$2; shift 2 ;;
    *) usage ;;
  esac
done

[ "$VERSION" = "0.1.0" ] || fail "only version 0.1.0 is supported"
case "$ARCH" in arm64|x86_64) ;; *) fail "arch must be arm64 or x86_64" ;; esac
[ "$MODE" = "candidate" ] || fail "only candidate mode is supported"
[ "$(uname -s)" = "Darwin" ] || fail "build must run on Darwin"
[ "$(uname -m)" = "$ARCH" ] || fail "requested arch must match native host arch"

for tool in git uv python3.12 xcode-select codesign file lipo otool shasum xattr tar; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing build tool: $tool"
done
xcode-select -p >/dev/null 2>&1 || fail "Xcode Command Line Tools are required"

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)
ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd -P)
BRIDGE="$ROOT/bridge"
MANIFEST="$SCRIPT_DIR/release-assets/macos-v0.1.0.json"
ASSET_HELPER="$SCRIPT_DIR/prepare-verified-release-asset.py"
ENTRYPOINT="$SCRIPT_DIR/windows_entrypoint.py"

SOURCE_COMMIT=$(git -C "$ROOT" rev-parse HEAD)
SOURCE_BRANCH=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
[ -z "$(git -C "$ROOT" status --porcelain)" ] || fail "release build requires a clean worktree"
EXPECTED_TAG="v$VERSION"
SOURCE_TAG=$(git -C "$ROOT" describe --tags --exact-match HEAD 2>/dev/null || true)
if [ -n "$SOURCE_TAG" ] && [ "$SOURCE_TAG" != "$EXPECTED_TAG" ]; then
  fail "HEAD exact tag must be $EXPECTED_TAG when a release tag is present"
fi
PROJECT_VERSION=$(python3.12 - "$BRIDGE/pyproject.toml" <<'PY'
import sys, tomllib
from pathlib import Path
print(tomllib.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["project"]["version"])
PY
)
[ "$PROJECT_VERSION" = "$VERSION" ] || fail "bridge version mismatch"
PYTHON_VERSION=$(python3.12 -c 'import platform; print(platform.python_version())')
case "$PYTHON_VERSION" in 3.12.*) ;; *) fail "Python 3.12 is required" ;; esac

# Must pass before any release input is downloaded.
python3.12 "$ASSET_HELPER" --manifest "$MANIFEST" --check

LOCAL="$ROOT/.local/macos-release/$ARCH"
CACHE="$ROOT/.local/third-party/macos-v0.1.0"
DIST="$LOCAL/dist"
WORK="$LOCAL/work"
STAGE_PARENT="$LOCAL/staging"
STAGE="$STAGE_PARENT/codemcp-remote"
LABEL="macos-arm64"
[ "$ARCH" = "arm64" ] || LABEL="macos-intel64"
ARCHIVE="$ROOT/.local/dist/codemcp-remote-v${VERSION}-${LABEL}.tar.gz"
rm -rf "$LOCAL"
mkdir -p "$DIST" "$WORK" "$STAGE" "$CACHE" "$(dirname "$ARCHIVE")"

fetch() {
  python3.12 "$ASSET_HELPER" --manifest "$MANIFEST" --asset "$1" --destination-dir "$CACHE" >/dev/null
}
for asset in pyinstaller pyinstaller-hooks-contrib altgraph macholib packaging setuptools; do fetch "$asset"; done

PYINSTALLER="$CACHE/pyinstaller-6.22.2-py3-none-macosx_10_13_universal2.whl"
HOOKS="$CACHE/pyinstaller_hooks_contrib-2026.6-py3-none-any.whl"
ALTGRAPH="$CACHE/altgraph-0.17.5-py2.py3-none-any.whl"
MACHOLIB="$CACHE/macholib-1.16.4-py2.py3-none-any.whl"
PACKAGING="$CACHE/packaging-26.3-py3-none-any.whl"
SETUPTOOLS="$CACHE/setuptools-84.0.0-py3-none-any.whl"

CF_ASSET="cloudflared-arm64"
[ "$ARCH" = "arm64" ] || CF_ASSET="cloudflared-x86_64"
CF_EXTRACTED="$WORK/cloudflared"
python3.12 "$ASSET_HELPER" --manifest "$MANIFEST" --asset "$CF_ASSET" \
  --destination-dir "$CACHE" --extract-to "$CF_EXTRACTED" >/dev/null
python3.12 "$ASSET_HELPER" --manifest "$MANIFEST" --asset cloudflared-license \
  --destination-dir "$CACHE" >/dev/null
CF_LICENSE="$CACHE/cloudflared-2026.7.3-LICENSE"

uv lock --project "$BRIDGE" --check
uv sync --project "$BRIDGE" --frozen --all-groups --python 3.12
uv run --project "$BRIDGE" --frozen \
  --with "$PYINSTALLER" --with "$HOOKS" --with "$ALTGRAPH" \
  --with "$MACHOLIB" --with "$PACKAGING" --with "$SETUPTOOLS" \
  pyinstaller --noconfirm --clean --onedir --console \
  --target-arch "$ARCH" --contents-directory .codemcp-runtime \
  --name codemcp-remote --paths "$BRIDGE/src" \
  --collect-submodules codemcp --copy-metadata codemcp \
  --hidden-import keyring.backends.macOS \
  --distpath "$DIST" --workpath "$WORK/pyinstaller" --specpath "$WORK/pyinstaller" \
  "$ENTRYPOINT"

BUILT="$DIST/codemcp-remote"
[ -x "$BUILT/codemcp-remote" ] || fail "frozen executable is missing"
[ -d "$BUILT/.codemcp-runtime" ] || fail "hidden runtime is missing"
cp -R "$BUILT/." "$STAGE/"
mkdir -p "$STAGE/.codemcp-runtime/bin" "$STAGE/config" "$STAGE/THIRD_PARTY/cloudflared"
cp "$CF_EXTRACTED" "$STAGE/.codemcp-runtime/bin/cloudflared"
cp "$ROOT/config/bridge.example.toml" "$STAGE/config/"
cp "$ROOT/config/projects.example.toml" "$STAGE/config/"
cp "$ROOT/LICENSE" "$STAGE/LICENSE"
cp "$SCRIPT_DIR/codemcp-install.sh" "$STAGE/"
cp "$SCRIPT_DIR/codemcp-start.sh" "$STAGE/"
cp "$SCRIPT_DIR/codemcp-stop.sh" "$STAGE/"
cp "$CF_LICENSE" "$STAGE/THIRD_PARTY/cloudflared/LICENSE"
cat > "$STAGE/THIRD_PARTY/cloudflared/NOTICE.txt" <<EOF
Cloudflare Tunnel client (cloudflared)
Version: 2026.7.3
License: Apache-2.0
Source: https://github.com/cloudflare/cloudflared
EOF

mkdir -p "$STAGE/THIRD_PARTY/pyinstaller"
python3.12 - "$PYINSTALLER" "$STAGE/THIRD_PARTY/pyinstaller/COPYING.txt" <<'PY'
import sys, zipfile
from pathlib import Path
wheel, output = Path(sys.argv[1]), Path(sys.argv[2])
with zipfile.ZipFile(wheel) as archive:
    names = [n for n in archive.namelist() if Path(n).name.lower() == "copying.txt"]
    if len(names) != 1 or ".." in Path(names[0]).parts or names[0].startswith("/"):
        raise SystemExit("invalid PyInstaller license evidence")
    data = archive.read(names[0])
    if b"bootloader" not in data.lower() or b"exception" not in data.lower():
        raise SystemExit("PyInstaller bootloader exception evidence is missing")
    output.write_bytes(data)
PY
cat > "$STAGE/THIRD_PARTY/pyinstaller/NOTICE.txt" <<EOF
PyInstaller 6.22.2
License: GPL-2.0-or-later WITH Bootloader-exception
EOF

mkdir -p "$STAGE/THIRD_PARTY/python-dependencies"
uv run --project "$BRIDGE" --frozen python "$SCRIPT_DIR/dependency_license_audit.py" \
  --lock "$BRIDGE/uv.lock" --output "$WORK/license-audit.json"
python3.12 - "$WORK/license-audit.json" "$STAGE/THIRD_PARTY/python-dependencies/license-audit.json" <<'PY'
import json, sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
data["lockfile"] = "bridge/uv.lock"
Path(sys.argv[2]).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

CPYTHON_LICENSE=$(python3.12 - <<'PY'
import os
import sys
from pathlib import Path

stdlib = Path(getattr(sys, "_stdlib_dir", "") or Path(os.__file__).resolve().parent)
roots = (stdlib.parent, stdlib, Path(sys.base_prefix))
seen = set()
for root in roots:
    try:
        root = root.resolve()
    except OSError:
        continue
    if root in seen:
        continue
    seen.add(root)
    for name in ("LICENSE.txt", "LICENSE"):
        candidate = root / name
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        if "PYTHON SOFTWARE FOUNDATION LICENSE VERSION 2" in text:
            print(candidate)
            raise SystemExit(0)
PY
)
[ -n "$CPYTHON_LICENSE" ] || fail "CPython license evidence is missing"
mkdir -p "$STAGE/THIRD_PARTY/cpython"
cp "$CPYTHON_LICENSE" "$STAGE/THIRD_PARTY/cpython/LICENSE"
printf 'CPython %s runtime license evidence.\n' "$PYTHON_VERSION" > "$STAGE/THIRD_PARTY/cpython/NOTICE.txt"

xattr -cr "$STAGE"
find "$STAGE" -name '.DS_Store' -delete
find "$STAGE" -name '._*' -delete
find "$STAGE" -name '__MACOSX' -type d -prune -exec rm -rf {} +
find "$STAGE" -type d -exec chmod 0755 {} +
find "$STAGE" -type f -exec chmod 0644 {} +
chmod 0755 "$STAGE/codemcp-remote" "$STAGE/codemcp-install.sh" \
  "$STAGE/codemcp-start.sh" "$STAGE/codemcp-stop.sh" "$STAGE/.codemcp-runtime/bin/cloudflared"

MACHO_LIST="$WORK/macho-files.bin"
: > "$MACHO_LIST"
while IFS= read -r -d '' path; do
  file -b "$path" | grep -q 'Mach-O' && printf '%s\0' "$path" >> "$MACHO_LIST"
done < <(find "$STAGE" -type f -print0)
[ -s "$MACHO_LIST" ] || fail "candidate contains no Mach-O files"
while IFS= read -r -d '' path; do codesign --force --sign - "$path"; done < "$MACHO_LIST"
while IFS= read -r -d '' path; do
  [ "$(lipo -archs "$path")" = "$ARCH" ] || fail "foreign/universal Mach-O: ${path#$STAGE/}"
  codesign --verify --strict --verbose=2 "$path"
  OTOOL_OUTPUT=$(otool -L "$path")
  if printf '%s\n' "$OTOOL_OUTPUT" | sed '1d' |
    grep -E '/Users/|/opt/homebrew/|/usr/local/Cellar/' >/dev/null; then
    fail "build-host dependency leaked into ${path#$STAGE/}"
  fi
done < "$MACHO_LIST"

CF_ARCHIVE_SHA=$(python3.12 - "$MANIFEST" "$CF_ASSET" <<'PY'
import json, sys
from pathlib import Path
print(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))["assets"][sys.argv[2]]["sha256"])
PY
)
CF_EXTRACTED_SHA=$(shasum -a 256 "$CF_EXTRACTED" | awk '{print $1}')
CF_INSTALLED_SHA=$(shasum -a 256 "$STAGE/.codemcp-runtime/bin/cloudflared" | awk '{print $1}')
LOCK_SHA=$(shasum -a 256 "$BRIDGE/uv.lock" | awk '{print $1}')
MANIFEST_SHA=$(shasum -a 256 "$MANIFEST" | awk '{print $1}')
UV_VERSION_OUTPUT=$(uv --version)
UV_VERSION=$(printf '%s\n' "$UV_VERSION_OUTPUT" | awk '{print $2}')
[ -n "$UV_VERSION" ] || fail "unable to determine uv semantic version"
GENERATED_AT=$(date -u '+%Y-%m-%dT%H:%M:%SZ')

python3.12 - "$STAGE/BUILD_PROVENANCE.json" "$MANIFEST" "$MANIFEST_SHA" "$CF_ASSET" \
  "$SOURCE_COMMIT" "$SOURCE_BRANCH" "$SOURCE_TAG" "$ARCH" "$PYTHON_VERSION" "$UV_VERSION" \
  "$LOCK_SHA" "$CF_ARCHIVE_SHA" "$CF_EXTRACTED_SHA" "$CF_INSTALLED_SHA" "$GENERATED_AT" <<'PY'
import json, platform, sys
from pathlib import Path

(
    out,
    manifest_path,
    manifest_sha,
    cloudflared_asset_id,
    commit,
    branch,
    tag,
    arch,
    py,
    uv,
    lock_sha,
    archive_sha,
    extracted,
    installed,
    generated,
) = sys.argv[1:]
manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
cloudflared_asset = manifest["assets"][cloudflared_asset_id]
cloudflared_license = manifest["cloudflared_license"]
used_asset_ids = (
    "pyinstaller",
    "pyinstaller-hooks-contrib",
    "altgraph",
    "macholib",
    "packaging",
    "setuptools",
    cloudflared_asset_id,
)
used_assets = {asset_id: manifest["assets"][asset_id] for asset_id in used_asset_ids}
payload = {
    "schema": "codemcp-remote-build-provenance-v2",
    "release_version": "0.1.0",
    "target_arch": arch,
    "source": {
        "git_commit": commit,
        "git_branch": branch,
        "git_tag": tag or None,
        "expected_release_tag": "v0.1.0",
        "worktree_dirty": False,
    },
    "runner": {"os": platform.platform(), "machine": platform.machine()},
    "tools": {"python": py, "uv": uv, "pyinstaller": "6.22.2"},
    "bridge_lock_sha256": lock_sha,
    "external_inputs": {
        "manifest": {"schema": manifest["schema"], "sha256": manifest_sha},
        "assets": used_assets,
        "cloudflared_license": cloudflared_license,
    },
    "cloudflared": {
        "asset_id": cloudflared_asset_id,
        "version": cloudflared_asset["version"],
        "download_archive_sha256": archive_sha,
        "github_release_asset_sha256": cloudflared_asset.get("github_asset_digest_sha256"),
        "upstream_release_notes_sha256": cloudflared_asset.get("upstream_release_notes_sha256"),
        "license_sha256": cloudflared_license["sha256"],
        "extracted_pre_sign_sha256": extracted,
        "installed_post_sign_sha256": installed,
    },
    "signing": {"mode": "adhoc", "developer_id": False, "identity": "-"},
    "notarization": {"status": "not_performed", "reason": "no_certificate"},
    "generated_at": generated,
}
Path(out).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

python3.12 - "$STAGE" <<'PY'
import hashlib, os, sys
from pathlib import Path
root = Path(sys.argv[1])
target = root / "SHA256SUMS.txt"
lines = []
for path in root.rglob("*"):
    if path.is_file() and path != target:
        relative = path.relative_to(root).as_posix()
        lines.append((relative.encode(), f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {relative}"))
lines.sort(key=lambda item: item[0])
target.write_text("\n".join(line for _, line in lines) + "\n", encoding="ascii")
os.chmod(target, 0o644)
PY
(cd "$STAGE" && shasum -a 256 -c SHA256SUMS.txt)

"$STAGE/codemcp-remote" --version | grep -F "0.1.0" >/dev/null
"$STAGE/codemcp-remote" check >/dev/null
"$STAGE/codemcp-remote" status --home "$WORK/status-home" >/dev/null
sh -n "$STAGE/codemcp-install.sh"
sh -n "$STAGE/codemcp-start.sh"
sh -n "$STAGE/codemcp-stop.sh"
"$STAGE/.codemcp-runtime/bin/cloudflared" --version | grep -F "2026.7.3" >/dev/null

rm -f "$ARCHIVE" "$ARCHIVE.sha256"
python3.12 - "$STAGE_PARENT" "$ARCHIVE" <<'PY'
import gzip, sys, tarfile
from pathlib import Path
parent, archive = Path(sys.argv[1]), Path(sys.argv[2])
root = parent / "codemcp-remote"
with archive.open("wb") as raw:
    with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path in [root, *sorted(root.rglob("*"), key=lambda p: p.relative_to(parent).as_posix())]:
                info = tar.gettarinfo(str(path), arcname=path.relative_to(parent).as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mtime = 0
                if info.isfile():
                    with path.open("rb") as handle: tar.addfile(info, handle)
                else:
                    tar.addfile(info)
PY
ARCHIVE_SHA=$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')
printf '%s  %s\n' "$ARCHIVE_SHA" "$(basename "$ARCHIVE")" > "$ARCHIVE.sha256"
printf '{"status":"ok","artifact":"%s","sha256":"%s","arch":"%s"}\n' "$ARCHIVE" "$ARCHIVE_SHA" "$ARCH"
