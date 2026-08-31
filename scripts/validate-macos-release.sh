#!/bin/bash
set -euo pipefail

ARCHIVE=""
ARCH=""
LABEL=""
SOURCE_COMMIT=""
EVIDENCE=""
EXPECT_SPCTL_REJECTION=0

fail() {
  printf 'validate-macos-release: %s\n' "$*" >&2
  exit 1
}

usage() {
  printf '%s\n' \
    'usage: validate-macos-release.sh --archive <tar.gz> --arch <arm64|x86_64> --label <macos-arm64|macos-intel64> --source-commit <sha> --evidence <json> [--expect-spctl-rejection]' >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --archive) [ "$#" -ge 2 ] || usage; ARCHIVE=$2; shift 2 ;;
    --arch) [ "$#" -ge 2 ] || usage; ARCH=$2; shift 2 ;;
    --label) [ "$#" -ge 2 ] || usage; LABEL=$2; shift 2 ;;
    --source-commit) [ "$#" -ge 2 ] || usage; SOURCE_COMMIT=$2; shift 2 ;;
    --evidence) [ "$#" -ge 2 ] || usage; EVIDENCE=$2; shift 2 ;;
    --expect-spctl-rejection) EXPECT_SPCTL_REJECTION=1; shift ;;
    *) usage ;;
  esac
done

[ -n "$ARCHIVE" ] || usage
[ -n "$ARCH" ] || usage
[ -n "$LABEL" ] || usage
[ -n "$SOURCE_COMMIT" ] || usage
[ -n "$EVIDENCE" ] || usage

case "$ARCH:$LABEL" in
  arm64:macos-arm64) ;;
  x86_64:macos-intel64) ;;
  *) fail "arch/label mismatch" ;;
esac

[ "$(uname -s)" = "Darwin" ] || fail "validation must run on Darwin"
[ "$(uname -m)" = "$ARCH" ] || fail "runner architecture does not match candidate"
[ -f "$ARCHIVE" ] || fail "candidate archive is missing: $ARCHIVE"

EXPECTED_NAME="codemcp-remote-v0.1.0-${LABEL}.tar.gz"
[ "$(basename "$ARCHIVE")" = "$EXPECTED_NAME" ] || fail "unexpected archive filename"

for tool in python3.12 shasum file lipo codesign otool tar xattr sw_vers; do
  command -v "$tool" >/dev/null 2>&1 || fail "missing validation tool: $tool"
done
if [ "$EXPECT_SPCTL_REJECTION" -eq 1 ]; then
  command -v spctl >/dev/null 2>&1 || fail "spctl is required for Gatekeeper evidence"
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/codemcp-macos-validate.XXXXXX")
trap 'rm -rf "$TMP"' EXIT HUP INT TERM
EXTRACT="$TMP/extracted"
mkdir -p "$EXTRACT"

python3.12 - "$ARCHIVE" "$EXTRACT" <<'PY'
import posixpath
import sys
import tarfile
from pathlib import Path, PurePosixPath

archive_path = Path(sys.argv[1])
extract_root = Path(sys.argv[2])
expected_top = {
    ".codemcp-runtime",
    "BUILD_PROVENANCE.json",
    "LICENSE",
    "SHA256SUMS.txt",
    "THIRD_PARTY",
    "codemcp-install.sh",
    "codemcp-remote",
    "codemcp-start.sh",
    "codemcp-stop.sh",
    "config",
}
executables = {
    "codemcp-remote/codemcp-remote",
    "codemcp-remote/codemcp-install.sh",
    "codemcp-remote/codemcp-start.sh",
    "codemcp-remote/codemcp-stop.sh",
    "codemcp-remote/.codemcp-runtime/bin/cloudflared",
}

