// A deliberately weak "model" for the escalation check: an OpenAI-compatible endpoint that never fixes anything. Asked with tools
// available, it answers every turn with one tool call, `python -m pytest -q`; asked without tools (titles), it says "Test".
// usage: node weak_provider.mjs <port>      (provider config: baseURL http://127.0.0.1:<port>/v1, model "weak-1", any key)
import http from "node:http"
const port = Number(process.argv[2] || 8799)
let n = 0
const sse = (res, chunks) => { res.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-cache" }); for (const c of chunks) res.write(`data: ${JSON.stringify(c)}\n\n`); res.end("data: [DONE]\n\n") }
http.createServer((req, res) => {
  let body = ""
  req.on("data", (d) => (body += d))
  req.on("end", () => {
    let j = {}; try { j = JSON.parse(body) } catch { /* not JSON */ }
    if (!/chat\/completions/.test(req.url)) { res.writeHead(404).end(); return }
    const base = { id: `weak-${++n}`, object: "chat.completion.chunk", created: 0, model: "weak-1" }
    const usage = { prompt_tokens: 10, completion_tokens: 5, total_tokens: 15 }
    const tool = Array.isArray(j.tools) && j.tools.some((t) => t?.function?.name === "shell")
    if (!tool) return sse(res, [{ ...base, choices: [{ index: 0, delta: { role: "assistant", content: "Test" }, finish_reason: null }] }, { ...base, choices: [{ index: 0, delta: {}, finish_reason: "stop" }], usage }])
    sse(res, [
      { ...base, choices: [{ index: 0, delta: { role: "assistant", tool_calls: [{ index: 0, id: `call_weak_${n}`, type: "function", function: { name: "shell", arguments: JSON.stringify({ command: "python -m pytest -q" }) } }] }, finish_reason: null }] },
      { ...base, choices: [{ index: 0, delta: {}, finish_reason: "tool_calls" }], usage },
    ])
  })
}).listen(port, "127.0.0.1", () => console.log(`weak provider on ${port}`))
