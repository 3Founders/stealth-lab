"""Goal-run visualizer page served by the MCP server.

The counterpart of `claim_graph_page.py`/`procedure_graph_page.py`, but a
deliberately DIFFERENT shape: those two render a whole-corpus force-graph
from the database; this page renders ONE local `.stealth/goal_run.md`
(compile-time `"planned"` or real post-execution trace, `compile_goal`/
`execute_goal`'s own `workspace_root` write -- see those tools' own
docstrings) plus its `.stealth/artifacts/` manifest, both real local
filesystem reads, no DB at all.

Real, disclosed shape limitation: `goal_run.md`'s own grammar
(`pipe_format.py`) does not persist per-node `deps` -- only the flattened
node list in the real execution/compile order
(`goal_compiler.py::flatten_goal_tree`'s own docstring: "steps are linear
by construction"). So this renders a real, honest LEFT-TO-RIGHT SEQUENCE,
not a general force-directed graph -- a general graph widget here would
imply dependency structure this file's own grammar does not actually
carry.
"""
from __future__ import annotations

GOAL_RUN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StealthLab — goal run</title>
<style>
  :root{
    --bg:#0d0f14; --panel:#151922; --line:#232936; --ink:#e7ebf3; --muted:#8b93a7;
    --accent:#6ea8fe; --ok:#3ecf8e; --bad:#e0574b; --pending:#e0b84b; --human:#b98cff;
  }
  *{box-sizing:border-box}
  html,body{margin:0;min-height:100%;background:var(--bg);color:var(--ink);
    font:13px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
  .bar{position:sticky;top:0;display:flex;gap:8px;align-items:center;flex-wrap:wrap;
    padding:12px;background:var(--bg);border-bottom:1px solid var(--line);z-index:5}
  .bar .brand{font-weight:600;letter-spacing:.02em;background:var(--panel);
    border:1px solid var(--line);border-radius:8px;padding:6px 10px}
  input,button{background:#0b0e14;color:var(--ink);border:1px solid var(--line);
    border-radius:7px;padding:6px 9px;font:inherit}
  input{flex:1;min-width:260px}
  button{cursor:pointer}
  button:hover,input:focus{border-color:var(--accent);outline:none}
  #status{margin-left:auto;color:var(--muted)}
  main{max-width:960px;margin:0 auto;padding:20px}
  #header{color:var(--muted);margin-bottom:18px}
  #header b{color:var(--ink)}
  .chain{display:flex;flex-direction:column;gap:0}
  .node{background:var(--panel);border:1px solid var(--line);border-radius:10px;
    padding:12px 14px;position:relative}
  .node + .node{margin-top:22px}
  .node + .node::before{content:"";position:absolute;left:20px;top:-22px;width:2px;height:22px;
    background:var(--line)}
  .node .top{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
  .pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:11px;font-weight:700;
    color:#0b0e14}
  .goal{font-weight:600}
  .kv{color:var(--muted);font-size:12px;margin-top:6px;display:grid;
    grid-template-columns:auto 1fr;gap:3px 12px}
  .kv dt{color:var(--muted)}
  .kv dd{margin:0;word-break:break-word}
  .artifacts{margin-top:22px}
  .artifacts h3{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
  .art-row{display:flex;gap:10px;align-items:baseline;padding:6px 0;border-top:1px solid var(--line);
    font-size:12px}
  .art-row a{color:var(--accent);text-decoration:none}
  .art-row a:hover{text-decoration:underline}
  .empty{color:var(--muted);padding:24px 0}
  code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px}
</style>
</head>
<body>
<div class="bar">
  <span class="brand">StealthLab · goal run</span>
  <input id="ws" type="text" placeholder="workspace_root, e.g. C:\path\to\checkout">
  <button id="load">Load</button>
  <span id="status"></span>
</div>
<main>
  <div id="header"></div>
  <div id="chain" class="chain"></div>
  <div id="artifacts" class="artifacts"></div>
</main>
<script>
const STATUS_COLOR = {
  planned: "var(--pending)", needs_input: "var(--human)",
  success: "var(--ok)", failure: "var(--bad)",
};

function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  Object.assign(n, props || {});
  for (const k of kids) n.append(k);
  return n;
}

function pill(text, color) {
  return el("span", {className: "pill", textContent: text, style: `background:${color || "#7a879e"}`});
}

async function load() {
  const ws = document.getElementById("ws").value.trim();
  const statusEl = document.getElementById("status");
  const headerEl = document.getElementById("header");
  const chainEl = document.getElementById("chain");
  const artEl = document.getElementById("artifacts");
  chainEl.innerHTML = ""; artEl.innerHTML = ""; headerEl.innerHTML = "";
  if (!ws) { statusEl.textContent = "enter a workspace_root"; return; }
  statusEl.textContent = "loading…";
  try {
    const res = await fetch("/goal-run/data?workspace_root=" + encodeURIComponent(ws));
    const data = await res.json();
    if (data.error) { statusEl.textContent = data.error; return; }
    statusEl.textContent = "";
    const run = data.status;
    if (!run) {
      chainEl.append(el("div", {className: "empty",
        textContent: "no .stealth/goal_run.md found at this workspace_root yet."}));
    } else {
      headerEl.append(el("div", {}, "execution_id: ", el("b", {textContent: run.execution_id}),
        "  ·  outcome: ", el("b", {textContent: run.outcome})));
      if (!run.nodes.length) {
        chainEl.append(el("div", {className: "empty", textContent: "(no nodes)"}));
      }
      for (const n of run.nodes) {
        const top = el("div", {className: "top"},
          pill(n.status, STATUS_COLOR[n.status]),
          el("span", {className: "goal", textContent: n.goal_id}),
          el("span", {style: "color:var(--muted)", textContent: n.kind}));
        const kv = el("dl", {className: "kv"});
        const add = (k, v) => { if (v) { kv.append(el("dt", {textContent: k}), el("dd", {textContent: v})); } };
        add("implementation", n.implementation_id);
        add("procedure", n.procedure_id);
        add("verify", n.verification_state);
        if (n.human_intervention_needed) add("human_intervention", "true");
        if (n.resumed_from_journal) add("resumed", "true");
        chainEl.append(el("div", {className: "node"}, top, kv));
      }
    }
    const artifacts = data.artifacts || [];
    artEl.append(el("h3", {textContent: `artifacts (${artifacts.length})`}));
    if (!artifacts.length) {
      artEl.append(el("div", {className: "empty", textContent: "(no artifacts written at this workspace_root)"}));
    }
    for (const a of artifacts) {
      const href = "/goal-run/artifact?workspace_root=" + encodeURIComponent(ws)
        + "&goal_id=" + encodeURIComponent(a.goal_id)
        + "&execution_id=" + encodeURIComponent(a.execution_id)
        + "&filename=" + encodeURIComponent(a.filename);
      artEl.append(el("div", {className: "art-row"},
        el("a", {href, textContent: a.filename, target: "_blank"}),
        el("code", {textContent: a.goal_id}),
        el("span", {style: "color:var(--muted)", textContent: `${a.size_bytes} bytes`})));
    }
  } catch (e) {
    statusEl.textContent = "error: " + e;
  }
}

document.getElementById("load").addEventListener("click", load);
document.getElementById("ws").addEventListener("keydown", (e) => { if (e.key === "Enter") load(); });

const params = new URLSearchParams(location.search);
if (params.get("workspace_root")) {
  document.getElementById("ws").value = params.get("workspace_root");
  load();
}
</script>
</body>
</html>
"""
