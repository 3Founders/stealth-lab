#!/usr/bin/env bash
# One prompt through headless Claude Code (kell's hooks from ~/.claude/settings.json) in fixtures/run/<name>, under 5 minutes.
# usage: run_cc.sh <name> "<prompt>"
set -u
here="$(cd "$(dirname "$0")/.." && pwd)"; name="$1"; prompt="$2"; dir="$here/fixtures/run/$name"
[ -d "$dir" ] || { echo "no fixture $dir" >&2; exit 2; }
unset STEALTHLAB_DISPATCH_CHECK
export STEALTHLAB_MCP_URL="${STEALTHLAB_MCP_URL:-https://mcp.kell.org.in/mcp}" STEALTHLAB_ANSWERS="${STEALTHLAB_ANSWERS:-on}"
if [ -z "${GENERAL_COMPUTE_API_KEY:-}" ] && [ -f "$here/../../backend/.env" ]; then
  GENERAL_COMPUTE_API_KEY="$(grep '^GENERAL_COMPUTE_API_KEY=' "$here/../../backend/.env" | head -1 | cut -d= -f2- | tr -d '"\r')"; export GENERAL_COMPUTE_API_KEY
fi
hooklog="$HOME/.stealthlab/hooks.log"; before=$(wc -l < "$hooklog" 2>/dev/null || echo 0)
start=$(date +%s)
(cd "$dir" && timeout 280 claude -p "$prompt" --output-format json --allowedTools "Bash,Read,Edit,Write,Glob,Grep" > out.json 2> err.log)
code=$?
echo "== $name (claude code) exit=$code wall=$(( $(date +%s) - start ))s"
node -e 'try{const j=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"));console.log("cost_usd",j.total_cost_usd,"turns",j.num_turns,"model(s)",Object.keys(j.modelUsage||{}).join(","));console.log(String(j.result||"").slice(-300))}catch(e){console.log("no json result")}' "$dir/out.json"
echo "-- kell hook log (this run):"; tail -n +"$((before + 1))" "$hooklog" 2>/dev/null | grep -a "prompt \|capture" | cut -c1-200 | tail -n 8
