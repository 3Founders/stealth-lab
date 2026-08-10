"""
Ingest tau2-bench's banking_knowledge document corpus (698 real policy
documents) as knowledge_nodes.

Run from backend/, with .env pointing at the target database, and with
the tau2-bench repo cloned somewhere accessible:
    python scripts/ingest_banking_knowledge.py /path/to/tau2-bench

Each documents/*.json file is one document: {id, title, content}.
Maps directly onto KnowledgeSpec:
  - key: the document's own id (e.g. "doc_bank_accounts_..._001") --
    stable, unique, and already meaningful, no need to invent one.
  - name: title.
  - properties: {"content": <full markdown text>, "category": <derived
    from the filename prefix, e.g. "bank_accounts">} -- content is what
    a real user/debate would need to read, category is a coarse label
    useful for later filtering without re-parsing every document.

Deliberately does NOT touch tasks.json / db.json in this pass -- those
are for a possible later Experiment-1-style retrieval evaluation on
this domain (tasks.json's `required_documents` field is a direct
analogue of AFTER's task.toml `skills` list, real ground truth for that
kind of test), out of scope for Experiment 3's ingestion specifically.
"""
import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool
from app.onboarding.seed import KnowledgeSpec, Onboarder, WorkflowSpec


_KNOWN_CATEGORIES = sorted([
    "bank_accounts", "business_checking_accounts", "business_credit_cards",
    "business_savings_accounts", "buy_now_pay_later", "checking_accounts",
    "credit_cards", "customer_support", "everyone_pay",
    "personal_subscriptions", "savings_accounts",
], key=len, reverse=True)  # longest first, so a category whose name is a
                            # prefix of another (none currently are, but
                            # cheap insurance) can't shadow the real match


def derive_category(doc_id: str) -> str:
    """
    'doc_bank_accounts_bank_accounts_(general)_001' -> 'bank_accounts'.

    Category names contain underscores themselves ('bank_accounts',
    'business_checking_accounts'), so splitting on the first underscore
    and taking one segment is wrong -- confirmed by testing against a
    real document id before this shipped: naive parts[1] returned
    'bank' instead of 'bank_accounts'. Matches against the known
    category list (from the directory listing) instead.
    """
    rest = doc_id[len("doc_"):] if doc_id.startswith("doc_") else doc_id
    for cat in _KNOWN_CATEGORIES:
        if rest.startswith(cat):
            return cat
    return "unknown"


async def main():
    if len(sys.argv) < 2:
        print("usage: python scripts/ingest_banking_knowledge.py /path/to/tau2-bench")
        sys.exit(1)
    repo_root = Path(sys.argv[1])
    docs_dir = repo_root / "data" / "tau2" / "domains" / "banking_knowledge" / "documents"
    if not docs_dir.is_dir():
        print(f"documents directory not found at {docs_dir} — check the repo path")
        sys.exit(1)

    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env")
        sys.exit(1)

    doc_files = sorted(docs_dir.glob("*.json"))
    print(f"found {len(doc_files)} documents")

    knowledge_specs = []
    for f in doc_files:
        data = json.loads(f.read_text(encoding="utf-8"))
        knowledge_specs.append(KnowledgeSpec(
            key=data["id"],
            node_type="policy_document",
            name=data["title"],
            properties={"content": data["content"], "category": derive_category(data["id"])},
        ))

    spec = WorkflowSpec(workflow_name="tau2_banking_knowledge", knowledge=knowledge_specs)
    problems = spec.validate_spec()
    if problems:
        print("spec validation failed:")
        for p in problems[:20]:
            print(f"  - {p}")
        if len(problems) > 20:
            print(f"  ... and {len(problems) - 20} more")
        sys.exit(1)

    print(f"ingesting {len(knowledge_specs)} knowledge nodes "
          f"(this embeds via embed_batched -- expect real pacing delay for a corpus this size)...")
    pool = await create_pool(os.environ["DATABASE_URL"])
    onboarder = Onboarder(pool)
    result = await onboarder.seed(spec, created_by="tau2_banking_ingestion")
    await pool.close()

    print(f"\nSeeded {len(result.knowledge_ids)} knowledge_nodes, {result.embedded} embedded.")
    if result.embedding_error:
        print(f"Embedding error (nodes created but not embedded — run backfill_embeddings.py "
              f"to finish): {result.embedding_error}")


if __name__ == "__main__":
    asyncio.run(main())