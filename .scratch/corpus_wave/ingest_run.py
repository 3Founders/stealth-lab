"""
Corpus wave Phase 9 -- canonical ingestion of the 29 ADMITTED procedures
+ the corpus claims into the running (Supabase) substrate.

USES THE EXISTING MACHINERY ONLY:
  - app.services.skill_ingestion.ingest_skill_md  (parse + dedup + embed + capture_procedure, provenance='prior_library')
  - app.services.skill_ingestion._write_task_nodes (one task_nodes row + OWNS/DECOMPOSES_TO edge per step)
  - app.services.claims.capture_claim              (knowledge_nodes 'claim' + real embedding, anchored to one document episode)
No new tables, no new extractor, no ad-hoc procedure/claim INSERTs.

Hand-run wave tooling (like backend/check_*.py), NOT part of pytest tests.
Run from repo root:
  DATABASE_URL=<supabase session pooler> python .scratch/corpus_wave/ingest_run.py
Writes .scratch/corpus_wave/ingest_result.json and prints a summary.

License-blocked sources (S10 S18 S26 S29 S37 S49) are NOT ingested here
(see rejected.json). Their source rows stay in source_registry.jsonl only.

RE-RUN WARNING: not idempotent. ingest_skill_md's novelty check caught only
19/28 procedures as duplicates on a second run (embedding-similarity
threshold), and capture_claim has NO dedup at all. Re-running this file
double-writes claims and can leave duplicate procedure rows. The clean
state after the authoritative run is in ingest_result.json; to re-ingest,
truncate the corpus_wave rows first.
"""
from __future__ import annotations
import asyncio, json, os, re, sys, pathlib

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp1252 console
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.session import create_pool                      # noqa: E402
from app.services.skill_ingestion import ingest_skill_md, _write_task_nodes  # noqa: E402
from app.services.claims import capture_claim               # noqa: E402

CW = pathlib.Path(__file__).resolve().parent
CREATED_BY = "corpus_wave"

_STEP_RE = re.compile(r"^\s*\d+\.\s+(.*\S)")
_SECTION_RE = re.compile(r"^##\s+(.+?)\s*$")


def load_candidate(sid: str) -> dict:
    """Pull goal + numbered steps out of candidates/Sxx.md."""
    txt = (CW / "candidates" / f"{sid}.md").read_text(encoding="utf-8")
    sec, buf = None, {}
    for line in txt.splitlines():
        m = _SECTION_RE.match(line)
        if m:
            sec = m.group(1).lower()
            buf[sec] = []
            continue
        if sec:
            buf.setdefault(sec, []).append(line)
    goal = " ".join(x.strip() for x in buf.get("goal", []) if x.strip())
    steps = []
    for line in buf.get("steps", []):
        sm = _STEP_RE.match(line)
        if sm:
            steps.append(re.sub(r"\s+", " ", sm.group(1)).strip().rstrip("."))
    return {"goal": goal, "steps": steps}


def render_skill_md(sid: str, target: str, goal: str, steps: list[str]) -> str:
    name = f"corpus-{sid.lower()}-{re.sub(r'[^a-z0-9]+', '-', target.lower())[:48].strip('-')}"
    desc = (goal or target).replace("\n", " ").strip()
    lines = ["---", f"name: {name}", f'description: "{desc[:280]}"', "---", ""]
    for i, s in enumerate(steps, 1):
        lines.append(f"{i}. {s}")
    return "\n".join(lines) + "\n"


# Corpus claims to ingest (attribution form preserved; epistemic_status tagged).
# Only from NON-license-blocked admitted sources.
CLAIMS = [
    ("S20", "LLM accuracy degrades non-uniformly as input length grows", "long-context-llm", "accuracy_vs_length", "monotonic-decline", "empirical", "EXPERIMENTAL"),
    ("S20", "Degradation accelerates as needle-question semantic similarity decreases", "niah-extension", "slope_vs_similarity", "steeper-at-low-similarity", "empirical", "EXPERIMENTAL"),
    ("S20", "A single distractor lowers performance vs needle-only; four distractors compound it", "niah-extension", "effect_of_distractors", "non-uniform-compounding", "empirical", "EXPERIMENTAL"),
    ("S20", "Models score higher on shuffled haystacks than logically-structured ones", "niah-extension", "effect_of_coherence", "shuffled-outperforms-structured", "empirical", "EXPERIMENTAL"),
    ("S20", "Focused (~300 tok) prompts beat full (~113k tok) prompts across all models on LongMemEval", "longmemeval", "focused_vs_full", "focused-wins", "empirical", "EXPERIMENTAL"),
    ("S02", "A skill should be admitted only when a benchmark shows it beats baseline and generalizes beyond its eval examples", "skill-authoring", "admission_rule", "benchmark-vs-baseline", "methodological", "SOURCE_DERIVED"),
    ("S07", "Before writing tests, discover the repo's own framework/build/commands", "tdd-in-existing-repo", "precondition", "discover-stack-first", "methodological", "SOURCE_DERIVED"),
    ("S09", "Code written before its corresponding test should be deleted", "tdd", "discipline", "delete-pre-test-code", "methodological", "SOURCE_DERIVED"),
    ("S11", "A maintenance loop should run report-only for its first week before being allowed to act", "autonomous-loop", "rollout_rule", "report-only-then-act", "methodological", "SOURCE_DERIVED"),
    ("S15", "Agent jobs run read-only and sandboxed by default; writes go through scoped safe-outputs jobs", "gh-aw", "safety_posture", "read-only-plus-safe-outputs", "design", "SOURCE_DERIVED"),
    ("S24", "Trimming MCP tool context to task-relevant reduced context ~69% weighted-avg with 100% sufficiency on 5 eligible cases (author benchmark)", "token-optimization", "measured_saving", "69pct-with-sufficiency", "empirical", "EXPERIMENTAL"),
    ("S36", "A code fix is verified iff it flips FAIL_TO_PASS while keeping PASS_TO_PASS green in a pinned environment", "repo-bug-fix", "verification_rule", "test-flip-in-pinned-env", "methodological", "SOURCE_DERIVED"),
    ("S46", "OWASP treats passwords <15 chars as weak without MFA (<8 with MFA), and requires identical error text + constant time for bad-user vs bad-password", "web-auth", "hardening_rule", "length-and-constant-time", "standard", "SOURCE_DERIVED"),
    ("S50", "DFSDT (a depth-first decision tree allowing backtracking) improves LLM planning on multi-tool tasks vs linear planning", "tool-use", "planning_method", "dfsdt-beats-linear", "empirical", "EXPERIMENTAL"),
    ("S33", "Client-side constraint validation is a UX layer only; the same rules must be enforced server-side", "web-forms", "validation_rule", "client-ux-server-authoritative", "standard", "SOURCE_DERIVED"),
]


