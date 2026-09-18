#!/usr/bin/env python
"""
Minimal local frontend for hand-editing `.stealth/*.md` projection files --
the "separate, simpler MCP frontend" `app/stealth/edit_ledger.py`'s own
module docstring already names and builds an audit trail for.

Thin, not a reimplementation: every write goes through the exact same
`atomic_write` + `record_stealth_edit` + `write_ledger_projection` real
service functions the `record_stealth_edit` MCP tool itself calls -- this
script is just an HTTP-plus-HTML front door onto them, no new business
logic, no second ledger, no parsing of the pipe-format content (these
files are read/written as plain text on purpose -- there is no real
structured parser for claims.md/procedures.md/goals.md/implementations.md
today, only for goal_run.md, so pretending to offer structured editing
would be dishonest).

`.stealth/` stays exactly as disposable/regenerated as it always was.
Editing here is a deliberate, logged, human override of that projection --
never fed back into canonical Claim/Procedure/Goal creation (that remains
`preview_local_sync`/`commit_local_sync`'s job, untouched by this file).

Usage (from backend/):
    python scripts/stealth_md_editor.py --port 8767
    # then open http://127.0.0.1:8767/?repo_path=C:/path/to/your/repo

Port 8767, not 8766 -- that one is already `stealthlab_connect`'s status
page (packaging/src/stealthlab_connect/status_entry.py), and 8765/8000
are the MCP server / main FastAPI app.

Windows note (same as every other script in this repo): run with
`python`, not `python3`.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.db.session import create_pool
from app.services.procedure_extraction import _project_id_from_repo_root
from app.stealth.atomic import atomic_write
from app.stealth.edit_ledger import (
    EditLedgerError,
    list_stealth_edits,
    record_stealth_edit,
    write_ledger_projection,
)
from app.stealth.generator import CONTENT_PAGE_FILES, OPTIONAL_CONTENT_PAGE_FILES
from app.stealth.legacy_context import STEALTH_DIRNAME

# Same "real known content pages" list edit_ledger.py itself uses --
# imported, not re-typed, so this editor can never drift into offering a
# file the ledger would refuse to log.
EDITABLE_FILES: tuple[str, ...] = CONTENT_PAGE_FILES + OPTIONAL_CONTENT_PAGE_FILES

app = FastAPI(title="StealthLab .stealth/ editor")

# Local-only tool (loopback default, same posture as every other server
# in this codebase) -- CORS is opened for the Vite dev server's own
# localhost origins only, not "*", so this stays honest about being a
# single-machine dev tool, not a public API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def _resolve_workspace(repo_path: str) -> str:
    if not repo_path or not os.path.isdir(repo_path):
        raise HTTPException(422, f"repo_path {repo_path!r} is not a real directory on this machine.")
    return os.path.realpath(repo_path)


def _stealth_path(repo_path: str, file_path: str) -> str:
    if file_path not in EDITABLE_FILES:
        raise HTTPException(422, f"file_path must be one of {EDITABLE_FILES}, got {file_path!r}.")
    return os.path.join(repo_path, STEALTH_DIRNAME, file_path)


@app.get("/api/files")
async def get_files(repo_path: str) -> JSONResponse:
    """Real current content of every known `.stealth/*.md` page for this
    workspace. A missing file (never yet generated/edited) comes back as
    an empty string -- never fabricated placeholder text -- with
    `exists: false` so the UI can say so honestly."""
    repo_path = _resolve_workspace(repo_path)
    pages = {}
    for file_path in EDITABLE_FILES:
        full_path = _stealth_path(repo_path, file_path)
        if os.path.isfile(full_path):
            # utf-8-sig tolerates a leading BOM (e.g. from a text editor
            # that adds one) without leaking it into the content -- every
            # real .stealth/ writer in this codebase (atomic_write) never
            # writes one, but a hand-edited file might.
            with open(full_path, "r", encoding="utf-8-sig") as f:
                pages[file_path] = {"content": f.read(), "exists": True}
        else:
            pages[file_path] = {"content": "", "exists": False}
    return JSONResponse({"repo_path": repo_path, "files": pages})


class SaveBody(BaseModel):
    repo_path: str
    file_path: str
    content: str
    summary: str
    actor: str | None = None


@app.post("/api/save")
async def save_file(body: SaveBody, request: Request) -> JSONResponse:
    """Writes the file for real (atomic_write -- same safety as every
    other `.stealth/` writer in this codebase), then logs the edit via
    the real `record_stealth_edit` service function and refreshes
    `.stealth/ledger.md`. The file write and the ledger write are two
    separate steps on purpose (same convention `record_stealth_edit`'s
    own MCP tool docstring states): a ledger-write failure is reported,
    never used to roll back the file write that already succeeded."""
    repo_path = _resolve_workspace(body.repo_path)
    full_path = _stealth_path(repo_path, body.file_path)
    if not body.summary or not body.summary.strip():
        raise HTTPException(422, "summary is required -- a short, human-written note, like a commit message.")

    atomic_write(full_path, body.content)

    pool = request.app.state.pool
    project_id = _project_id_from_repo_root(repo_path)
    actor = body.actor or "stealth_md_editor"
    try:
        entry = await record_stealth_edit(
            pool, project_id=project_id, file_path=body.file_path,
            actor=actor, summary=body.summary, repo_path=repo_path,
        )
    except EditLedgerError as exc:
        # The file write already succeeded and is real -- only the audit
        # log failed to validate (should be unreachable here since
        # file_path/summary are already checked above, but never silently
        # swallowed if it somehow does).
        return JSONResponse({"saved": True, "ledger_error": str(exc)}, status_code=207)

    ledger_projection = "written"
    try:
        await write_ledger_projection(pool, workspace_root=repo_path, project_id=project_id)
    except OSError as exc:
        ledger_projection = f"write_failed: {exc}"

    return JSONResponse({
        "saved": True,
        "ledger_entry": {k: str(v) for k, v in entry.items()},
        "ledger_projection": ledger_projection,
    })


@app.get("/api/ledger")
async def get_ledger(repo_path: str, request: Request, file_path: str | None = None) -> JSONResponse:
    repo_path = _resolve_workspace(repo_path)
    pool = request.app.state.pool
    project_id = _project_id_from_repo_root(repo_path)
    entries = await list_stealth_edits(pool, project_id=project_id, file_path=file_path, limit=50)
    return JSONResponse([{k: str(v) for k, v in e.items()} for e in entries])


# ---------------------------------------------------------------------------
# Versioning: real diff-before-promote, not something new. `.stealth/` stays
# a disposable projection; the "versioning system" is app.stealth.local_sync
# -- it parses each CLAIM/PROCEDURE/GOAL pipe line's own `version=` field
# (the object's real bi-temporal version) and classifies it against the
# CURRENT canonical Postgres row: NEW / CHANGED / ALREADY_SYNCED /
# CONFLICTING (backend moved on since this projection was generated) /
# LOCAL_ONLY. This editor only exposes that existing preview/commit pair
# over HTTP -- no new sync logic, no second classifier.
# ---------------------------------------------------------------------------


@app.get("/api/sync/preview")
async def sync_preview(repo_path: str, request: Request) -> JSONResponse:
    """Real, read-only: writes nothing. See `preview_local_sync`'s own
    docstring -- recomputed fresh every call, never cached, since the
    backend can change between two calls."""
    from app.stealth.local_sync import preview_local_sync

    repo_path = _resolve_workspace(repo_path)
    pool = request.app.state.pool
    try:
        candidates = await preview_local_sync(pool, repo_path)
    except Exception as exc:  # noqa: BLE001 -- surfaced verbatim, never swallowed
        raise HTTPException(500, f"preview_local_sync failed: {exc}") from exc
    return JSONResponse(candidates)


class SyncCommitBody(BaseModel):
    repo_path: str
    selected_ids: list[str]
    allow_local_only: bool = False
    actor: str | None = None


@app.post("/api/sync/commit")
async def sync_commit(body: SyncCommitBody, request: Request) -> JSONResponse:
    """Real write: only the explicitly `selected_ids` are acted on, and
    only after re-deriving each one's classification fresh (never trusts
    a stale classification the browser is holding) -- see
    `commit_local_sync_items`'s own docstring. Each committed item lands
    as a normal candidate/private Claim/Procedure/Goal row via the exact
    same service-layer writers every other submission path uses."""
    from app.stealth.local_sync import commit_local_sync_items

    repo_path = _resolve_workspace(body.repo_path)
    pool = request.app.state.pool
    actor = body.actor or "stealth_md_editor"
    try:
        results = await commit_local_sync_items(
            pool, repo_path, body.selected_ids,
            created_by=actor, allow_local_only=body.allow_local_only,
        )
    except Exception as exc:  # noqa: BLE001 -- surfaced verbatim, never swallowed
        raise HTTPException(500, f"commit_local_sync_items failed: {exc}") from exc
    return JSONResponse([r for r in results])


@app.on_event("startup")
async def _startup() -> None:
    app.state.pool = await create_pool()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await app.state.pool.close()


_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>.stealth/ editor</title>
<style>
  body { font-family: -apple-system, Segoe UI, sans-serif; margin: 0; display: flex; height: 100vh; background: #1e1e1e; color: #ddd; }
  #sidebar { width: 220px; border-right: 1px solid #333; padding: 12px; box-sizing: border-box; overflow-y: auto; }
  #main { flex: 1; display: flex; flex-direction: column; padding: 12px; box-sizing: border-box; }
  h1 { font-size: 14px; margin: 0 0 12px; color: #999; }
  .tab { padding: 8px 10px; cursor: pointer; border-radius: 4px; margin-bottom: 4px; font-size: 13px; }
  .tab.active { background: #3a6df0; color: white; }
  .tab:not(.active):hover { background: #2a2a2a; }
  .missing { color: #888; font-style: italic; }
  textarea { flex: 1; width: 100%; font-family: "SF Mono", Consolas, monospace; font-size: 13px;
             background: #111; color: #ddd; border: 1px solid #333; border-radius: 4px; padding: 10px;
             box-sizing: border-box; resize: none; white-space: pre; }
  #toolbar { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
  #summary { flex: 1; padding: 6px 8px; background: #111; color: #ddd; border: 1px solid #333; border-radius: 4px; }
  button { padding: 7px 14px; border: none; border-radius: 4px; background: #3a6df0; color: white; cursor: pointer; }
  button:hover { background: #2f5bd0; }
  button:disabled { background: #444; cursor: not-allowed; }
  #status { font-size: 12px; color: #8f8; margin-left: 8px; }
  #repoInput { width: 100%; padding: 6px; margin-bottom: 10px; background: #111; color: #ddd; border: 1px solid #333; border-radius: 4px; box-sizing: border-box; }
  #ledger { margin-top: 12px; font-size: 12px; max-height: 160px; overflow-y: auto; border-top: 1px solid #333; padding-top: 8px; }
  #ledger div { padding: 3px 0; border-bottom: 1px solid #2a2a2a; }
</style>
</head>
<body>
<div id="sidebar">
  <h1>WORKSPACE</h1>
  <input id="repoInput" placeholder="C:\\path\\to\\repo" />
  <button onclick="loadWorkspace()" style="width:100%;margin-bottom:14px">Load</button>
  <h1>.stealth/ FILES</h1>
  <div id="tabs"></div>
  <h1 style="margin-top:16px">RECENT EDITS</h1>
  <div id="ledger">(load a workspace)</div>
</div>
<div id="main">
  <div id="toolbar">
    <input id="summary" placeholder="Summary of this edit (required, like a commit message)" />
    <button id="saveBtn" onclick="save()">Save</button>
    <span id="status"></span>
  </div>
  <textarea id="editor" placeholder="Load a workspace and pick a file on the left."></textarea>
</div>
<script>
const FILES = ["claims.md", "procedures.md", "implementations.md", "goals.md", "run.md", "exploration.md"];
let state = { repo_path: null, files: {}, current: null };

function qsRepoPath() {
  const p = new URLSearchParams(location.search).get("repo_path");
  return p || "";
}

async function loadWorkspace() {
  const repo_path = document.getElementById("repoInput").value.trim();
  if (!repo_path) return;
  const res = await fetch(`/api/files?repo_path=${encodeURIComponent(repo_path)}`);
  if (!res.ok) { alert(await res.text()); return; }
  const data = await res.json();
  state.repo_path = data.repo_path;
  state.files = data.files;
  renderTabs();
  loadLedger();
  if (!state.current) selectFile(FILES[0]);
}

function renderTabs() {
  const tabs = document.getElementById("tabs");
  tabs.innerHTML = "";
  for (const f of FILES) {
    const info = state.files[f] || { exists: false };
    const div = document.createElement("div");
    div.className = "tab" + (f === state.current ? " active" : "");
    div.textContent = f + (info.exists ? "" : "  (empty)");
    if (!info.exists) div.classList.add("missing");
    div.onclick = () => selectFile(f);
    tabs.appendChild(div);
  }
}

function selectFile(f) {
  state.current = f;
  document.getElementById("editor").value = (state.files[f] || {}).content || "";
  renderTabs();
}

async function save() {
  const summary = document.getElementById("summary").value.trim();
  if (!summary) { alert("Summary is required."); return; }
  if (!state.current) return;
  const content = document.getElementById("editor").value;
  const btn = document.getElementById("saveBtn");
  btn.disabled = true;
  const res = await fetch("/api/save", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ repo_path: state.repo_path, file_path: state.current, content, summary }),
  });
  btn.disabled = false;
  if (!res.ok) { alert(await res.text()); return; }
  const data = await res.json();
  state.files[state.current] = { content, exists: true };
  document.getElementById("status").textContent = "Saved + logged " + new Date().toLocaleTimeString();
  document.getElementById("summary").value = "";
  renderTabs();
  loadLedger();
}

async function loadLedger() {
  if (!state.repo_path) return;
  const res = await fetch(`/api/ledger?repo_path=${encodeURIComponent(state.repo_path)}`);
  const entries = await res.json();
  const el = document.getElementById("ledger");
  if (!entries.length) { el.textContent = "(no edits logged yet)"; return; }
  el.innerHTML = entries.map(e =>
    `<div><b>${e.file_path}</b> by ${e.actor}<br>${e.summary}<br><span style="color:#888">${e.created_at}</span></div>`
  ).join("");
}

window.onload = () => {
  const rp = qsRepoPath();
  if (rp) { document.getElementById("repoInput").value = rp; loadWorkspace(); }
};
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return _PAGE


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
