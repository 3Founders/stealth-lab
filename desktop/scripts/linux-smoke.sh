#!/bin/bash
# Linux release check (2026-11-17), run on Ubuntu 20.04 by .github/workflows/desktop-linux.yml after `npm run dist:linux`. Checks what a
# Linux user touches: the native parts load on 20.04's glibc, the engine and the document maker run, the app starts and its routes
# answer (Chats, documents, schedules, inbox, connectors, quick ask), the AppImage starts, the .deb installs with its dependencies,
# and the one-line installer works. Model calls are not made (they need a signed-in account).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE/../release"
REL="$PWD"
FAIL=0
ok()   { echo "PASS  $*"; }
bad()  { echo "FAIL  $*"; FAIL=1; }
step() { echo; echo "== $*"; }

APP=$(ls kell-*-x86_64.AppImage | head -1)
DEB=$(ls kell-*-amd64.deb | head -1)
U="$REL/linux-unpacked"
R="$U/resources/app"
[ -f "$APP" ] && ok "AppImage built: $APP ($(du -h "$APP" | cut -f1))" || bad "no AppImage"
[ -f "$DEB" ] && ok ".deb built: $DEB ($(du -h "$DEB" | cut -f1))" || bad "no .deb"
[ -f latest-linux.yml ] && ok "latest-linux.yml (auto-update feed)" || bad "no latest-linux.yml"

step "native modules need at most glibc 2.31 (Ubuntu 20.04)"
for f in $(find "$U" -name '*.node'); do
  need=$(objdump -T "$f" 2>/dev/null | grep -o 'GLIBC_[0-9.]*' | sed 's/GLIBC_//' | sort -V | tail -1)
  if [ -z "$need" ] || [ "$(printf '%s\n2.31\n' "$need" | sort -V | tail -1)" = "2.31" ]; then ok "$f needs glibc ${need:-none}"; else bad "$f needs glibc $need"; fi
done
need=$(objdump -T "$U/kell" 2>/dev/null | grep -o 'GLIBC_[0-9.]*' | sed 's/GLIBC_//' | sort -V | tail -1)
[ "$(printf '%s\n2.31\n' "$need" | sort -V | tail -1)" = "2.31" ] && ok "kell (Electron) needs glibc $need" || bad "kell (Electron) needs glibc $need"

step "the coding engine runs"
ENG=$(find "$R/node_modules/@opencode" -path '*cli-linux-x64/bin/opencode' | head -1)
[ -x "$ENG" ] && v=$("$ENG" --version 2>&1 | head -1) && ok "engine $v" || bad "engine missing or does not run ($ENG)"
( cd /tmp && timeout 40 "$ENG" serve --hostname 127.0.0.1 --port 0 > /tmp/engine.log 2>&1 & )
for i in $(seq 1 40); do grep -q "server listening" /tmp/engine.log && break; sleep 1; done
grep -q "server listening" /tmp/engine.log && ok "engine serves: $(grep -o 'server listening on [^ ]*' /tmp/engine.log)" || { bad "engine did not serve"; tail -5 /tmp/engine.log; }
pkill -f "opencode serve" || true

step "the terminal panel (node-pty) works"
ELECTRON_RUN_AS_NODE=1 "$U/kell" -e "
  const pty = require('$R/node_modules/node-pty');
  const p = pty.spawn('/bin/sh', ['-c', 'echo pty-ok'], { cols: 80, rows: 24 });
  let out = ''; p.onData((d) => out += d); p.onExit(() => { console.log(out.includes('pty-ok') ? 'PTY_OK' : 'PTY_BAD ' + out); process.exit(0) });
  setTimeout(() => { console.log('PTY_TIMEOUT'); process.exit(0) }, 10000);
" 2>&1 | tee /tmp/pty.log | tail -1
grep -q PTY_OK /tmp/pty.log && ok "node-pty spawns a shell" || bad "node-pty: $(tail -3 /tmp/pty.log)"

step "documents (kell-docs) through the app's own runtime"
D=$(mktemp -d)
cat > "$D/w.json" <<'J'
{ "path": "Report.docx", "title": "Report", "markdown": "# Report\n\nRevenue grew **12%**.\n\n| a | b |\n|---|---|\n| 1 | 2 |" }
J
sed 's/Report.docx/Report.pdf/' "$D/w.json" > "$D/p.json"
cat > "$D/x.json" <<'J'
{ "path": "Budget.xlsx", "sheets": [ { "name": "B", "columns": ["M", "R", "C", "P"], "rows": [["Jan", 10, 4, "=B2-C2"]], "totals": true } ] }
J
cat > "$D/s.json" <<'J'
{ "path": "Deck.pptx", "slides": [ { "title": "T", "subtitle": "s" }, { "title": "Chart", "chart": { "type": "bar", "labels": ["a", "b"], "series": [{ "name": "x", "values": [1, 2] }] } } ] }
J
for j in w p x s; do (cd "$D" && STEALTHLAB_HOME="$D/h" ELECTRON_RUN_AS_NODE=1 "$U/kell" "$R/vendor/kell-docs/kell-docs.mjs" make "$j.json" >/dev/null 2>&1) ; done
for f in Report.docx Report.pdf Budget.xlsx Deck.pptx; do [ -s "$D/$f" ] && ok "made $f" || bad "did not make $f"; done
ELECTRON_RUN_AS_NODE=1 "$U/kell" "$R/vendor/kell-docs/kell-docs.mjs" read "$D/Budget.xlsx" | grep -q "6" && ok "read xlsx with computed formula" || bad "xlsx read"
ELECTRON_RUN_AS_NODE=1 "$U/kell" "$R/vendor/kell-docs/kell-docs.mjs" html "$D/Deck.pptx" | grep -q "slide" && ok "deck preview html" || bad "deck preview"

