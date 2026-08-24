#!/usr/bin/env python3
"""Render the live-counters block of usageapi.md from gemini_usage.jsonl.

Reads backend/logs/gemini_usage.jsonl (one JSON line per Gemini embedding
attempt, written by Embedder._embed_gemini) and prints per-key totals for
the last minute / hour / day (Pacific-midnight reset, matching Google's
free-tier window), plus recent 429s. With --write, replaces the block
between <!-- BEGIN:COUNTERS --> and <!-- END:COUNTERS --> markers in
docs/usageapi.md.

Usage:
    python scripts/gemini_usage_report.py [--write]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
LOG = BACKEND / "logs" / "gemini_usage.jsonl"
DOC = BACKEND / "docs" / "usageapi.md"

# Free tier per key: 100 RPM / 30K TPM / 1K RPD; pool = x len(keys)
KEY_COUNT = 3


def pacific_now():
    return datetime.now(timezone.utc) - timedelta(hours=7)


def main() -> int:
    if not LOG.exists():
        print("no usage log yet at", LOG)
        return 0
    now = datetime.now(timezone.utc)
    day_start = pacific_now().replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)

    rows = []
    with LOG.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    def since(minutes=None, start=None):
        cut = start or (now - timedelta(minutes=minutes or 0))
        return [r for r in rows if datetime.fromisoformat(r["ts"]) >= cut]

    win_60s, win_60m, today = since(1), since(60), since(start=day_start)

    def table(rows_):
        calls = defaultdict(int)
        toks = defaultdict(int)
        errs = defaultdict(int)
        callers = defaultdict(int)
        for r in rows_:
            k = r.get("key_idx", "?")
            calls[k] += 1
            toks[k] += r.get("est_tokens", 0)
            if not r.get("ok"):
                errs[k] += 1
            callers[r.get("caller", "?")] += 1
        lines = ["| key | reqs | est tokens | errors |", "|---|---|---|---|"]
        for k in sorted(calls):
            lines.append(f"| #{k} | {calls[k]} | {toks[k]:,} | {errs[k]} |")
        lines.append(f"| **pool** | **{sum(calls.values())}** | **{sum(toks.values()):,}** | **{sum(errs.values())}** |")
        lines.append("")
        lines.append("Callers: " + (", ".join(f"{c}={n}" for c, n in sorted(callers.items())) or "none"))
        return "\n".join(lines), sum(calls.values()), sum(toks.values()), sum(errs.values())

    t60s, _, _, _ = table(win_60s)
    t60m, c60m, tok60m, _ = table(win_60m)
    tday, cday, tokday, eday = table(today)

    recent429 = [r for r in rows if r.get("err_class") == "429"][-5:]

    report = (
        f"<!-- rendered {datetime.now(timezone.utc).isoformat()} -->\n"
        f"- last 60s: **{_60s_calls(win_60s)} reqs**\n"
        f"- last 60min: {c60m} reqs / {tok60m:,} tokens "
        f"(budget {KEY_COUNT * 25000:,} TPM)\n"
        f"- today (PT): **{cday} / {KEY_COUNT * 1000} RPD** · {tokday:,} tokens · {eday} errors\n\n"
        f"### Last 60 minutes per key\n{t60m}\n\n"
        f"### Today per key (PT)\n{tday}\n\n"
        f"### Recent 429s\n"
        + ("\n".join(f"- `{r['ts']}` key#{r.get('key_idx')} caller={r.get('caller')}" for r in recent429) or "- none recorded")
    )
    print(report)

    if "--write" in sys.argv and DOC.exists():
        text = DOC.read_text(encoding="utf-8")
        begin = "<!-- BEGIN:COUNTERS -->"
        end = "<!-- END:COUNTERS -->"
        if begin in text and end in text:
            pre, _, rest = text.partition(begin)
            _, _, post = rest.partition(end)
            DOC.write_text(pre + begin + "\n" + report + "\n" + end + post, encoding="utf-8")
            print("\nusageapi.md counters block updated")
        else:
            print("\nmarkers missing in usageapi.md -- counters NOT written")
    return 0


def _60s_calls(rows_):
    return len(rows_)


if __name__ == "__main__":
    sys.exit(main())
