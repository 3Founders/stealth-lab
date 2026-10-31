#!/usr/bin/env bash
# One acceptance prompt through run-sandbox.sh, in its own fixture under fixtures/run/<name>, under 5 minutes.
# usage: run_prompt.sh <name> "<prompt>"   (the fixture must exist: make_fixture.sh fixtures/run/<name>; with_opencode.mjs the same)
set -u
here="$(cd "$(dirname "$0")/.." && pwd)"; name="$1"; prompt="$2"
dir="$here/fixtures/run/$name"
[ -d "$dir" ] || { echo "no fixture $dir" >&2; exit 2; }
export KELL_OC_DEBUG=1 KELL_OC_STATE_DIR="$here/sandbox/.state" KELL_OC_MIN_TIMEOUT_S="${KELL_OC_MIN_TIMEOUT_S:-90}" STEALTHLAB_ANSWERS="${STEALTHLAB_ANSWERS:-on}"
hooklog="$HOME/.stealthlab/hooks.log"; before=$(wc -l < "$hooklog" 2>/dev/null || echo 0)
start=$(date +%s)
OC_CWD="$dir" timeout 280 "$here/run-sandbox.sh" run --standalone --auto "$prompt" > "$dir/out.log" 2>&1
code=$?
echo "== $name exit=$code wall=$(( $(date +%s) - start ))s"
echo "-- last lines of the session:"; tail -n 8 "$dir/out.log" | cut -c1-300
echo "-- kell hook log (this run):"; tail -n +"$((before + 1))" "$hooklog" 2>/dev/null | cut -c1-260 | tail -n 12
echo "-- fixture changes:"; (cd "$dir" && git status --short | head -5)
