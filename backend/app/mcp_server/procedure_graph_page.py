"""Procedure / task-node graph visualizer page served by the MCP server.

The procedure-side counterpart of `claim_graph_page.py`. Same renderer
(`force-graph`, vendored, served from `/claim-graph/vendor/force-graph.js`
-- this page reuses that one asset, it does not ship its own), same inline
single-HTML-string design, same dark Obsidian-style graph view.

It calls `./procedure-graph/data` for `{nodes, edges, counts, ...}`.
Nodes are live procedures + live task_nodes; edges are procedure version
chains (`SUPERSEDES`), procedure -> task decomposition (`DECOMPOSES_TO`),
task -> task hierarchy (`PARENT_OF`), and step-level subprocedure
references (`SUBPROCEDURE_OF`). Read-only -- there is no edit surface here.
"""
from __future__ import annotations

PROCEDURE_GRAPH_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StealthLab — procedure & task graph</title>
<style>
  :root{
    --bg:#0d0f14; --panel:#151922; --line:#232936; --ink:#e7ebf3; --muted:#8b93a7;
    --accent:#6ea8fe;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);overflow:hidden;
    font:13px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
  #graph{position:fixed;inset:0}
  #graph canvas{display:block}
  .bar{position:fixed;top:10px;left:10px;right:10px;display:flex;gap:8px;align-items:center;
    flex-wrap:wrap;z-index:5;pointer-events:none}
  .bar>*{pointer-events:auto}
  .bar .brand{font-weight:600;letter-spacing:.02em;background:var(--panel);
    border:1px solid var(--line);border-radius:8px;padding:6px 10px}
  input,select,button{background:#0b0e14;color:var(--ink);border:1px solid var(--line);
    border-radius:7px;padding:6px 9px;font:inherit}
  button{cursor:pointer}
  button:hover,input:focus,select:focus{border-color:var(--accent);outline:none}
  label.chk{display:inline-flex;gap:5px;align-items:center;color:var(--muted);
    background:var(--panel);border:1px solid var(--line);border-radius:7px;padding:6px 9px}
  #stats{margin-left:auto;color:var(--muted);background:var(--panel);border:1px solid var(--line);
    border-radius:8px;padding:6px 10px;font-variant-numeric:tabular-nums}
  #legend{position:fixed;left:12px;bottom:12px;z-index:5;background:rgba(21,25,34,.92);
    border:1px solid var(--line);border-radius:10px;padding:9px 11px;display:grid;gap:4px;
    max-width:240px}
  #legend .t{color:var(--muted);text-transform:uppercase;letter-spacing:.06em;font-size:10px;
    margin-bottom:2px}
  #legend .row{display:flex;align-items:center;gap:7px;color:var(--muted)}
  #legend i{width:10px;height:10px;border-radius:50%;flex:none}
  #legend i.sq{border-radius:2px}
  #legend i.ring{box-shadow:0 0 0 2px #e0574b inset}
  #legend .e{width:16px;height:0;border-top:2px solid #7a879e}
  #legend .e.ver{border-top-style:dashed;border-color:#e0574b}
  #legend .e.sub{border-color:#b98cff}
  #hint{position:fixed;right:12px;bottom:12px;z-index:5;color:var(--muted);font-size:11px;
    background:rgba(21,25,34,.85);border:1px solid var(--line);border-radius:8px;padding:5px 9px}
  #panel{position:fixed;top:0;right:0;bottom:0;width:390px;max-width:90vw;z-index:6;
    background:var(--panel);border-left:1px solid var(--line);padding:18px;overflow:auto;
    transform:translateX(101%);transition:transform .16s ease}
  #panel.open{transform:none}
  #panel h2{margin:0 0 2px;font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
  #panel .stmt{font-size:15px;line-height:1.5;margin:6px 0 6px}
  #panel .goal{color:var(--muted);font-size:12.5px;margin:0 0 14px}
  #panel dl{display:grid;grid-template-columns:92px 1fr;gap:5px 12px;margin:0 0 14px}
  #panel dt{color:var(--muted)}
  #panel dd{margin:0;word-break:break-word}
  #panel .pill{display:inline-block;padding:1px 9px;border-radius:999px;font-size:11px;
    font-weight:700;color:#0b0e14}
  #panel h3{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;
    margin:14px 0 6px}
  #panel .nb{padding:6px 0;border-top:1px solid var(--line);cursor:pointer;display:flex;
    gap:8px;align-items:baseline}
  #panel .nb:hover{color:var(--accent)}
  #panel .nb .k{font-size:10px;color:var(--muted);flex:none;min-width:104px}
  #panel .close{position:absolute;right:12px;top:12px;background:none;border:none;color:var(--muted);
    font-size:20px;line-height:1;cursor:pointer}
  #loading{position:fixed;inset:0;display:grid;place-items:center;color:var(--muted);z-index:4;
    pointer-events:none}
  code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:11px}
