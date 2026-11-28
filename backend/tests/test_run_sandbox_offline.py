"""A website run's sandbox (app/execution/run_sandbox.py) and its token and model door, with no network.

FakeAgent37 runs every command the module sends in a local shell, with the sandbox's home mapped to a temp folder, so
the real transport (base64 chunks in, dd slices out, `find -newer` for what a step wrote) and the agent's JSON parsing
run for real. The agent is a stand-in script at the engine's path that writes a file and prints OpenCode's events.
"""
from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from app.execution import run_sandbox as rs
from app.execution import run_token as rt

BASH = shutil.which("bash") or "bash"
pytestmark = pytest.mark.skipif(not shutil.which("bash") or not shutil.which("node"), reason="needs bash and node")


class FakeAgent37(rs.Agent37):
    def __init__(self, root: Path):
        self.root = root.as_posix()
        self.commands: list[str] = []
        self.created = 0
        self.deleted: list[str] = []

    async def create(self, *, run_id: str) -> str:
        self.created += 1
        return "inst-1"

    async def exec(self, iid, command, *, background=False, user=None):
        self.commands.append(command)
        # Git Bash's `test -x` does not see a script's mode bits; an existing engine file counts as installed.
        local = command.replace(rs.HOME, self.root).replace("test -x ", "test -f ")
        # The full path: on Windows a bare "bash" resolves to WSL's launcher in System32 before PATH is searched.
        p = subprocess.run([BASH, "-c", local], capture_output=True, text=True, timeout=120)
        if background:
            return {"pid": 1}
        return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr, "truncated": False}

    async def delete(self, iid):
        self.deleted.append(iid)

    async def aclose(self):
        pass


ENGINE = """#!/bin/bash
# a stand-in for `opencode run`: proves it got the token by env and the config inline, writes a file, answers.
[ "$1" = "--version" ] && { echo 2.2.5; exit 0; }
[ -n "$KELL_RUN_TOKEN" ] || { echo "no token" >&2; exit 3; }
echo "$OPENCODE_CONFIG_CONTENT" > .kell/seen-config.json
echo "$@" > .kell/seen-args.txt
printf '# Literature review\\n\\nThree papers.\\n' > literaturereview.md
printf 'scratch' > notes.txt
echo '{"type":"step_start","part":{"messageID":"m1"}}'
echo '{"type":"tool_use","part":{"messageID":"m1","tool":"write"}}'
echo '{"type":"text","part":{"messageID":"m1","text":"thinking out loud"}}'
echo '{"type":"step_finish","part":{"messageID":"m1","tokens":{"input":100,"output":20}}}'
echo '{"type":"text","part":{"messageID":"m2","text":"I wrote literaturereview.md: three papers, compared."}}'
"""


@pytest.fixture()
def box(tmp_path, monkeypatch):
    monkeypatch.setenv("KELL_RUN_TOKEN_SECRET", "test-secret")
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.example.test")
    monkeypatch.setattr(rs, "POLL_S", 0.01)
    eng = tmp_path / ".kell-engine" / "bin"
    eng.mkdir(parents=True)
    (eng / "opencode").write_text(ENGINE, encoding="utf-8", newline="\n")
    (eng / "opencode").chmod(0o755)
    return FakeAgent37(tmp_path)


def _run():
    return {"id": "11111111-1111-1111-1111-111111111111", "owner_id": "user-1", "mode": "headless", "outputs": []}


def test_an_ai_step_runs_the_agent_in_the_workspace_and_hands_back_its_files(box, tmp_path):
    run = _run()
    step = {"order": 2, "kind": "prompt", "title": "Review", "prompt": "Review the literature.",
            "produces": "literaturereview.md"}
    text, files = asyncio.run(rs.run_ai_step(None, run, step, "The task: diffusion", a37=box))
    assert text == "I wrote literaturereview.md: three papers, compared."     # the last reply, not the thinking
    names = [n for n, _ in files]
    assert names[0] == "literaturereview.md" and "notes.txt" in names            # declared first, then the rest
    assert dict(files)["literaturereview.md"].startswith(b"# Literature review")
    assert (tmp_path / "work" / ".kell" / "earlier.md").read_text(encoding="utf-8") == "The task: diffusion"
    prompt = (tmp_path / "work" / ".kell" / "step-2.md").read_text(encoding="utf-8")
    assert "Review the literature." in prompt and "literaturereview.md" in prompt
    # The message is a pointer to the step file, never the step's text (one argument is capped at 128 KB).
    assert "Read .kell/step-2.md" in (tmp_path / "work" / ".kell" / "seen-args.txt").read_text(encoding="utf-8")
    cfg = json.loads((tmp_path / "work" / ".kell" / "seen-config.json").read_text(encoding="utf-8"))
    kell = cfg["provider"]["kell"]["options"]
    assert kell["baseURL"] == f"https://api.example.test/v1/runs/{run['id']}"
    assert kell["apiKey"] == "{env:KELL_RUN_TOKEN}"                                # the token is never in the config
    assert cfg["tools"]["question"] is False
    assert run["sandbox"]["id"] == "inst-1" and run["sandbox"]["ready"] is True and box.created == 1


