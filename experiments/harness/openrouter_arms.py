"""
Real-model arms (board Lane MEASURE-WAVE item 0, founder go 2026-08-26).

Replaces scripted_arms' DECISION logic with live OpenRouter chat calls
(OpenAI-compatible API, key from env). The AgentAdapter contract is kept:
one episode record per task per arm, consumed by the UNCHANGED
scoring/scoreboard/micro_pack stack.

Documented judgment calls (board discipline):

  - WHAT THE MODEL DECIDES: `resolved`, plus which OFFERED procedures to
    reuse/refuse. The substrate contract stays scripted-parity: arm B reads
    the same rag_corpus blob, arm C runs the same search/gate/refusal dance
    through mcp_surface.StubSurface, and a reuse is credited only AFTER a
    fresh check_applicability verdict - the model proposes, the gate
    disposes. A gate-blocked candidate becomes a RECORDED REFUSAL regardless
    of model intent (spec 39 invariant 11's shape).
  - ATTRIBUTION: reuse_caused_failure := (reused or followed_memory) and
    not resolved - mechanical and ground-truth-free. Fixture truth like
    `stale` / `rag=misleading` is NEVER read by these agents (the scripted
    arms did read it; that is exactly the leak a real arm must not have);
    grading truth stays in scoring.py / micro_pack.check_requirement.
  - COST HONESTY: only successful completions carry usage numbers (what
    OpenRouter bills); EVERY attempt - including 429s and network errors -
    lands in the spend log with its status, so a saturated-pool run shows
    its true attempt profile, not just its bill.
  - RESUMABILITY: rows append per task; run_harness.load_done() skips a task
    only when every arm produced a VALID episode, so unparseable-model-output
    rows (valid=False) are retried on the next --auto-resume pass instead of
    silently poisoning the paired statistics.
  - MODEL CHAIN: primary ox-alpha; alternates are cheap documented standbys,
    overridable with --models. Chain order IS the preference order; a model
    is skipped permanently for the rest of the CALL (not the run) once it
    exhausts retries or answers non-retryable 4xx.

Nothing here imports backend/** (lane rule); the backend/.env file is READ
for the key only - parsed, never printed, never logged.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from pathlib import Path
from typing import Callable, Protocol

import mcp_surface
import scripted_arms

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Preference order. Primary per board item text; alternates are documented
# cheap standbys so a dead primary degrades to a slower sweep, not a dead one.
DEFAULT_MODEL_CHAIN = (
    "ox-alpha",
    "openai/gpt-4o-mini",
    "anthropic/claude-3-5-haiku",
)

# 429 is the headline (shared upstream pool saturates - verified live per
# board); the rest are the transient set worth a retry, never a fallback.
RETRYABLE_STATUSES = frozenset({408, 409, 429, 500, 502, 503, 504})

BACKOFF_BASE_S = 1.5
BACKOFF_CAP_S = 60.0
MAX_ATTEMPTS_PER_MODEL = 6
REQUEST_TIMEOUT_S = 120.0
# 700 sheared live replies mid-object: run2's ledger shows billed calls
# pinned at exactly tokens_out=700 coming back unparseable even after the
# repair round-trip (JSON never closes). 1400 gives headroom; the spend
# ledger keeps the true token bill visible either way.
MAX_COMPLETION_TOKENS = 1400

# Approximate $/Mtok used ONLY for the spend-log cost column; authoritative
# billing lives in the OpenRouter dashboard. Unknown models take the default.
PRICE_PER_MTOK = {
    "default": {"input": 2.50, "output": 10.00},
}

HERE = Path(__file__).resolve().parent
BACKEND_ENV_PATH = HERE.parents[1] / "backend" / ".env"


class AllModelsFailedError(RuntimeError):
    """Every model in the chain exhausted its attempts. Carries the per-model
    attempt trail so the run log explains WHY, not just THAT."""

    def __init__(self, attempts: list[dict]):
        self.attempts = attempts
        tail = "; ".join(
            f"{a['model']}#{a['attempt']}:"
            f"{a.get('status') or a.get('error')}" for a in attempts[-6:])
        super().__init__(f"all {len(set(a['model'] for a in attempts))} chain "
                         f"model(s) exhausted ({len(attempts)} attempts): "
                         f"{tail}")


def build_headers(api_key: str) -> dict[str, str]:
    """OpenRouter convention headers. Split out for offline proof."""
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://localhost/harness",
        "X-Title": "spec40-harness",
    }


def resolve_api_key(env: dict | None = None,
                    env_path: Path | None = None) -> str | None:
    """Process env first, else parse backend/.env (KEY=VALUE lines).
    Returns None when nowhere found - callers must fail LOUD, never send an
    anonymous request. The value itself is never echoed anywhere."""
    env = os.environ if env is None else env
    key = env.get("OPENROUTER_API_KEY")
    if key:
        return key
    path = BACKEND_ENV_PATH if env_path is None else env_path
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OPENROUTER_API_KEY="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
                return val or None
    return None


class SpendLog:
    """Append-only per-run spend ledger (JSONL). One row PER ATTEMPT -
    successes carry usage/cost, failures carry their status/error so pool
    saturation is visible AS DATA. Flushed on every record (crash-safe)."""

    def __init__(self, path: Path | str | None):
        self.path = Path(path) if path else None
        self.rows: list[dict] = []

    def record(self, *, task_id: str, arm: str, model: str, attempt: int,
               status: int | None, latency_s: float,
               tokens_in: int = 0, tokens_out: int = 0,
               error: str | None = None) -> None:
        price = PRICE_PER_MTOK.get(model.split("/", 1)[0],
                                   PRICE_PER_MTOK["default"])
        row = {
            "ts": round(time.time(), 3),
            "task_id": task_id,
            "arm": arm,
            "model": model,
            "attempt": attempt,
            "status": status,
            "error": error,
            "latency_s": round(latency_s, 3),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": round(tokens_in / 1e6 * price["input"]
                              + tokens_out / 1e6 * price["output"], 6),
        }
        self.rows.append(row)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")

    def summarize(self) -> dict:
        billed = [r for r in self.rows if r["tokens_in"] or r["tokens_out"]]
        return {
            "attempts": len(self.rows),
            "billed_calls": len(billed),
            "failed_attempts": len(self.rows) - len(billed),
            "tokens_in": sum(r["tokens_in"] for r in billed),
            "tokens_out": sum(r["tokens_out"] for r in billed),
            "cost_usd": round(sum(r["cost_usd"] for r in billed), 4),
            "by_model": sorted({r["model"] for r in self.rows}),
        }

    def render(self) -> str:
        s = self.summarize()
        return (f"SPEND: {s['attempts']} attempts "
                f"({s['billed_calls']} billed, {s['failed_attempts']} failed)"
                f" · tokens {s['tokens_in']:,}in/{s['tokens_out']:,}out"
                f" · ${s['cost_usd']:.4f}"
                f" · models: {', '.join(s['by_model']) or '-'}")


Transport = Callable[[dict], object]


class FrontierClient(Protocol):
    async def chat(self, messages: list[dict], *, task_id: str = "",
                   arm: str = "") -> dict: ...


def httpx_transport(api_key: str):
    """Default transport: httpx async post. Raised network errors are the
    caller's retryable signal."""
    import httpx

    headers = build_headers(api_key)

    async def _send(payload: dict) -> tuple[int, dict]:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S) as client:
            resp = await client.post(OPENROUTER_URL,
                                     headers=headers, json=payload)
            try:
                body = resp.json()
            except Exception:
                body = {}
            return resp.status_code, body

    return _send


