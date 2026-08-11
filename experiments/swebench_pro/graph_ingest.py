"""
Ingest every SWE-bench Pro gold patch into the REAL backend graph.

This replaces experiments/swebench_pro/knowledge.py's standalone store for
the memory arm. That store was a faithful port of the RRF arithmetic but it
was a flat Python list -- no task/knowledge split, no edges, no hierarchy,
no bi-temporal validity. A result from it was evidence about rank fusion,
which knowledge.py says plainly in its own docstring. It was never evidence
about this system.

THE ONTOLOGY MAPPING, and why it is this one:

  task_node       one per instance -- the issue as a unit of work.
                  name = title, description = problem statement.
                  This is what a new issue is matched AGAINST.

  knowledge_node  one per instance -- where the fix actually landed:
                  files, symbols, and the patch's hunk headers.
                  This is the durable part. An issue is a one-off; "bugs
                  of this shape live in lib/ansible/plugins/lookup/" is
                  what survives and transfers.

  edge            task --OWNS/RESOLVED_AT--> knowledge

The split is the point. HybridRetriever matches a new issue against
task_nodes by meaning and keyword, then expands one hop along the edge to
pull in the localization knowledge. Retrieval finds the SIMILAR PROBLEM;
graph traversal supplies the ANSWER LOCATION. Flattening both into one node
would work but would collapse the distinction the ontology exists to make,
and there would be nothing for `expand_depth` to do.

LEAVE-ONE-OUT IS DONE WITH THE BI-TEMPORAL COLUMNS, NOT BY RE-INGESTING.

Holding an instance out means setting t_invalid on its two nodes. Every
query path in the backend already filters `t_invalid IS NULL` -- that is
what the column is for -- so the held-out instance becomes invisible to
retrieval without being deleted, and testing a different instance is two
UPDATEs rather than a 40-minute re-embed. Using the truth-maintenance
mechanism to run the experiment is also a live test of it.

One subtlety that is NOT optional: the hierarchy must be rebuilt AFTER the
held-out node is invalidated. Internal nodes route on the mean of their
children's embeddings (hierarchy.py:217), so a tree built while the
held-out leaf was live has that leaf's vector baked into its parent's
routing signal. Small, but it is leakage, and it is the kind that makes a
retrieval number look better than the mechanism deserves. See
graph_memory.py, which rebuilds every time.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import glob
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.db.session import create_pool  # noqa: E402
from app.services.embeddings import to_pgvector  # noqa: E402
from app.services.embed_cache import CachedEmbedder  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pro_harness import strip_binary_hunks  # noqa: E402

CREATED_BY = "swebench_ingest"
SWEBENCH_DSN = "postgresql://postgres:stealthlab@127.0.0.1:5433/stealthlab_swebench"

# Voyage free tier is 3 requests/min AND 10K tokens/min. CachedEmbedder's
# default 7000-token batch at a 21s interval is 20K tokens/min -- it honours
# the request ceiling and blows straight through the token one. 3300/21s is
# 9.4K/min, which is the actual constraint.
MAX_BATCH_TOKENS = 3300
MIN_INTERVAL = 21.0
# Rows committed per database write. Small enough that a kill costs at most
# one chunk, large enough that the writes are not the bottleneck.
CHUNK = 50
# Cap on stored gold-diff text. Uncapped this is 9.7 MB across the corpus and
# one instance alone is 180 KB; capped it is 7.1 MB and no single precedent
# can swamp the store.
MAX_STORED_PATCH = 20_000

_HUNK = re.compile(r"^@@.*@@\s*(.*)$", re.M)
_DIFF_FILE = re.compile(r"^diff --git a/(\S+)", re.M)
_TITLE = re.compile(r"\*\*Title:\s*(.+?)\*\*")


def normalize_statement(text) -> str:
    r"""
    Undo the double-encoding in 391 of the 731 problem statements.

    Those rows contain the two characters backslash-n where a newline was
    meant, so nothing that splits on "\n" ever sees more than a single
    enormous line -- title extraction, and any later chunking, silently
    operate on one 3000-character blob. Fixed here, at the single point
    where the field is read, rather than in each consumer.
    """
    s = str(text or "")
    if "\\n" in s:
        s = s.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    return s.strip()


def title_of(problem_statement: str) -> str:
    """
    Pro problem statements open with a title line, in one of three shapes.

    There is no `issue_title` column -- the dataset's columns are repo,
    instance_id, base_commit, patch, test_patch, problem_statement,
    requirements, interface, repo_language, fail_to_pass, pass_to_pass,
    issue_specificity, issue_categories, before_repo_set_cmd,
    selected_test_files_to_run, dockerhub_tag.

    Deliberately stronger than select_subset.py:57, which handles only the
    `**Title: ...**` shape and falls through to the raw first line for the
    rest. Measured across all 731 instances that produced a bare "Title:"
    for instances whose heading and text sit on separate lines, and left a
    redundant "Title: " prefix on the `### Title: ...` shape. This string
    becomes the task node's `name` -- it is half of the lexical index
    (`name || description`) and the lead line of the embedding text -- so a
    node called "Title:" is one that keyword search can never usefully
    match and whose vector is dominated by boilerplate.
    """
    text = normalize_statement(problem_statement)
    m = _TITLE.search(text)
    if m and m.group(1).strip():
        return m.group(1).strip()[:160]
    for line in text.split("\n"):
        # Strip markdown heading marks, bold and quotes, then drop a leading
        # "Title:" label. A line that is ONLY the label -- `# Title`, with
        # the actual title on the next line -- has to be skipped, not
        # returned: that shape accounts for 52 instances, every one of which
        # would otherwise be a node literally named "Title", colliding with
        # the other 51 in both the lexical index and the vector space.
        cleaned = re.sub(r"^[#*\s\">]+|[\"*\s]+$", "", line)
        cleaned = re.sub(r"^(?:issue\s+)?title\s*\**\s*:?\s*", "", cleaned,
                         flags=re.I).strip()
        if cleaned:
            return cleaned[:160]
    return "(untitled)"


def _json_list(value) -> list[str]:
    """issue_specificity / issue_categories are JSON arrays, not Python reprs."""
    if value is None:
        return []
    try:
        parsed = json.loads(str(value))
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def load_dataset():
    import pandas as pd

    path = glob.glob(os.path.expanduser(
        "~/.cache/huggingface/hub/datasets--ScaleAI--SWE-bench_Pro/"
        "snapshots/*/data/*.parquet"))[0]
    return pd.read_parquet(path)


def _pylist(value) -> list[str]:
    if not value:
        return []
    try:
        parsed = ast.literal_eval(str(value))
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except (ValueError, SyntaxError):
        return []


def patch_facts(patch: str) -> tuple[list[str], list[str]]:
    """
    Files and symbol context extracted from the gold diff.

    Symbols come from the text git puts after `@@ ... @@`, which is the
    enclosing function or class it detected. That is free, language-agnostic
    localization -- no per-language parser to write and keep correct across
    the corpus's four languages.
    """
    files = _DIFF_FILE.findall(patch or "")
    symbols: list[str] = []
    for ctx in _HUNK.findall(patch or ""):
        ctx = ctx.strip()
        if not ctx:
            continue
        name = re.split(r"[({:]", ctx)[0].strip()
        name = name.replace("func ", "").replace("def ", "").replace("class ", "")
        name = name.split()[-1] if name.split() else ""
        if name and name not in symbols:
            symbols.append(name)
    return files, symbols[:12]


def task_description(row) -> str:
    """
    The full issue as a maintainer would have received it: the reported
    problem, the acceptance criteria, and the API contract the fix has to
    satisfy. All three are stored, because this is the text the agent is
    shown when this instance is retrieved as a precedent -- "a similar issue
    was reported, here is what it actually required" is worth more than a
    title.

    Stored but NOT embedded -- see task_embedding_text.
    """
    parts = [normalize_statement(row["problem_statement"])]
    reqs = normalize_statement(row["requirements"])
    if reqs:
        parts.append(f"REQUIREMENTS (what the fix had to satisfy):\n{reqs}")
    iface = normalize_statement(row["interface"])
    if iface:
        parts.append(f"INTERFACE (API surface the fix introduced or changed):\n{iface}")
    return "\n\n".join(parts)


def task_embedding_text(row) -> str:
    """
    Title + problem statement ONLY, deliberately.

    At query time the incoming text is a new issue's problem statement, so
    the vector comparison should be problem-against-problem. `requirements`
    and `interface` describe the SOLUTION -- folding them in would push
    stored issues toward the shape of their own answers and away from the
    shape of the question being asked. They are still ingested, still
    rendered to the agent, and still reachable through the knowledge node's
    own embedding; they just do not distort this comparison.
    """
    return (f"{title_of(row['problem_statement'])}\n\n"
            f"{normalize_statement(row['problem_statement'])[:1500]}")


def joint_embedding_text(row, patch_chars: int = 3000) -> str:
    """
    ONE vector per instance covering the problem AND the change that fixed it.

    The alternative hypothesis to the split representation. The split embeds
    the issue on the task node and the code locations on the knowledge node,
    on the reasoning that a query IS an issue statement so the comparison
    should be problem-against-problem. That reasoning has a hole: it means
    nothing in the retrieval vector knows what the fix actually DID. Two
    issues can read almost identically and be fixed in completely different
    ways, and two issues can read differently and share a fix pattern -- the
    problem-only vector cannot tell those apart, which is exactly the failure
    seen on flipt, where "authenticate" as registry-login outranked
    "authenticate" as request-middleware.

    Folding the diff in makes the vector describe problem-plus-resolution.
    The cost is that the query side stays problem-only, so the two sides are
    no longer symmetric -- which is a real objection, and the reason this
    lives in a SEPARATE column rather than replacing the original. Both can
    be measured against the same corpus.

    The diff is bounded: median gold patch is 7,846 chars and the max is
    179,594, so an unbounded join would let a handful of enormous patches
    dominate their own vectors and swamp the issue text entirely.
    """
    diff = strip_binary_hunks(str(row["patch"] or ""))[:patch_chars]
    return (f"{title_of(row['problem_statement'])}\n\n"
            f"{normalize_statement(row['problem_statement'])[:1200]}\n\n"
            f"--- the change that fixed it ---\n{diff}")


def knowledge_text(repo: str, files: list[str], symbols: list[str], interface: str) -> str:
    """
    What the knowledge node is embedded ON.

    Paths and identifiers, not prose. This node answers "where does this
    kind of change land", so its vector should sit near text that names
    modules and functions -- which is what a problem statement mentioning a
    specific plugin or package actually looks like. `interface` belongs here
    rather than on the task node for the same reason: it is mostly type
    names, method names and paths.
    """
    body = (f"{repo} - code locations\n"
            f"files: {', '.join(files[:20])}\n"
            f"symbols: {', '.join(symbols)}")
    if interface.strip():
        body += f"\ninterface: {interface.strip()[:600]}"
    return body


async def wipe(pool) -> None:
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM edges WHERE created_by = ANY($1)",
                               [CREATED_BY, "hierarchy_builder"])
            await conn.execute("DELETE FROM task_nodes WHERE created_by = ANY($1)",
                               [CREATED_BY, "hierarchy_builder"])
            await conn.execute("DELETE FROM knowledge_nodes WHERE created_by = ANY($1)",
                               [CREATED_BY, "hierarchy_builder"])


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=SWEBENCH_DSN)
    ap.add_argument("--limit", type=int, default=0, help="0 = all 731")
    ap.add_argument("--min-interval", type=float, default=MIN_INTERVAL)
    ap.add_argument("--embed-only", action="store_true",
                    help="fill missing embeddings on an existing ingest and exit")
    ap.add_argument("--joint-embeddings", action="store_true",
                    help="fill task_nodes.embedding_joint with a single vector "
                         "per instance covering issue AND gold diff. Writes a "
                         "SEPARATE column, so the existing `embedding` and any "
                         "run using it are untouched.")
    ap.add_argument("--joint-patch-chars", type=int, default=3000,
                    help="diff budget inside the joint embedding text")
    ap.add_argument("--cache-path", default=None,
                    help="embedding cache file. Give a DISTINCT path when running "
                         "concurrently with another embedding job: CachedEmbedder "
                         "rewrites the whole cache file on every batch, so two "
                         "processes sharing one path silently drop each other's "
                         "entries on the last write.")
    ap.add_argument("--backfill-patches", action="store_true",
                    help="add gold diffs to already-ingested knowledge nodes. "
                         "No re-embedding: the diff is deliberately NOT part of "
                         "the embedding text (queries are issue statements, and "
                         "matching prose against diff syntax only adds noise), "
                         "so the vectors are unchanged and this is a cheap UPDATE.")
    args = ap.parse_args()

    df = load_dataset()
    if args.limit:
        df = df.head(args.limit)
    print(f"{len(df)} instances")

    embedder = CachedEmbedder(min_interval=args.min_interval,
                              cache_path=args.cache_path)
    embedder.MAX_BATCH_TOKENS = MAX_BATCH_TOKENS
    pool = await create_pool(dsn=args.dsn, min_size=1, max_size=4)

    try:
        if args.joint_embeddings:
            by_iid = {r["instance_id"]: r for _, r in df.iterrows()}
            pending = await pool.fetch(
                "SELECT id, skill_ref FROM task_nodes WHERE created_by = $1 "
                "AND embedding_joint IS NULL ORDER BY id", CREATED_BY)
            if not pending:
                print("embedding_joint: already complete")
                return 0
            texts, rows = [], []
            for row in pending:
                src = by_iid.get(row["skill_ref"])
                if src is None:
                    continue
                rows.append(row)
                texts.append(joint_embedding_text(src, args.joint_patch_chars))
            est = sum(len(t) // 4 for t in texts)
            print(f"joint embeddings: {len(rows)} rows (~{est:,} tokens, "
                  f"~{est / 9400:.0f} min at free-tier rate)", flush=True)
            t0 = time.time()
            for start in range(0, len(rows), CHUNK):
                vecs = await embedder.embed(texts[start:start + CHUNK],
                                            input_type="document")
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        for row, vec in zip(rows[start:start + CHUNK], vecs):
                            await conn.execute(
                                "UPDATE task_nodes SET embedding_joint = $2::vector "
                                "WHERE id = $1", row["id"], to_pgvector(vec))
                print(f"  {min(start + CHUNK, len(rows))}/{len(rows)} committed "
                      f"({time.time()-t0:.0f}s)", flush=True)
            left = await pool.fetchval(
                "SELECT count(*) FROM task_nodes WHERE created_by = $1 "
                "AND embedding_joint IS NULL", CREATED_BY)
            print(f"done in {time.time()-t0:.0f}s; {left} still unembedded")
            return 1 if left else 0

        if args.backfill_patches:
            by_iid = {r["instance_id"]: r for _, r in df.iterrows()}
            rows = await pool.fetch(
                "SELECT id, properties->>'instance_id' AS iid FROM knowledge_nodes "
                "WHERE created_by = $1", CREATED_BY)
            n = truncated = 0
            for start in range(0, len(rows), CHUNK):
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        for row in rows[start:start + CHUNK]:
                            src = by_iid.get(row["iid"])
                            if src is None:
                                continue
                            raw = str(src["patch"] or "")
                            await conn.execute(
                                "UPDATE knowledge_nodes SET properties = properties || "
                                "jsonb_build_object('patch', $2::text, "
                                "  'patch_bytes', $3::int, 'patch_truncated', $4::bool) "
                                "WHERE id = $1",
                                row["id"], strip_binary_hunks(raw)[:MAX_STORED_PATCH],
                                len(raw), len(raw) > MAX_STORED_PATCH)
                            n += 1
                            truncated += len(raw) > MAX_STORED_PATCH
                print(f"  {min(start + CHUNK, len(rows))}/{len(rows)}", flush=True)
            stored = await pool.fetchval(
                "SELECT sum(length(properties->>'patch')) FROM knowledge_nodes "
                "WHERE created_by = $1", CREATED_BY)
            print(f"backfilled {n} patches ({truncated} truncated at "
                  f"{MAX_STORED_PATCH:,} chars), {int(stored or 0)/1e6:.1f} MB stored")
            return 0

        if not args.embed_only:
            await wipe(pool)
            print("inserting nodes and edges...")
            t0 = time.time()
            rows = []
            for _, r in df.iterrows():
                files, symbols = patch_facts(str(r["patch"]))
                rows.append((r, files, symbols))

            async with pool.acquire() as conn:
                async with conn.transaction():
                    for r, files, symbols in rows:
                        iid = r["instance_id"]
                        categories = _json_list(r["issue_categories"])
                        specificity = _json_list(r["issue_specificity"])
                        task_id = await conn.fetchval(
                            "INSERT INTO task_nodes "
                            "(name, description, skill_ref, io_schema, "
                            " success_criteria, created_by, provenance) "
                            "VALUES ($1,$2,$3,$4,$5,$6,'company_ingested') RETURNING id",
                            title_of(r["problem_statement"])[:500],
                            task_description(r)[:16000],
                            iid,
                            # The issue's own taxonomy, kept structured rather
                            # than flattened into prose: these are the tags the
                            # Rule 1 postcondition gate compares by Jaccard
                            # overlap, so they have to survive as a list.
                            {"issue_categories": categories,
                             "issue_specificity": specificity,
                             "repo": r["repo"], "language": r["repo_language"]},
                            {"fail_to_pass": _pylist(r["fail_to_pass"])[:20],
                             "postconditions": categories,
                             "n_pass_to_pass": len(_pylist(r["pass_to_pass"]))},
                            CREATED_BY,
                        )
                        kn_id = await conn.fetchval(
                            "INSERT INTO knowledge_nodes "
                            "(node_type, name, properties, created_by, provenance) "
                            "VALUES ('code_location',$1,$2,$3,'company_ingested') "
                            "RETURNING id",
                            knowledge_text(r["repo"], files, symbols,
                                           str(r["interface"] or ""))[:500],
                            {"instance_id": iid, "repo": r["repo"],
                             "language": r["repo_language"],
                             "files": files[:40], "symbols": symbols,
                             "base_commit": r["base_commit"],
                             "interface": str(r["interface"] or "")[:2000],
                             "requirements": str(r["requirements"] or "")[:4000],
                             "issue_categories": categories,
                             "postconditions": categories,
                             # The gold diff itself. Median 7.8 KB, p90 25 KB,
                             # max 180 KB -- capped so a handful of enormous
                             # generated-code patches do not dominate storage.
                             # Binary hunks stripped: they cannot be read or
                             # copied, only wasted.
                             "patch": strip_binary_hunks(
                                 str(r["patch"] or ""))[:MAX_STORED_PATCH],
                             "patch_bytes": len(str(r["patch"] or "")),
                             "patch_truncated": len(str(r["patch"] or "")) > MAX_STORED_PATCH,
                             "n_files": len(files)},
                            CREATED_BY,
                        )
                        await conn.execute(
                            "INSERT INTO edges (edge_type, custom_edge_type, "
                            " source_id, source_table, target_id, target_table, "
                            " properties, created_by, provenance) "
                            "VALUES ('OWNS','RESOLVED_AT',$1,'task_nodes',$2,"
                            " 'knowledge_nodes',$3,$4,'company_ingested')",
                            task_id, kn_id, {"instance_id": iid}, CREATED_BY,
                        )
            print(f"inserted {len(rows)} task+knowledge pairs in {time.time()-t0:.1f}s")

        # Embedding text is re-derived from the dataset rather than read back
        # out of the row, because the two are deliberately different:
        # `description` now carries requirements and interface as well, and
        # embedding that would undo the split task_embedding_text() exists to
        # make. Re-deriving also means --embed-only produces byte-identical
        # text to the first pass, so the disk cache actually hits.
        by_iid = {r["instance_id"]: r for _, r in df.iterrows()}

        def _text_for(table: str, row) -> str:
            iid = (row["skill_ref"] if table == "task_nodes"
                   else (row["properties"] or {}).get("instance_id"))
            src = by_iid.get(iid)
            if src is None:
                return row["name"]  # ingested from a dataset slice we no longer have
            if table == "task_nodes":
                return task_embedding_text(src)
            files, symbols = patch_facts(str(src["patch"]))
            return knowledge_text(src["repo"], files, symbols, str(src["interface"] or ""))

        # Embedding is separate from insertion so a rate-limit stall is
        # resumable: the rows exist, the cache holds what already succeeded,
        # and --embed-only picks up exactly the NULLs that remain.
        for table in ("task_nodes", "knowledge_nodes"):
            id_col = "skill_ref" if table == "task_nodes" else "properties"
            pending = await pool.fetch(
                f"SELECT id, name, {id_col} FROM {table} "
                f"WHERE created_by = $1 AND embedding IS NULL ORDER BY id", CREATED_BY)
            if not pending:
                print(f"{table}: already fully embedded")
                continue
            texts = [_text_for(table, r) for r in pending]
            est = sum(len(t) // 4 for t in texts)
            print(f"{table}: embedding {len(pending)} rows (~{est:,} tokens, "
                  f"~{est / 9400:.0f} min at free-tier rate)...", flush=True)
            t0 = time.time()
            # Committed in chunks rather than one write at the end. A 30-minute
            # throttled embed that only touches the database on the final line
            # loses every row if the process is killed at minute 29 -- which is
            # exactly what happened on the first attempt. The embedder's disk
            # cache made the API calls recoverable, but the DB still read zero,
            # and "cache full, table empty" is a confusing state to land in.
            # Chunking makes progress durable on both sides.
            for start in range(0, len(pending), CHUNK):
                rows = pending[start:start + CHUNK]
                vectors = await embedder.embed(texts[start:start + CHUNK],
                                               input_type="document")
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        for row, vec in zip(rows, vectors):
                            await conn.execute(
                                f"UPDATE {table} SET embedding = $2::vector WHERE id = $1",
                                row["id"], to_pgvector(vec))
                done = min(start + CHUNK, len(pending))
                print(f"  {done}/{len(pending)} committed "
                      f"({time.time()-t0:.0f}s elapsed)", flush=True)
            print(f"  done in {time.time()-t0:.0f}s  stats={embedder.stats()}", flush=True)

        counts = await pool.fetchrow(
            "SELECT (SELECT count(*) FROM task_nodes WHERE created_by=$1) tn,"
            " (SELECT count(*) FROM task_nodes WHERE created_by=$1 AND embedding IS NULL) tn_null,"
            " (SELECT count(*) FROM knowledge_nodes WHERE created_by=$1) kn,"
            " (SELECT count(*) FROM knowledge_nodes WHERE created_by=$1 AND embedding IS NULL) kn_null,"
            " (SELECT count(*) FROM edges WHERE created_by=$1) e", CREATED_BY)
        print(f"\ntask_nodes={counts['tn']} (unembedded {counts['tn_null']})  "
              f"knowledge_nodes={counts['kn']} (unembedded {counts['kn_null']})  "
              f"edges={counts['e']}")
        if counts["tn_null"] or counts["kn_null"]:
            print("INCOMPLETE: rerun with --embed-only to fill the rest. Retrieval "
                  "with NULL embeddings silently degrades to lexical-only.")
            return 1
        Path(__file__).parent.joinpath("graph_manifest.json").write_text(
            json.dumps({"task_nodes": counts["tn"], "knowledge_nodes": counts["kn"],
                        "edges": counts["e"], "model": embedder.model,
                        "dsn": args.dsn}, indent=2), encoding="utf-8")
        print("wrote graph_manifest.json")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
