"""
Local-first retrieval hierarchy (ticket 14, memory-substrate map).
Builds on HybridRetriever (retrieval.py) rather than replacing it --
that module's RRF-fused semantic+lexical search plus bounded graph
expansion is the "semantic retrieval" tier this ticket's hierarchy
composes with, not something to duplicate.

THE CENTRAL DECISION, stated once here rather than re-derived at every
call site: tiers compose by UNION with weighted fusion, NOT a strict
filter cascade -- the opposite composition rule from ticket 12's
applicability.py, deliberately. A violated precondition (ticket 12) is
a disqualification; a low structural-locality score (here) is a weak
signal. The two mechanisms must not be unified, per both tickets'
explicit cross-reference to each other.

SIGNAL CLASSIFICATION, per ticket 14's resolved answer -- split by how
precisely each signal was derived, not by which "tier" it conceptually
belongs to:

| Signal                          | Role   | Why                              |
|----------------------------------|--------|-----------------------------------|
| Current working set (open files) | FILTER | high precision, moderate recall  |
| Relevant symbols (defs/calls)    | FILTER | high precision AND recall        |
| Import-derived dependency edges  | FILTER | deterministic derivation         |
| Related tests                   | FILTER | high precision for impl tasks    |
| Name-resolved call-graph edges   | RANK   | ~75-85% precision -- imprecise   |
| Recency (recent commits)         | RANK   | moderate precision, low recall   |
| Recent failures                  | RANK   | moderate precision, low recall   |
| Semantic (RRF hybrid)            | RANK   | HybridRetriever, unchanged       |

HONEST SCOPE -- real vs unwired signals, updated as each gets a real
producer (all but one now have one):

- **Current working set**: REAL -- observations.py's `file_touched`
  observations (ticket 04), joined through observation_events ->
  trace_events by session_id. Producer: get_current_working_set().
- **Recent commits**: REAL, same data source (`commit_made`
  observations). Producer: get_recent_commit_files().
- **Related tests**: REAL -- app/services/related_tests.py, naming-
  convention discovery checked against a real repo checkout on disk.
  Producer: related_test_files_for_many().
- **Relevant symbols**: REAL -- wraps code_index.py's own outline()
  directly (byte-exact symbol extraction, already existed for a
  different purpose). Producer: get_relevant_symbols().
- **Import-derived dependency edges**: REAL -- app/services/import_deps.py,
  new tree-sitter-based import extraction with real filesystem
  resolution for Python and relative JS/TS imports (Go and bare JS/TS
  specifiers are returned as honest, unresolved raw strings -- see that
  module's own docstring for why full resolution isn't attempted).
  Producer: import_targets_for_many().
- **Name-resolved call-graph edges**: REAL -- wraps call_graph.py's own
  build_repo_symbol_index + seeds_in_file + reachable_symbols, seeded
  from open_files. Producer: get_call_graph_ranked_names(). Genuinely
  more expensive than the other producers (a repo-wide symbol index
  build) -- see that function's own docstring for the caching caveat.
- **Recent failures**: NOT wired -- confirmed by directly reading
  failure_capture.py, not assumed: `capture_failure()`'s real schema
  has NO file-path field at all (instance_id/repo/arm/reason/
  last_evidence are all free text). Linking a failure to "which files
  are relevant to avoid repeating it" needs real schema/design work
  this pass doesn't invent.

All five real producers above are still CALLER-INVOKED, not automatic
inside retrieve_local_first() itself -- that function takes an already-
built StructuralContext, it doesn't accept a repo root or session_id
and derive one internally. This keeps local_retrieval.py itself a pure
DB-service module (no filesystem coupling), matching retrieval.py's own
existing boundary; a caller with both a live session and a real repo
checkout (the natural case once ticket 15's deferred scheduler-strategy
piece exists) calls the producers itself and passes the result in.

Reranking is explicitly out of milestone 1 (ticket 14's own decision --
marginal gain too small against an already-strong first stage). Not
present here or anywhere in this module.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

import asyncpg

from app.services.access import AccessScope
from app.services.retrieval import HybridRetriever, RetrievedNode, RetrievalResult

# Ticket 14's own number: "a knee at ~8k tokens for code tasks, beyond
# which reported degradation is severe." Configuration, not a literal
# buried in logic -- same discipline as ticket 13's borrowed constants.
DEFAULT_TOKEN_BUDGET = 8_000

# HONEST APPROXIMATION: no tokenizer dependency exists anywhere in this
# codebase (confirmed by search) and adding one for a single budget
# calculation is a real cost against a well-known, defensible estimate.
# ~4 characters per token is the standard rough English-text ratio; this
# is an approximation stated as such, not presented as an exact count.
CHARS_PER_TOKEN_ESTIMATE = 4

# Priority order for token-budgeted fill, per ticket 14's resolved
# answer verbatim: "structural > temporal > causal > semantic."
TIER_PRIORITY = ("structural", "temporal", "causal", "semantic")


@dataclass
class StructuralContext:
    """
    Caller-supplied structural signals -- this module fuses/filters with
    them, it does not compute most of them (see module docstring for
    which fields are real vs. caller-populated in this pass).
    """
    open_files: list[str] = field(default_factory=list)             # FILTER, real
    relevant_symbols: list[str] = field(default_factory=list)       # FILTER, real (see get_relevant_symbols)
    import_deps: list[str] = field(default_factory=list)            # FILTER, real (see app/services/import_deps.py)
    related_tests: list[str] = field(default_factory=list)          # FILTER, real (see related_tests.related_test_files_for_many)
    call_graph_ranked_names: list[str] = field(default_factory=list)  # RANK, real (see get_call_graph_ranked_names)
    recent_commit_files: list[str] = field(default_factory=list)    # RANK, real (see get_recent_commit_files)
    recent_failure_files: list[str] = field(default_factory=list)   # RANK, NOT wired -- see module docstring

    def has_any_filter(self) -> bool:
        return bool(self.open_files or self.relevant_symbols
                    or self.import_deps or self.related_tests)

    def has_any_rank_boost(self) -> bool:
        return bool(self.call_graph_ranked_names or self.recent_commit_files
                    or self.recent_failure_files)


async def get_current_working_set(
    pool: asyncpg.Pool, *, session_id: str, limit: int = 50,
) -> list[str]:
    """
    Real, DB-backed "current working set" signal -- distinct file paths
    from `file_touched` observations (ticket 04) for this session, most
    recently touched first. This is the real producer for
    StructuralContext.open_files; a caller with a live session should
    call this rather than leaving open_files empty.
    """
    rows = await pool.fetch(
        """
        SELECT o.properties->>'file_path' AS file_path, MAX(o.extracted_at) AS last_touched
        FROM observations o
        JOIN observation_events oe ON oe.observation_id = o.id
        JOIN trace_events te ON te.id = oe.event_id
        WHERE te.session_id = $1 AND o.observation_type = 'file_touched'
          AND o.properties->>'file_path' IS NOT NULL
        GROUP BY o.properties->>'file_path'
        ORDER BY last_touched DESC
        LIMIT $2
        """,
        session_id, limit,
    )
    return [r["file_path"] for r in rows]


async def get_recent_commit_files(
    pool: asyncpg.Pool, *, session_id: str, limit: int = 20,
) -> list[str]:
    """
    Real, DB-backed "recent commits" soft-ranking signal (ticket 14:
    "moderate precision, low recall -- soft ranking / tiebreaker only").
    Derived from `commit_made` observations' own recorded file touches
    within the same session, not from git log directly -- this module
    has no filesystem access, same boundary discipline as the rest of
    this file. Returns the SAME kind of value as open_files (file
    paths) so both can be compared against retrieved node names/paths
    uniformly by the caller.
    """
    rows = await pool.fetch(
        """
        SELECT DISTINCT fo.properties->>'file_path' AS file_path
        FROM observations c
        JOIN observation_events coe ON coe.observation_id = c.id
        JOIN trace_events cte ON cte.id = coe.event_id
        JOIN trace_events fte ON fte.session_id = cte.session_id
        JOIN observation_events foe ON foe.event_id = fte.id
        JOIN observations fo ON fo.id = foe.observation_id
        WHERE cte.session_id = $1 AND c.observation_type = 'commit_made'
          AND fo.observation_type = 'file_touched'
          AND fo.extracted_at <= c.extracted_at
          AND fo.properties->>'file_path' IS NOT NULL
        ORDER BY file_path
        LIMIT $2
        """,
        session_id, limit,
    )
    return [r["file_path"] for r in rows]


def get_relevant_symbols(root: str, rel_paths: list[str]) -> list[str]:
    """
    Real, filesystem-backed "relevant symbols" FILTER signal (ticket 14:
    "high precision AND recall" -- the strongest of the four filter
    signals in its own table). Wraps code_index.outline() directly --
    every top-level (and class-nested) symbol NAME defined in each given
    file, real and byte-exact per that module's own extraction, not
    guessed. Union across `rel_paths`, deduplicated, in call order.

    Host-side/filesystem-only, same boundary discipline as call_graph.py/
    related_tests.py/import_deps.py -- no DB, needs a real repo checkout.
    """
    from app.services import code_index

    seen: set[str] = set()
    result: list[str] = []
    for rel_path in rel_paths:
        full = os.path.join(root, rel_path)
        if not os.path.isfile(full):
            continue
        try:
            with open(full, "rb") as f:
                source = f.read()
        except OSError:
            continue
        symbols = code_index.outline(source, rel_path)
        if not symbols:
            continue
        for s in symbols:
            if s.name not in seen:
                seen.add(s.name)
                result.append(s.name)
    return result


def get_call_graph_ranked_names(
    root: str, open_files: list[str], *, max_hops: int = 2, max_nodes: int = 200,
) -> list[str]:
    """
    Real, filesystem-backed "name-resolved call-graph edges" RANK signal
    (ticket 14: "~75-85% precision -- imprecise, rank don't filter").
    Wraps call_graph.py's own build_repo_symbol_index + seeds_in_file +
    reachable_symbols -- this function does not reimplement any
    reachability logic, only seeds it from `open_files` (the same
    current-working-set signal open_files already represents) and
    returns the real FILES call_graph.py's BFS reached.

    Genuinely more expensive than the other producers here (a full
    repo-wide symbol index build) -- call_graph.py's own MAX_INDEX_FILES/
    MAX_INDEX_SECONDS bounds already cap that cost; this function adds
    no further bound of its own; a caller retrieving on every turn of a
    long-running agent should consider caching build_repo_symbol_index's
    result across calls rather than calling this function directly each
    time (not done here -- this is a thin, honest wrapper, not a cache).
    """
    from app.services import call_graph

    if not open_files:
        return []
    index = call_graph.build_repo_symbol_index(root)
    seeds: list[tuple[str, str]] = []
    for rel_path in open_files:
        seeds.extend(call_graph.seeds_in_file(root, rel_path))
    if not seeds:
        return []
    reachability = call_graph.reachable_symbols(
        seeds, root, index, max_hops=max_hops, max_nodes=max_nodes,
    )
    return reachability.files


async def assemble_structural_context(
    pool: asyncpg.Pool,
    *,
    session_id: Optional[str] = None,
    repo_root: Optional[str] = None,
    seed_files: Optional[list[str]] = None,
) -> StructuralContext:
    """
    The caller-facing orchestrator the module docstring's own "HONEST
    SCOPE" section names as missing: every producer above already
    existed and was independently tested, but nothing assembled them
    into one StructuralContext for a real (session_id, repo_root) pair.
    retrieve_local_first() itself deliberately stays DB-only per this
    module's own boundary discipline -- this function is the one place
    that boundary is allowed to be crossed, since it's explicitly the
    caller-side assembly step the docstring already anticipated.

    COLD START, stated plainly rather than glossed over: open_files
    comes from get_current_working_set(), which reads file_touched
    observations for `session_id`. A session's first retrieval, by
    definition, has no observations yet -- and get_call_graph_ranked_
    names() early-returns on empty open_files (local_retrieval.py's own
    producer), so without a seed the ENTIRE structural tier is empty on
    turn one, degrading silently to semantic-only.

    `seed_files` exists for exactly that gap -- e.g. `git diff --name-
    only HEAD` from a real checkout. Deliberately named "seed", not
    folded into open_files: ticket 14 classifies the working set as a
    FILTER signal specifically BECAUSE it's session-scoped (high
    precision). A git-derived seed is repo-scoped -- two concurrent
    sessions on the same checkout get the same seed, and it measures
    "what's uncommitted here" rather than "what this session is doing".
    Smuggling a lower-precision signal into a FILTER slot is exactly the
    criterion-compensation mistake ticket 12 and this module's own
    header warn against for the opposite pairing (similarity outscoring
    a hard constraint) -- the same logic applies in reverse here.
    `seed_files` therefore ONLY seeds the three filesystem producers
    (relevant_symbols / import_deps / related_tests), which each
    re-derive their own real, high-precision output from whatever files
    they're pointed at. It never enters open_files and is never itself a
    FILTER candidate.

    Every field left at its default (empty list) if the precondition for
    computing it doesn't hold (no session_id -> no DB-backed fields; no
    repo_root -> no filesystem-backed fields) -- an honestly-empty
    StructuralContext, same discipline as the rest of this module.
    """
    open_files: list[str] = []
    recent_commit_files: list[str] = []
    if session_id:
        open_files = await get_current_working_set(pool, session_id=session_id)
        recent_commit_files = await get_recent_commit_files(pool, session_id=session_id)

    filesystem_seed = open_files or list(seed_files or [])

    relevant_symbols: list[str] = []
    import_deps: list[str] = []
    related_tests: list[str] = []
    call_graph_ranked_names: list[str] = []
    if repo_root and filesystem_seed:
        # REAL FIX, found by measuring rather than assuming: these four
        # producers are synchronous, filesystem/tree-sitter-bound calls
        # (get_call_graph_ranked_names alone measured at 4.2s against
        # this repo -- call_graph.py's own MAX_INDEX_SECONDS caps it at
        # 20s, not milliseconds). Calling them directly, unawaited,
        # inside this async function blocks the ENTIRE event loop for
        # that whole duration -- and since the MCP server this feeds
        # (server.py's solve_task) runs with `--workers 1`, that is not
        # "one slow request", it is the WHOLE SERVER going unresponsive
        # to every concurrent user for several real seconds, once per
        # solve_task call. run_in_executor() moves each call to a real
        # OS thread, off the event loop, fixing that.
        #
        # SEQUENTIAL awaits, deliberately NOT asyncio.gather()-ed
        # concurrently -- measured, not assumed: gathering all four at
        # once (they don't depend on each other's output, so this
        # looked like a free wall-clock win) actually made the event
        # loop WORSE, not better -- 1 heartbeat fired during a ~4s run
        # with 4 CPU-bound tree-sitter threads contending for the GIL
        # at once, versus 19 heartbeats with the exact same 4 calls run
        # one at a time. CPython's GIL means "off the event loop thread"
        # is not the same as "not competing with it" -- a single
        # CPU-heavy worker thread interleaves fine with the main
        # thread's event loop, but several at once starve it far more
        # than the wall-clock savings are worth. Confirmed directly with
        # both versions before choosing this one, not decided from
        # theory.
        from app.services.import_deps import import_targets_for_many
        from app.services.related_tests import related_test_files_for_many

        loop = asyncio.get_event_loop()
        relevant_symbols = await loop.run_in_executor(
            None, get_relevant_symbols, repo_root, filesystem_seed)
        import_deps = await loop.run_in_executor(
            None, import_targets_for_many, repo_root, filesystem_seed)
        related_tests = await loop.run_in_executor(
            None, related_test_files_for_many, repo_root, filesystem_seed)
        # Only real session-observed open_files seed the call graph --
        # see COLD START above: a git-derived seed is repo-scoped, and
        # get_call_graph_ranked_names's own docstring says it seeds from
        # "the current-working-set signal open_files already
        # represents" -- feeding it a repo-scoped seed would launder
        # that same lower-precision signal one hop further downstream.
        if open_files:
            call_graph_ranked_names = await loop.run_in_executor(
                None, get_call_graph_ranked_names, repo_root, open_files)

    return StructuralContext(
        open_files=open_files,
        relevant_symbols=relevant_symbols,
        import_deps=import_deps,
        related_tests=related_tests,
        call_graph_ranked_names=call_graph_ranked_names,
        recent_commit_files=recent_commit_files,
    )


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN_ESTIMATE)


def _node_render(n: RetrievedNode) -> str:
    kind = "task" if n.table == "task_nodes" else "knowledge"
    line = f"[{kind}:{n.id}] {n.name}"
    if n.description:
        line += f" — {n.description}"
    if n.hops > 0:
        line += f" (related, {n.hops} hop{'s' if n.hops > 1 else ''} away)"
    return line


@dataclass
class AssembledContext:
    text: str
    included_node_ids: list[UUID]
    excluded_node_ids: list[UUID]
    estimated_tokens: int
    token_budget: int
    tiers_included: dict[str, int]  # tier name -> count of nodes actually included


def assemble_context(
    tiers: list[tuple[str, list[RetrievedNode]]],
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> AssembledContext:
    """
    Ticket 14's token-budgeted, priority-ordered fill -- "denominated in
    tokens (not items, not FLOPs)... filled in priority order structural
    > temporal > causal > semantic, truncating at the cap rather than
    degrading silently." This is what makes "context stays approximately
    local as memory grows" an enforced property rather than an
    aspiration, per the ticket's own framing.

    `tiers`: list of (tier_name, nodes) -- tier_name should be one of
    TIER_PRIORITY, but an unrecognized name is not an error (appended
    after the known tiers, in the order given, rather than silently
    dropped -- a caller experimenting with a new tier should not lose
    its output for not having updated this constant).

    A node appearing in more than one tier is rendered ONCE, counted
    against whichever tier fills it first (priority order) -- ticket 14
    explicitly treats this as a union, not per-tier duplication.
    """
    order = {name: i for i, name in enumerate(TIER_PRIORITY)}
    ordered_tiers = sorted(tiers, key=lambda t: order.get(t[0], len(TIER_PRIORITY)))

    seen: set[UUID] = set()
    included_lines: list[str] = []
    included_ids: list[UUID] = []
    excluded_ids: list[UUID] = []
    tiers_included: dict[str, int] = {}
    used_tokens = 0

    for tier_name, nodes in ordered_tiers:
        count_this_tier = 0
        for n in nodes:
            if n.id in seen:
                continue
            seen.add(n.id)
            line = _node_render(n)
            line_tokens = _estimate_tokens(line)
            if used_tokens + line_tokens > token_budget:
                excluded_ids.append(n.id)
                continue
            included_lines.append(line)
            included_ids.append(n.id)
            used_tokens += line_tokens
            count_this_tier += 1
        if count_this_tier:
            tiers_included[tier_name] = count_this_tier

    return AssembledContext(
        text="\n".join(included_lines),
        included_node_ids=included_ids,
        excluded_node_ids=excluded_ids,
        estimated_tokens=used_tokens,
        token_budget=token_budget,
        tiers_included=tiers_included,
    )


def _path_matches(node_name: str, node_description: Optional[str], candidates: list[str]) -> bool:
    """A node "matches" a structural filter candidate if the candidate
    (a file path or symbol name) appears as a substring of the node's
    own name or description. Deliberately simple substring matching,
    not a path-parsing library -- task_nodes/knowledge_nodes carry free
    text names, not structured path fields, so this is the same kind of
    best-effort text match retrieval.py's own lexical search already
    relies on, not a new, more fragile mechanism."""
    haystack = (node_name or "") + " " + (node_description or "")
    return any(c and c in haystack for c in candidates)


async def _tier_candidates_by_path(
    pool: asyncpg.Pool, candidates: list[str], *, scope: Optional[AccessScope],
    matched_by_label: str, limit: int = 25,
) -> list[RetrievedNode]:
    """
    Real, independent tier retrieval -- NOT a filter/boost on the
    semantic tier's own results. Queries task_nodes/knowledge_nodes
    directly by substring match against `candidates` (file paths), so a
    node the semantic (RRF) tier never surfaced at all can still enter
    the union through this tier -- the actual point of "union with
    weighted fusion, not a strict cascade" (ticket 14): a signal source
    finding something the others missed must be able to contribute it,
    not merely re-rank what another source already found.

    Score is a flat, deliberately-low constant (0.05, well below a
    typical real RRF score) -- these results are ordered by tier
    priority in assemble_context, not by this score directly; the score
    only matters for stable secondary sorting within one tier's own
    candidate list.
    """
    if not candidates or scope is None:
        scope = scope or AccessScope.unrestricted()
    from app.services.access import visibility_predicate
    found: list[RetrievedNode] = []
    for table, desc_col in (("task_nodes", "description"), ("knowledge_nodes", "NULL")):
        vis_sql, vis_params = visibility_predicate(scope, param_index=2)
        rows = await pool.fetch(
            f"SELECT id, name, {desc_col} AS description FROM {table} "
            f"WHERE t_invalid IS NULL AND {vis_sql} "
            f"AND (name ILIKE ANY($1::text[]) "
            f"OR ({desc_col} IS NOT NULL AND {desc_col} ILIKE ANY($1::text[]))) "
            f"LIMIT {limit}",
            [f"%{c}%" for c in candidates if c], *vis_params,
        )
        for row in rows:
            found.append(RetrievedNode(
                id=row["id"], table=table, name=row["name"],
                description=row["description"], score=0.05,
                matched_by=[matched_by_label], hops=0,
            ))
    return found


async def retrieve_local_first(
    pool: asyncpg.Pool,
    query: str,
    *,
    embedder=None,
    scope: Optional[AccessScope] = None,
    structural: Optional[StructuralContext] = None,
    top_k: int = 6,
    expand_depth: int = 1,
    max_context_nodes: int = 25,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> AssembledContext:
    """
    The real ticket-14 pipeline: three genuinely independent tiers,
    UNIONED (a node any one tier finds can enter the result -- this is
    what makes it a union rather than a cascade), then assembled under
    the token budget in priority order (structural > temporal > causal
    > semantic).

    - **structural tier**: real nodes matched directly against
      structural.open_files (+ relevant_symbols/import_deps/
      related_tests, when a caller has populated them) -- queried
      independently of the semantic search, so a structurally-relevant
      node the RRF hybrid never ranked highly still gets in.
    - **temporal tier**: real nodes matched against
      structural.recent_commit_files -- same independent-query
      treatment, lower priority than structural per the fixed order.
    - **semantic tier**: HybridRetriever's own RRF-fused result,
      unchanged -- this function does not reimplement or replace it.
      call_graph_ranked_names/recent_failure_files (when populated)
      apply as a small SCORE BOOST within this tier only, per ticket
      14's classification of those specific signals as soft ranking
      features rather than independently-queryable filters (they name
      SYMBOLS/imprecise associations, not retrievable node identity the
      way a file path naturally maps to a node's own name/description).

    No "causal" tier is populated in this pass -- ticket 14 names it in
    the priority order but this repo has no wired producer for a
    genuinely causal (not just temporal) signal yet; assemble_context
    handles an empty or absent tier name without needing a placeholder.
    """
    structural = structural or StructuralContext()
    retriever = HybridRetriever(pool, embedder=embedder, scope=scope)
    semantic_result: RetrievalResult = await retriever.retrieve(
        query, top_k=top_k, expand_depth=expand_depth, max_context_nodes=max_context_nodes,
    )
    semantic_nodes = list(semantic_result.nodes)

    if structural.call_graph_ranked_names or structural.recent_failure_files:
        rank_candidates = structural.call_graph_ranked_names + structural.recent_failure_files
        boosted = []
        for n in semantic_nodes:
            boost = 0.01 if _path_matches(n.name, n.description, rank_candidates) else 0.0
            boosted.append(RetrievedNode(
                id=n.id, table=n.table, name=n.name, description=n.description,
                score=n.score + boost, matched_by=n.matched_by, hops=n.hops,
            ))
        semantic_nodes = sorted(boosted, key=lambda n: (n.hops, -n.score))

    structural_candidates = (
        structural.open_files + structural.relevant_symbols
        + structural.import_deps + structural.related_tests
    )
    structural_nodes = (
        await _tier_candidates_by_path(
            pool, structural_candidates, scope=scope, matched_by_label="structural",
        )
        if structural_candidates else []
    )

    temporal_nodes = (
        await _tier_candidates_by_path(
            pool, structural.recent_commit_files, scope=scope, matched_by_label="temporal",
        )
        if structural.recent_commit_files else []
    )

    return assemble_context(
        [
            ("structural", structural_nodes),
            ("temporal", temporal_nodes),
            ("semantic", semantic_nodes),
        ],
        token_budget=token_budget,
    )