</style>
</head>
<body>
<div id="graph"></div>
<div id="loading">loading procedure graph…</div>

<div class="bar">
  <span class="brand">StealthLab · procedures &amp; tasks</span>
  <input id="q" type="search" placeholder="filter name / goal…" size="18">
  <select id="group" title="colour procedures by">
    <option value="verification">colour: verification</option>
    <option value="provenance">colour: provenance</option>
    <option value="scope">colour: scope</option>
    <option value="source">colour: created_by</option>
  </select>
  <select id="links" title="which edges">
    <option value="all">links: all</option>
    <option value="version">links: version chains</option>
    <option value="decomposition">links: decomposition</option>
  </select>
  <select id="limit" title="max nodes">
    <option>80</option><option selected>150</option><option>300</option><option>500</option>
  </select>
  <label class="chk"><input id="tasks" type="checkbox" checked> task nodes</label>
  <label class="chk"><input id="stale" type="checkbox"> stale</label>
  <label class="chk"><input id="orphans" type="checkbox"> hide orphans</label>
  <button id="refresh">Refresh</button>
  <span id="stats"></span>
</div>

<div id="legend"></div>
<div id="hint">scroll = zoom · drag = pan · drag node = pin · click = details</div>

<aside id="panel"><button class="close" title="close">×</button><div id="body"></div></aside>

<script src="./claim-graph/vendor/force-graph.js"></script>
<script>
"use strict";
const $ = s => document.querySelector(s);
const esc = s => (s==null?"":String(s)).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

const VSTATE = {
  verified:"#4ec9a3", candidate:"#e0b64b", draft:"#9aa3b2",
  deprecated:"#7a8095", retired:"#7a8095", superseded:"#7a8095", unknown:"#9aa3b2",
};
const TASK_COLOR = "#6ea8fe";
const GROUP_PALETTE = ["#6ea8fe","#4ec9a3","#e0b64b","#e08b4b","#e0574b","#b98cff",
  "#5bc0de","#8bc34a","#ff8fab","#9aa3b2","#c1a15a","#63d1c9"];
// version-lineage edges get the dashed "belief revision" treatment, same
// as SUPERSEDES on the claim graph.
const VERSION_KIND = new Set(["version"]);

let RAW = { nodes: [], edges: [] };
let ADJ = new Map();                 // id -> [{id, link}]
let nodeById = new Map();
let groupColors = new Map();
let selectedId = null;
let hoverId = null;
let hiN = new Set(), hiL = new Set();
let didFit = false;

const el = document.getElementById('graph');
const Graph = ForceGraph()(el)
  .backgroundColor('#0d0f14')
  .autoPauseRedraw(false)
  .nodeId('id')
  .nodeRelSize(1)
  .nodeVal(n => n.__r * n.__r)
  .nodeLabel(() => '')                // custom labels drawn in nodeCanvasObject
  .linkColor(linkColor)
  .linkWidth(l => hiL.has(l.__id) ? 2.4 : (l.kind === 'hierarchy' ? 0.9 : 1.4))
  .linkLineDash(l => VERSION_KIND.has(l.kind) ? [4,3] : null)
  .linkDirectionalArrowLength(4)
  .linkDirectionalArrowRelPos(1)
  .linkDirectionalArrowColor(linkArrowColor)
  .nodeCanvasObject(drawNode)
  .nodePointerAreaPaint((n, color, ctx) => {
    ctx.fillStyle = color;
    ctx.beginPath(); ctx.arc(n.x, n.y, n.__r + 2, 0, 6.2832); ctx.fill();
  })
  .onNodeHover(n => { hoverId = n ? n.id : null; recomputeHighlight(); el.style.cursor = n ? 'pointer' : 'grab'; })
  .onNodeClick(n => { select(n.id); Graph.centerAt(n.x, n.y, 500); })
  .onBackgroundClick(() => { select(null); })
  .cooldownTicks(220)
  .onEngineStop(() => { if (!didFit) { didFit = true; Graph.zoomToFit(500, 70); } });

