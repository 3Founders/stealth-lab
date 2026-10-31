#!/usr/bin/env bash
# Runs the pinned OpenCode in an isolated profile (XDG dirs under sandbox/.xdg) so the user's own OpenCode is untouched.
# The provider key is read from backend/.env into this process only and never printed.
set -e
here="$(cd "$(dirname "$0")" && pwd)"
if [ -z "$GENERAL_COMPUTE_API_KEY" ] && [ -f "$here/../../backend/.env" ]; then
  GENERAL_COMPUTE_API_KEY="$(grep '^GENERAL_COMPUTE_API_KEY=' "$here/../../backend/.env" | head -1 | cut -d= -f2- | tr -d '"\r')"
  export GENERAL_COMPUTE_API_KEY
fi
export XDG_CONFIG_HOME="$here/sandbox/.xdg/config" XDG_DATA_HOME="$here/sandbox/.xdg/data" XDG_CACHE_HOME="$here/sandbox/.xdg/cache" XDG_STATE_HOME="$here/sandbox/.xdg/state"
mkdir -p "$XDG_CONFIG_HOME" "$XDG_DATA_HOME" "$XDG_CACHE_HOME" "$XDG_STATE_HOME"
cd "${OC_CWD:-$here/sandbox}"
exec "$here/node_modules/.bin/opencode" "$@"
