"""Print a readable outline of one session transcript: python peek.py <run> [task.arm]"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

logs = Path(__file__).resolve().parent / ".local" / "logs" / sys.argv[1]
files = sorted(logs.glob(f"{sys.argv[2]}*.jsonl" if len(sys.argv) > 2 else "*.jsonl"))
for f in files:
    print(f"== {f.name}")
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            m = json.loads(line)
        except json.JSONDecodeError:
            print("  raw", line[:200])
            continue
        t, sub = m.get("type"), "  " if m.get("parent_tool_use_id") else ""
        if t == "system" and m.get("subtype") == "init":
            print("  init model:", m.get("model"), "| mcp:", m.get("mcp_servers"),
                  "| agents:", m.get("agents"))
        elif t == "system":
            print("  system", m.get("subtype"), str(m.get("hook_name") or "")[:60])
        elif t == "assistant":
            for c in m["message"].get("content", []):
                if c.get("type") == "tool_use":
                    print(f"  {sub}tool_use [{m['message'].get('model')}]", c["name"], json.dumps(c["input"])[:220])
                elif c.get("type") == "text":
                    print(f"  {sub}text", c["text"][:260].replace("\n", " "))
        elif t == "user" and isinstance(m["message"].get("content"), list):
            for c in m["message"]["content"]:
                if c.get("type") == "tool_result":
                    print(f"  {sub}  -> {'ERROR ' if c.get('is_error') else ''}", str(c.get("content"))[:220].replace("\n", " "))
        elif t == "result":
            print("  RESULT", m.get("subtype"), "cost", m.get("total_cost_usd"), "turns", m.get("num_turns"),
                  list((m.get("modelUsage") or {}).keys()))
