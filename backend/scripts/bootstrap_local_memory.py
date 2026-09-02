#!/usr/bin/env python
"""
Historical local memory bootstrap CLI (Ideal V1 directive §12).

The new-user experience: connect StealthLab to the work you already have,
process it LOCALLY, and end with a usable private procedural library --
no server, no Postgres, no network call anywhere in this path.

    python scripts/bootstrap_local_memory.py --repo ..            # a repo's procedural material
    python scripts/bootstrap_local_memory.py --claude-export conversations.json
    python scripts/bootstrap_local_memory.py --chatgpt-export conversations.json
    python scripts/bootstrap_local_memory.py --claude-traces ~/.claude/projects/<proj>
    python scripts/bootstrap_local_memory.py --all-of-the-above --workspace <dir>

Everything converges into <workspace>/.stealthlab/local_procedures.db via
the SAME LocalProcedureStore live runs write to -- one substrate, one
candidate/trust model, per-source provenance preserved (and deduped into
one canonical procedure when sources describe the same workflow).

Output ends with the honest summary line, e.g.:
    Found 14 candidate procedures (3 merged, 5 skipped: discussion-only)
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.local_agent.historical_bootstrap import run_bootstrap
from app.local_agent.local_store import LocalProcedureStore


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--workspace", default=os.getcwd(),
                    help="workspace whose private library receives the imports "
                         "(default: cwd; the store lives at .stealthlab/local_procedures.db)")
    ap.add_argument("--repo", help="existing repository to extract procedural material from")
    ap.add_argument("--claude-export", help="Claude conversations.json export file")
    ap.add_argument("--chatgpt-export", help="ChatGPT conversations.json export file")
    ap.add_argument("--claude-traces", help="directory of Claude Code session transcripts (*.jsonl)")
    ap.add_argument("--no-embed", action="store_true",
                    help="skip embeddings (dedup falls back to strict lexical matching)")
    args = ap.parse_args()

    if not any([args.repo, args.claude_export, args.chatgpt_export, args.claude_traces]):
        ap.error("nothing to import: pass --repo and/or --claude-export and/or "
                 "--chatgpt-export and/or --claude-traces")

    embed = None
    if not args.no_embed:
        try:
            from app.services.embeddings import Embedder
            _embedder = Embedder()
            async def embed(text, *, input_type="document"):
                return await _embedder.embed_one(text, input_type=input_type)
        except Exception as exc:  # noqa: BLE001 -- degrade honestly, print why
            print(f"[bootstrap] embeddings unavailable ({exc}); "
                  "dedup will use strict lexical matching", file=sys.stderr)

    store = LocalProcedureStore(args.workspace)
    summary = run_bootstrap(
        store,
        repo_root=args.repo,
        claude_export=args.claude_export,
        chatgpt_export=args.chatgpt_export,
        claude_traces_dir=args.claude_traces,
        embed=embed,
        workspace_entity_id=os.path.abspath(args.workspace),
    )

    print(f"episodes found in your material: {summary['episodes']}")
    print(f"new candidates captured:         {summary['captured']}")
    print(f"merged into existing candidates: {summary['merged']}")
    print(f"skipped (discussion-only):       {summary['skipped']}")
    n = summary["captured"] + summary["merged"]
    print(f"\nFound {n} candidate procedures "
          f"({summary['merged']} merged, {summary['skipped']} skipped: discussion-only)")
    print(f"Library: {store.db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
