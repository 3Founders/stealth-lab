"""
Automated fabrication check: for every 'passed' candidate, extracts
every number written into the NEW properties, and checks whether it
actually appears in either source document's ORIGINAL content. Flags
anything that doesn't -- a real, cheap, scalable proxy for "did the
panel actually cite this, or invent a plausible-sounding specific
detail." Manual review confirmed this distinction matters (checked one
real case: a category label like 'external transfer' was fine, but a
specific term like 'ACH' that wasn't in the source was NOT caught by
groundedness scoring) -- this exists because manual spot-checking
doesn't scale past a handful of results, and a full-scale run needs
something a human isn't going to read every transcript for.

Numbers only, deliberately -- not a general fact-checker. A fabricated
number ($500 that should have been $50, a wrong percentage) is the
highest-stakes kind of error to write into a knowledge graph silently;
prose-level embellishment ("ACH" vs "wire transfer") is real but lower
stakes and much harder to catch cheaply.

Run from backend/, after a batch run has written experiment_3_batch_results.json
(with trigger_id included -- see run_experiment_3_batch.py):
    python scripts/verify_change_set_grounding.py
"""
import asyncio
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()

from app.db.session import create_pool

NUMBER_RE = re.compile(r"\$?\d[\d,]*\.?\d*%?")


def extract_numbers(text: str) -> set[str]:
    if not text:
        return set()
    return {n.strip("$,.%") for n in NUMBER_RE.findall(text) if any(c.isdigit() for c in n)}


def flatten_strings(obj) -> str:
    """Pulls every string value out of a nested dict/list -- change_set
    properties can nest ({"properties": {"statement": "...", "scope": "..."}})."""
    if isinstance(obj, str):
        return obj + " "
    if isinstance(obj, dict):
        return "".join(flatten_strings(v) for v in obj.values())
    if isinstance(obj, list):
        return "".join(flatten_strings(v) for v in obj)
    return ""


def extract_new_content(change_set: dict, exclude_ids: list[str] = ()) -> str:
    """
    ONLY the `changes` payload of each op, plus `reason` -- the actual
    new content and the panel's stated justification. Excludes op_type,
    knowledge_node_id, task_node_id: structural references, not content
    -- a real bug, caught on real output, where UUID substrings like
    '4604' got flagged as "ungrounded facts".

    `exclude_ids`: the pair's own real node UUIDs (full and short-
    prefix form). A SECOND, related real bug: the panel citing a node
    by its short-id prefix in `reason` ("the Automatic Sweep node
    (b64ed04b)") is GOOD citation behavior, not a defect -- but digits
    inside that id fragment ('64', '04') were getting flagged as
    ungrounded numbers too. Strip the pair's own ids out of the text
    before number extraction, so a legitimate id citation is never
    mistaken for a numeric claim.
    """
    text = ""
    for op in change_set.get("ops", []):
        text += flatten_strings(op.get("changes", {}))
        text += (op.get("reason") or "") + " "
    for node_id in exclude_ids:
        if not node_id:
            continue
        text = text.replace(node_id, " ")
        text = text.replace(node_id.split("-")[0], " ")  # short-prefix form, e.g. 'b64ed04b'
    return text


def excerpt_around(text: str, needle: str, window: int = 60) -> str:
    """A short window of context around where an ungrounded number
    actually appears in the new content -- so grounding_check_results.json
    is reviewable on its own, without a manual DB query for the
    candidate's full change_set every time. Confirmed necessary: the
    first real PEP-corpus run's flagged output had numbers and a pair
    label but no actual text, requiring a separate SQL round-trip to
    even see what was flagged."""
    idx = text.find(needle)
    if idx == -1:
        return ""
    start, end = max(0, idx - window), min(len(text), idx + len(needle) + window)
    return ("..." if start > 0 else "") + text[start:end].strip() + ("..." if end < len(text) else "")


async def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", default="experiment_3_batch_results.json",
                         help="was hardcoded to only the original batch file -- a real gap: "
                              "a later run (run_experiment_3_real_conflicts.py) writes a "
                              "DIFFERENT results file, and running this checker without "
                              "specifying --results silently re-checked stale data instead")
    args = parser.parse_args()
    results = json.load(open(args.results))
    passed = [r for r in results if r["outcome"] == "passed"]
    print(f"checking {len(passed)} passed candidate(s) for ungrounded numbers\n")

    pool = await create_pool(os.environ["DATABASE_URL"])

    flagged = []
    for r in passed:
        pair = r["pair"]
        # Two different runners write two different pair shapes into
        # this same results-file format: run_experiment_3_real_conflicts.py
        # (banking) uses id_a/id_b/name_a/name_b; run_experiment_3_pep_
        # corpus.py writes the raw ground-truth record, which uses
        # superseded_id/superseding_id/superseded_title/superseding_title
        # instead. Confirmed real, not hypothetical -- the first fix here
        # only patched the FIRST usage and still crashed two lines further
        # down on pair["id_a"] again; normalizing once, up front, avoids
        # that class of miss recurring a third time.
        if "id_a" in pair and "id_b" in pair:
            id_a, id_b = pair["id_a"], pair["id_b"]
            label = f"{pair.get('name_a', id_a)} vs {pair.get('name_b', id_b)}"
        elif "superseded_id" in pair and "superseding_id" in pair:
            id_a, id_b = pair["superseded_id"], pair["superseding_id"]
            label = f"{pair.get('superseded_title', id_a)} vs {pair.get('superseding_title', id_b)}"
        else:
            print(f"  SKIPPING candidate {r.get('candidate_id')}: pair has neither "
                  f"id_a/id_b nor superseded_id/superseding_id -- {sorted(pair.keys())}")
            continue
        source_row = await pool.fetch(
            "SELECT properties->>'content' AS content, properties->>'statement' AS statement "
            "FROM knowledge_nodes WHERE id = ANY($1::uuid[])",
            [id_a, id_b],
        )
        source_text = " ".join(
            (row["content"] or "") + " " + (row["statement"] or "") for row in source_row
        )
        source_numbers = extract_numbers(source_text)

        candidate_row = await pool.fetchrow(
            "SELECT change_set FROM candidates WHERE id = $1", r["candidate_id"]
        )
        if not candidate_row:
            continue
        change_set = candidate_row["change_set"]
        new_content = extract_new_content(change_set, exclude_ids=[id_a, id_b])
        new_numbers = extract_numbers(new_content)

        ungrounded = new_numbers - source_numbers
        if ungrounded:
            flagged.append({
                "candidate_id": r["candidate_id"],
                "pair": label,
                "ungrounded_numbers": sorted(ungrounded),
                "source_numbers_available": sorted(source_numbers),
                "excerpts": {n: excerpt_around(new_content, n) for n in sorted(ungrounded)},
            })

    await pool.close()

    print(f"=== {len(flagged)}/{len(passed)} candidate(s) contain a number not found in either source ===\n")
    for f in flagged:
        print(f"  candidate {f['candidate_id']}")
        print(f"    pair: {f['pair']}")
        print(f"    ungrounded number(s): {f['ungrounded_numbers']}")
        print(f"    (source actually contains: {f['source_numbers_available']})")
        for n, ex in f["excerpts"].items():
            print(f"      '{n}' appears in: {ex!r}")
        print()

    with open("grounding_check_results.json", "w") as fh:
        json.dump(flagged, fh, indent=2)
    print("wrote grounding_check_results.json -- review these specific candidates by hand, "
          "not the whole batch")


if __name__ == "__main__":
    asyncio.run(main())
