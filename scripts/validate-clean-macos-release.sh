#!/bin/bash
set -euo pipefail

ACTION=""
ARCHIVE=""
VALIDATION_JSON=""
EXPECTED_SHA256=""
ROOT="${CODEMCP_MACOS_PHASE4_ROOT:-$HOME/codemcp-remote-phase4}"
CYCLES=20

fail() { printf 'validate-clean-macos-release: %s\n' "$*" >&2; exit 1; }
usage() {
  cat >&2 <<'EOF'
usage:
  validate-clean-macos-release.sh --action prepare --archive <tar.gz> --validation-json <json> [--expected-sha256 <sha>] [--root <dir>]
  validate-clean-macos-release.sh --action verify [--root <dir>]
  validate-clean-macos-release.sh --action secret-scan [--root <dir>]
  validate-clean-macos-release.sh --action lifecycle [--cycles 20] [--root <dir>]
  validate-clean-macos-release.sh --action cleanup [--root <dir>]
EOF
  exit 2
}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --action) ACTION=${2:-}; shift 2 ;;
    --archive) ARCHIVE=${2:-}; shift 2 ;;
    --validation-json) VALIDATION_JSON=${2:-}; shift 2 ;;
    --expected-sha256) EXPECTED_SHA256=${2:-}; shift 2 ;;
    --root) ROOT=${2:-}; shift 2 ;;
    --cycles) CYCLES=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
case "$ACTION" in prepare|verify|secret-scan|lifecycle|cleanup) ;; *) usage ;; esac

[ "$(uname -s)" = Darwin ] || fail "must run on macOS"
case "$(uname -m)" in
  arm64) ARCH=arm64; LABEL=macos-arm64 ;;
  x86_64) ARCH=x86_64; LABEL=macos-intel64 ;;
  *) fail "unsupported architecture" ;;
esac
case "$CYCLES" in ''|*[!0-9]*) fail "invalid cycle count" ;; esac
[ "$CYCLES" -ge 1 ] && [ "$CYCLES" -le 100 ] || fail "cycles must be 1-100"

PARENT=$(dirname "$ROOT")
mkdir -p "$PARENT"
PARENT=$(cd "$PARENT" && pwd -P)
ROOT="$PARENT/$(basename "$ROOT")"
MARKER="$ROOT/.codemcp-phase4-owned"
STATE="$ROOT/state.plist"
EVIDENCE="$ROOT/evidence.plist"
EVIDENCE_JSON="$ROOT/evidence.json"
SANDBOX_HOME="$ROOT/user-home"
APP_HOME="$SANDBOX_HOME/Library/Application Support/codemcp-remote"
DIST="$ROOT/distribution/codemcp-remote"
CODEMCP="$DIST/codemcp-remote"
PROJECT="$ROOT/project"
PROJECT_ID=phase4-macos
SAFE_PATH=/usr/bin:/bin:/usr/sbin:/sbin

getv() { /usr/bin/plutil -extract "$2" raw -o - "$1"; }
setv() {
  if /usr/bin/plutil -extract "$2" raw -o - "$1" >/dev/null 2>&1; then
    /usr/bin/plutil -replace "$2" -string "$3" "$1"
  else
    /usr/bin/plutil -insert "$2" -string "$3" "$1"
  fi
}
sync_json() { /usr/bin/plutil -convert json -o "$EVIDENCE_JSON" "$EVIDENCE"; }
owned() {
  [ -f "$MARKER" ] && [ "$(cat "$MARKER")" = codemcp-remote-macos-phase4-v1 ] || fail "acceptance root is not harness-owned"
}
run_clean() { env -i HOME="$SANDBOX_HOME" PATH="$SAFE_PATH" LC_ALL=C LANG=C "$@"; }

