"""Multi-turn sessions for the long-horizon test: a vague opener, a simulated user, `claude --resume` turns.

A real developer does not paste the issue. They type a short, vague message, the assistant asks something or makes a
first try, and they answer or add a detail. This module drives that, the same way for every arm:

  opener      glm-5.3-flash rewrites the issue as the first message a busy developer would type. Written ONCE per task
              (`<cache>/openers/<task>.json` + `.txt`), so A, C and C0 all get the SAME opener. Rubric: it keeps the ONE
              fact needed to find the problem and drops the reproduction steps; `check_opener` tests what can be tested
              without a model (length, no code block, no traceback, no pasted run of the issue's own words).
  sim user    glm-5.3-flash, holding the full issue, reads each assistant turn and CLASSIFIES it: it asked a question ->
              answer it from the issue (never the tests or the fix); it says it is done -> the user says thanks and the
              session ends; otherwise -> send the next follow-up. The follow-up TEXT is never the model's: it is the next
              item of the list written together with the opener, so a model slip cannot change what the arms are told.
              Cached per (task, arm, turn) in `<cache>/simuser/`; a cached reply is used only while the assistant's text
              is the one it answered (its hash is stored), otherwise it is regenerated and flagged.
  turns       the first turn is `claude -p <opener>`; every later turn `claude -p <reply> --resume <session_id>`, so the
              hooks see the earlier turns in `transcript_path` (what lib/brief.mjs and lib/continuity.mjs read). At most
              MAX_TURNS user turns, the opener included.

`run_session` is the loop; it knows nothing about Claude or the arms: the runner passes `run_turn` (one `claude -p`) and
`observe` (what the runner measures after each turn). What it returns is one dict in the shape of `runner.run_claude`'s,
with the costs and times summed over the turns, plus `turn_log` (one record per turn) and `questions_asked`.

Honest limits: the simulated user is a model, so it is one more source of variation (cached, so reruns repeat it); "thanks"
is recorded as the end of the session but is NOT sent (it would only cost a turn); the session that was cut by a usage limit
is not resumed, the task is run again from its first turn.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
MAX_TURNS = 4                       # user turns in a session, the opener included
SEED = 20261014                     # written into the prompts: the model is asked at temperature 0 and the result cached
LLM_PROFILE = "glm-5.3-flash"       # a profile of exec.json (base url, model id, key variable name, extras)
OPENER_WORDS = (3, 60)
FORBIDDEN_IN_OPENER = ("steps to reproduce", "to reproduce", "reproduction", "expected behavior", "actual behavior",
                       "expected behaviour", "actual behaviour", "traceback")
MAX_SHARED_RUN = 10                 # words in a row an opener may copy from the issue

Llm = Callable[[str, str], str]     # (system, user) -> the model's text


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def one_line(text: object) -> str:
    """One line: on Windows `claude` is a .cmd shim and a newline in an argument cuts the command line there."""
    return " ".join(str(text or "").split())


def safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name)


# ---------------------------------------------------------------- the model behind the opener and the user

def zai_llm(key: str, *, profile: str = LLM_PROFILE, config: Path = HERE / "exec.json", retries: int = 3,
            timeout: int = 120, max_tokens: int = 700) -> Llm:
    """An `Llm` over the OpenAI-style chat endpoint of an exec.json profile. The key is passed in (the runner reads it
    from backend/.env by name); it is only ever sent as the Authorization header, never logged or put in an error."""
    spec = json.loads(config.read_text(encoding="utf-8"))["profiles"][profile]
    url = spec["openai_base_url"].rstrip("/") + "/chat/completions"

    def call(system: str, user: str) -> str:
        if spec.get("no_system_role"):
            messages = [{"role": "user", "content": f"{system}\n\n{user}"}]
        else:
            messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        body = json.dumps({"model": spec["model_id"], "messages": messages, "temperature": 0, "max_tokens": max_tokens,
                           **(spec.get("request_extras") or {})}).encode()
        last = ""
        for attempt in range(retries):
            req = urllib.request.Request(url, data=body, method="POST", headers={
                "content-type": "application/json", "authorization": f"Bearer {key}"})
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = json.loads(r.read().decode("utf-8"))
                return str((data.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
            except urllib.error.HTTPError as e:
                last = f"HTTP {e.code}"
                if e.code not in (429, 500, 502, 503, 504):
                    break
            except Exception as e:  # noqa: BLE001 -- timeouts, resets: retried
                last = type(e).__name__
            time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"{profile} call failed ({last})")

    return call


def parse_json(text: str) -> dict | None:
    """The first JSON object in a model's reply (it may wrap it in a code fence or add a sentence), or None."""
    s = str(text or "")
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        obj = json.loads(s[a:b + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


# ---------------------------------------------------------------- the opener

OPENER_SYSTEM = (
    "You rewrite a GitHub issue as the FIRST message a busy developer would type to a coding assistant, then list what "
    "the same developer would add if the assistant has not found the problem. Rules for the opener: one or two short "
    "sentences (under 40 words); no file names unless the issue itself names them; it may say 'the parser' or 'that "
    "error' instead of a name; it keeps the ONE fact needed to find the problem; it drops the reproduction steps, code, "
    "tracebacks and the expected/actual layout; it never mentions tests or how to fix it; it is not a copy of the "
    "issue's sentences. Rules for the follow-ups: up to 3 messages of under 25 words, each adding ONE detail from the "
    "issue, in the order a person would think of them; never a patch, never the tests. Reply with JSON only: "
    '{"opener": "...", "must_keep": "the one fact the opener keeps", "followups": ["...", "..."]}.')


def max_shared_run(a: str, b: str) -> int:
    """The longest run of words (case-insensitive) that appears in both texts."""
    wa, wb = re.findall(r"\w+", a.lower()), re.findall(r"\w+", b.lower())
    if not wa or not wb:
        return 0
    prev, best = [0] * (len(wb) + 1), 0
    for x in wa:
        cur = [0] * (len(wb) + 1)
        for j, y in enumerate(wb, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def check_opener(opener: str, issue: str) -> list[str]:
    """What the rubric lets us test without a model; an empty list means the opener passes."""
    problems = []
    words = len(opener.split())
    if not OPENER_WORDS[0] <= words <= OPENER_WORDS[1]:
        problems.append(f"{words} words (want {OPENER_WORDS[0]}-{OPENER_WORDS[1]})")
    if "```" in opener or "\n" in opener.strip():
        problems.append("a code block or several lines")
    low = opener.lower()
    problems += [f"contains {w!r}" for w in FORBIDDEN_IN_OPENER if w in low]
    if max_shared_run(opener, issue) > MAX_SHARED_RUN:
        problems.append(f"copies {max_shared_run(opener, issue)} words in a row from the issue")
    return problems


def make_opener(task_id: str, issue: str, llm: Llm, cache_dir: Path, *, tries: int = 2) -> dict:
    """The task's opener, follow-ups and rubric: from the cache when it is there, else written by the model and cached.
    Cached per task, never per arm, so every arm is told the same thing."""
    path = cache_dir / f"{safe(task_id)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    note, last = "", None
    for _ in range(tries):
        reply = llm(OPENER_SYSTEM, f"Variation seed: {SEED}\n{note}\nThe issue:\n{issue}")
        obj = parse_json(reply) or {}
        opener = one_line(obj.get("opener"))
        followups = [one_line(x) for x in (obj.get("followups") or []) if one_line(x)][: MAX_TURNS - 1]
        problems = check_opener(opener, issue) if opener else ["no opener in the reply"]
        last = {"task_id": task_id, "opener": opener, "followups": followups, "must_keep": one_line(obj.get("must_keep")),
                "rubric": {"keep": "the one fact needed to find the problem", "drop": "the reproduction steps"},
                "checks": {"ok": not problems, "problems": problems},
                "issue_sha": hashlib.sha256(issue.encode("utf-8")).hexdigest()[:16], "seed": SEED, "created_at": now_iso()}
        if not problems:
            break
        note = "Your last opener was rejected: " + "; ".join(problems) + ". Write a shorter, vaguer one."
    if not (last and last["opener"]):
        raise RuntimeError(f"no opener could be made for {task_id}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{safe(task_id)}.txt").write_text(last["opener"] + "\n", encoding="utf-8")
    path.write_text(json.dumps(last, indent=1), encoding="utf-8")
    return last


# ---------------------------------------------------------------- the simulated user

SIM_SYSTEM = (
    "You are the developer who wrote this issue, talking to a coding assistant in a chat. You know the whole issue; you "
    "have NEVER seen the tests or the fix and you do not write code. Read the assistant's last message and reply with "
    'JSON only: {"action": "answer" | "done" | "followup", "text": "..."}. Use "answer" when the assistant asked you '
    "something: put your answer in text, from the issue only, in under 40 words, one line; if the issue does not say, "
    "say you do not know. Use \"done\" when the assistant says it has finished or fixed it (text empty). Otherwise use "
    '"followup" (text empty; the next follow-up is added for you).')


class SimUser:
    """One task in one arm. `reply(turn, assistant_text, sent)` -> {"action", "text", "cached", "regenerated", ...}."""

    def __init__(self, task_id: str, arm: str, issue: str, followups: list[str], llm: Llm, cache_dir: Path):
        self.task_id, self.arm, self.issue, self.followups, self.llm, self.cache_dir = task_id, arm, issue, followups, llm, cache_dir

    def _path(self, turn: int) -> Path:
        return self.cache_dir / f"{safe(self.task_id)}.{safe(self.arm)}.{turn}.json"

    def classify(self, assistant_text: str) -> dict:
        reply = self.llm(SIM_SYSTEM, f"The issue:\n{self.issue}\n\nThe assistant's last message:\n{assistant_text[:4000]}")
        obj = parse_json(reply)
        action = str((obj or {}).get("action") or "").strip().lower()
        if action not in ("answer", "done", "followup"):
            # an unreadable reply: a message that ends in a question is answered vaguely, anything else ends the session
            action = "answer" if assistant_text.rstrip().endswith("?") else "done"
            return {"action": action, "text": one_line((obj or {}).get("text")), "parse_failed": True}
        return {"action": action, "text": one_line(obj.get("text"))}

    def reply(self, turn: int, assistant_text: str, sent: int) -> dict:
        sha = hashlib.sha256(assistant_text.encode("utf-8")).hexdigest()[:16]
        path = self._path(turn)
        cached = regenerated = False
        decision = None
        if path.exists():
            old = json.loads(path.read_text(encoding="utf-8"))
            if old.get("assistant_sha") == sha:
                decision, cached = old["decision"], True
            else:
                regenerated = True            # the assistant said something else than last time: the old reply fits nothing
        if decision is None:
            decision = self.classify(assistant_text)
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"assistant_sha": sha, "decision": decision}, indent=1), encoding="utf-8")
        action, text = decision["action"], decision.get("text", "")
        if action == "followup":
            if sent < len(self.followups):
                text = self.followups[sent]       # the script's words, never the model's
            else:
                action = "done"
        if action == "answer" and not text:
            text = "I don't have more detail than what I wrote."
        if action == "done":
            text = "thanks"
        return {"action": action, "text": one_line(text)[:400], "cached": cached, "regenerated": regenerated,
                **({"parse_failed": True} if decision.get("parse_failed") else {})}


