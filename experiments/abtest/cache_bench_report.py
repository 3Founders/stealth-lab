"""The prompt-cache benchmark's numbers (docs/cache_bench.md), from a run's files: each Claude Code turn's proxy log
(`<task>.<arm>[.tN].requests.jsonl`, one line per API request, from cache_proxy.py) and the run's results lines (solved or
not, and the cheap models' cost for the kell arm).

Cost is computed from the token counts the provider reported for each request -- fresh input, cache reads, cache writes,
output -- at the list price of the model that actually answered (Claude Code's own cost figure prices every token at the
model it asked for, which is wrong for a router arm).

    python cache_bench_report.py <dir with the run's logs and results> [--json out.json]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

# $ per million tokens: input, output, cache read, cache write (5-minute TTL). Claude API list prices, 2026-10.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00, 0.20, 2.50),
    "claude-haiku-5-5": (0.10, 0.50, 0.01, 0.125),           # prompts up to 100K tokens
    "glm-5.3-flash": (0.15, 0.50, 0.03, 0.15),               # z.ai list price; its cache read rate is an assumption (none was reported)
}
HAIKU_LONG = (0.50, 2.50, 0.05, 0.625)                        # Haiku 5.5 above 100K prompt tokens
NAME = re.compile(r"^(?P<task>.+)\.(?P<arm>A|R|RC|C0|C-R|C-L|C)(?:\.t(?P<turn>\d+))?\.requests\.jsonl$")


def price(model: str, prompt_tokens: int) -> tuple[float, float, float, float]:
    m = model or ""
    if m.startswith("claude-haiku-5-5") and prompt_tokens > 100_000:
        return HAIKU_LONG
    for k, p in PRICES.items():
        if m.startswith(k):
            return p
    if "haiku" in m:
        return PRICES["claude-haiku-5-5"]
    if "sonnet" in m:
        return PRICES["claude-sonnet-5-5"]
    return PRICES["claude-opus-5-5"]


def request_cost(r: dict, cold: bool = False) -> float:
    """What one request cost. cold: price it as if nothing were cached yet (cache reads become writes) -- used for a
    session's first request, so arms that ran side by side on one account do not ride on each other's cache."""
    fresh, read, write, out = (int(r.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens",
                                                             "cache_creation_input_tokens", "output_tokens"))
    if cold:
        read, write = 0, write + read
    pi, po, pr, pw = price(r.get("served_model") or r.get("model") or "", fresh + read + write)
    return (fresh * pi + out * po + read * pr + write * pw) / 1e6


def load(root: Path) -> tuple[dict, dict]:
    """requests[(task, arm)] -> the requests of every turn in order; results[(task, arm)] -> the result line."""
    requests: dict = defaultdict(list)
    for f in sorted(root.rglob("*.requests.jsonl")):
        m = NAME.match(f.name)
        if not m:
            continue
        turn = int(m["turn"] or 1)
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                r["turn"] = turn
                requests[(m["task"], m["arm"])].append(r)
    for k in requests:
        requests[k].sort(key=lambda r: (r["turn"], r.get("ts", 0)))
    results: dict = {}
    for f in root.rglob("*.jsonl"):
        if f.name.endswith(".requests.jsonl") or "logs" in f.parts:
            continue
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict) and "task_id" in r and "arm" in r and "resolved" in r:
                results[(r["task_id"], r["arm"])] = r
    return requests, results


def session_numbers(reqs: list[dict]) -> dict:
    ok = [r for r in reqs if r.get("status") == 200 and r.get("path", "").rstrip("/").endswith("/v1/messages")]
    fresh = sum(int(r.get("input_tokens") or 0) for r in ok)
    read = sum(int(r.get("cache_read_input_tokens") or 0) for r in ok)
    write = sum(int(r.get("cache_creation_input_tokens") or 0) for r in ok)
    out = sum(int(r.get("output_tokens") or 0) for r in ok)
    switched = [r for r in ok if r.get("switched")]
    # what a switch costs in rereading: the input the switched request could not read from a cache, and the same for the
    # first session-model request after it (the way back)
    reread, back = 0, False
    for r in ok:
        if r.get("switched"):
            reread += int(r.get("input_tokens") or 0) + int(r.get("cache_creation_input_tokens") or 0)
            back = True
        elif back and not r.get("switched"):
            reread += int(r.get("input_tokens") or 0) + int(r.get("cache_creation_input_tokens") or 0)
            back = False
    ttfts = sorted(r["ttft_s"] for r in ok if r.get("ttft_s") is not None)
    return {"requests": len(ok), "switched": len(switched), "fresh": fresh, "cache_read": read, "cache_write": write,
            "output": out, "cache_share": round(read / max(1, fresh + read + write), 4),
            "claude_cost": round(sum(request_cost(r, cold=(i == 0)) for i, r in enumerate(ok)), 6), "reread_tokens": reread,
            "ttft_median_s": ttfts[len(ttfts) // 2] if ttfts else None,
            "per_request": [{"i": i + 1, "model": r.get("served_model") or r.get("model"), "switched": bool(r.get("switched")),
                             "cache_share": round(int(r.get("cache_read_input_tokens") or 0) /
                                                  max(1, sum(int(r.get(k) or 0) for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))), 4),
                             "cost": round(request_cost(r, cold=(i == 0)), 6)} for i, r in enumerate(ok)]}


def report(root: Path) -> dict:
    requests, results = load(root)
    arms: dict = defaultdict(lambda: {"tasks": 0, "solved": 0, "claude_cost": 0.0, "open_cost": 0.0, "requests": 0,
                                      "switched": 0, "fresh": 0, "cache_read": 0, "cache_write": 0, "output": 0,
                                      "reread_tokens": 0, "ttfts": []})
    per_task = {}
    for key in sorted(set(requests) | set(results)):
        task, arm = key
        s = session_numbers(requests.get(key, []))
        res = results.get(key, {})
        a = arms[arm]
        a["tasks"] += 1
        a["solved"] += 1 if res.get("resolved") else 0
        a["claude_cost"] += s["claude_cost"]
        a["open_cost"] += float(res.get("open_cost_usd") or 0)
        for k in ("requests", "switched", "fresh", "cache_read", "cache_write", "output", "reread_tokens"):
            a[k] += s[k]
        if s["ttft_median_s"] is not None:
            a["ttfts"].append(s["ttft_median_s"])
        per_task[f"{task}|{arm}"] = {**{k: v for k, v in s.items() if k != "per_request"}, "resolved": res.get("resolved"),
                                     "open_cost": res.get("open_cost_usd"), "per_request": s["per_request"]}
    summary = {}
    for arm, a in arms.items():
        total = a["claude_cost"] + a["open_cost"]
        inp = a["fresh"] + a["cache_read"] + a["cache_write"]
        t = sorted(a["ttfts"])
        summary[arm] = {"tasks": a["tasks"], "solved": a["solved"], "requests": a["requests"], "switched": a["switched"],
                        "cache_share": round(a["cache_read"] / max(1, inp), 4), "reread_tokens": a["reread_tokens"],
                        "claude_cost": round(a["claude_cost"], 4), "open_cost": round(a["open_cost"], 4),
                        "total_cost": round(total, 4),
                        "cost_per_solved": round(total / a["solved"], 4) if a["solved"] else None,
                        "ttft_median_s": t[len(t) // 2] if t else None}
    return {"summary": summary, "per_task": per_task}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("root")
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    rep = report(Path(a.root))
    cols = ("tasks", "solved", "requests", "switched", "cache_share", "reread_tokens", "claude_cost", "open_cost",
            "total_cost", "cost_per_solved", "ttft_median_s")
    print("arm  " + "  ".join(f"{c:>14}" for c in cols))
    for arm in ("A", "R", "RC", "C", "C0", "C-R", "C-L"):
        if arm in rep["summary"]:
            s = rep["summary"][arm]
            print(f"{arm:<4} " + "  ".join(f"{str(s[c]):>14}" for c in cols))
    if a.json:
        Path(a.json).write_text(json.dumps(rep, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
