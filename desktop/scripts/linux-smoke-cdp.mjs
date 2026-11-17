// Drives the running app over the DevTools protocol for scripts/linux-smoke.sh: the page renders, and the routes behind Chats,
// documents, schedules, the inbox, connectors and quick ask answer. Prints PASS/FAIL lines. Arg: a .docx to preview.
const doc = process.argv[2]
const targets = await (await fetch("http://127.0.0.1:9333/json")).json()
const t = targets.find((x) => x.type === "page" && /^http:\/\/127\.0\.0\.1:\d+\/$/.test(x.url))
if (!t) { console.log("FAIL  no app page over DevTools"); process.exit(0) }
const ws = new WebSocket(t.webSocketDebuggerUrl)
let id = 0
const pending = new Map()
ws.onmessage = (m) => { const j = JSON.parse(m.data); if (j.id && pending.has(j.id)) { pending.get(j.id)(j); pending.delete(j.id) } }
await new Promise((r) => (ws.onopen = r))
const ev = (expression) => new Promise((r) => { const i = ++id; pending.set(i, (j) => r(j.result?.result?.value)); ws.send(JSON.stringify({ id: i, method: "Runtime.evaluate", params: { expression, awaitPromise: true, returnByValue: true } })) })
const check = async (name, expr) => { let v; try { v = await ev(expr) } catch (e) { v = `error ${e}` } console.log(v === true ? `PASS  ${name}` : `FAIL  ${name}: ${JSON.stringify(v)}`) }

await new Promise((r) => setTimeout(r, 4000))
await check("page renders the app", `!!document.querySelector('.dk-foot') && document.body.innerText.length > 50`)
await check("Chats folder (no project) is made", `(async()=>{const j=await (await fetch('/api/home')).json(); return !!j.path && /Documents\\/kell$|kell$/.test(j.path)})()`)
await check("document preview renders HTML", `(async()=>{const r=await fetch('/api/docs/preview?path='+encodeURIComponent(${JSON.stringify(doc)})); const t=await r.text(); return r.ok && /Revenue/.test(t)})()`)
await check("document versions route", `(async()=>{const j=await (await fetch('/api/docs/versions?path='+encodeURIComponent(${JSON.stringify(doc)}))).json(); return Array.isArray(j.versions)})()`)
await check("scheduled tasks: create, list, delete", `(async()=>{const p=(b)=>fetch('/api/schedules',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json()); const s=(await p({action:'save',prompt:'test',repeat:'daily',time:'09:00'})).schedule; const l=(await (await fetch('/api/schedules')).json()).schedules; await p({action:'delete',id:s.id}); return !!s.nextAt && l.some(x=>x.id===s.id)})()`)
await check("inbox answers", `(async()=>{const j=await (await fetch('/api/inbox')).json(); return Array.isArray(j.items)})()`)
await check("connectors catalog", `(async()=>{const j=await (await fetch('/api/connectors')).json(); return j.catalog.length>=10})()`)
await check("quick-ask page", `(async()=>{const r=await fetch('/quick'); return r.ok && /Ask kell/.test(await r.text())})()`)
await check("chats store answers", `(async()=>{const j=await (await fetch('/api/local/sessions')).json(); return Array.isArray(j.sessions)})()`)
await check("Scheduled / inbox / connectors buttons are on screen", `['Scheduled tasks','Connectors'].every(l=>!!document.querySelector('button[aria-label="'+l+'"]')) && !!document.querySelector('.dk-badged')`)
ws.close()
process.exit(0)