prepare() {
  [ -f "$ARCHIVE" ] || fail "--archive is required"
  [ -f "$VALIDATION_JSON" ] || fail "--validation-json is required"
  [ ! -e "$ROOT" ] || fail "acceptance root already exists"
  case "$EXPECTED_SHA256" in
    '') ;;
    *[!0-9A-Fa-f]*) fail "expected SHA-256 is invalid" ;;
  esac
  [ -z "$EXPECTED_SHA256" ] || [ "${#EXPECTED_SHA256}" -eq 64 ] || fail "expected SHA-256 is invalid"

  ARCHIVE=$(cd "$(dirname "$ARCHIVE")" && pwd -P)/$(basename "$ARCHIVE")
  VALIDATION_JSON=$(cd "$(dirname "$VALIDATION_JSON")" && pwd -P)/$(basename "$VALIDATION_JSON")
  SHA=$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')
  [ "$(getv "$VALIDATION_JSON" archive_sha256)" = "$SHA" ] || fail "archive hash differs from CI evidence"
  [ "$(getv "$VALIDATION_JSON" target_arch)" = "$ARCH" ] || fail "candidate architecture differs from host"
  SOURCE=$(getv "$VALIDATION_JSON" source_commit)
  if [ -n "$EXPECTED_SHA256" ]; then
    EXPECTED_SHA256=$(printf '%s' "$EXPECTED_SHA256" | tr 'A-F' 'a-f')
    [ "$EXPECTED_SHA256" = "$SHA" ] || fail "archive hash differs from trusted expected hash"
  fi

  mkdir -p "$ROOT/distribution" "$SANDBOX_HOME"
  printf '%s\n' codemcp-remote-macos-phase4-v1 > "$MARKER"
  chmod 0700 "$ROOT" "$SANDBOX_HOME"
  tar -xzf "$ARCHIVE" -C "$ROOT/distribution"
  [ -x "$CODEMCP" ] || fail "packaged executable is missing"

  (cd "$DIST" && shasum -a 256 -c SHA256SUMS.txt >/dev/null) || fail "internal checksum failed"
  [ "$(lipo -archs "$CODEMCP")" = "$ARCH" ] || fail "main binary is not native thin $ARCH"
  [ "$(lipo -archs "$DIST/.codemcp-runtime/bin/cloudflared")" = "$ARCH" ] || fail "cloudflared arch mismatch"
  codesign --verify --strict --all-architectures "$CODEMCP" >/dev/null 2>&1 || fail "main codesign invalid"

  PROV="$DIST/BUILD_PROVENANCE.json"
  [ "$(getv "$PROV" schema)" = codemcp-remote-build-provenance-v2 ] || fail "provenance schema mismatch"
  [ "$(getv "$PROV" source.git_commit)" = "$SOURCE" ] || fail "provenance source mismatch"
  [ "$(getv "$PROV" target_arch)" = "$ARCH" ] || fail "provenance arch mismatch"
  [ "$(getv "$PROV" signing.mode)" = adhoc ] || fail "candidate is not ad-hoc signed"
  [ "$(getv "$PROV" notarization.status)" = not_performed ] || fail "candidate unexpectedly claims notarization"

  QUARANTINE=inherited
  if ! xattr -p com.apple.quarantine "$CODEMCP" >/dev/null 2>&1; then
    xattr -w com.apple.quarantine "0081;phase4;codemcp-remote;download" "$CODEMCP"
    QUARANTINE=simulated-after-sha-verification
  fi
  spctl --assess --type execute "$CODEMCP" >/dev/null 2>&1 && fail "Gatekeeper unexpectedly accepted candidate"

  mkdir -p "$PROJECT"
  git -C "$PROJECT" init -b main >/dev/null 2>&1 || git -C "$PROJECT" init >/dev/null
  git -C "$PROJECT" config user.name codemcp-phase4
  git -C "$PROJECT" config user.email codemcp-phase4@example.invalid
  printf 'phase4 baseline\n' > "$PROJECT/README.md"
  git -C "$PROJECT" add README.md
  git -C "$PROJECT" commit -m "test: phase4 baseline" >/dev/null

  /usr/bin/plutil -create xml1 "$STATE"
  /usr/bin/plutil -insert schema -string codemcp-remote-macos-phase4-state-v1 "$STATE"
  /usr/bin/plutil -insert source_commit -string "$SOURCE" "$STATE"
  /usr/bin/plutil -insert archive_sha256 -string "$SHA" "$STATE"
  /usr/bin/plutil -insert host_arch -string "$ARCH" "$STATE"

  /usr/bin/plutil -create xml1 "$EVIDENCE"
  /usr/bin/plutil -insert schema -string codemcp-remote-macos-clean-host-v1 "$EVIDENCE"
  /usr/bin/plutil -insert status -string prepared "$EVIDENCE"
  /usr/bin/plutil -insert source_commit -string "$SOURCE" "$EVIDENCE"
  /usr/bin/plutil -insert archive_sha256 -string "$SHA" "$EVIDENCE"
  /usr/bin/plutil -insert host_arch -string "$ARCH" "$EVIDENCE"
  /usr/bin/plutil -insert macos_version -string "$(sw_vers -productVersion)" "$EVIDENCE"
  /usr/bin/plutil -insert macos_build -string "$(sw_vers -buildVersion)" "$EVIDENCE"
  /usr/bin/plutil -insert signing -string adhoc "$EVIDENCE"
  /usr/bin/plutil -insert notarization -string not_performed "$EVIDENCE"
  /usr/bin/plutil -insert gatekeeper_default -string rejected "$EVIDENCE"
  /usr/bin/plutil -insert quarantine_mode -string "$QUARANTINE" "$EVIDENCE"
  /usr/bin/plutil -insert lifecycle_cycles_passed -integer 0 "$EVIDENCE"
  sync_json

  cat <<EOF
{"status":"ready-for-interactive-install","arch":"$ARCH","source_commit":"$SOURCE","archive_sha256":"$SHA"}
Run this explicit release step after verification:
  xattr -dr com.apple.quarantine "$DIST"