Graph.d3Force('charge').strength(-105).distanceMax(540);
Graph.d3Force('link').distance(l => l.kind === 'decomposition' ? 40 : 58).strength(0.5);
Graph.d3VelocityDecay(0.28);

function sizeToWindow(){ Graph.width(window.innerWidth).height(window.innerHeight); }
window.addEventListener('resize', sizeToWindow);
sizeToWindow();

// ---- colour / grouping -------------------------------------------------
function groupKey(n){
  switch ($('#group').value) {
    case 'provenance': return n.provenance || 'unspecified';
    case 'scope':      return n.scope_type || 'unscoped';
    case 'source':     return n.created_by || 'unknown';
    default:           return n.verification_state || 'unknown';
  }
}
function colorOf(n){
  if (n.kind === 'task') return TASK_COLOR;
  if ($('#group').value === 'verification') return VSTATE[n.verification_state] || VSTATE.unknown;
  const k = groupKey(n);
  if (!groupColors.has(k)) groupColors.set(k, GROUP_PALETTE[groupColors.size % GROUP_PALETTE.length]);
  return groupColors.get(k);
}
function linkArrowColor(l){ return VERSION_KIND.has(l.kind) ? '#e0574b' : (l.kind === 'subprocedure' ? '#b98cff' : '#5b8dd6'); }
function linkColor(l){
  const on = hiL.has(l.__id);
  const dim = hoverId && !on;
  if (VERSION_KIND.has(l.kind)) return `rgba(224,87,75,${dim ? 0.08 : (on ? 0.95 : 0.5)})`;
  if (l.kind === 'subprocedure') return `rgba(185,140,255,${dim ? 0.08 : (on ? 0.95 : 0.5)})`;
  return `rgba(122,135,158,${dim ? 0.07 : (on ? 0.85 : 0.34)})`;
}

// ---- node drawing ----------------------------------------------------
function drawNode(n, ctx, scale){
  const dim = hoverId && !hiN.has(n.id);
  const sel = n.id === selectedId;
  const r = n.__r;
  ctx.globalAlpha = dim ? 0.14 : 1;
  if (sel || hoverId === n.id){
    ctx.beginPath(); ctx.arc(n.x, n.y, r + 3.5, 0, 6.2832);
    ctx.fillStyle = 'rgba(255,255,255,0.14)'; ctx.fill();
  }
  ctx.fillStyle = colorOf(n);
  if (n.kind === 'task'){
    // task nodes are squares, to read apart from the round procedures.
    ctx.beginPath(); ctx.rect(n.x - r, n.y - r, r * 2, r * 2); ctx.fill();
  } else {
    ctx.beginPath(); ctx.arc(n.x, n.y, r, 0, 6.2832); ctx.fill();
    if (n.staleness === 'stale'){
      ctx.lineWidth = 2 / scale; ctx.strokeStyle = '#e0574b';
      ctx.beginPath(); ctx.arc(n.x, n.y, r + 1.6 / scale, 0, 6.2832); ctx.stroke();
    }
  }
  const showLabel = sel || hiN.has(n.id) || scale > 1.6;
  if (showLabel){
    const t = (n.name || '').slice(0, 34);
    ctx.font = `${11/scale}px ui-sans-serif, system-ui, sans-serif`;
    const w = ctx.measureText(t).width;
    ctx.globalAlpha = dim ? 0.2 : 0.92;
    ctx.fillStyle = 'rgba(13,15,20,0.72)';
    ctx.fillRect(n.x - w/2 - 2/scale, n.y + r + 1/scale, w + 4/scale, 13/scale);
    ctx.fillStyle = '#d6dbe6';
    ctx.textAlign = 'center'; ctx.textBaseline = 'top';
    ctx.fillText(t, n.x, n.y + r + 2/scale);
  }
  ctx.globalAlpha = 1;
}

// ---- highlight (Obsidian-style spotlight) --------------------------
function recomputeHighlight(){
  hiN = new Set(); hiL = new Set();
  const focus = hoverId || selectedId;
  if (focus){
    hiN.add(focus);
    for (const {id, link} of (ADJ.get(focus) || [])){ hiN.add(id); hiL.add(link.__id); }
  }
}
function select(id){
  selectedId = id;
  recomputeHighlight();
  if (id) openPanel(nodeById.get(id)); else closePanel();
}

