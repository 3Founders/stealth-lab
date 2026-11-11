"""Materialize a tool card as a one-step Way (procedure) for its Goal (docs/goal_router.md).

The model is Goal -> Way -> Tool, where the tool card is the binding target of a way's step. The
catalog today has the inverse: 125k card->goal edges in `tool_card_goals` and no `procedures`, so a
Goal shows an empty `ways` list and a flat `tools` list. This script writes the missing layer: one
procedure per (card, goal), whose single step is bound to that card.

    python scripts/materialize_tool_procedures.py --dry-run
    python scripts/materialize_tool_procedures.py --apply --limit-per-goal 20
    python scripts/materialize_tool_procedures.py --apply --all
    python scripts/materialize_tool_procedures.py --apply --goal web
    python scripts/materialize_tool_procedures.py --apply --replace

It is idempotent: `source_key = 'tool_card:<goal>:<card>'` identifies the row, and a re-run updates
it in place. `--replace` deletes the generated rows first (only `provenance='tool_card'`). By default
it takes the top 20 cards per Goal (the ways the router shows); `--all` materializes the whole runnable
library -- every card->goal edge whose card has a connector and a tool -- in one bulk insert. The
router still shows only the top 5 per Goal, so the full library is stored for trust/lifecycle and the
comparison harness without flooding the list. `--all` keeps the curated top-20 rows (`DO NOTHING`).

The step binding is `{kind: 'tool', tool: <name>, server_url: <card connector>}`. Cards without a
connector URL (not gateway-callable) are skipped: a way whose binding cannot run is worse than no way.

CAUTION: these procedures are `availability='active'` so the router shows them. The comparison
procedure runner (`scripts/compare/procedure_runner.py`) recognises a one-step tool procedure and
runs it through the same `gate.authorize()` + adapter/gateway path as the tool arm, so it does not
bypass the gate. A multi-step procedure that binds a tool still goes through
`app/execution/step_binding.execute_node` (whose McpToolAdapter calls the MCP server directly); there
are none today, and adding one needs the same gate guard.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values, load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")   # the tool-goal namespace
VERB_BONUS = {"search": 3, "query": 3, "scan": 3, "fetch": 2, "check": 2,
              "analyze": 2, "review": 2, "get": 1, "list": 1, "create": 0}


def choose_tool(tools: list, goal_text: str) -> str:
    """The card's tool that best matches the Goal: shared words first, then an action verb.

    `tools[0]` is not the primary tool (OSV's first is `osv_list_ecosystems`, not `osv_query_package`),
    so a one-step way bound to it would run the wrong call.
    """
    import re

    words = set(re.findall(r"[a-z0-9]+", str(goal_text or "").lower()))
    best, best_score = "", -1.0
    for tool in tools:
        name = str(tool or "")
        tokens = set(re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", name).lower()))
        score = 2.0 * len(tokens & words) + max((VERB_BONUS.get(t, 0) for t in tokens), default=0)
        if score > best_score:
            best, best_score = name, score
    return best or (str(tools[0]) if tools else "")


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""


def procedure_id(goal_id: str, card_id: str) -> uuid.UUID:
    return uuid.uuid5(NS, f"kell:tool-procedure:{goal_id}:{card_id}")


def _extra(row) -> dict:
    raw = row["extra"]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raw = {}
    return raw or {}


_RANKED = """
    SELECT cg.card_id, cg.goal_id, cg.score, t.name, t.title, t.server, t.tools, t.extra,
           row_number() OVER (PARTITION BY cg.goal_id
                              ORDER BY cg.score DESC,
                                       NULLIF(t.extra->>'stars', '')::int DESC NULLS LAST,
                                       t.id) AS rn
    FROM tool_card_goals cg
    JOIN tool_cards t ON t.id = cg.card_id
    WHERE t.extra ? 'connector_url' AND COALESCE(array_length(t.tools, 1), 0) > 0
      AND ($1::text IS NULL OR cg.goal_id::text = $1
           OR EXISTS (SELECT 1 FROM goals gg WHERE gg.id = cg.goal_id
                      AND gg.canonical_name ILIKE '%' || $1 || '%'))
"""

_PICKED = f"""
    SELECT gen_random_uuid() AS pid, r.*,
           CASE r.card_id
               WHEN 'registry:io.github.cyanheads/osv-advisory-mcp-server' THEN 'osv_query_package'
               WHEN 'registry:ai.exa/exa' THEN 'web_search_exa'
               ELSE r.tools[1]
           END AS tool
    FROM ({_RANKED}) r
    WHERE ($2 = 0 OR r.rn <= $2)
"""


async def bulk(conn, args: argparse.Namespace) -> int:
    """The whole runnable library in one statement: every (card, goal) with a connector and a tool.

    `ON CONFLICT DO NOTHING` keeps the curated top-20 rows the per-row pass already wrote (their tool
    choice is better than `tools[1]`). Run the per-row pass afterwards to curate the top of each Goal.
    """
    cap = 0 if args.all else max(0, int(args.limit_per_goal))
    if args.dry_run:
        n = await conn.fetchval(
            f"""WITH picked AS ({_PICKED})
                SELECT count(*) FROM picked p JOIN goals g ON g.id = p.goal_id
                WHERE g.provenance = 'tool_goal_taxonomy' AND g.t_invalid IS NULL
                  AND NOT EXISTS (SELECT 1 FROM procedures pr
                                  WHERE pr.source_key = 'tool_card:' || p.goal_id::text || ':' || p.card_id
                                    AND pr.t_invalid IS NULL)""",
            args.goal, cap)
        print(f"would create: {n} (runnable card->goal edges, cap per goal {cap or 'none'})")
        return 0
    status = await conn.execute(
        f"""INSERT INTO procedures
                (id, procedure_id, name, goal, achieves_goal_id, steps, provenance, domain,
                 domain_payload, source_key, created_by, verification_state, availability,
                 visibility, version)
            SELECT p.pid, p.pid, COALESCE(NULLIF(p.title, ''), p.name), g.canonical_name, p.goal_id,
                   jsonb_build_array(jsonb_build_object(
                       'order', 0,
                       'goal', 'Use ' || p.tool || ' on ' || COALESCE(p.server, p.name),
                       'binding', jsonb_build_object('kind', 'tool', 'tool', p.tool,
                                                     'server_url', p.extra->>'connector_url'))),
                   'tool_card', 'tool',
                   jsonb_build_object('card_id', p.card_id, 'server', p.server, 'tool', p.tool,
                                      'goal_id', p.goal_id::text, 'goal_score', p.score),
                   'tool_card:' || p.goal_id::text || ':' || p.card_id,
                   'materialize_tool_procedures', 'candidate', 'active', 'public', 1
            FROM ({_PICKED}) p
            JOIN goals g ON g.id = p.goal_id
            WHERE g.provenance = 'tool_goal_taxonomy' AND g.t_invalid IS NULL
            ON CONFLICT (source_key) WHERE source_key IS NOT NULL AND t_invalid IS NULL DO NOTHING""",
        args.goal, cap)
    print(f"bulk insert: {status}  (cap per goal {cap or 'none'})")
    mapped = await conn.execute(
        """
        INSERT INTO procedure_tool_cards (procedure_id, step_order, card_id, score, source)
        SELECT p.id, 0, p.domain_payload->>'card_id',
               COALESCE((p.domain_payload->>'goal_score')::real, 0), 'materialized'
        FROM procedures p
        WHERE p.provenance = 'tool_card' AND p.t_invalid IS NULL
          AND p.domain_payload ? 'card_id'
          AND EXISTS (SELECT 1 FROM tool_cards tc WHERE tc.id = p.domain_payload->>'card_id')
        ON CONFLICT DO NOTHING
        """)
    print(f"card->way mapping: {mapped}")
    return 0


async def run(args: argparse.Namespace) -> int:
    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")
    import asyncpg

    conn = await asyncpg.connect(dsn, timeout=60)
    try:
        goals = await conn.fetch(
            """
            SELECT id::text AS id, canonical_name, description
            FROM goals
            WHERE provenance = 'tool_goal_taxonomy' AND t_invalid IS NULL AND status = 'active'
              AND ($1::text IS NULL OR id::text = $1 OR canonical_name ILIKE '%' || $1 || '%')
            ORDER BY canonical_name
            """,
            args.goal,
        )
        if not goals:
            raise SystemExit("no matching tool goals")
        print(f"goals: {len(goals)}  limit per goal: {args.limit_per_goal}")

        if args.replace and not args.dry_run:
            deleted = await conn.execute(
                "DELETE FROM procedures WHERE provenance = 'tool_card' AND t_invalid IS NULL")
            print(f"deleted generated procedures: {deleted.split()[-1]}")

        if args.all:
            return await bulk(conn, args)

        made = skipped = 0
        for goal in goals:
            cards = await conn.fetch(
                """
                SELECT t.id, t.name, t.title, t.server, t.tools, t.extra, g.score
                FROM tool_card_goals g
                JOIN tool_cards t ON t.id = g.card_id
                WHERE g.goal_id = $1::uuid
                ORDER BY g.score DESC, NULLIF(t.extra->>'stars', '')::int DESC NULLS LAST, t.id
                LIMIT $2
                """,
                goal["id"], max(1, int(args.limit_per_goal)),
            )
            for card in cards:
                extra = _extra(card)
                connector = str(extra.get("connector_url") or "").strip()
                tools = list(card["tools"] or [])
                if not connector or not tools:
                    skipped += 1
                    continue
                tool = choose_tool(tools, f"{goal['canonical_name']} {goal['description'] or ''}")
                server = str(card["server"] or "")
                name = str(card["title"] or card["name"] or tool)
                binding = {"kind": "tool", "tool": tool, "server_url": connector}
                steps = [{"order": 0, "goal": f"Use {tool} on {server or name}", "binding": binding}]
                payload = {"card_id": str(card["id"]), "server": server, "tool": tool,
                           "goal_id": goal["id"], "goal_score": float(card["score"] or 0)}
                source_key = f"tool_card:{goal['id']}:{card['id']}"
                pid = procedure_id(goal["id"], str(card["id"]))
                if args.dry_run:
                    print(f"  {goal['canonical_name'][:50]:<50} <- {card['id']:<48} "
                          f"tool={tool:<24} binding={binding['kind']}")
                    made += 1
                    continue
                existing = await conn.fetchval(
                    "SELECT id FROM procedures WHERE source_key = $1 AND t_invalid IS NULL", source_key)
                if existing is None:
                    await conn.execute(
                        """
                        INSERT INTO procedures
                            (id, procedure_id, name, goal, achieves_goal_id, steps, provenance, domain,
                             domain_payload, source_key, created_by, verification_state, availability,
                             visibility, version)
                        VALUES ($1::uuid, $1::uuid, $2, $3, $4::uuid, $5::jsonb, 'tool_card', 'tool',
                                $6::jsonb, $7, 'materialize_tool_procedures', 'candidate', 'active',
                                'public', 1)
                        """,
                        str(pid), name, goal["canonical_name"], goal["id"], json.dumps(steps),
                        json.dumps(payload), source_key,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE procedures
                        SET name = $2, goal = $3, steps = $4::jsonb, domain_payload = $5::jsonb,
                            updated_at = now()
                        WHERE id = $1::uuid
                        """,
                        str(existing), name, goal["canonical_name"], json.dumps(steps),
                        json.dumps(payload),
                    )
                await conn.execute(
                    """
                    INSERT INTO procedure_tool_cards (procedure_id, step_order, card_id, score, source)
                    VALUES ($1::uuid, 0, $2, $3, 'materialized')
                    ON CONFLICT (procedure_id, step_order, card_id)
                    DO UPDATE SET score = EXCLUDED.score, source = 'materialized'
                    """,
                    str(existing or pid), str(card["id"]), float(card["score"] or 0),
                )
                made += 1
        print(f"{'would create' if args.dry_run else 'created/updated'}: {made}  skipped (no connector/tool): {skipped}")
        return 0
    finally:
        await conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Turn tool cards into one-step Ways for their Goals.")
    ap.add_argument("--apply", action="store_true", help="write the procedures (default: dry run)")
    ap.add_argument("--all", action="store_true",
                    help="materialize the whole runnable library in one bulk insert (ignores --limit-per-goal)")
    ap.add_argument("--dry-run", action="store_true", help="show what would be written")
    ap.add_argument("--limit-per-goal", type=int, default=20, help="top cards per Goal (default 20)")
    ap.add_argument("--goal", default=None, help="only this Goal uuid or a canonical-name substring")
    ap.add_argument("--replace", action="store_true", help="delete generated rows first")
    args = ap.parse_args()
    if args.apply and args.dry_run:
        raise SystemExit("--apply and --dry-run are different modes; pick one")
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