Then run the installer without exposing the token on argv:
  env -i HOME="$SANDBOX_HOME" PATH="$SAFE_PATH" TERM="\${TERM:-xterm-256color}" "$DIST/codemcp-install.sh"
Register project ID "$PROJECT_ID" at "$PROJECT".
EOF
}

assert_no_plaintext_secret() {
  [ -d "$APP_HOME" ] || fail "interactive install has not completed"
  if grep -R -n -E '(^|[^A-Za-z0-9_])(TUNNEL_TOKEN|CLOUDFLARE_TUNNEL_TOKEN)[[:space:]]*=' "$APP_HOME" "$DIST" >/dev/null 2>&1; then
    fail "plaintext token assignment found"
  fi
  if find "$APP_HOME" -type f \( -name '*.dpapi' -o -name '*token*' -o -name '*.key' \) -print | grep -q .; then
    fail "secret-like file found in macOS runtime home"
  fi
}

verify() {
  owned
  assert_no_plaintext_secret
  DOCTOR="$ROOT/doctor.json"
  run_clean "$CODEMCP" doctor > "$DOCTOR"
  [ "$(getv "$DOCTOR" status)" = ok ] || fail "doctor failed"
  [ "$(getv "$DOCTOR" checks.tunnel_token.status)" = ok ] || fail "tunnel token unavailable"
  [ "$(getv "$DOCTOR" checks.tunnel_token.source)" = macos-keychain ] || fail "token source is not macOS Keychain"
  [ "$(getv "$DOCTOR" checks.cloudflared.version)" = 2026.7.3 ] || fail "cloudflared version mismatch"
  [ "$(getv "$DOCTOR" checks.cloudflared.path)" = "$DIST/.codemcp-runtime/bin/cloudflared" ] || fail "bundled cloudflared not selected"
  [ -f "$APP_HOME/config/projects.toml" ] || fail "projects.toml missing"
  grep -F "[projects.$PROJECT_ID]" "$APP_HOME/config/projects.toml" >/dev/null || fail "test project not registered"
  grep -F "$PROJECT" "$APP_HOME/config/projects.toml" >/dev/null || fail "test project root mismatch"

  setv "$EVIDENCE" status installed-and-verified
  setv "$EVIDENCE" keychain_source macos-keychain
  setv "$EVIDENCE" cloudflared_version 2026.7.3
  sync_json
  printf '{"status":"ok","phase":4,"action":"verify","evidence":"%s"}\n' "$EVIDENCE_JSON"
}

