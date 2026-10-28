#!/usr/bin/env python3
"""Create (or update) the pilot organization that kell's own GLM and General Compute credits are offered through.

    python scripts/setup_pilot.py --dry-run         # show what would be written
    python scripts/setup_pilot.py --apply           # write it, print the organization id

Writes, all idempotent:
  * the organization `kell-pilot` (reused if the slug exists);
  * its `org_policies` row: only the providers and models named in deploy/provider_connections.json, only the
    `call_model` and `chat_completions` tools (the latter is the website chat), only the data classes below, a per-user daily cap and an organization monthly cap;
  * `model_provider_policies` rows for those providers (the platform egress policy a kell-owned key must also pass).

Then set KELL_PILOT_ORG_ID on the server to the printed id: every person who signs in is enrolled as a member
(app/services/pilot_enrol.py) and their calls are held against this policy. Nothing here widens beyond the numbers
below; to stop the pilot, set `kill_switch` in the admin console or remove KELL_PILOT_ORG_ID.

Data classes: PUBLIC_SOURCE, PUBLIC_DERIVED and GLOBAL_PROCEDURE only. These providers receive the prompt, and no data
processing agreement is on record with either, so private classes stay out until one is (see the notes on the
model_provider_policies rows). Widen it deliberately, not here.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SLUG = "kell-pilot"
NAME = "kell pilot"
PROVIDERS = ("zai", "general_compute")
MODELS = ("glm-5.3-flash", "gemma-4-31b-it")
TOOLS = ("call_model", "chat_completions")  # chat_completions: the site Delegator (mcp_server/chat_routes.py) calls under this tool name
DATA_CLASSES = ("PUBLIC_SOURCE", "PUBLIC_DERIVED", "GLOBAL_PROCEDURE")
PER_USER_DAILY_USD = "1.00"
MONTHLY_USD = "30.00"          # $1 a day for a month: a hard stop on the organization's total
POLICY_VERSION = "pilot-v1"
NOTES = {
    "zai": "Z.ai API (pay-as-you-go credits; API terms allow downstream apps for end users). No DPA on record: public classes only.",
    "general_compute": "General Compute API. No DPA on record: public classes only.",
}


async def setup(conn, *, apply: bool) -> dict:
    org = await conn.fetchrow("SELECT id::text AS id FROM organizations WHERE slug = $1 AND t_expired IS NULL", SLUG)
    plan = {"slug": SLUG, "organization_exists": org is not None, "providers": PROVIDERS, "models": MODELS,
            "tools": TOOLS, "data_classes": DATA_CLASSES, "per_user_daily_usd": PER_USER_DAILY_USD,
            "monthly_usd": MONTHLY_USD}
    if not apply:
        return {**plan, "dry_run": True}
    async with conn.transaction():
        if org is None:
            org = await conn.fetchrow("INSERT INTO organizations (name, slug) VALUES ($1, $2) RETURNING id::text AS id",
                                      NAME, SLUG)
        await conn.execute(
            "INSERT INTO org_policies (organization_id, allowed_providers, allowed_models, allowed_tools, "
            "allowed_data_classes, monthly_budget_usd, per_user_daily_budget_usd, updated_by) "
            "VALUES ($1::uuid, $2, $3, $4, $5, $6::numeric, $7::numeric, 'setup_pilot') "
            "ON CONFLICT (organization_id) DO UPDATE SET allowed_providers = EXCLUDED.allowed_providers, "
            "allowed_models = EXCLUDED.allowed_models, allowed_tools = EXCLUDED.allowed_tools, "
            "allowed_data_classes = EXCLUDED.allowed_data_classes, monthly_budget_usd = EXCLUDED.monthly_budget_usd, "
            "per_user_daily_budget_usd = EXCLUDED.per_user_daily_budget_usd, version = org_policies.version + 1, "
            "updated_by = 'setup_pilot', updated_at = now()",
            org["id"], list(PROVIDERS), list(MODELS), list(TOOLS), list(DATA_CLASSES), MONTHLY_USD, PER_USER_DAILY_USD)
        for provider in PROVIDERS:
            have = await conn.fetchval(
                "SELECT 1 FROM model_provider_policies WHERE provider = $1 AND model = '*' AND policy_version = $2 "
                "AND tenant_id IS NULL", provider, POLICY_VERSION)
            if not have:
                await conn.execute(
                    "INSERT INTO model_provider_policies (provider, model, region, retention_policy, "
                    "training_or_improvement_use, dpa_available, allowed_data_classes, policy_version, notes) "
                    "VALUES ($1, '*', 'unknown', 'provider default', FALSE, FALSE, $2, $3, $4)",
                    provider, list(DATA_CLASSES), POLICY_VERSION, NOTES[provider])
    return {**plan, "organization_id": org["id"], "applied": True}


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="defaults to $DATABASE_URL")
    args = ap.parse_args()
    if not args.dsn:
        raise SystemExit("no database: pass --dsn or set DATABASE_URL")
    import asyncpg

    conn = await asyncpg.connect(args.dsn)
    try:
        result = await setup(conn, apply=args.apply)
    finally:
        await conn.close()
    for k, v in result.items():
        print(f"{k}: {v}")
    if result.get("organization_id"):
        print(f"\nSet on the server:  KELL_PILOT_ORG_ID={result['organization_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