async def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: set DATABASE_URL (Supabase session pooler)"); return 2
    pool = await create_pool(statement_cache_size=0)
    adm = json.loads((CW / "admitted.json").read_text(encoding="utf-8"))["admitted"]
    procs = [a for a in adm if a["ingest_as"] == "procedure"]

    result = {"procedures": [], "claims": [], "errors": []}

    # 1. procedures + task nodes
    for a in procs:
        sid = a["source_id"]
        try:
            c = load_candidate(sid)
            if not c["steps"]:
                result["errors"].append(f"{sid}: no numbered steps parsed"); continue
            md = render_skill_md(sid, a["procedure_target"], c["goal"], c["steps"])
            r = await ingest_skill_md(pool, md, fallback_name=f"corpus-{sid.lower()}",
                                      created_by=CREATED_BY)
            entry = {"source_id": sid, "status": r["status"]}
            if r["status"] == "captured":
                tn = await _write_task_nodes(
                    pool, procedure_row_id=r["id"], steps=c["steps"],
                    created_by=CREATED_BY, scope_type="global",
                )
                entry.update(procedure_id=r["procedure_id"], version_row_id=r["id"],
                             task_nodes=len(tn))
            else:
                entry.update(existing_procedure_id=r.get("existing_procedure_id"),
                             similarity=r.get("similarity"))
            result["procedures"].append(entry)
            print(f"  {sid:4} {r['status']:9} {a['procedure_target'][:64]}")
        except Exception as e:  # noqa: BLE001 -- one bad candidate must not abort the run
            result["errors"].append(f"{sid}: {type(e).__name__}: {e}")
            print(f"  {sid:4} ERROR     {type(e).__name__}: {e}")

    # 2. one document episode to anchor the claims (provenance, not business logic)
    ep_id = await pool.fetchval(
        "INSERT INTO episodes (episode_type, content, metadata) "
        "VALUES ('document', $1, $2::jsonb) RETURNING id",
        "corpus_wave admitted claim set (S01-S50 wave)",
        json.dumps({"wave": "corpus_wave", "phase": 9, "created_by": CREATED_BY}),
    )
    result["episode_id"] = str(ep_id)
    print(f"\n  episode {ep_id} (anchors {len(CLAIMS)} claims)")

    # 3. claims
    # wave epistemic vocab -> substrate's ClaimProperties.epistemic_status {observed, inferred}
    EPI_MAP = {"EXPERIMENTAL": "observed", "USER_REPORTED": "inferred", "SOURCE_DERIVED": "inferred"}
    for sid, stmt, subj, pred, obj, ctype, epi in CLAIMS:
        try:
            cid = await capture_claim(
                pool, statement=stmt, task_ids=[], justification_episode_id=str(ep_id),
                subject=subj, predicate=pred, object=obj, claim_type=ctype,
                epistemic_status=EPI_MAP[epi], created_by=CREATED_BY, scope_type="global",
                properties={"source_id": sid, "wave": "corpus_wave", "epistemic_origin": epi},
            )
            result["claims"].append({"source_id": sid, "claim_id": str(cid),
                                     "epistemic_status": EPI_MAP[epi], "epistemic_origin": epi})
            print(f"  {sid:4} claim {str(cid)[:8]}  {epi:14} {stmt[:56]}")
        except Exception as e:  # noqa: BLE001
            result["errors"].append(f"{sid} claim: {type(e).__name__}: {e}")
            print(f"  {sid:4} claim ERROR {type(e).__name__}: {e}")

    # 4. what the claim-graph viewer will now see
    try:
        from app.services.claim_graph_api import get_claim_graph_overview
        from app.services.access import AccessScope
        ov = await get_claim_graph_overview(pool, scope=AccessScope.unrestricted(),
                                            link_mode="both", with_status=False)
        result["claim_graph_overview_counts"] = ov.get("counts", {})
        print(f"\n  claim-graph overview: {ov['counts']}")
    except Exception as e:  # noqa: BLE001
        result["errors"].append(f"overview: {type(e).__name__}: {e}")
        print(f"  overview ERROR {type(e).__name__}: {e}")

    (CW / "ingest_result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    cap = sum(1 for p in result["procedures"] if p["status"] == "captured")
    dup = sum(1 for p in result["procedures"] if p["status"] == "duplicate")
    print(f"\nprocedures: {cap} captured / {dup} duplicate ; claims: {len(result['claims'])} ; errors: {len(result['errors'])}")
    for e in result["errors"]:
        print("  ERR", e)
    await pool.close()
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
