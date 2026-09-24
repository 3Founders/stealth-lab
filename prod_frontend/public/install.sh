#!/usr/bin/env bash
# StealthLab MCP installer for macOS / Linux.
#
#   curl -fsSL https://<site>/install.sh | bash
#   curl -fsSL https://<site>/install.sh | bash -s -- --client claude-code --token <tok>
#
# Installs nothing StealthLab-side on this machine: it registers the HOSTED
# StealthLab MCP endpoint with the coding agents it finds (Claude Code,
# Cursor, VS Code, Windsurf, Codex, Claude Desktop) and exits. With Node.js
# 18+ it runs `npx stealthlab-mcp install`; without Node it still registers
# Claude Code directly via `claude mcp add --transport http`.
set -euo pipefail

# Hosted endpoint. Empty until the production URL is fixed; override with
# STEALTHLAB_MCP_URL=... or --url.
DEFAULT_URL=""
PKG="${STEALTHLAB_MCP_PACKAGE:-stealthlab-mcp@latest}"   # override to pin a version

URL="${STEALTHLAB_MCP_URL:-$DEFAULT_URL}"
ARGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="${2:-}"; shift 2 ;;
    --url=*) URL="${1#--url=}"; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done

say()  { printf '%s\n' "$*" >&2; }
fail() { say "error: $*"; exit 1; }

[ -n "$URL" ] || fail "no MCP URL -- re-run with: curl -fsSL <site>/install.sh | bash -s -- --url https://<host>/mcp"

node_ok() {
  command -v node >/dev/null 2>&1 && command -v npx >/dev/null 2>&1 || return 1
  node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a>18||(a===18&&b>=17)?0:1)'
}

if node_ok; then
  say "==> Registering StealthLab MCP ($URL) with your agents"
  exec npx -y --package="$PKG" stealthlab-mcp install --url "$URL" ${ARGS[@]+"${ARGS[@]}"}
fi

say "Node.js 18.17+ not found -- registering what can be done without it."
if command -v claude >/dev/null 2>&1; then
  TOKEN=""
  set -- ${ARGS[@]+"${ARGS[@]}"}
  while [ $# -gt 0 ]; do
    case "$1" in --token) TOKEN="${2:-}"; shift 2 ;; --token=*) TOKEN="${1#--token=}"; shift ;; *) shift ;; esac
  done
  claude mcp remove --scope user stealthlab >/dev/null 2>&1 || true
  if [ -n "$TOKEN" ]; then
    claude mcp add --scope user --transport http stealthlab "$URL" --header "Authorization: Bearer $TOKEN"
  else
    claude mcp add --scope user --transport http stealthlab "$URL"
  fi
  say "  ok    Claude Code"
else
  say "  Claude Code not found."
fi
say ""
say "For other agents, add this remote MCP server in their settings:"
say "  { \"stealthlab\": { \"url\": \"$URL\" } }"
say "Or install Node.js (https://nodejs.org) and re-run this script to configure them automatically."