def test_the_same_instance_serves_every_step_and_is_deleted_at_the_end(box):
    run = _run()
    for n in (1, 2):
        asyncio.run(rs.run_ai_step(None, run, {"order": n, "kind": "prompt", "title": "s", "prompt": "do"}, "",
                                   a37=box))
    assert box.created == 1
    asyncio.run(rs.close(None, run, box))
    assert box.deleted == ["inst-1"] and run["sandbox"]["deleted"] is True
    asyncio.run(rs.close(None, run, box))                                         # twice is harmless
    assert box.deleted == ["inst-1"]


def test_files_cross_in_both_directions_byte_for_byte_in_chunks(box, tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "WRITE_CHUNK", 1000)
    monkeypatch.setattr(rs, "READ_CHUNK", 700)
    data = bytes(range(256)) * 40                                                 # 10 KB of every byte value
    path = f"{rs.WORK}/sub/blob.bin"
    asyncio.run(rs.write_file(box, "inst-1", path, data))
    assert asyncio.run(rs.read_file(box, "inst-1", path)) == data


def test_a_step_that_writes_nothing_still_answers_and_a_failing_agent_says_why(box, tmp_path):
    (tmp_path / ".kell-engine" / "bin" / "opencode").write_text(
        "#!/bin/bash\n[ \"$1\" = \"--version\" ] && { echo 2.2.5; exit 0; }\necho 'model refused' >&2\nexit 4\n",
        encoding="utf-8", newline="\n")
    with pytest.raises(rs.SandboxError, match="exit 4.*model refused"):
        asyncio.run(rs.run_ai_step(None, _run(), {"order": 1, "kind": "prompt", "title": "s", "prompt": "x"}, "",
                                   a37=box))


def test_a_workspace_name_cannot_leave_the_workspace_or_touch_kells_own_folder():
    assert rs.safe_relpath("out/main.tex") == "out/main.tex"
    assert rs.safe_relpath("../../etc/passwd") == "etc/passwd"          # pinned inside the workspace, not outside it
    for bad in (".kell/step-1.md", ".kell", "", "/"):
        with pytest.raises(rs.SandboxError):
            rs.safe_relpath(bad)


def test_a_script_step_runs_in_the_same_workspace_and_sees_earlier_files(box, tmp_path):
    run = _run()
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "literaturereview.md").write_text("earlier step's file", encoding="utf-8")
    time.sleep(1.1)                                         # `find -newer` has one-second resolution on some disks
    code = "cat literaturereview.md > copy.txt\necho done-$(wc -c < copy.txt)\n"
    exit_code, out, err, files = asyncio.run(rs.run_script_step(None, run, {"order": 3, "kind": "script"}, code,
                                                                "https://x/s.sh", "stdin text", a37=box))
    assert exit_code == 0 and out.strip() == "done-19"
    assert dict(files) == {"copy.txt": b"earlier step's file"}


# ------------------------------------------------------------------------------------------------- the run token

def test_a_run_token_names_its_run_and_cannot_be_forged_or_outlive_its_expiry(monkeypatch):
    monkeypatch.setenv("KELL_RUN_TOKEN_SECRET", "k1")
    tok = rt.mint("run-a", "owner-a", ttl_s=60, now=1000)
    c = rt.verify(tok, now=1010)
    assert (c.run_id, c.owner_id) == ("run-a", "owner-a")
    with pytest.raises(rt.RunTokenInvalid, match="expired"):
        rt.verify(tok, now=2000)
    body, sig = tok[len(rt.PREFIX):].split(".")
    forged = rt.PREFIX + body.replace(body[-3:], "AAA") + "." + sig
    with pytest.raises(rt.RunTokenInvalid):
        rt.verify(forged, now=1010)
    monkeypatch.setenv("KELL_RUN_TOKEN_SECRET", "k2")                            # another server's key
    with pytest.raises(rt.RunTokenInvalid, match="signature"):
        rt.verify(tok, now=1010)