class OpenRouterClient:
    """Fallback-chain chat client: exponential backoff + FULL jitter on
    retryable statuses/network errors; immediate fallthrough to the next
    model on non-retryable 4xx. Sleeps and jitter come from injectables so
    tests prove timing behavior offline with zero wall-clock cost."""

    def __init__(self, api_key: str, *, models: tuple[str, ...] = (),
                 transport: Transport | None = None,
                 sleep: Callable[[float], object] | None = None,
                 rng: Callable[[], float] | None = None,
                 max_attempts: int = MAX_ATTEMPTS_PER_MODEL,
                 spend: SpendLog | None = None):
        self.api_key = api_key
        self.models = tuple(models) or DEFAULT_MODEL_CHAIN
        self._transport = transport or httpx_transport(api_key)
        self._sleep = sleep or asyncio.sleep
        self._rng = rng or random.random
        self.max_attempts = max_attempts
        self.spend = spend

    def backoff_delay(self, attempt: int) -> float:
        """Full jitter: uniform in [0, min(cap, base*2^attempt)) - bounded,
        exponentially growing ceiling, no thundering-herd alignment."""
        import math
        ceiling = min(BACKOFF_CAP_S, BACKOFF_BASE_S * math.pow(2, attempt))
        return self._rng() * ceiling

    async def chat(self, messages: list[dict], *, task_id: str = "",
                   arm: str = "") -> dict:
        attempts: list[dict] = []
        last_model = self.models[-1]
        for model in self.models:
            payload = {
                "model": model,
                "messages": messages,
                "max_tokens": MAX_COMPLETION_TOKENS,
                "temperature": 0.2,
            }
            for attempt in range(self.max_attempts):
                more_tries_left = not (model == last_model
                                       and attempt == self.max_attempts - 1)
                t0 = time.perf_counter()
                try:
                    status, body = await self._transport(payload)
                except Exception as exc:  # noqa: BLE001 - network layer
                    dt = time.perf_counter() - t0
                    err = f"{type(exc).__name__}: {exc}"
                    attempts.append({"model": model, "attempt": attempt,
                                     "status": None, "error": err})
                    if self.spend:
                        self.spend.record(task_id=task_id, arm=arm,
                                          model=model, attempt=attempt,
                                          status=None, latency_s=dt,
                                          error=err)
                    if more_tries_left:
                        await self._sleep(self.backoff_delay(attempt))
                        continue
                    continue
                dt = time.perf_counter() - t0
                if status == 200:
                    usage = body.get("usage") or {}
                    tin = int(usage.get("prompt_tokens") or 0)
                    tout = int(usage.get("completion_tokens") or 0)
                    if self.spend:
                        self.spend.record(task_id=task_id, arm=arm,
                                          model=model, attempt=attempt,
                                          status=status, latency_s=dt,
                                          tokens_in=tin, tokens_out=tout)
                    choices = body.get("choices") or [{}]
                    content = ((choices[0].get("message") or {})
                               .get("content") or "")
                    return {"content": content, "model": model,
                            "tokens_in": tin, "tokens_out": tout,
                            "latency_s": dt, "attempts": attempts}
                attempts.append({"model": model, "attempt": attempt,
                                 "status": status})
                if self.spend:
                    self.spend.record(task_id=task_id, arm=arm, model=model,
                                      attempt=attempt, status=status,
                                      latency_s=dt)
                if status in RETRYABLE_STATUSES:
                    if more_tries_left:
                        await self._sleep(self.backoff_delay(attempt))
                    continue
                break  # non-retryable for THIS model -> next in chain
        raise AllModelsFailedError(attempts)