# ---------------------------------------------------------------- what a turn changed (never touches the real index)

def tree_snapshot(ws: Path) -> str | None:
    """A git tree id of the workspace as it is now (tracked and new files, not .stealth/ or .claude/), made with a private
    index so the workspace's own index, HEAD and commits are untouched. None if git cannot do it: it is only a record."""
    index = ws / ".git" / f"abtest-snap-{os.getpid()}.index"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    try:
        for args in (["read-tree", "HEAD"], ["add", "-A", "--", ".", ":(exclude).stealth", ":(exclude).claude"]):
            subprocess.run(["git", "-C", str(ws), *args], env=env, check=True, capture_output=True)
        return subprocess.run(["git", "-C", str(ws), "write-tree"], env=env, check=True, capture_output=True,
                              text=True).stdout.strip() or None
    except (subprocess.CalledProcessError, OSError):
        return None
    finally:
        try:
            index.unlink()
        except OSError:
            pass


def tree_diff(ws: Path, before: str | None, after: str | None, limit: int = 20000) -> str:
    """The unified diff between two snapshots: what ONE turn changed (empty when it changed nothing or git failed)."""
    if not before or not after or before == after:
        return ""
    try:
        out = subprocess.run(["git", "-C", str(ws), "diff", "--no-color", before, after], check=True, capture_output=True,
                             text=True, encoding="utf-8", errors="replace").stdout
    except (subprocess.CalledProcessError, OSError):
        return ""
    return out if len(out) <= limit else out[:limit] + "\n[diff cut]"


