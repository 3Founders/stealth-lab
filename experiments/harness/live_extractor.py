"""
OpenRouter-backed observation extractor for the error-floor instrument
(board Lane MEASURE item - first LIVE extraction pass).

Feeds each hand-gold trace excerpt (fixtures/error_floor/) to the frontier
model through openrouter_arms.OpenRouterClient (same backoff/chain/spend
machinery as the real-arms sweeps, same backend/.env key) and writes a
predictions JSONL that run_error_floor.py grades under the rubric:

    live_extractor.py --auto-resume --out live_extractor_preds.jsonl
    run_error_floor.py --predictions live_extractor_preds.jsonl

Documented judgment calls:

  - PROMPT = TICKET 04's PUBLISHED TAXONOMY, not the demo mirror's rules:
    the mechanical layer is typed by what the event IS (file edit / git
    commit / test command / other shell command), not by string prefixes;
    the semantic layer follows the NONE contract ("too generic -> emit
    nothing"); hard-negative tools warrant nothing; ONE event may warrant
    several observations.
  - HONEST FLOOR: dict-shaped model output passes through to the grader
    VERBATIM - an unknown type or missing key is graded as the FP it is
    (rubric: malformed predictions count as FPs). Only NON-dict items
    inside the observations array are dropped (they would crash the grader
    rather than grade); each drop is counted in the row's meta.
  - ONE repair round-trip on unparseable output, mirroring RealAgentBase;
    both calls' usage lands in the spend ledger either way.
  - RESUME: a non-empty predictions file refuses to run without
    --auto-resume; resume skips excerpt_ids already holding a prediction
    row (paid history never clobbered, same discipline as run_real_arms).
  - LANE RULE: no backend import; key read from env / backend/.env only.

Offline tests: tests/test_live_extractor.py (fake client, zero network).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import error_floor
import openrouter_arms

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_OUT = HERE / "live_extractor_preds.jsonl"
DEFAULT_SPEND = HERE / "live_extractor_spend.jsonl"

EXTRACT_SCHEMA = (
    '{"observations": [{"observation_type": "file_touched|commit_made|'
    'test_run|command_executed|semantic_label", "label": "...", '
    '"properties": {"file_path": "...", "command": "..."}}]}'
)

SYSTEM_PROMPT = (
    "You extract structured observations from ONE software-engineering "
    "trace event (a tool call). Emit every observation a careful engineer "
    "would agree the event warrants, of these types:\n"
    "- file_touched: the event created or modified a file. properties must "
    "carry file_path exactly as the event names it.\n"
    "- commit_made: the executed command created a git commit. properties "
    "must carry command = the full command line.\n"
    "- test_run: the executed command ran a test suite. properties.command "
    "= full command line.\n"
    "- command_executed: any other shell command that actually executed. "
    "properties.command = full command line.\n"
    "- semantic_label: free text describing WHAT changed and WHY it "
    "matters (e.g. 'authentication implementation was modified'). If the "
    "event is too generic to say anything meaningful, emit NO label for "
    "it - silence beats filler.\n"
    "Rules: one event can warrant several observations (a file edit can "
    "carry both its file_touched fact and a semantic label). Planning, "
    "search, lookup and delegation tools (todos, web search, subagent "
    "spawns, file reads/greps) warrant NOTHING. Do not invent facts the "
    "event does not show.\n"
    f"Reply with ONLY this JSON object, no other text:\n{EXTRACT_SCHEMA}"
)

REPAIR_PROMPT = ("That reply was not valid JSON per the schema. "
                 f"Reply again with ONLY the JSON object:\n{EXTRACT_SCHEMA}")


def parse_observations(content: str) -> tuple[list[dict], int] | None:
    """Model reply -> ([dict predictions verbatim], n_nondict_dropped).

    Tolerates code fences and surrounding prose like parse_decision does;
    returns None when no coherent object with an observations LIST is
    found. Dict items pass through UNVALIDATED - grading owns judgment.
    """
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
    if not isinstance(obj, dict) \
            or not isinstance(obj.get("observations"), list):
        return None
    kept = [o for o in obj["observations"] if isinstance(o, dict)]
    return kept, len(obj["observations"]) - len(kept)


def build_messages(trace_event: dict) -> list[dict]:
    user = ("Trace event:\n"
            + json.dumps(trace_event, ensure_ascii=False)
            + "\n\nExtract the observations.")
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user}]


class LiveExtractor:
    """One excerpt in, one predictions list out."""

    def __init__(self, client, spend=None):
        self.client = client
        self.spend = spend
        self.usage_totals = {"tokens_in": 0, "tokens_out": 0, "calls": 0}

    async def extract_async(self, trace_event: dict, excerpt_id: str = ""):
        """-> (predictions-or-None, meta). One repair round-trip."""
        meta = {"tokens_in": 0, "tokens_out": 0, "calls": 0,
                "model": None, "nondict_dropped": 0}
        messages = build_messages(trace_event)
        parsed = None
        rounds = (messages,
                  messages + [{"role": "assistant", "content": "(reply)"},
                              {"role": "user", "content": REPAIR_PROMPT}])
        for round_no, msgs in enumerate(rounds):
            resp = await self.client.chat(msgs, task_id=excerpt_id, arm="EX")
            meta["tokens_in"] += resp.get("tokens_in", 0)
            meta["tokens_out"] += resp.get("tokens_out", 0)
            meta["calls"] += 1
            meta["model"] = resp.get("model")
            result = parse_observations(resp.get("content", ""))
            if result is not None:
                preds, dropped = result
                meta["nondict_dropped"] = dropped
                parsed = preds
                break
            if round_no == 0:
                continue
        self.usage_totals["tokens_in"] += meta["tokens_in"]
        self.usage_totals["tokens_out"] += meta["tokens_out"]
        self.usage_totals["calls"] += meta["calls"]
        return parsed, meta

    def extract(self, trace_event: dict, excerpt_id: str = ""):
        return asyncio.run(self.extract_async(trace_event, excerpt_id))


def load_done(path: Path) -> set[str]:
    """excerpt_ids already holding a PARSED predictions row; unparseable
    rows stay absent so --auto-resume retries them (run_real_arms
    discipline). Torn final lines skipped like scoreboard.load_rows."""
    done: set[str] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row.get("observations"), list) \
                    and not row.get("unparseable"):
                done.add(row.get("excerpt_id", ""))
    return done


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--fixtures-dir",
                    default=str(error_floor.DEFAULT_FIXTURES_DIR))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--spend-log", default=str(DEFAULT_SPEND))
    ap.add_argument("--models", default=None,
                    help="comma-separated fallback chain; default "
                         f"{','.join(openrouter_arms.DEFAULT_MODEL_CHAIN)}")
    ap.add_argument("--auto-resume", action="store_true",
                    help="skip excerpts already predicted; without it an "
                         "existing predictions file is refused")
    return ap


async def async_main(args) -> int:
    out = Path(args.out)
    if out.exists() and out.stat().st_size > 0 and not args.auto_resume:
        print(f"REFUSING to overwrite paid predictions: {out} exists.\n"
              f"Re-run with --auto-resume to skip completed excerpts.")
        return 2

    api_key = openrouter_arms.resolve_api_key()
    if not api_key:
        print("OPENROUTER_API_KEY not found (process env, then "
              "backend/.env). Refusing to run a billed extraction "
              "anonymously.")
        return 2

    excerpts = error_floor.load_excerpts(Path(args.fixtures_dir))
    models = (tuple(m.strip() for m in args.models.split(",") if m.strip())
              if args.models else ())
    spend = openrouter_arms.SpendLog(args.spend_log)
    client = openrouter_arms.OpenRouterClient(api_key, models=models,
                                              spend=spend)
    extractor = LiveExtractor(client, spend=spend)
    done = load_done(out)
    todo = [ex for ex in excerpts if ex["excerpt_id"] not in done]
    print(f"live extractor: {len(excerpts)} excerpts, {len(done)} done via "
          f"resume, {len(todo)} to run; chain: "
          f"{', '.join(models or openrouter_arms.DEFAULT_MODEL_CHAIN)}")

    for i, ex in enumerate(todo, 1):
        eid = ex["excerpt_id"]
        print(f"[{i}/{len(todo)}] {eid}...", flush=True)
        try:
            preds, meta = await extractor.extract_async(ex["trace_event"],
                                                        eid)
        except openrouter_arms.AllModelsFailedError as exc:
            print(f"    all models failed: {exc}")
            preds = None
            meta = {"error": str(exc)}
        if preds is None:
            # Unparseable after repair: record an EMPTY prediction list
            # under an explicit flag so grading counts the miss honestly
            # instead of crashing or silently dropping the excerpt.
            row = {"excerpt_id": eid, "observations": [],
                   "unparseable": True, "meta": meta}
        else:
            row = {"excerpt_id": eid, "observations": preds, "meta": meta}
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")

    print(extractor_usage_line(spend))
    print(f"[predictions -> {out}; spend ledger -> {args.spend_log}]")
    return 0


def extractor_usage_line(spend: openrouter_arms.SpendLog) -> str:
    s = spend.summarize()
    return (f"EXTRACT SPEND: {s['attempts']} attempts "
            f"({s['billed_calls']} billed) · tokens "
            f"{s['tokens_in']:,}in/{s['tokens_out']:,}out · "
            f"${s['cost_usd']:.4f}")


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(build_arg_parser().parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