# --------------------------------------------------------------------------
# Decision prompting
# --------------------------------------------------------------------------

DECISION_SCHEMA = (
    '{"resolved": true|false, '
    '"reuse": ["procedure_id", ...], '
    '"refuse": [{"procedure_id": "...", "reason": "..."}], '
    '"notes": "one sentence"}'
)

SYSTEM_PROMPT = (
    "You are the decision core of a software-engineering evaluation agent. "
    "You receive ONE work situation and must decide how it concludes.\n"
    "If candidate procedures are offered you may reuse them by id (they come "
    "from a verified experience store) or refuse them (state why). Reusing a "
    "procedure whose stated assumptions no longer hold is a serious error; "
    "so is refusing one that plainly applies.\n"
    f"Reply with ONLY this JSON object, no other text:\n{DECISION_SCHEMA}"
)


def parse_decision(content: str) -> dict | None:
    """Extract the JSON decision from a model reply. Tolerates code fences
    and surrounding prose; returns None when no coherent object is found."""
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("resolved"), bool):
        return None
    obj["reuse"] = [r for r in (obj.get("reuse") or []) if isinstance(r, str)]
    refuses = []
    for r in obj.get("refuse") or []:
        if isinstance(r, dict) and isinstance(r.get("procedure_id"), str):
            refuses.append(r)
        elif isinstance(r, str):
            refuses.append({"procedure_id": r, "reason": ""})
    obj["refuse"] = refuses
    obj.setdefault("notes", "")
    return obj


