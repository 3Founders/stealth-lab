"""Kel's MCP tools as a command, for Claude Code subagents in DS-1000 round 4 (arm KP, model Sonnet).

    python kel_cli.py find_ways --ws <workspace> --query "<what you want done>" [--claims @.stealth/claims.md]
    python kel_cli.py read_procedure_claims --ws <workspace> --id <procedure_id>

Same backend as the open models' KP arm: the real `find_ways` / claims resource, in-process, against the
frozen `kel_ds1000_r4` (KNOWLEDGE_VERIFIED_EXAMPLES on). Output is the tool's reply, cut at the same caps
(24,000 / 8,000 characters). Every call is logged to runs4/sonnet_kel_calls.jsonl with its workspace.
"""
from __future__ import annotations

import os

os.environ["KEL_DS1000_RUNS"] = "runs4"
os.environ["KEL_DS1000_DSN"] = "postgresql://postgres@127.0.0.1:55432/kel_ds1000_r4"
os.environ["KNOWLEDGE_VERIFIED_EXAMPLES"] = "true"

import demo_env  # noqa: E402

import argparse  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

sys.path.insert(0, str(demo_env.HERE.parent))
import kel_product_arm as kpa  # noqa: E402

from run_r4 import TOOL_MAX_CHARS  # noqa: E402

LOG = demo_env.RUNS / "sonnet_kel_calls.jsonl"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tool", choices=["find_ways", "read_procedure_claims"])
    ap.add_argument("--ws", required=True)
    ap.add_argument("--query", default="")
    ap.add_argument("--claims", default=kpa.CLAIMS_REF)
    ap.add_argument("--id", default="")
    a = ap.parse_args()
    logging.disable(logging.CRITICAL)
    demo_env.verify_after_import()
    ws = os.path.abspath(a.ws)
    bridge = kpa.KelBridge(demo_env.DEMO_DSN)
    t0 = time.time()
    try:
        if a.tool == "find_ways":
            claims = a.claims
            if claims.strip() == kpa.CLAIMS_REF:
                path = os.path.join(ws, kpa.STEALTH, "claims.md")
                claims = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
            out = bridge.find_ways(a.query, claims[:65536])
            try:
                d = json.loads(out)
                procs = d.get("procedures") or []
                ways = [w for c in (d.get("candidates") or []) for w in (c.get("ways") or [])]
                summary = {"outcome": d.get("outcome"), "procedures": [str(p.get("procedure_id")) for p in procs],
                           "candidate_ways": [str(w.get("procedure_id")) for w in ways],
                           "verified_solution": any(p.get("verified_solution") for p in procs + ways)}
            except (json.JSONDecodeError, AttributeError):
                summary = {"outcome": "unparsed"}
        else:
            out = bridge.procedure_claims(a.id)
            summary = {"procedure_id": a.id}
    finally:
        bridge.close()
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ws": ws, "tool": a.tool, "query": a.query[:300], "ms": round((time.time() - t0) * 1000),
                             "chars": len(out), **summary}) + "\n")
    sys.stdout.write(out[:TOOL_MAX_CHARS[a.tool]] + "\n")


if __name__ == "__main__":
    main()
