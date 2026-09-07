"""
Gate 2B REAL REHEARSAL -- the full chain, run for real, against the real
running global server:

    CAPTURED CANONICAL CANDIDATE PROCEDURE (mcp-lazy-tool-schema-loading,
    real Postgres row via scripts/capture_mcp_lazy_tool_schema_loading.py,
    verification_state='candidate')
      -> MCP (real wire, search_procedures with require_verified=False)
      -> LOCAL AGENT (LocalAgentRunner, allow_unverified=True)
      -> LOCAL EXECUTION GRAPH -> DISPOSABLE REPO (real Agent+RepoSandbox,
         REAL billed LLM calls)
      -> COMPOSED SUCCESS GATE: artifact validation + the procedure's own
         behavioral contract (capability kind lazy_tool_schema) -- the
         behavioral verifier, not the agent's stop_reason, decides success
      -> OUTCOME -> GLOBAL EVIDENCE (report_execution)

Hand-run, not part of pytest (registered in
test_live_scripts_not_collected.py, same as test_full_chain_live.py).
Requires the real global server already running:
    uvicorn app.mcp_server.server:app --host 127.0.0.1 --port 8765
"""
import asyncio
import json
import os
import tempfile

from dotenv import load_dotenv

load_dotenv()

from app.local_agent.runner import LocalAgentRunner

SERVER_URL = os.environ.get("STEALTHLAB_GLOBAL_SERVER_URL", "http://127.0.0.1:8765/mcp")
TOKEN = os.environ["STEALTHLAB_MCP_TOKEN"]

REPO = os.path.join(tempfile.gettempdir(), "gate2b_lazy_schema_rehearsal")

TASK = (
    "Make the MCP tool server in tool_server.py lazy-load tool schemas: "
    "the default list_tools() response must return lightweight metadata "
    "only (tool name and description, no full JSON schema per tool), a "
    "get_tool_schema(name) function must return the tool's full JSON "
    "schema on demand, and ordinary call_tool(name, args) behavior must "
    "not change."
)

# The disposable repo's REAL starting state: a tool server that embeds the
# full JSON schema for every tool in the default listing -- the exact
# problem the captured procedure describes. No get_tool_schema exists yet.
LEAKY_SOURCE = '''"""MCP-style tool server (rehearsal starting state)."""

TOOLS = {
    "echo": {
        "description": "Echo the given text back.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
}


def list_tools():
    """Default listing -- currently embeds every tool's FULL schema."""
    return [
        {"name": name, "description": meta["description"],
         "input_schema": meta["input_schema"]}
        for name, meta in TOOLS.items()
    ]


def call_tool(name, args):
    if name == "echo":
        return args["text"]
    raise KeyError(f"unknown tool: {name}")
'''


def seed_repo(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "tool_server.py"), "w", encoding="utf-8") as f:
        f.write(LEAKY_SOURCE)


async def main() -> None:
    print("=== STAGE 1: seed the disposable repo (leaky default listing) ===")
    seed_repo(REPO)
    print("repo:", REPO)
    print("before:", open(os.path.join(REPO, "tool_server.py")).read()[:200], "...")

    print("\n=== STAGE 2: real LocalAgentRunner run (allow_unverified=True) ===")
    runner = LocalAgentRunner(
        server_url=SERVER_URL, token=TOKEN,
        model=os.environ.get("GATE2B_REHEARSAL_MODEL", "gemma-4-31B-it"),
        max_steps=int(os.environ.get("GATE2B_REHEARSAL_MAX_STEPS", "40")),
    )
    result = await runner.run(task_description=TASK, repo_path=REPO,
                              allow_unverified=True)

    matched_name = result.matched_procedure["name"] if result.matched_procedure else None
    print("matched_procedure:", matched_name, "(source:", result.source, ")")
    print("graph_outcome:", result.graph_outcome)
    print("files_edited:", result.files_edited)
    print("\nnode notes:")
    for note in result.node_notes:
        print("  -", note)

    print("\n=== STAGE 3: the BEHAVIORAL VERIFIER, run independently ===")
    # Independent confirmation of what the gate decided: run the exact
    # checks the lazy_tool_schema verifier runs, straight against the repo.
    import subprocess
    import sys

    check = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import tool_server
listing = tool_server.list_tools()
leak = [e for e in listing if any(k in e for k in
        ("input_schema", "inputSchema", "parameters", "input_schema_json"))]
assert not leak, f"default listing still leaks schema keys: {leak}"
schema = tool_server.get_tool_schema("echo")
assert schema == {"type": "object",
                  "properties": {"text": {"type": "string"}},
                  "required": ["text"]}, f"wrong schema: {schema}"
out = tool_server.call_tool("echo", {"text": "hello"})
assert out == "hello", f"call_tool broken: {out!r}"
print("BEHAVIORAL CHECK: PASS")
"""
    proc = subprocess.run(
        [sys.executable, "-c", check, REPO],
        capture_output=True, text=True, timeout=60,
    )
    print(proc.stdout.strip())
    if proc.returncode != 0:
        print(proc.stderr.strip())
    print("independent check exit:", proc.returncode)

    print("\n=== VERDICT ===")
    if (result.graph_outcome == "success" and proc.returncode == 0
            and not any("VALIDATION FAILED" in n for n in result.node_notes)):
        print("REHEARSAL PASS: real agent execution accepted by the "
              "composed artifact+behavioral gate, independently confirmed.")
    else:
        print("REHEARSAL outcome recorded above -- the gate's decision "
              "(including any downgrade to failure) is the honest result; "
              "report_execution has already recorded it either way.")


if __name__ == "__main__":
    asyncio.run(main())