def situation_text(task: dict, scenarios_by_id: dict[str, dict]) -> str:
    """Task goal for the prompt: the scenario's written `situation` when the
    fixture pack carries one, else a neutral synthesis (real-corpus dry-run
    tasks have no narrative; they stay unscored pipeline exercises anyway).

    KNOWN BIAS, sanitized here: some scenario narratives state the expected
    conclusion outright (mic-dep-003 ends 'Honest outcome: everyone falls
    back and fails'). Everything from an 'Honest outcome:' marker on is
    stripped - the model gets the SITUATION, never the scripted verdict.
    Softer framing hints remain in the prose; flagged on the board for a
    founder ruling before any headline data collection."""
    sc = scenarios_by_id.get(task["task_id"]) or {}
    if sc.get("situation"):
        return sc["situation"].split("Honest outcome:")[0].rstrip()
    return (f"Domain: {task.get('domain', 'general')}. "
            f"Handle task {task['task_id']} end to end.")


def build_messages(arm: str, task: dict, situations: dict[str, dict],
                   rag_blob: str | None = None,
                   offered_cards: list[dict] | None = None) -> list[dict]:
    user = situation_text(task, situations)
    if rag_blob:
        user += ("\n\n[Retrieved conventional memory - unverified store]\n"
                 + rag_blob)
    if offered_cards:
        cards = "\n".join(
            f"- id={c['procedure_id']} v{c.get('version')} "
            f"(verified={c.get('verified')}, past executions="
            f"{c.get('evidence', {}).get('executions')}, success_rate="
            f"{c.get('evidence', {}).get('success_rate')})\n"
            f"  assumptions: {'; '.join(c.get('assumptions', []))}"
            for c in offered_cards)
        user += ("\n\n[Candidate procedures surfaced by the experience "
                 f"store]\n{cards}\n"
                 "Decide whether to reuse any of them (by exact id) or "
                 "refuse.")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user}]


# --------------------------------------------------------------------------
# Real agents (episode schema identical to scripted_arms._base_episode)
# --------------------------------------------------------------------------

REPAIR_PROMPT = ("That reply was not valid JSON per the schema. "
                 f"Reply again with ONLY the JSON object:\n{DECISION_SCHEMA}")


class RealAgentBase:
    """Shared machinery: chat (with one JSON-repair round-trip), usage
    accumulation, episode scaffolding."""

    arm = "?"
    # Arm-parity surface visibility (scripted_arms contract): A sees nothing,
    # B and C see the stale offer.
    sees_offers = False

    def __init__(self, client: FrontierClient, fixtures_dir: Path | str,
                 situations: dict[str, dict] | None = None):
        self.client = client
        self.fixtures_dir = Path(fixtures_dir)
        self.situations = situations or {}

    async def _decide(self, messages: list[dict], task_id: str) \
            -> tuple[dict | None, dict]:
        """-> (decision-or-None, aggregate_usage). One repair round-trip
        before giving up; both calls' usage accumulates (both were billed)."""
        usage = {"tokens_in": 0, "tokens_out": 0, "latency_s": 0.0,
                 "calls": 0, "model": None}
        content = ""
        for round_no, msgs in enumerate((messages,
                                         messages + [
                                             {"role": "assistant",
                                              "content": content or "(reply)"},
                                             {"role": "user",
                                              "content": REPAIR_PROMPT}])):
            resp = await self.client.chat(msgs, task_id=task_id,
                                          arm=self.arm)
            usage["tokens_in"] += resp.get("tokens_in", 0)
            usage["tokens_out"] += resp.get("tokens_out", 0)
            usage["latency_s"] += resp.get("latency_s", 0.0)
            usage["calls"] += 1
            usage["model"] = resp.get("model")
            content = resp.get("content", "")
            decision = parse_decision(content)
            if decision is not None:
                return decision, usage
            if round_no == 0:
                continue
        return None, usage

    def _episode(self, task: dict) -> dict:
        ep = scripted_arms._base_episode(task, self.arm)
        offer = task.get("stale_offer")
        ep["stale_offered"] = [offer] if (self.sees_offers and offer) else []
        ep["tokens_in"] = ep["tokens_out"] = 0
        ep["tool_calls"] = 0
        return ep

    async def arun(self, task: dict) -> dict:
        raise NotImplementedError

    def run(self, task: dict) -> dict:
        """Sync AgentAdapter shim for runners built on the scripted loop."""
        return asyncio.run(self.arun(task))