step "the app starts (Xvfb) and its features answer"
export HOME_T=$(mktemp -d)
rm -f /tmp/app.log
( HOME="$HOME_T" KELL_NO_UPDATE=1 xvfb-run -a -s "-screen 0 1280x900x24" "$U/kell" --no-sandbox --remote-debugging-port=9333 > /tmp/app.log 2>&1 & )
for i in $(seq 1 120); do grep -q "window loaded" /tmp/app.log && break; sleep 1; done
grep -q "window loaded" /tmp/app.log && ok "window loaded" || { bad "window did not load"; tail -20 /tmp/app.log; }
grep -o 'presence {[^}]*}' /tmp/app.log | head -1 | sed 's/^/INFO  /'
HOME="$HOME_T" node "$HERE/linux-smoke-cdp.mjs" "$D/Report.docx" 2>&1 | tee /tmp/cdp.log
grep -q "^FAIL" /tmp/cdp.log && FAIL=1
pkill -f "$U/kell" || true; sleep 1

step "the AppImage itself starts"
rm -f /tmp/ai.log
( HOME=$(mktemp -d) APPIMAGE_EXTRACT_AND_RUN=1 KELL_NO_UPDATE=1 timeout 90 xvfb-run -a "./$APP" --no-sandbox > /tmp/ai.log 2>&1 & )
for i in $(seq 1 90); do grep -q "window loaded" /tmp/ai.log && break; sleep 1; done
grep -q "window loaded" /tmp/ai.log && ok "AppImage window loaded" || { bad "AppImage did not start"; tail -15 /tmp/ai.log; }
pkill -f "kell" || true; sleep 1

step "the .deb installs with its dependencies"
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "./$DEB" > /tmp/deb.log 2>&1 && ok ".deb installed" || { bad ".deb install"; tail -10 /tmp/deb.log; }
FILES=$(dpkg -L kell-desktop 2>/dev/null || true)
BIN=$(printf '%s
' "$FILES" | grep -E '/kell$' | while read -r f; do [ -f "$f" ] && echo "$f"; done | head -1)
[ -n "$BIN" ] && [ -x "$BIN" ] && ok "installed at $BIN" || bad "kell binary not found after install"
printf '%s
' "$FILES" | grep '\.desktop$' > /tmp/desk.txt; [ -s /tmp/desk.txt ] && ok "menu entry installed: $(head -1 /tmp/desk.txt)" || bad "no menu entry"
[ -n "$BIN" ] && { ( HOME=$(mktemp -d) KELL_NO_UPDATE=1 timeout 90 xvfb-run -a "$BIN" --no-sandbox > /tmp/debrun.log 2>&1 & ); for i in $(seq 1 90); do grep -q "window loaded" /tmp/debrun.log && break; sleep 1; done; grep -q "window loaded" /tmp/debrun.log && ok "installed app starts" || { bad "installed app did not start"; tail -10 /tmp/debrun.log; }; pkill -f "$BIN" || true; }

step "the one-line installer (desktop.sh) against a local copy of /download"
SITE_DIR=$(mktemp -d)
cat > "$SITE_DIR/srv.py" <<PY
import http.server, os
APP = os.path.abspath("$APP")
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/download"):
            self.send_response(200); self.send_header("content-length", str(os.path.getsize(APP))); self.end_headers()
            with open(APP, "rb") as f: self.wfile.write(f.read())
        else: self.send_response(404); self.end_headers()
    def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", 8765), H).serve_forever()
PY
( python3 "$SITE_DIR/srv.py" & ) ; sleep 1
H2=$(mktemp -d)
HOME="$H2" KELL_SITE=http://127.0.0.1:8765 DISPLAY= sh "$HERE/../../routing_kell_frontend/public/desktop.sh" > /tmp/inst.log 2>&1 && ok "desktop.sh ran" || { bad "desktop.sh"; tail -10 /tmp/inst.log; }
[ -x "$H2/.local/bin/kell.AppImage" ] && ok "AppImage in ~/.local/bin" || bad "no ~/.local/bin/kell.AppImage"
[ -f "$H2/.local/share/applications/kell.desktop" ] && ok "menu entry written" || bad "no kell.desktop"
pkill -f srv.py || true; pkill -f kell.AppImage || true

echo
[ $FAIL = 0 ] && echo "ALL LINUX CHECKS PASSED" || echo "SOME LINUX CHECKS FAILED"
exit $FAIL
