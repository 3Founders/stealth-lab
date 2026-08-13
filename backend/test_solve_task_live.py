"""
Real test of solve_task's wiring: RepoSandbox file operations are 100% real
(no stub). Only the LLM call itself is scripted -- this sandbox has no
network path to General Compute's real API (same class of limitation as
Voyage earlier), so a canned response stands in, CLEARLY labeled. Anuj
needs to run this same tool against a real repo + real API key to verify
the actual model's tool-calling behavior; this test verifies the plumbing
around it.
"""
import asyncio
import json
import os
import sys
import types

os.environ["DATABASE_URL"] = "postgresql://postgres:stealthlab@localhost:5432/stealthlab_local"
sys.path.insert(0, os.path.dirname(__file__))

import app.mcp_server.server as srv


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = types.SimpleNamespace(name=name, arguments=json.dumps(arguments))


class _FakeMessage:
    def __init__(self, tool_calls):
        self.content = ""
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeUsage:
    prompt_tokens = 100
    completion_tokens = 20


class _FakeResponse:
    def __init__(self, tool_calls):
        self.choices = [_FakeChoice(_FakeMessage(tool_calls))]
        self.usage = _FakeUsage()


class _ScriptedClient:
    """Stands in for the real OpenAI-compatible client -- scripts exactly
    two turns: a real edit_file call fixing the seeded bug, then finish."""

    def __init__(self):
        self._step = 0
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, model, messages, tools, temperature, max_tokens, timeout):
        self._step += 1
        if self._step == 1:
            return _FakeResponse([_FakeToolCall(
                "call_1", "edit_file",
                {"path": "calc.py", "old_str": "return a - b  # bug: should be +",
                 "new_str": "return a + b"},
            )])
        return _FakeResponse([_FakeToolCall("call_2", "finish", {"summary": "Fixed the bug"})])


class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool):
        self.request_context = FakeRequestContext(pool)


async def main():
    # Patch OpenAI construction inside server.py to return our scripted
    # client -- everything else (RepoSandbox, Agent.run's real loop,
    # _dispatch, diff production) runs for real, unmodified.
    srv.OpenAI = lambda **kwargs: _ScriptedClient()

    # HONEST STUB (same real, confirmed wall as apply_change_set's test):
    # this sandbox cannot reach api.voyageai.com. Retrieval itself is not
    # verified by this script -- only solve_task's downstream wiring
    # (RepoSandbox + Agent.run + diff) is. Anuj: this same stub is not
    # needed on real infra with real network access.
    async def _fake_embed_one(self, text, input_type="query"):
        return [0.0] * 1024
    import app.services.embeddings as emb
    emb.Embedder.embed_one = _fake_embed_one

    from app.db.session import create_pool
    pool = await create_pool(os.environ["DATABASE_URL"])
    ctx = FakeContext(pool)

    result = await srv.solve_task(
        task_description="Fix the bug in calc.add -- it subtracts instead of adding.",
        repo_path="/tmp/test_repo",
        ctx=ctx,
    )
    print(result)

    assert "stop_reason: finish" in result, "FAIL: agent did not reach finish"
    assert "calc.py" in result, "FAIL: expected calc.py in files_edited"
    real_content = open("/tmp/test_repo/calc.py").read()
    print("\n--- real file content on disk after solve_task ---")
    print(real_content)
    assert "return a + b" in real_content, "FAIL: real file on disk was not actually fixed"
    assert "a - b" not in real_content, "FAIL: buggy line still present"

    await pool.close()
    print("\nPASS: real RepoSandbox edit landed on disk, real diff produced, "
          "real Agent.run loop completed via finish -- only the LLM response "
          "itself was scripted (network-blocked in this sandbox).")


if __name__ == "__main__":
    asyncio.run(main())