class RealSoloAgent(RealAgentBase):
    """Arm A: solo frontier call per step - situation in, decision out.
    No retrieval surface of any kind (scripted parity: no stale offers)."""

    arm = "A"

    async def arun(self, task: dict) -> dict:
        ep = self._episode(task)
        decision, usage = await self._decide(
            build_messages("A", task, self.situations), task["task_id"])
        if decision is None:
            ep.update(valid=False,
                      invalid_reason="unparseable_decision_after_repair")
            return ep
        ep["resolved"] = decision["resolved"]
        ep["tokens_in"], ep["tokens_out"] = (usage["tokens_in"],
                                             usage["tokens_out"])
        ep["latency_seconds"] = round(usage["latency_s"], 2)
        ep["tool_calls"] = usage["calls"]
        ep["served_by_model"] = usage["model"]
        ep["decision_notes"] = decision.get("notes", "")
        return ep


class RealMemoryAgent(RealSoloAgent):
    """Arm B: same frontier call plus the SAME rag blob the scripted agent
    reads (first hit for the task's domain). Following bad lore is the
    scored behavior; attribution is mechanical (followed + failed)."""

    arm = "B"
    sees_offers = True

    def __init__(self, client: FrontierClient, fixtures_dir: Path | str,
                 situations: dict[str, dict] | None = None):
        super().__init__(client, fixtures_dir, situations)
        blobs = json.loads((self.fixtures_dir / "rag_corpus.json")
                           .read_text(encoding="utf-8"))["blobs"]
        self._blobs = {}
        for b in blobs:
            self._blobs.setdefault(b["domain"], b)

    async def arun(self, task: dict) -> dict:
        ep = self._episode(task)
        blob = self._blobs.get(task.get("domain"))
        rag = task.get("rag", "none")
        if rag != "none":
            # Validation guarantees rag tasks have a domain blob; corpus
            # dry-runs may not - degrade to a named placeholder, never crash.
            ep["followed_memory_ids"].append(
                blob["blob_id"] if blob else "rag-missing")
            ep["tool_calls"] += 1  # the retrieval call itself
        decision, usage = await self._decide(
            build_messages("B", task, self.situations,
                           rag_blob=blob["text"]
                           if (blob and rag != "none") else None),
            task["task_id"])
        if decision is None:
            ep.update(valid=False,
                      invalid_reason="unparseable_decision_after_repair")
            return ep
        ep["resolved"] = decision["resolved"]
        ep["reuse_caused_failure"] = (
            bool(ep["followed_memory_ids"]) and not ep["resolved"])
        ep["tokens_in"], ep["tokens_out"] = (usage["tokens_in"],
                                             usage["tokens_out"])
        ep["latency_seconds"] = round(usage["latency_s"], 2)
        ep["tool_calls"] += usage["calls"]
        ep["served_by_model"] = usage["model"]
        ep["decision_notes"] = decision.get("notes", "")
        return ep


