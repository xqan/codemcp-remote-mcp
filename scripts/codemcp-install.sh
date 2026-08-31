#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)

# Remove macOS quarantine attributes from the extracted distribution if present.
if [ "$(uname -s)" = "Darwin" ]; then
    xattr -dr com.apple.quarantine "$SCRIPT_DIR" 2>/dev/null || true
fi

CODEMCP="$SCRIPT_DIR/codemcp-remote"
CHECKSUMS="$SCRIPT_DIR/SHA256SUMS.txt"
ECHO_DISABLED=0
TUNNEL_TOKEN=""

cleanup() {
    if [ "$ECHO_DISABLED" -eq 1 ]; then
        stty echo 2>/dev/null || true
        ECHO_DISABLED=0
        printf '\n'
    fi
    TUNNEL_TOKEN=""
    unset TUNNEL_TOKEN
}

on_hup() {
    cleanup
    exit 129
}

on_int() {
    cleanup
    exit 130
}

on_term() {
    cleanup
    exit 143
}

trap cleanup 0
trap on_hup 1
trap on_int 2
trap on_term 15

fail() {
    printf '%s\n' "codemcp-install: $*" >&2
    exit 1
}

prompt_required() {
    prompt=$1
    value=""
    while [ -z "$value" ]; do
        printf '%s' "$prompt" >&2
        if ! IFS= read -r value; then
            fail "input ended before setup was complete"
        fi
    done
    printf '%s' "$value"
}

[ -t 0 ] && [ -t 1 ] || fail "interactive setup requires a TTY on stdin and stdout"
[ -x "$CODEMCP" ] || fail "packaged codemcp-remote executable is missing or not executable"
[ -f "$CHECKSUMS" ] || fail "SHA256SUMS.txt is missing"
command -v shasum >/dev/null 2>&1 || fail "shasum is required to verify the extracted distribution"
command -v stty >/dev/null 2>&1 || fail "stty is required for secret input"
[ -n "${HOME:-}" ] || fail "HOME is required"
[ -z "${CODEMCP_HOME:-}" ] || fail "CODEMCP_HOME is set; use the non-interactive CLI for a custom home"

(
    cd "$SCRIPT_DIR"
    shasum -a 256 -c SHA256SUMS.txt
) || fail "distribution checksum verification failed"

DEFAULT_HOME="$HOME/Library/Application Support/codemcp-remote"
if [ -d "$DEFAULT_HOME" ] && [ -n "$(find "$DEFAULT_HOME" -mindepth 1 -print -quit 2>/dev/null)" ]; then
    fail "runtime home already contains data: $DEFAULT_HOME; setup will not overwrite it"
fi

printf '%s\n' ""
printf '%s\n' "codemcp-remote macOS setup"
printf '%s\n' "Writable home: $DEFAULT_HOME"
printf '%s\n' "Prerequisites: create the Cloudflare Tunnel, DNS route, and required WAF/IP controls first."
printf '%s\n' "This wizard does not call the Cloudflare API and does not create account resources."
printf '%s\n' ""

PUBLIC_URL=$(prompt_required "Cloudflare public MCP URL (https://.../mcp): ")
ALLOWED_HOST=$(prompt_required "Exact allowed host (for example codemcp.example.com): ")
printf '%s' "Allowed origin [https://chatgpt.com]: "
if ! IFS= read -r ALLOWED_ORIGIN; then
    fail "input ended before setup was complete"
fi
if [ -z "$ALLOWED_ORIGIN" ]; then
    ALLOWED_ORIGIN="https://chatgpt.com"
fi

printf '%s' "Cloudflare remotely-managed Tunnel token: "
stty -echo
ECHO_DISABLED=1
if ! IFS= read -r TUNNEL_TOKEN; then
    fail "input ended while reading the Tunnel token"
fi
stty echo
ECHO_DISABLED=0
printf '\n'
[ -n "$TUNNEL_TOKEN" ] || fail "Tunnel token must not be empty"

printf '%s\n' ""
printf '%s\n' "Configuration summary:"
printf '  Public URL: %s\n' "$PUBLIC_URL"
printf '  Allowed host: %s\n' "$ALLOWED_HOST"
printf '  Allowed origin: %s\n' "$ALLOWED_ORIGIN"
printf '  Writable home: %s\n' "$DEFAULT_HOME"
printf '%s\n' "  Transport: cloudflare"
printf '%s\n' "  Auth mode: none"
printf '%s\n' "  Network trust: cloudflare-chatgpt"
printf '%s' "Continue? [y/N]: "
if ! IFS= read -r CONFIRM; then
    fail "input ended before confirmation"
fi
case "$CONFIRM" in
    y|Y|yes|YES|Yes) ;;
    *) fail "setup cancelled" ;;
esac

export TUNNEL_TOKEN
"$CODEMCP" init \
    --transport cloudflare \
    --auth-mode none \
    --network-trust cloudflare-chatgpt \
    --public-url "$PUBLIC_URL" \
    --allowed-host "$ALLOWED_HOST" \
    --allowed-origin "$ALLOWED_ORIGIN" \
    --store-transport-secret
TUNNEL_TOKEN=""
unset TUNNEL_TOKEN

printf '%s' "Register a Git project now? [y/N]: "
if ! IFS= read -r REGISTER_PROJECT; then
    fail "input ended before setup was complete"
fi
case "$REGISTER_PROJECT" in
    y|Y|yes|YES|Yes)
        PROJECT_ID=$(prompt_required "Project ID: ")
        PROJECT_ROOT=$(prompt_required "Absolute Git worktree root: ")
        "$CODEMCP" project add "$PROJECT_ID" "$PROJECT_ROOT"
        ;;
esac

"$CODEMCP" doctor

printf '%s\n' ""
printf '%s\n' "Setup completed. Services were not started automatically."
printf '%s\n' "Run ./codemcp-start.sh when you are ready to start codemcp-remote."
