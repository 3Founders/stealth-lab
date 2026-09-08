"""
Content-grounded display-name / display-description regeneration for the
retrieval release closure (sections 3 + 4).

- generation model : gemma-4-31B-it  (cheapest capable model on the
  existing General Compute provider abstraction -- app/config general_compute_*)
- judge model      : gpt-oss-120b    (independent, same provider)
- both reached through the SAME OpenAI-compatible client the ingestion
  path already builds (ingestion_jobs._extraction_client); no new LLM
  abstraction.

Pipeline per procedure version:
  1. build a content context from canonical_name + goal + capability_statement
     + applies_when + steps + constraints/invariants + dependencies + domain
  2. GENERATE display_name (4-10 words, action/capability language, no ids/
     slugs/db-terms/hype) + display_description (grounded, concise) as strict JSON
  3. DETERMINISTIC VALIDATION (non-empty, length, no UUID, no raw slug, no
     db terminology, no fabricated metric, valid UTF-8, idempotent-shaped)
  4. INDEPENDENT JUDGE -> PASS | REGENERATE | HUMAN_REVIEW
  5. REGENERATE -> one retry with the judge's reason fed back
  6. PASS  -> persist (display_name, display_description,
              display_metadata_version='disp_v2_llm', provenance in
              domain_payload.display_provenance). canonical `name` is NEVER touched.
  7. else  -> review queue, DB left unchanged

    python scripts/regenerate_display_metadata.py --select deslug   [--limit N] [--apply]
    python scripts/regenerate_display_metadata.py --select ids --ids <uuid,uuid>  [--apply]
    python scripts/regenerate_display_metadata.py --select audit-disp-v1 --limit 200  (dry audit)
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
except ImportError:  # pragma: no cover
    pass

from openai import OpenAI

from app.config import settings
from app.db.session import create_pool

_CLOSURE = Path(__file__).resolve().parents[2] / ".scratch" / "retrieval-release-closure"
_GEN = _CLOSURE / "display-name-generation.jsonl"
_VAL = _CLOSURE / "display-name-validation.jsonl"
_QUEUE = _CLOSURE / "display-name-review-queue.jsonl"

GEN_MODEL = os.environ.get("DISPLAY_GEN_MODEL", "gemma-4-31B-it")
JUDGE_MODEL = "gpt-oss-120b"
DISPLAY_VERSION = "disp_v2_llm"

_UUID_RE = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)
_HEXHASH_RE = re.compile(r"\b[0-9a-f]{7,}\b", re.I)
_SLUGISH_RE = re.compile(r"^[a-z0-9]+([-_][a-z0-9]+)+$")
_DB_TERMS = re.compile(
    r"\b(procedure_id|proc(?:edure)?[ _]row|t_invalid|embedding|vector|uuid|jsonb|"
    r"schema|migration|slug|canonical_name|display_name|retrieval_document)\b", re.I)
_HYPE_RE = re.compile(
    r"\b(\d+\s*x\b|\d+%|blazing|lightning[- ]fast|10x|100x|revolutionary|"
    r"best[- ]in[- ]class|state[- ]of[- ]the[- ]art|guarantee[sd]?|dramatically|"
    r"instantly|effortless(?:ly)?)\b", re.I)


def _client():
    key = settings.general_compute_api_key
    if not key:
        raise SystemExit("general_compute_api_key not configured")
    return OpenAI(api_key=key, base_url=settings.general_compute_base_url, max_retries=1, timeout=90)


def _context(row: dict) -> str:
    steps = row["steps"] or []
    if isinstance(steps, str):
        steps = json.loads(steps)
    step_lines = [str(s.get("goal") or s.get("description") or s.get("action") or "")
                  for s in steps if isinstance(s, dict)]
    step_lines = [s for s in step_lines if s][:15]
    dp = row["domain_payload"] or {}
    if isinstance(dp, str):
        dp = json.loads(dp)
    applies = dp.get("applies_when") if isinstance(dp, dict) else None
    tools = dp.get("tool_requirements") if isinstance(dp, dict) else None
    deps = dp.get("dependencies") if isinstance(dp, dict) else None
    inv = row["invariants"] or []
    if isinstance(inv, str):
        inv = json.loads(inv)
    parts = [f"canonical_name: {row['name']}"]
    if row.get("capability_statement"):
        parts.append(f"capability: {row['capability_statement']}")
    if row.get("goal"):
        parts.append(f"goal: {row['goal']}")
    if applies:
        parts.append(f"when_to_use: {applies}")
    if row.get("domain"):
        parts.append(f"domain: {row['domain']}")
    if tools:
        parts.append(f"tools: {', '.join(map(str, tools[:12]))}")
    if deps:
        names = [d.get("reference") if isinstance(d, dict) else str(d) for d in deps][:8]
        parts.append(f"depends_on: {', '.join(str(n) for n in names if n)}")
    if inv:
        parts.append("constraints: " + "; ".join(
            (i.get("expr") or i.get("description") or "") if isinstance(i, dict) else str(i)
            for i in inv[:6]))
    if step_lines:
        parts.append("steps:\n" + "\n".join(f"  {i+1}. {s}" for i, s in enumerate(step_lines)))
    return "\n".join(parts)


_GEN_SYS = (
    "You write concise, human-facing names for software 'procedures' (reusable "
    "task workflows). You are given the procedure's own recorded content as DATA. "
    "Produce a JSON object with exactly two keys:\n"
    '  "display_name": 4-10 words, action/capability phrasing (e.g. "Load Tool '
    'Details Only When Needed", "Isolate Parallel Coding Agents with Git '
    'Worktrees"). Title case. NO internal ids, NO UUIDs, NO hex, NO slug, NO '
    "database terms, NO hype, NO unsupported claims. Must accurately describe "
    "THIS procedure. Must NOT be just the canonical_name title-cased.\n"
    '  "display_description": 1-2 sentences. What it does and when it is useful. '
    "Grounded ONLY in the given content. No fabricated metrics, prerequisites, or "
    "integrations.\n"
    "If the given content is too thin or placeholder-like to describe honestly, "
    'return {"display_name": "", "display_description": "", "insufficient": true}.'
)


def _chat_json(client, model, system, user, retry_reason=None):
    msgs = [{"role": "system", "content": system}]
    if retry_reason:
        user = user + f"\n\nA previous attempt was rejected: {retry_reason}\nFix it."
    msgs.append({"role": "user", "content": user})
    last = None
    for attempt in range(6):
        try:
            r = client.chat.completions.create(
                model=model, messages=msgs, temperature=0.2, max_tokens=400,
                response_format={"type": "json_object"},
            )
            txt = (r.choices[0].message.content or "").strip()
            try:
                return json.loads(txt)
            except Exception:
                m = re.search(r"\{.*\}", txt, re.S)
                return json.loads(m.group(0)) if m else {}
        except Exception as exc:  # noqa: BLE001
            last = exc
            if "429" in str(exc) or "high demand" in str(exc).lower() or "rate" in str(exc).lower():
                time.sleep(8 * (attempt + 1))
            else:
                raise
    raise last


_PLACEHOLDER_GOAL = re.compile(r"^\s*[a-z0-9][a-z0-9 _-]*\s+goal\s*$", re.I)


def _is_noncontent(row: dict) -> bool:
    """A test/eval fixture with no describable procedure content: a
    placeholder goal ('<name> goal') and no steps."""
    steps = row["steps"] or []
    if isinstance(steps, str):
        steps = json.loads(steps)
    goal = (row.get("goal") or "").strip()
    n = len([s for s in steps if isinstance(s, dict)]) if steps else 0
    if n >= 1 and len(goal) > 12:
        return False
    if _PLACEHOLDER_GOAL.match(goal) or goal.lower() in (
        (row["name"] + " goal").lower(), row["name"].replace("-", " ").lower() + " goal"):
        return True
    return n == 0 and len(goal) <= 12


def _deterministic_validate(name: str, desc: str, canonical: str) -> list[str]:
    errs = []
    name = (name or "").strip()
    desc = (desc or "").strip()
    if not name:
        errs.append("empty display_name")
    if not desc:
        errs.append("empty display_description")
    if name:
        wc = len(name.split())
        if wc < 3 or wc > 12:
            errs.append(f"display_name word count {wc} outside 3-12")
        if len(name) > 80:
            errs.append("display_name too long")
        if _UUID_RE.search(name) or _HEXHASH_RE.search(name):
            errs.append("display_name contains id/hex")
        if _SLUGISH_RE.match(name.lower().replace(" ", "-")) and " " not in name:
            errs.append("display_name is a raw slug")
        if name.lower().replace(" ", "-").strip("-") == canonical.lower().strip("-"):
            errs.append("display_name is just the canonical slug re-cased")
        if _DB_TERMS.search(name):
            errs.append("display_name contains database terminology")
        if _HYPE_RE.search(name):
            errs.append("display_name contains hype/unsupported claim")
        try:
            name.encode("utf-8")
        except Exception:
            errs.append("display_name not valid UTF-8")
    if desc:
        if len(desc) < 20:
            errs.append("display_description too short")
        if len(desc) > 320:
            errs.append("display_description too long")
        if _UUID_RE.search(desc):
            errs.append("display_description contains a UUID")
        if _HYPE_RE.search(desc):
            errs.append("display_description contains a fabricated metric/hype")
        if _DB_TERMS.search(desc):
            errs.append("display_description contains database terminology")
    return errs


_JUDGE_SYS = (
    "You are an independent reviewer of a generated human-facing name and "
    "description for a software procedure. You are given the procedure's own "
    "recorded content and the generated name/description. Decide:\n"
    '  "verdict": one of "PASS", "REGENERATE", "HUMAN_REVIEW".\n'
    '  "reason": one sentence.\n'
    "PASS = name is 4-10 words, action/capability phrasing, accurate to the "
    "content, no ids/slug/db-terms/hype/unsupported claims, and NOT just the "
    "slug re-cased; description is grounded, concise, invents nothing.\n"
    "REGENERATE = fixable wording/length/grounding problem.\n"
    "HUMAN_REVIEW = the source content is too thin/placeholder to describe "
    "honestly, or the generation makes claims the content does not support."
)


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--select", choices=("deslug", "ids", "audit-disp-v1"), required=True)
    ap.add_argument("--ids", default="")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--apply", action="store_true", help="persist PASS results to the DB")
    a = ap.parse_args()
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not set"); return 1

    _CLOSURE.mkdir(parents=True, exist_ok=True)
    pool = await create_pool(min_size=1, max_size=2)
    where = {
        "deslug": "display_metadata_version = 'disp_v1_deslug_only'",
        "audit-disp-v1": "display_metadata_version = 'disp_v1'",
        "ids": "id = ANY($1::uuid[])",
    }[a.select]
    sql = (f"SELECT id, name, goal, capability_statement, steps, invariants, domain, "
           f"domain_payload, display_name, display_description, display_metadata_version, "
           f"is_engineering_fixture FROM procedures WHERE t_invalid IS NULL AND {where} "
           f"ORDER BY is_engineering_fixture, name")
    if a.limit:
        sql += f" LIMIT {int(a.limit)}"
    rows = await (pool.fetch(sql, [x for x in a.ids.split(",") if x]) if a.select == "ids"
                  else pool.fetch(sql))
    print(f"{len(rows)} procedures selected ({a.select}); apply={a.apply}")

    client = _client()
    gen_f = _GEN.open("w", encoding="utf-8")
    val_f = _VAL.open("w", encoding="utf-8")
    q_f = _QUEUE.open("w", encoding="utf-8")
    counts = {"PASS": 0, "REGENERATE": 0, "HUMAN_REVIEW": 0, "applied": 0, "gen_error": 0}
    now = datetime.now(timezone.utc).isoformat()

    for n, r in enumerate(rows):
        row = dict(r)
        ctx = _context(row)
        rec = {"id": str(row["id"]), "canonical_name": row["name"],
               "is_engineering_fixture": row["is_engineering_fixture"],
               "prev_display_name": row["display_name"],
               "prev_display_metadata_version": row["display_metadata_version"]}
        # Pre-filter: a fixture with a placeholder goal and no steps has no
        # procedure content to describe. Do NOT ask a model to invent one.
        if _is_noncontent(row):
            rec.update({"verdict": "HUMAN_REVIEW",
                        "reason": "non-content test fixture: placeholder goal, no steps -- nothing to describe honestly",
                        "skipped_llm": True})
            val_f.write(json.dumps(rec) + "\n")
            q_f.write(json.dumps(rec) + "\n")
            counts["HUMAN_REVIEW"] += 1
            continue
        try:
            g = _chat_json(client, GEN_MODEL, _GEN_SYS, ctx)
        except Exception as exc:  # noqa: BLE001
            counts["gen_error"] += 1
            rec.update({"verdict": "HUMAN_REVIEW", "reason": f"generation error: {str(exc)[:120]}"})
            q_f.write(json.dumps(rec) + "\n"); gen_f.write(json.dumps({**rec, "raw": None}) + "\n")
            continue
        name, desc = (g.get("display_name") or "").strip(), (g.get("display_description") or "").strip()
        rec.update({"gen_model": GEN_MODEL, "generated_at": now,
                    "display_name": name, "display_description": desc,
                    "insufficient_flag": bool(g.get("insufficient"))})
        gen_f.write(json.dumps({**rec, "context_sha": hashlib.sha256(ctx.encode()).hexdigest()[:12]}) + "\n")

        det = _deterministic_validate(name, desc, row["name"])
        if g.get("insufficient"):
            verdict, reason = "HUMAN_REVIEW", "generator flagged content insufficient"
        elif det:
            # one regenerate attempt with the deterministic errors as feedback
            try:
                g2 = _chat_json(client, GEN_MODEL, _GEN_SYS, ctx, retry_reason="; ".join(det))
                name, desc = (g2.get("display_name") or "").strip(), (g2.get("display_description") or "").strip()
                det2 = _deterministic_validate(name, desc, row["name"])
                if det2 or g2.get("insufficient"):
                    verdict, reason = "HUMAN_REVIEW", "failed deterministic validation twice: " + "; ".join(det2 or det)
                    counts["REGENERATE"] += 1
                else:
                    rec.update({"display_name": name, "display_description": desc, "regenerated": True})
                    verdict, reason = "_judge", ""
                    counts["REGENERATE"] += 1
            except Exception as exc:  # noqa: BLE001
                verdict, reason = "HUMAN_REVIEW", f"regenerate error: {str(exc)[:100]}"
        else:
            verdict, reason = "_judge", ""

        if verdict == "_judge":
            juser = (f"PROCEDURE CONTENT:\n{ctx}\n\nGENERATED:\n"
                     f'display_name: "{name}"\ndisplay_description: "{desc}"')
            try:
                j = _chat_json(client, JUDGE_MODEL, _JUDGE_SYS, juser)
                verdict = (j.get("verdict") or "HUMAN_REVIEW").upper()
                reason = j.get("reason") or ""
                if verdict not in ("PASS", "REGENERATE", "HUMAN_REVIEW"):
                    verdict = "HUMAN_REVIEW"
            except Exception as exc:  # noqa: BLE001
                verdict, reason = "HUMAN_REVIEW", f"judge error: {str(exc)[:100]}"

        rec.update({"verdict": verdict, "reason": reason, "judge_model": JUDGE_MODEL})
        val_f.write(json.dumps(rec) + "\n")
        counts[verdict] = counts.get(verdict, 0) + 1

        if verdict == "PASS":
            if a.apply:
                dp = row["domain_payload"] or {}
                if isinstance(dp, str):
                    dp = json.loads(dp)
                dp["display_provenance"] = {
                    "generation_model": GEN_MODEL, "judge_model": JUDGE_MODEL,
                    "generated_at": now, "regenerated": rec.get("regenerated", False),
                    "source_fields": ["name", "goal", "capability_statement",
                                      "applies_when", "steps", "invariants", "domain"],
                }
                await pool.execute(
                    "UPDATE procedures SET display_name=$2, display_description=$3, "
                    "display_metadata_version=$4, domain_payload=$5::jsonb, updated_at=now() "
                    "WHERE id=$1",
                    row["id"], name, desc, DISPLAY_VERSION, json.dumps(dp),
                )
                counts["applied"] += 1
        else:
            q_f.write(json.dumps(rec) + "\n")

        if (n + 1) % 25 == 0:
            print(f"  {n+1}/{len(rows)}  {json.dumps(counts)}", flush=True)
        time.sleep(0.2)

    for f in (gen_f, val_f, q_f):
        f.close()
    await pool.close()
    print(f"\nDONE {json.dumps(counts)}")
    print(f"  {_GEN}\n  {_VAL}\n  {_QUEUE}")


if __name__ == "__main__":
    asyncio.run(main())