class RealProcedureAgent(RealSoloAgent):
    """Arm C: the SAME surface dance as scripted VerifiedProcedureAgent
    (search -> upfront gate consult on the stale offer -> offer cards ->
    model decides -> gate-enforced reuse credits -> refusals recorded),
    with the model replacing every hardcoded outcome."""

    arm = "C"
    sees_offers = True

    def __init__(self, client: FrontierClient, fixtures_dir: Path | str,
                 situations: dict[str, dict] | None = None,
                 surface: mcp_surface.McpSurface | None = None):
        super().__init__(client, fixtures_dir, situations)
        self.surface = surface or mcp_surface.StubSurface(fixtures_dir)

    async def arun(self, task: dict) -> dict:
        ep = self._episode(task)
        domain = task.get("domain", "")

        candidates = self.surface.search(domain)
        ep["tool_calls"] += 1
        by_id = {c["procedure_id"]: c for c in candidates}
        # One task-scoped gate context for EVERY consult this episode makes
        # (scripted parity: the substrate's poisoning applies to the whole
        # task, so a bypass that offers the card also credits its reuse).
        gate_ctx = ({"bypasses_gate": True}
                    if task.get("substrate_bypasses_gate") else {})

        offered: list[dict] = []
        stale_offer = task.get("stale_offer")
        if stale_offer:
            if self.surface.check_applicability(stale_offer, gate_ctx):
                if stale_offer in by_id:
                    offered.append(by_id[stale_offer])
            else:
                # Substrate-driven refusal (scripted parity): the gate says
                # no, the refusal is RECORDED, the model never sees the card.
                ep["refused_procedure_ids"].append(stale_offer)
                self.surface.record_refusal(
                    stale_offer, "assumptions no longer hold (gate)")
            ep["tool_calls"] += 1

        applicable_id = task.get("applicable_procedure")
        if applicable_id and applicable_id in by_id \
                and applicable_id != stale_offer:
            offered.append(by_id[applicable_id])

        decision, usage = await self._decide(
            build_messages("C", task, self.situations,
                           offered_cards=offered), task["task_id"])
        if decision is None:
            ep.update(valid=False,
                      invalid_reason="unparseable_decision_after_repair")
            return ep

        offered_ids = {c["procedure_id"] for c in offered}
        # Gate-enforced reuse: every model-proposed reuse earns a FRESH
        # applicability call; only passing verdicts are credited.
        for pid in decision.get("reuse", []):
            if pid not in offered_ids:
                continue
            if self.surface.check_applicability(pid, gate_ctx):
                ep["reused_procedure_ids"].append(pid)
            else:
                ep["refused_procedure_ids"].append(pid)
                self.surface.record_refusal(
                    pid, "model proposed reuse; gate blocked")
            ep["tool_calls"] += 1
        # Agent-initiated refusals: the scored stale-detection behavior.
        for r in decision.get("refuse", []):
            pid = r["procedure_id"]
            if pid in offered_ids and pid not in ep["refused_procedure_ids"] \
                    and pid not in ep["reused_procedure_ids"]:
                ep["refused_procedure_ids"].append(pid)
                self.surface.record_refusal(
                    pid, r.get("reason") or "agent refusal")
        ep["resolved"] = decision["resolved"]
        ep["reuse_caused_failure"] = (
            bool(ep["reused_procedure_ids"]) and not ep["resolved"])
        ep["tokens_in"], ep["tokens_out"] = (usage["tokens_in"],
                                             usage["tokens_out"])
        ep["latency_seconds"] = round(usage["latency_s"], 2)
        ep["tool_calls"] += usage["calls"]
        ep["served_by_model"] = usage["model"]
        ep["decision_notes"] = decision.get("notes", "")
        return ep


def load_situations(fixtures_dir: Path | str) -> dict[str, dict]:
    """scenario_id -> scenario, when the pack carries a scenarios.json
    (micro pack); empty for the skeleton fixtures."""
    p = Path(fixtures_dir) / "scenarios.json"
    if not p.exists():
        return {}
    data = json.loads(p.read_text(encoding="utf-8"))
    return {s["scenario_id"]: s for s in data["scenarios"]}


def build_agents(fixtures_dir: Path | str, api_key: str, *,
                 models: tuple[str, ...] = (), spend: SpendLog | None = None,
                 transport: Transport | None = None,
                 sleep=None, rng=None,
                 surface: mcp_surface.McpSurface | None = None) \
        -> dict[str, RealAgentBase]:
    """Same shape as scripted_arms.build_agents - drop-in for the runner."""
    client = OpenRouterClient(api_key, models=models, transport=transport,
                              sleep=sleep, rng=rng, spend=spend)
    situations = load_situations(fixtures_dir)
    return {
        "A": RealSoloAgent(client, fixtures_dir, situations),
        "B": RealMemoryAgent(client, fixtures_dir, situations),
        "C": RealProcedureAgent(client, fixtures_dir, situations,
                                surface=surface),
    }