// ---- data ----------------------------------------------------------
async function load(){
  const p = new URLSearchParams();
  p.set('limit', $('#limit').value);
  p.set('include_stale', $('#stale').checked ? 'true' : 'false');
  p.set('kinds', $('#tasks').checked ? 'procedure,task' : 'procedure');
  p.set('link_mode', $('#links').value);
  const q = $('#q').value.trim(); if (q) p.set('q', q);
  $('#stats').textContent = 'loading…';
  $('#loading').style.display = 'grid';
  let d;
  try{
    const r = await fetch('./procedure-graph/data?' + p.toString(), {headers:{accept:'application/json'}});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    d = await r.json();
  }catch(e){ $('#stats').textContent = 'error: ' + e.message; $('#loading').style.display='none'; return; }

  RAW = d;
  nodeById = new Map(d.nodes.map(n => [n.id, n]));
  let nodes = d.nodes.map(n => Object.assign({}, n, {
    __r: 2.4 + Math.sqrt(n.degree || 0) * 1.9,
  }));
  let links = d.edges
    .filter(e => nodeById.has(e.source) && nodeById.has(e.target))
    .map(e => Object.assign({}, e, {__id: e.id}));

  if ($('#orphans').checked){
    const deg = new Set();
    links.forEach(l => { deg.add(l.source); deg.add(l.target); });
    nodes = nodes.filter(n => deg.has(n.id));
  }

  ADJ = new Map();
  const push = (a, b, l) => { if (!ADJ.has(a)) ADJ.set(a, []); ADJ.get(a).push({id: b, link: l}); };
  for (const l of links){ push(l.source, l.target, l); push(l.target, l.source, l); }

  groupColors = new Map();
  didFit = false;
  Graph.graphData({nodes, links});
  buildLegend(nodes);

  const c = d.counts || {}, bk = c.by_kind || {}, ek = c.edges_by_kind || {};
  $('#stats').textContent =
    `${nodes.length}/${c.nodes_total ?? nodes.length} nodes ` +
    `(${bk.procedure||0} proc, ${bk.task||0} task) · ${links.length} links ` +
    `(${ek.version||0} ver, ${ek.decomposition||0} dec, ${ek.hierarchy||0} hier, ${ek.subprocedure||0} sub)` +
    (d.truncated ? ' · truncated' : '');
  $('#loading').style.display = 'none';
  if (selectedId && nodeById.has(selectedId)) openPanel(nodeById.get(selectedId));
  else select(null);
}

function buildLegend(nodes){
  const box = $('#legend');
  const mode = $('#group').value;
  let items;
  if (mode === 'verification'){
    const present = new Set(nodes.filter(n => n.kind === 'procedure').map(n => n.verification_state || 'unknown'));
    const order = ['verified','candidate','draft','deprecated','retired','superseded','unknown'];
    items = order.filter(k => present.has(k)).map(k => [k, VSTATE[k]]);
  } else {
    const keys = [...new Set(nodes.filter(n => n.kind === 'procedure').map(groupKey))].slice(0, 12);
    items = keys.map(k => [k, colorOf(pseudoNodeFor(k))]);
  }
  box.innerHTML =
    `<div class="t">nodes</div>` +
    `<div class="row"><i></i>procedure</div>` +
    `<div class="row"><i class="sq" style="background:${TASK_COLOR}"></i>task node</div>` +
    `<div class="row"><i class="ring" style="background:${VSTATE.candidate}"></i>stale procedure</div>` +
    `<div class="t" style="margin-top:6px">${esc(mode === 'verification' ? 'verification state' : $('#group').selectedOptions[0].text.replace('colour: ',''))}</div>` +
    items.map(([k, c]) => `<div class="row"><i style="background:${c}"></i>${esc(k)}</div>`).join('') +
    `<div class="t" style="margin-top:6px">edges</div>` +
    `<div class="row"><span class="e ver"></span>version chain</div>` +
    `<div class="row"><span class="e"></span>decomposition / hierarchy</div>` +
    `<div class="row"><span class="e sub"></span>subprocedure</div>`;
}
function pseudoNodeFor(k){
  const g = $('#group').value;
  if (g === 'provenance') return {kind:'procedure', provenance:k};
  if (g === 'scope') return {kind:'procedure', scope_type:k};
  if (g === 'source') return {kind:'procedure', created_by:k};
  return {kind:'procedure', verification_state:k};
}