secret_scan() {
  owned
  assert_no_plaintext_secret
  [ -t 0 ] && [ -t 1 ] || fail "secret-scan requires TTY"
  TOKEN=""
  ECHO_OFF=0
  restore() { [ "$ECHO_OFF" -eq 0 ] || stty echo 2>/dev/null || true; TOKEN=""; }
  trap restore EXIT
  trap 'restore; exit 130' INT
  trap 'restore; exit 143' TERM
  printf 'Re-enter Tunnel token for in-memory leak scan: '
  stty -echo; ECHO_OFF=1
  IFS= read -r TOKEN || fail "token input ended"
  stty echo; ECHO_OFF=0; printf '\n'
  [ -n "$TOKEN" ] || fail "token is empty"
  MATCHES=$(printf '%s\n' "$TOKEN" | grep -R -l -F -f - "$APP_HOME" "$DIST" 2>/dev/null || true)
  TOKEN=""
  [ -z "$MATCHES" ] || { printf '%s\n' "$MATCHES" >&2; fail "exact token found in file"; }
  setv "$EVIDENCE" secret_scan exact-token-not-found
  sync_json
  printf '{"status":"ok","phase":4,"action":"secret-scan","evidence":"%s"}\n' "$EVIDENCE_JSON"
}

lifecycle() {
  owned
  assert_no_plaintext_secret
  I=1
  while [ "$I" -le "$CYCLES" ]; do
    START="$ROOT/start-$I.json"
    RUNNING="$ROOT/running-$I.json"
    STOP="$ROOT/stop-$I.json"
    STOPPED="$ROOT/stopped-$I.json"

    run_clean "$CODEMCP" start > "$START"
    [ "$(getv "$START" status)" = ok ] || fail "cycle $I start failed"
    run_clean "$CODEMCP" status > "$RUNNING"
    [ "$(getv "$RUNNING" status)" = running ] || fail "cycle $I not running"
    [ "$(getv "$RUNNING" bridge.owned)" = true ] || fail "cycle $I bridge ownership failed"
    [ "$(getv "$RUNNING" tunnel.owned)" = true ] || fail "cycle $I tunnel ownership failed"

    run_clean "$CODEMCP" stop > "$STOP"
    [ "$(getv "$STOP" status)" = ok ] || fail "cycle $I stop failed"
    run_clean "$CODEMCP" status > "$STOPPED"
    [ "$(getv "$STOPPED" status)" = stopped ] || fail "cycle $I not stopped"

    ps -axo command= | grep -F "$DIST/codemcp-remote" | grep -v 'grep -F' >/dev/null 2>&1 &&
      fail "cycle $I left codemcp-remote process"
    ps -axo command= | grep -F "$DIST/.codemcp-runtime/bin/cloudflared" | grep -v 'grep -F' >/dev/null 2>&1 &&
      fail "cycle $I left cloudflared process"
    printf 'Phase 4 lifecycle %d/%d PASS\n' "$I" "$CYCLES"
    I=$((I + 1))
  done

  /usr/bin/plutil -replace lifecycle_cycles_passed -integer "$CYCLES" "$EVIDENCE"
  setv "$EVIDENCE" status lifecycle-pass
  sync_json
  printf '{"status":"ok","phase":4,"action":"lifecycle","cycles":%d,"evidence":"%s"}\n' "$CYCLES" "$EVIDENCE_JSON"
}

cleanup() {
  owned
  if [ -x "$CODEMCP" ] && [ -d "$APP_HOME" ]; then run_clean "$CODEMCP" stop >/dev/null 2>&1 || true; fi
  if [ -d "$APP_HOME" ]; then
    CANON=$(cd "$APP_HOME" && pwd -P)
    HOME_HASH=$(printf '%s' "$CANON" | shasum -a 256 | awk '{print $1}')
    ACCOUNT="transport:cloudflare:TUNNEL_TOKEN:$HOME_HASH"
    security delete-generic-password -s codemcp-remote -a "$ACCOUNT" >/dev/null 2>&1 || true
  fi
  case "$ROOT" in /|"$HOME"|"$HOME/") fail "unsafe cleanup root" ;; esac
  rm -rf "$ROOT"
  printf '{"status":"ok","phase":4,"action":"cleanup"}\n'
}

case "$ACTION" in
  prepare) prepare ;;
  verify) verify ;;
  secret-scan) secret_scan ;;
  lifecycle) lifecycle ;;
  cleanup) cleanup ;;
esac
