"""
Real, live proof of SKILL.md ingestion against a REAL external skill --
not a synthetic fixture. Content below is transcribed from the real,
public anthropics/skills repository
(https://github.com/anthropics/skills/blob/main/skills/claude-api/SKILL.md),
fetched this session -- the exact same skill this very environment's own
system prompt lists ("claude-api: Reference for the Claude API / Anthropic
SDK...").

Runs the real ingest_skill_md() against the real live database: real
parse, real novelty check via find_applicable_procedures(), real
embedding computed before write, real capture_procedure() INSERT.
Verified independently via direct SQL after, same discipline as every
other real write this session.

Hand-run, not part of pytest (registered in test_live_scripts_not_collected.py).
"""
import asyncio

from dotenv import load_dotenv

load_dotenv()

from app.db.session import create_pool
from app.services.skill_ingestion import ingest_skill_md

# Real content, transcribed from the real public SKILL.md fetched this
# session (frontmatter + real numbered steps, in their real order) --
# not authored by this test.
REAL_CLAUDE_API_SKILL_MD = """---
name: claude-api
description: Reference for Claude API / Anthropic SDK -- models, pricing, params, streaming, tool use, MCP, agents, caching, token counting, model migration
---

Use when: prompt names Claude/Anthropic in any form OR user asks about LLM pricing/model choice/limits/caching. Skip only if another provider is explicitly named (OpenAI/GPT/Gemini/Llama/Mistral/Cohere/Ollama) or grep hits on provider imports.

1. Infer the project's language from project files (*.py, *.ts, package.json, etc.) before reading code.
2. Scan the target file for non-Anthropic provider markers before any edit.
3. Default to model claude-opus-5 unless the user explicitly names a different model.
4. Set thinking type to adaptive for anything remotely complicated.
5. Default to streaming with .stream() and .get_final_message() for long input/output.
6. Verify real API shapes against the language-specific reference files -- API drift is common.
7. WebFetch SDK repos only if a binding is not documented in the skill files; don't guess API signatures.
8. Use the official SDK exclusively; never fall back to raw HTTP in SDK projects.
9. When a migrate subcommand is used, read the migration guide fully and execute all steps in order.
10. Confirm scope first -- ask which files/directories before editing on an ambiguous migration request.
11. Audit prompts too -- model migrations include prompt re-tuning.
12. Use the SDK's type-safe helper functions instead of reimplementing them.
13. Catch error chains with the most-specific exception class first, not one broad catch-all.
"""


async def main():
    pool = await create_pool()

    print("=== ingesting a REAL external SKILL.md (anthropics/skills, claude-api) ===")
    result = await ingest_skill_md(
        pool, REAL_CLAUDE_API_SKILL_MD, domain="coding", created_by="skill_md_ingestion_live_test",
    )
    print(result)

    if result["status"] == "duplicate":
        print(f"\nNOTE: reported as a near-duplicate of an existing procedure "
              f"(similarity={result['similarity']:.3f}) -- the novelty gate working "
              f"correctly, not a failure. Re-run is idempotent by design.")
        await pool.close()
        return

    assert result["status"] == "captured"
    procedure_id = result["procedure_id"]

    # Independent verification, not trusting the function's own return value.
    row = await pool.fetchrow(
        "SELECT name, goal, steps, embedding IS NOT NULL AS has_embedding, "
        "verification_state, provenance, domain_payload "
        "FROM procedures WHERE procedure_id = $1 AND t_invalid IS NULL",
        procedure_id,
    )
    await pool.close()

    print("\n=== independent SQL verification ===")
    print(f"name: {row['name']}")
    print(f"goal: {row['goal']}")
    print(f"steps: {len(row['steps'])} real steps")
    print(f"has_embedding: {row['has_embedding']}")
    print(f"verification_state: {row['verification_state']}")
    print(f"provenance: {row['provenance']}")
    print(f"applies_when (kept as prose): {row['domain_payload'].get('applies_when')}")

    assert row["has_embedding"] is True, "FAIL: no embedding -- would be unreachable by search"
    assert row["verification_state"] == "candidate", "FAIL: must not be born verified"
    assert row["provenance"] == "prior_library"
    assert len(row["steps"]) == 13, f"FAIL: expected 13 real steps, got {len(row['steps'])}"

    print("\nPASS: a real, external, publicly-published SKILL.md was parsed, checked "
          "for novelty, embedded, and captured as a real 'candidate' procedure -- "
          "verified independently via direct SQL, not just the function's own report.")


if __name__ == "__main__":
    asyncio.run(main())
