#!/usr/bin/env python3
"""LLMRouterBench's result archive -> one JSON line per (dataset, model): what the routing import reads.

    python scripts/llmrouterbench_summary.py bench-release.tar.gz > llmrouterbench/summary.jsonl

The archive (HF `NPULH/LLMRouterBench`, bench-release.tar.gz, 1.3 GB, MIT) holds every model's answer to every
prompt, as `bench-release/<dataset>[/<split>]/<model>/<dataset>-<split>-<model>-<timestamp>.json`; the
dataset here is the file name's part before the model (`aime-hybrid`, `arenahard-coding`), which both layouts share. The import needs only how many each model got
right and what it spent, so this streams the archive once (nothing is extracted) and keeps the newest run per
(dataset, model). Each line: dataset, model, run, n, k (records scoring >= 0.5), score_sum, binary
(every score 0 or 1), prompt_tokens, completion_tokens, cost_usd, seconds.
"""
from __future__ import annotations

import json
import sys
import tarfile


def summarize(fh) -> dict:
    data = json.load(fh)
    records = data.get("records") or []
    scores = [float(r.get("score") or 0.0) for r in records]
    return {"n": len(scores) or int(data.get("counts") or 0), "k": sum(1 for s in scores if s >= 0.5),
            "score_sum": round(sum(scores), 4), "binary": all(s in (0.0, 1.0) for s in scores),
            "prompt_tokens": int(data.get("prompt_tokens") or 0),
            "completion_tokens": int(data.get("completion_tokens") or 0),
            "cost_usd": float(data.get("cost") or 0.0), "seconds": float(data.get("time_taken") or 0.0)}


def main(path: str) -> int:
    newest: dict[tuple[str, str], dict] = {}
    with tarfile.open(path, "r|gz") as tar:
        for member in tar:
            parts = member.name.replace("\\", "/").split("/")
            if not member.isfile() or not parts[-1].endswith(".json") or len(parts) < 3:
                continue
            model, run = parts[-2], parts[-1][:-5]
            dataset = run.split(f"-{model}-")[0] if f"-{model}-" in run else "-".join(parts[1:-2])
            fh = tar.extractfile(member)
            if fh is None:
                continue
            try:
                row = summarize(fh)
            except (ValueError, TypeError) as exc:
                print(f"skipped {member.name}: {exc}", file=sys.stderr)
                continue
            key = (dataset, model)
            if key not in newest or run > newest[key]["run"]:
                newest[key] = {"dataset": dataset, "model": model, "run": run, **row}
    for key in sorted(newest):
        print(json.dumps(newest[key]))
    print(f"{len(newest)} (dataset, model) results", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
