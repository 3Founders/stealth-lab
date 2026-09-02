"""
Corpus wave -- Phase 10, deliverable 1: retrieval + solution-search proof.

HAND-RUN probe (like backend/check_*.py), NOT a pytest file. Proves the 28
externally-ingested `prior_library` procedures are RETRIEVABLE and rankable
through the EXISTING retrieval stack, without forcing them into the
production execution architecture.

Real callable exercised:
  app.services.domain_search.search_global(object_types=["procedure"])
    -> app.services.applicability.find_applicable_procedures
       (hard-constraint cascade + similarity/capability RRF fusion of
        survivors -- the same ranking /v1/procedures/search and the MCP
        `search_procedures` tool use, reused verbatim)

`require_verified=False` is passed via `filters` because all 28 corpus
procedures are `verification_state=candidate` (no execution evidence yet --
that is the production hardening wave). This is search_global's own
documented browse-mode default; every other hard constraint (temporal
validity, staleness, availability, scope, preconditions, invariants) still
applies.

Embeddings: the real `app.services.embeddings.Embedder` (Voyage/Gemini
chain) -- search_global embeds the query once internally with
input_type='query'.

Run:
  export DATABASE_URL='postgresql://...pooler.supabase.com:5432/postgres'
  cd backend && python ../.scratch/corpus_wave/retrieval_eval.py

Writes: .scratch/corpus_wave/retrieval_results.json
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # .scratch/corpus_wave/ -> StealthLab/
sys.path.insert(0, str(ROOT / "backend"))

from app.db.session import create_pool  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from app.services.domain_search import search_global  # noqa: E402

OUT = Path(__file__).resolve().parent / "retrieval_results.json"

TOP_K = 10  # pull depth per query; recall@5 / recall@10 both computed from this

# (procedure_id, source_id, primary NL query, paraphrase NL query)
# Each primary query is what a user who WANTS this procedure would plausibly
# type; the paraphrase restates the same intent with different surface words.
QUERIES: list[tuple[str, str, str, str]] = [
    ("83d2e2f8-d79f-45c7-9ac8-9d9d63ea4542", "S36",
     "turn a merged pull request that fixes a bug into a pass/fail task with FAIL_TO_PASS and PASS_TO_PASS tests",
     "how do I build a verifiable SWE-bench style task instance from a real merged PR and its test patch"),
    ("8927d8cd-79e4-4a90-8dbc-9f338fae2b34", "S02",
     "eval-driven loop for authoring an agent skill and proving it beats a baseline before shipping",
     "create a skill, write evals, run with-skill and baseline, grade, and iterate until feedback is empty"),
    ("9795b49f-83a3-4ea1-8256-a9439c8a800c", "S19",
     "reproduce the Context Rot benchmark end to end with a venv, provider API keys and downloaded datasets",
     "run the Chroma context-rot experiments and compare the degradation curves to the published figures"),
    ("b1d824c0-1f91-4b77-b851-78d99f18dd46", "S46",
     "harden a login system to the OWASP baseline: password length rules, breached-password blocking, account lockout, constant-time generic errors",
     "authentication hardening: enforce minimum password length, check Pwned Passwords, add failed-login lockout, identical error text for bad user vs bad password"),
    ("dc1ce2aa-26ad-46b3-ba6c-bc9d34d3446d", "S24",
     "trim MCP tool context to only what a task needs, staying under a token budget while keeping the task completable",
     "produce a client-specific tool allowlist compressed to a budget and confirm sufficiency is retained"),
    ("8e2ef4a2-2d06-486a-a19f-542a279f8bc9", "S43",
     "verify a scientific claim against cited abstracts and label it SUPPORT, CONTRADICT or NOINFO with rationale sentences",
     "scientific fact-checking protocol: retrieve abstracts, select rationale sentences, predict a label, evaluate on the dev set"),
    ("a04f8261-a001-4304-b8b1-3206ba39044a", "S15",
     "compile a Markdown agent workflow definition into a sandboxed lock.yml GitHub Actions workflow",
     "author a natural-language workflow, run gh aw compile, and review the generated executable .lock.yml via safe-outputs"),
    ("adf6a524-fc58-4951-b734-3e694e6658e9", "S12",
     "author an AGENTS.md file at the repo root with dev environment tips, testing instructions and PR instructions",
     "give coding agents one predictable instructions file at the repository root; nearest file wins for nested packages"),
    ("c15c20fc-40d2-41e4-a04e-303fe64fbc1e", "S30",
     "drive a browser via stable element refs: open a url, snapshot to get @e1 @e2, click and fill refs, snapshot again",
     "navigate a web page and interact with its elements using stable references, confirming each command with --json"),
    ("3a3d1fb6-d95c-4dca-8eb1-a17d5bbab36a", "S09",
     "strict red-green-refactor TDD loop, deleting any code that was written before its test",
     "test-first development cycle: write a failing test, watch it fail, minimal code to pass, refactor, commit"),
    ("32c834fa-ec8b-4290-b358-436c407fe59f", "S27",
     "generate a single self-contained HTML slide deck with inline CSS and JS and zero dependencies, shareable by URL",
     "produce a shareable HTML presentation from content or a pptx, pick a style preview, render the full deck"),
    ("3d66d551-1c93-4ff6-ad00-a0de78832ba9", "S33",
     "client-side form validation with HTML constraint attributes and the Constraint Validation API, with visible feedback",
     "validate form input in the browser using required/pattern/min/max, setCustomValidity, checkValidity and reportValidity, then also validate server-side"),
    ("19711438-a56f-40a7-8663-0590d966d204", "S11",
     "scaffold an autonomous repo maintenance loop that runs report-only for its first week before it is allowed to act",
     "stand up a daily-triage automation loop with a dry-run period, then promote it to act"),
    ("5ca819d8-a43d-4ebd-931b-00fe5b95fa9f", "S41",
     "query the OpenAlex works API with cursor pagination and a mailto polite-pool parameter until next_cursor is null",
     "programmatically page through every work from OpenAlex filtered by a field, as a good API citizen"),
    ("9cc278f0-a00b-4df4-bbbe-e13def67ccfa", "S42",
     "retrieve a paper plus its citations and references from the Semantic Scholar Graph API with field selection and batch",
     "fetch a paper's citation context via the S2 graph API, paging citations and references and respecting rate-limit headers"),
    ("3103b71b-1af8-4b9a-bf63-41c69cc918a0", "S01",
     "author a new Agent Skill: a folder with a SKILL.md, frontmatter, and Examples and Guidelines sections",
     "create a valid loadable agent skill that a model can discover and follow"),
    ("72f30232-0b43-4351-a666-6dd67ce2af5a", "S04",
     "review UI code against the Web Interface Guidelines and emit file:line findings",
     "audit a frontend implementation for compliance with the current web interface guidelines, terse and actionable"),
    ("28decdd0-0d90-45e3-8187-1e7eafee3abc", "S31",
     "eliminate request waterfalls by hoisting sequential awaits of independent operations into Promise.all",
     "convert serial awaits of independent fetches into parallel ones on both client and server components"),
    ("5acf0d36-e357-4863-b203-b6bc484a604f", "S32",
     "accessibility audit slice: check semantics, focus order, contrast, labels and keyboard, emit file:line a11y findings",
     "run an accessibility-only review of UI code against the web interface guidelines"),
    ("2a5f8316-707f-4bf1-a98b-9c153ca40508", "S34",
     "a compiled GitHub issue auto-triage workflow: cron plus issues trigger, read-only agent proposes labels, safe-outputs add-labels with a bounded budget and one audit discussion",
     "automatically keep issues labeled by type and component, safely, with a capped budget and a noop when nothing to do"),
    ("8b574279-fd5b-4435-af80-47b84c85e291", "S47",
     "debug a running or crashing Kubernetes pod with kubectl describe, kubectl logs, kubectl exec and ephemeral debug containers, then clean up",
     "troubleshoot a crashlooping pod using progressively more invasive kubectl tools including kubectl debug node and copy-to"),
    ("aba29676-3455-40d1-be97-6113f148b5f3", "S14",
     "one rule set across all AI coding tools with commitlint and GitHub Actions CI enforcement referencing STANDARDS.md",
     "share a single standards file across every AI coding assistant and make commit and branch policy machine-enforced in CI"),
    ("651688c2-9f38-48e9-afea-065b1bafca82", "S23",
     "an MCP smart_read tool that caches a file on first read and returns only a diff on later reads of an unchanged file",
     "pay the full token cost of reading a file once, then pay only for the diff on subsequent unchanged reads"),
    ("fef6ceb9-a3c4-4f2c-bcc7-956f6a25d996", "S50",
     "build and evaluate a tool-use task with a DFSDT annotated solution path and ToolEval pass rate and win rate",
     "construct a multi-tool instruction with an annotated depth-first solution tree and score an agent with pass rate and pairwise preference"),
    ("5f1263b5-1f9e-4b71-930b-63f1c28ef10d", "S05",
     "apply React and Next.js performance rules by priority: waterfalls, bundle size, server cache, memoization, JS patterns",
     "systematically optimize a Next.js codebase with the 70 performance rules, highest impact first, with a measured before and after"),
    ("00afa1e7-9c13-462b-8679-24fd160c39a9", "S38",
     "represent a software-engineering agent trajectory: role-tagged conversation, final unified-diff patch, exit status, resolved flag, generated-test signals",
     "define a record shape that makes an SE agent run both replayable and automatically gradable"),
    ("78431387-1ffb-459d-b94a-4b80bd873100", "S07",
     "TDD that first discovers the repository's own test framework, build system and commands before writing any test",
     "test-first workflow using the repo's existing tooling; a bug fix carries a reproduction test that failed before the fix"),
    ("8cd6f567-c269-4b62-beae-b64fd9be66f6", "S03",
     "deploy a project to Vercel as a claimable deployment: tarball, framework detect, upload, preview URL plus claim URL",
     "produce a live Vercel preview URL and a claim URL that transfers the deployment to an account"),
]

# 3 paraphrase pairs called out explicitly in the report (subset of the
# above, chosen for maximally different surface wording).
HIGHLIGHT_PARAPHRASE_PAIRS = ["S46", "S31", "S38"]


def _rank_of(target: str, ordered_ids: list[str]) -> int | None:
    for i, pid in enumerate(ordered_ids):
        if pid == target:
            return i + 1
    return None


async def run_query(pool, scope, query: str, target: str) -> dict:
    res = await search_global(
        pool, query,
        object_types=["procedure"],
        limit=TOP_K,
        scope=scope,
        filters={"require_verified": False},
    )
    procs = res["results"]["procedure"]
    ordered_ids = [p["procedure_id"] for p in procs]
    rank = _rank_of(target, ordered_ids)
    top_5 = [
        {"id": p["procedure_id"], "name": p["name"],
         "score": p.get("similarity_score")}
        for p in procs[:5]
    ]
    return {
        "query": query,
        "target_procedure_id": target,
        "hit": rank is not None,
        "rank": rank,
        "returned": len(procs),
        "top_5": top_5,
    }


async def run_crosstype(pool, scope, query: str) -> dict:
    """Probe whether the search path cross-ranks a Task against a Procedure.
    search_global returns results GROUPED by object_type (documented hard
    rule: retrieval RRF and applicability cascade are kept apart, never
    merged into one cross-type list). We record the shape to prove it."""
    res = await search_global(
        pool, query,
        object_types=["procedure", "task"],
        limit=5,
        scope=scope,
        filters={"require_verified": False},
    )
    return {
        "query": query,
        "result_keys": sorted(res["results"].keys()),
        "grouped_by_object_type": set(res["results"].keys()) == {"procedure", "task"},
        "counts": res["counts"],
        "cross_type_single_list": False,
        "note": (
            "search_global returns a dict of per-object-type buckets, each "
            "ordered by that type's own ranking mechanism. There is no "
            "combined ranked list in which a task could 'outrank' a "
            "procedure -- cross-type ordering is not exercisable on this "
            "search path by design."
        ),
    }


async def main() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set -- aborting (this probe needs live Supabase)")
        sys.exit(2)
    pool = await create_pool(os.environ["DATABASE_URL"], statement_cache_size=0)
    scope = AccessScope.unrestricted()

    primary_results: list[dict] = []
    paraphrase_results: list[dict] = []
    paraphrase_stability: list[dict] = []

    for target, sid, primary_q, para_q in QUERIES:
        p = await run_query(pool, scope, primary_q, target)
        p["source_id"] = sid
        p["kind"] = "primary"
        primary_results.append(p)

        q = await run_query(pool, scope, para_q, target)
        q["source_id"] = sid
        q["kind"] = "paraphrase"
        paraphrase_results.append(q)

        paraphrase_stability.append({
            "source_id": sid,
            "target_procedure_id": target,
            "primary_rank": p["rank"],
            "paraphrase_rank": q["rank"],
            "both_hit_top_10": bool(p["hit"] and q["hit"]),
            "both_hit_top_5": bool(
                (p["rank"] or 99) <= 5 and (q["rank"] or 99) <= 5
            ),
            "rank_delta": (
                None if p["rank"] is None or q["rank"] is None
                else abs(p["rank"] - q["rank"])
            ),
            "highlighted": sid in HIGHLIGHT_PARAPHRASE_PAIRS,
        })

    # cross-type probe on one representative query
    crosstype = await run_crosstype(
        pool, scope,
        "represent a software-engineering agent trajectory so a run is replayable and gradable",
    )

    await pool.close()

    n = len(primary_results)
    hits_at_5 = sum(1 for r in primary_results if r["rank"] and r["rank"] <= 5)
    hits_at_10 = sum(1 for r in primary_results if r["rank"] and r["rank"] <= 10)
    rr = [1.0 / r["rank"] for r in primary_results if r["rank"]]
    mrr = sum(rr) / n if n else 0.0

    para_n = len(paraphrase_results)
    para_hits_at_5 = sum(1 for r in paraphrase_results if r["rank"] and r["rank"] <= 5)
    para_hits_at_10 = sum(1 for r in paraphrase_results if r["rank"] and r["rank"] <= 10)
    para_rr = [1.0 / r["rank"] for r in paraphrase_results if r["rank"]]
    para_mrr = sum(para_rr) / para_n if para_n else 0.0

    both_top5 = sum(1 for s in paraphrase_stability if s["both_hit_top_5"])
    both_top10 = sum(1 for s in paraphrase_stability if s["both_hit_top_10"])
    deltas = [s["rank_delta"] for s in paraphrase_stability if s["rank_delta"] is not None]

    summary = {
        "n_primary_queries": n,
        "n_paraphrase_queries": para_n,
        "procedure_recall_at_5": round(hits_at_5 / n, 4) if n else None,
        "procedure_recall_at_10": round(hits_at_10 / n, 4) if n else None,
        "mean_reciprocal_rank": round(mrr, 4),
        "paraphrase_recall_at_5": round(para_hits_at_5 / para_n, 4) if para_n else None,
        "paraphrase_recall_at_10": round(para_hits_at_10 / para_n, 4) if para_n else None,
        "paraphrase_mrr": round(para_mrr, 4),
        "paraphrase_stability_both_top5": round(both_top5 / n, 4) if n else None,
        "paraphrase_stability_both_top10": round(both_top10 / n, 4) if n else None,
        "paraphrase_mean_abs_rank_delta": round(statistics.mean(deltas), 4) if deltas else None,
        "paraphrase_median_abs_rank_delta": (statistics.median(deltas) if deltas else None),
    }

    out = {
        "_generated_by": ".scratch/corpus_wave/retrieval_eval.py",
        "callable": "app.services.domain_search.search_global(object_types=['procedure'], filters={'require_verified': False})",
        "underlying": "app.services.applicability.find_applicable_procedures -- hard-constraint cascade + similarity/capability RRF",
        "scope": "AccessScope.unrestricted()",
        "top_k": TOP_K,
        "corpus": "28 prior_library procedures ingested Phase 9 (verification_state=candidate)",
        "summary": summary,
        "primary_results": primary_results,
        "paraphrase_results": paraphrase_results,
        "paraphrase_stability": paraphrase_stability,
        "cross_type_probe": crosstype,
    }
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")

    print(f"wrote {OUT}")
    print(json.dumps(summary, indent=2))
    misses = [(r["source_id"], r["query"][:60]) for r in primary_results if not r["hit"]]
    if misses:
        print("\nprimary MISSES (target not in top 10):")
        for sid, q in misses:
            print(f"  {sid}: {q}...")


if __name__ == "__main__":
    asyncio.run(main())
