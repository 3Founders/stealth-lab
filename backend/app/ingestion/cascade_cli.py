"""`cascade-repo`: run the code cascade on one GitHub repository.

    python -m app.ingestion.admin cascade-repo pallets/click                     # dry run: ranks + judges, writes NOTHING
    python -m app.ingestion.admin cascade-repo pallets/click --no-judge          # structure only: free, no model call
    python -m app.ingestion.admin cascade-repo pallets/click --apply --max-usd 1 # store the accepted spans

A dry run still calls the model unless `--no-judge` (that is how you read what would be stored), so it is capped by `--max-usd`
like everything else that spends. `--apply` needs the model: nothing is stored on structure alone.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from app.utils.aio import run_blocking

COMMANDS = ("cascade-repo",)


def add_parsers(sub: Any) -> None:
    p = sub.add_parser("cascade-repo")
    p.add_argument("repository", help="owner/name on GitHub")
    p.add_argument("--ref", default=None, help="branch, tag or sha (default: the default branch); pinned to a commit either way")
    p.add_argument("--top", type=int, default=20, help="exemplars to keep (default 20)")
    p.add_argument("--no-judge", action="store_true", help="structure only: no model call, nothing can be applied")
    p.add_argument("--apply", action="store_true", help="store the accepted spans (default: dry run)")
    p.add_argument("--max-usd", type=float, default=1.0, help="model-spend ceiling, enforced BEFORE each paid call")
    p.add_argument("--languages", default=None, help="comma list to restrict, e.g. python,go")
    p.add_argument("--json", default=None, help="write the full report here")


async def run(pool: Any, a: Any) -> int:
    from app.config import settings
    from app.services import ingest_budget
    from app.services.code_cascade.pipeline import run_repo_cascade
    from app.services.governance import BudgetExceeded

    if a.apply and a.no_judge:
        print("ERROR: --apply needs the model judge; nothing is stored on structure alone", file=sys.stderr)
        return 2
    client = model = None
    budget = None
    if not a.no_judge:
        from app.services.ingestion_jobs import _general_compute_client

        client = await run_blocking(_general_compute_client)
        model = settings.general_compute_judge_model or "gemma-4-31B-it"
        if client is None:
            print("ERROR: no model client is configured (Vertex ADC or a General Compute key); use --no-judge for a free "
                  "structural run", file=sys.stderr)
            return 2
        budget = ingest_budget.install(pool, cap_usd=a.max_usd) if ingest_budget.active() is None else None
    languages = {x.strip() for x in a.languages.split(",") if x.strip()} if a.languages else None
    try:
        report = await run_repo_cascade(pool, a.repository, ref=a.ref, top=a.top, client=client, model=model, apply=a.apply,
                                        languages=languages)
    except BudgetExceeded as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001 -- a refusal (archived, unreadable, over the size cap) is an answer, not a crash
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if budget is not None:
            ingest_budget.uninstall()
    text = json.dumps(report, indent=2, default=str)
    print(text)
    if a.json:
        await run_blocking(lambda: open(a.json, "w", encoding="utf-8").write(text))
    return 0