// ---- detail panel ------------------------------------------------
function openPanel(n){
  if (!n) return;
  const col = colorOf(n);
  const nbrs = (ADJ.get(n.id) || []).map(({id, link}) => {
    const o = nodeById.get(id);
    return {k: esc(link.relation || link.kind || 'link'), id, text: o ? o.name : id};
  }).sort((a,b) => a.k < b.k ? 1 : -1);
  const nbHtml = nbrs.length ? nbrs.map(x =>
      `<div class="nb" data-id="${esc(x.id)}"><span class="k">${x.k}</span><span>${esc((x.text||'').slice(0,90))}</span></div>`
    ).join('') : '<div style="color:var(--muted)">none in this view</div>';

  if (n.kind === 'task'){
    $('#body').innerHTML =
      `<h2>Task node</h2>
       <div class="stmt">${esc(n.name)}</div>
       ${n.description ? `<div class="goal">${esc(n.description)}</div>` : ''}
       <dl>
         <dt>skill_ref</dt><dd>${n.skill_ref ? `<code>${esc(n.skill_ref)}</code>` : '—'}</dd>
         <dt>provenance</dt><dd>${esc(n.provenance) || '—'}</dd>
         <dt>scope</dt><dd>${esc(n.scope_type) || '—'}${n.scope_entity_id ? ' / ' + esc(n.scope_entity_id) : ''}</dd>
         <dt>created by</dt><dd>${esc(n.created_by) || '—'}</dd>
         <dt>valid from</dt><dd>${esc((n.t_valid||'').slice(0,19).replace('T',' ')) || '—'}</dd>
         <dt>links</dt><dd>${n.degree || 0}</dd>
         <dt>id</dt><dd><code>${esc(n.id)}</code></dd>
       </dl>
       <h3>connected (${nbrs.length})</h3>${nbHtml}`;
  } else {
    $('#body').innerHTML =
      `<h2>Procedure</h2>
       <div class="stmt">${esc(n.name)}</div>
       ${n.goal && n.goal !== n.name ? `<div class="goal">${esc(n.goal)}</div>` : ''}
       <dl>
         <dt>verification</dt><dd><span class="pill" style="background:${col}">${esc(n.verification_state || 'unknown')}</span></dd>
         <dt>staleness</dt><dd>${n.staleness === 'stale'
            ? '<span style="color:#e0574b;font-weight:700">stale</span>' : esc(n.staleness) || '—'}</dd>
         <dt>availability</dt><dd>${esc(n.availability) || '—'}</dd>
         <dt>version</dt><dd>v${esc(n.version)}${n.procedure_id ? ` · <code>${esc(n.procedure_id)}</code>` : ''}</dd>
         <dt>steps</dt><dd>${n.step_count ?? '—'}</dd>
         <dt>provenance</dt><dd>${esc(n.provenance) || '—'}</dd>
         <dt>scope</dt><dd>${esc(n.scope_type) || '—'}${n.scope_entity_id ? ' / ' + esc(n.scope_entity_id) : ''}</dd>
         <dt>created by</dt><dd>${esc(n.created_by) || '—'}</dd>
         <dt>valid from</dt><dd>${esc((n.t_valid||'').slice(0,19).replace('T',' ')) || '—'}</dd>
         <dt>links</dt><dd>${n.degree || 0}</dd>
         <dt>id</dt><dd><code>${esc(n.id)}</code></dd>
       </dl>
       <h3>connected (${nbrs.length})</h3>${nbHtml}`;
  }
  $('#body').querySelectorAll('.nb').forEach(d =>
    d.addEventListener('click', () => { const t = nodeById.get(d.dataset.id); if (t){ select(t.id); Graph.centerAt(t.x, t.y, 500); Graph.zoom(2.2, 500); } }));
  $('#panel').classList.add('open');
}
function closePanel(){ $('#panel').classList.remove('open'); }
$('#panel .close').addEventListener('click', () => select(null));

// ---- controls --------------------------------------------------
$('#refresh').addEventListener('click', load);
['#tasks', '#stale', '#links', '#limit', '#orphans'].forEach(s => $(s).addEventListener('change', load));
$('#group').addEventListener('change', () => { groupColors = new Map(); buildLegend(Graph.graphData().nodes); });
let qt; $('#q').addEventListener('input', () => { clearTimeout(qt); qt = setTimeout(load, 350); });

load();
</script>
</body>
</html>
"""