with tarfile.open(archive_path, "r:gz") as archive:
    members = archive.getmembers()
    if not members:
        raise SystemExit("candidate archive is empty")

    top_children: set[str] = set()
    for member in members:
        name = member.name.rstrip("/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts:
            raise SystemExit(f"unsafe archive path: {member.name}")
        if not path.parts or path.parts[0] != "codemcp-remote":
            raise SystemExit(f"unexpected top-level archive entry: {member.name}")
        if len(path.parts) >= 2:
            top_children.add(path.parts[1])

        if member.islnk():
            raise SystemExit(f"hardlink is not allowed in release archive: {member.name}")
        if member.issym():
            target = PurePosixPath(member.linkname)
            if target.is_absolute():
                raise SystemExit(f"absolute symlink is not allowed: {member.name}")
            resolved = posixpath.normpath(
                posixpath.join(posixpath.dirname(member.name), member.linkname)
            )
            if resolved != "codemcp-remote" and not resolved.startswith("codemcp-remote/"):
                raise SystemExit(f"symlink escapes release root: {member.name}")
        elif not (member.isfile() or member.isdir()):
            raise SystemExit(f"unsupported archive entry type: {member.name}")

        mode = member.mode & 0o777
        if member.isdir() and mode != 0o755:
            raise SystemExit(f"directory mode must be 0755: {member.name}={mode:o}")
        if member.isfile():
            expected_mode = 0o755 if member.name in executables else 0o644
            if mode != expected_mode:
                raise SystemExit(
                    f"file mode mismatch: {member.name}={mode:o}, expected={expected_mode:o}"
                )

    if top_children != expected_top:
        missing = sorted(expected_top - top_children)
        extra = sorted(top_children - expected_top)
        raise SystemExit(f"top-level layout mismatch: missing={missing}, extra={extra}")

    names = "\n".join(member.name.lower() for member in members)
    if "tunnel-client" in names:
        raise SystemExit("tunnel-client must not be present in macOS release archive")

    archive.extractall(extract_root, filter="data")
PY

ROOT="$EXTRACT/codemcp-remote"
[ -d "$ROOT" ] || fail "candidate root was not extracted"

(
  cd "$ROOT"
  shasum -a 256 -c SHA256SUMS.txt
)

for executable in \
  "$ROOT/codemcp-remote" \
  "$ROOT/codemcp-install.sh" \
  "$ROOT/codemcp-start.sh" \
  "$ROOT/codemcp-stop.sh" \
  "$ROOT/.codemcp-runtime/bin/cloudflared"; do
  [ -x "$executable" ] || fail "expected executable bit is missing: ${executable#$ROOT/}"
done

MACHO_LIST="$TMP/macho-files.bin"
: > "$MACHO_LIST"
while IFS= read -r -d '' path; do
  if file -b "$path" | grep -q 'Mach-O'; then
    printf '%s\0' "$path" >> "$MACHO_LIST"
  fi
done < <(find "$ROOT" -type f -print0)
[ -s "$MACHO_LIST" ] || fail "candidate contains no Mach-O files"

while IFS= read -r -d '' path; do
  [ "$(lipo -archs "$path")" = "$ARCH" ] ||
    fail "foreign or universal Mach-O: ${path#$ROOT/}"

  codesign --verify --strict --all-architectures --verbose=2 "$path"
  SIGNATURE_INFO=$(codesign -dv --verbose=4 "$path" 2>&1)
  printf '%s\n' "$SIGNATURE_INFO" | grep -F 'Signature=adhoc' >/dev/null ||
    fail "Mach-O is not ad-hoc signed: ${path#$ROOT/}"

  TEAM_LINE=$(printf '%s\n' "$SIGNATURE_INFO" | grep '^TeamIdentifier=' || true)
  if [ -n "$TEAM_LINE" ] && [ "$TEAM_LINE" != "TeamIdentifier=not set" ]; then
    fail "unexpected Developer ID team identity: ${path#$ROOT/}: $TEAM_LINE"
  fi

  OTOOL_OUTPUT=$(otool -L "$path")
  if printf '%s\n' "$OTOOL_OUTPUT" | sed '1d' |
    grep -E '/Users/|/opt/homebrew/|/usr/local/Cellar/' >/dev/null; then
    fail "build-host dependency leaked into ${path#$ROOT/}"
  fi
done < "$MACHO_LIST"

python3.12 - "$ROOT/BUILD_PROVENANCE.json" "$ARCH" "$SOURCE_COMMIT" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
arch = sys.argv[2]
source_commit = sys.argv[3]
data = json.loads(path.read_text(encoding="utf-8"))

assert data["schema"] == "codemcp-remote-build-provenance-v2"
assert data["release_version"] == "0.1.0"
assert data["target_arch"] == arch
assert data["source"]["git_commit"] == source_commit
assert data["source"]["worktree_dirty"] is False
assert data["source"]["expected_release_tag"] == "v0.1.0"
assert data["source"]["git_tag"] in (None, "v0.1.0")
assert data["signing"] == {
    "developer_id": False,
    "identity": "-",
    "mode": "adhoc",
}
assert data["notarization"]["status"] == "not_performed"
assert data["notarization"]["reason"] == "no_certificate"
assert data["cloudflared"]["version"] == "2026.7.3"
assert len(data["external_inputs"]["assets"]) == 7
assert "tunnel-client" not in json.dumps(data, sort_keys=True).lower()
PY

"$ROOT/codemcp-remote" --version | grep -F '0.1.0' >/dev/null
"$ROOT/codemcp-remote" check >/dev/null
"$ROOT/codemcp-remote" status --home "$TMP/status-home" >/dev/null
"$ROOT/.codemcp-runtime/bin/cloudflared" --version | grep -F '2026.7.3' >/dev/null
sh -n "$ROOT/codemcp-install.sh"
sh -n "$ROOT/codemcp-start.sh"
sh -n "$ROOT/codemcp-stop.sh"

SPCTL_STATUS=""
SPCTL_OUTPUT=""
if [ "$EXPECT_SPCTL_REJECTION" -eq 1 ]; then
  QUARANTINE_VALUE="0081;$(printf '%x' "$(date +%s)");codemcp-remote-ci;https://github.com/"
  xattr -w com.apple.quarantine "$QUARANTINE_VALUE" "$ROOT/codemcp-remote"
  set +e
  SPCTL_OUTPUT=$(spctl --assess --type execute --verbose=4 "$ROOT/codemcp-remote" 2>&1)
  SPCTL_STATUS=$?
  set -e
  xattr -d com.apple.quarantine "$ROOT/codemcp-remote" 2>/dev/null || true
  [ "$SPCTL_STATUS" -ne 0 ] ||
    fail "Gatekeeper unexpectedly accepted an ad-hoc, non-notarized candidate"
fi

ARCHIVE_SHA=$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')
OS_VERSION=$(sw_vers -productVersion)
OS_BUILD=$(sw_vers -buildVersion)
CODESIGN_INFO=$(codesign -dv --verbose=4 "$ROOT/codemcp-remote" 2>&1)

mkdir -p "$(dirname "$EVIDENCE")"
python3.12 - \
  "$EVIDENCE" "$ARCHIVE" "$ARCHIVE_SHA" "$ARCH" "$LABEL" "$SOURCE_COMMIT" \
  "$OS_VERSION" "$OS_BUILD" "$CODESIGN_INFO" "$SPCTL_STATUS" "$SPCTL_OUTPUT" <<'PY'
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    output,
    archive,
    archive_sha,
    arch,
    label,
    source_commit,
    os_version,
    os_build,
    codesign_info,
    spctl_status,
    spctl_output,
) = sys.argv[1:]

payload = {
    "schema": "codemcp-remote-macos-validation-v1",
    "archive": Path(archive).name,
    "archive_sha256": archive_sha,
    "target_arch": arch,
    "artifact_label": label,
    "source_commit": source_commit,
    "runner": {
        "machine": platform.machine(),
        "platform": platform.platform(),
        "macos_version": os_version,
        "macos_build": os_build,
    },
    "signing": {
        "expected": "adhoc",
        "details": codesign_info.splitlines(),
    },
    "gatekeeper": {
        "quarantine_assessment_expected_rejection": bool(spctl_status),
        "spctl_exit_code": int(spctl_status) if spctl_status else None,
        "spctl_output": spctl_output,
    },
    "validated_at": datetime.now(timezone.utc).isoformat(),
}
Path(output).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

printf '{"status":"ok","archive":"%s","sha256":"%s","arch":"%s","evidence":"%s"}\n' \
  "$ARCHIVE" "$ARCHIVE_SHA" "$ARCH" "$EVIDENCE"