def test_with_no_key_there_are_no_tokens_and_no_sandbox(monkeypatch):
    for v in ("KELL_RUN_TOKEN_SECRET", "ORG_KEY_ENCRYPTION_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("AGENT37_API_KEY", "x")
    monkeypatch.setenv("PUBLIC_API_URL", "https://api.example.test")
    assert not rt.available() and not rs.enabled()
    monkeypatch.setenv("ORG_KEY_ENCRYPTION_KEY", "base")                         # derived key, domain-separated
    assert rt.available() and rs.enabled()
    monkeypatch.setenv("KELL_RUN_SANDBOX", "off")
    assert not rs.enabled()
    assert not rs.wanted_for({"mode": "session"})


# ------------------------------------------------------------------------------------------------ the model door

class _Pool:
    def __init__(self, row):
        self.row = row

    async def fetchrow(self, *a):
        return self.row


class _Req:
    def __init__(self, token):
        self.headers = {"authorization": f"Bearer {token}"}


def test_the_model_door_opens_only_for_its_own_running_run(monkeypatch):
    from app.mcp_server import run_chat_routes as rcr

    monkeypatch.setenv("KELL_RUN_TOKEN_SECRET", "k1")
    tok = rt.mint("11111111-1111-1111-1111-111111111111", "owner-a")
    running = {"owner_id": "owner-a", "status": "running", "next_step": 3}
    ok, why = asyncio.run(rcr.authorise(_Pool(running), _Req(tok), "11111111-1111-1111-1111-111111111111"))
    assert why is None and ok == {"run_id": "11111111-1111-1111-1111-111111111111", "owner_id": "owner-a", "step": 3}
    # Another run's id in the path, a finished run, someone else's run, and a person's session token: all refused.
    assert asyncio.run(rcr.authorise(_Pool(running), _Req(tok), "22222222-2222-2222-2222-222222222222"))[1][0] == 403
    assert asyncio.run(rcr.authorise(_Pool({**running, "status": "done"}), _Req(tok),
                                     "11111111-1111-1111-1111-111111111111"))[1][0] == 403
    assert asyncio.run(rcr.authorise(_Pool({**running, "owner_id": "x"}), _Req(tok),
                                     "11111111-1111-1111-1111-111111111111"))[1][0] == 403
    assert asyncio.run(rcr.authorise(_Pool(running), _Req("eyJhbGciOi.session.jwt"),
                                     "11111111-1111-1111-1111-111111111111"))[1][0] == 401


# ------------------------------------------------------------------------------------------------- the runner

def test_a_website_run_sends_ai_steps_to_the_sandbox_and_keeps_every_file(monkeypatch):
    from app.execution import way_runs as wr

    monkeypatch.setattr(rs, "wanted_for", lambda run: True)

    async def fake_ai(pool, run, step, context, **kw):
        assert "Review the literature." in step["prompt"]
        assert "The files this step reads" not in step["prompt"]      # imports are on disk, not pasted in
        return "done", [("main.tex", b"\\documentclass{article}")]

    async def no_write(*a, **k):
        return None

    monkeypatch.setattr(rs, "run_ai_step", fake_ai)
    monkeypatch.setattr(wr, "_write_supplied", no_write)
    run = {**_run(), "sandbox": {"provider": "agent37", "id": "inst-1", "ready": True},
           "outputs": [{"order": 1, "title": "lit", "actor": "ai", "output": "body", "files": [{"name": "lit.md"}]}]}
    step = {"order": 2, "kind": "prompt", "title": "TeX", "prompt": "Review the literature.", "imports": ["lit.md"],
            "produces": "main.tex"}
    out = asyncio.run(wr._run_by_kell(step, run))
    assert out.startswith("done") and "main.tex" in out
    assert run["_captured"] == [("main.tex", b"\\documentclass{article}")]


def test_a_run_that_cannot_get_a_sandbox_runs_in_process_and_says_so(monkeypatch):
    """Agent37 with no balance answers 402 (measured 2026-11-28): the run must go on, not fail at step 1."""
    from app.execution import way_runs as wr

    monkeypatch.setattr(rs, "wanted_for", lambda run: True)

    class Broke(rs.Agent37):
        def __init__(self):
            pass

        async def create(self, *, run_id):
            raise rs.SandboxError("the run's sandbox could not be created (402): insufficient_balance")

        async def aclose(self):
            pass

    monkeypatch.setattr(rs, "Agent37", Broke)
    run = _run()
    step = {"order": 1, "kind": "prompt", "title": "s", "prompt": "do it"}
    assert asyncio.run(wr._in_sandbox(step, run, None)) is None            # the caller runs it in-process
    assert "402" in run["sandbox"]["unavailable"]
    assert asyncio.run(wr._in_sandbox(step, run, None)) is None            # and never tries again in this run
    v = wr.view({**run, "way_id": "w", "status": "running", "next_step": 1, "error": "", "waiting": None,
                 "plan": [{**step, "model": None, "strict": False}]})
    assert v["sandbox"]["state"] == "unavailable"


def test_tool_steps_and_session_runs_never_touch_the_sandbox(monkeypatch):
    from app.execution import way_runs as wr

    monkeypatch.setattr(rs, "wanted_for", lambda run: run.get("mode") == "headless")
    assert asyncio.run(wr._in_sandbox({"order": 1, "kind": "tool", "title": "t"}, _run(), None)) is None
    assert asyncio.run(wr._in_sandbox({"order": 1, "kind": "prompt", "prompt": "x", "title": "t"},
                                      {**_run(), "mode": "session"}, None)) is None