DONE_BY = re.compile(r"Done by (\S+) in (\d+) s")


def shown_message(log_path: Path) -> str | None:
    """The message StealthLab showed the user when a cheap model did the turn ("Done by <model> in N s ..."), found in
    the turn's stream-json log: the hook's output reaches the log inside some message field, so every string in every
    line is searched. None when the turn was not dispatched."""
    if not log_path.exists():
        return None
    found: list[str] = []

    def walk(x: object) -> None:
        if isinstance(x, str):
            if "Done by " in x and "StealthLab dispatched it" in x:
                found.append(x)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            walk(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not found:
        return None
    text = found[0]
    try:                                    # a hook's stdout is JSON ({"decision": "block", "reason": ...}): keep the reason
        reason = json.loads(text).get("reason")
        if isinstance(reason, str) and "Done by " in reason:
            text = reason
    except (json.JSONDecodeError, AttributeError):
        pass
    i = text.find("Done by ")
    return text[i:i + 4000]


# ---------------------------------------------------------------- the session loop

def merge_outs(outs: list[dict]) -> dict:
    """One `run_claude`-shaped dict for the whole session: the first turn that did not finish decides the status; costs,
    times and turn counts are summed; models are the union; `final` is the last turn's."""
    bad = next((o for o in outs if o.get("status") != "done"), None)
    if bad:
        return {k: v for k, v in bad.items() if k in ("status", "error")}
    usage: dict[str, dict] = {}
    for o in outs:
        for model, u in (o.get("model_usage") or {}).items():
            tot = usage.setdefault(model, {})
            for k, v in u.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    tot[k] = tot.get(k, 0) + v
    costs = [o["claude_cost_usd"] for o in outs if o.get("claude_cost_usd") is not None]
    return {"status": "done", "is_error": any(o.get("is_error") for o in outs),
            "claude_cost_usd": round(sum(costs), 6) if costs else None,
            "turns": sum(int(o.get("turns") or 0) for o in outs), "duration_ms": sum(int(o.get("duration_ms") or 0) for o in outs),
            "models": sorted({m for o in outs for m in o.get("models") or []}), "model_usage": usage or None,
            "final": outs[-1].get("final", "")}


def run_session(*, task_id: str, arm: str, issue: str, rule: str, max_turns: int, llm: Llm, openers_dir: Path,
                simuser_dir: Path, run_turn: Callable[[str, str | None, int], dict],
                observe: Callable[[int, str, str, dict], dict] | None = None) -> dict:
    """Drive one task as a conversation of at most `max_turns` user turns.

    run_turn(prompt, resume_session_id_or_None, turn_number) -> the dict of one `claude -p` (status, claude_cost_usd,
        session_id, text, final ...); `rule` is appended to the first prompt only.
    observe(turn_number, started_iso, ended_iso, out) -> extra fields for that turn's record (the runner adds the open
        models' cost, the shown message, the diff).
    """
    if not 1 <= max_turns <= MAX_TURNS:
        raise ValueError(f"max_turns must be 1-{MAX_TURNS}")
    op = make_opener(task_id, issue, llm, openers_dir)
    user = SimUser(task_id, arm, issue, op["followups"], llm, simuser_dir)
    text, kind, resume = op["opener"], "opener", None
    sent = asked = 0
    outs: list[dict] = []
    log: list[dict] = []
    ended_by = "turn_limit"
    for n in range(1, max_turns + 1):
        started, t0 = now_iso(), time.time()
        out = run_turn(text + (rule if n == 1 else ""), resume, n)
        secs = round(time.time() - t0, 1)
        outs.append(out)
        entry = {"turn": n, "user_kind": kind, "user_text": text, "seconds": secs, "status": out.get("status"),
                 "claude_cost_usd": out.get("claude_cost_usd"), "session_id": out.get("session_id"),
                 "final": out.get("final")}
        if observe:
            entry.update(observe(n, started, now_iso(), out))
        log.append(entry)
        if out.get("status") != "done":
            ended_by = str(out.get("status"))
            break
        resume = out.get("session_id") or resume
        if not resume:
            outs[-1] = {"status": "infra", "error": "the result carried no session_id, so the next turn cannot be resumed"}
            ended_by = "infra"
            break
        if n == max_turns:
            break
        seen = entry.get("shown") or out.get("text") or out.get("final") or ""
        decision = user.reply(n, seen, sent)
        entry["sim"] = {k: decision[k] for k in decision if k != "text"}
        if decision["action"] == "done":
            entry["sim"]["closing"] = decision["text"]       # "thanks": the end of the session, not sent
            ended_by = "thanks"
            break
        if decision["action"] == "answer":
            asked += 1
            kind = "answer"
        else:
            sent += 1
            kind = "followup"
        text = decision["text"]
    return {**merge_outs(outs), "opener": op["opener"], "opener_ok": op["checks"]["ok"], "user_turns": len(log),
            "questions_asked": asked, "ended_by": ended_by, "turn_log": log}
