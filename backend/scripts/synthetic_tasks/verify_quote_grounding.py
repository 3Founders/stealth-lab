"""
Mechanical check for a real, confirmed failure mode: the debate panel
has twice now made a confident claim about a content DIFFERENCE
between two nodes that turned out to be false when checked against the
real text ("word-for-word identical" when they weren't; "lacks status
filtering" when it explicitly had it). Both times the claim was a
paraphrase/summary, never an actual quote -- unlike numeric
fabrication (already caught by verify_change_set_grounding.py), this
class of error was never mechanically checked at all.

The prompt now requires verbatim quotes (in quotation marks) whenever
a resolution's reasoning depends on a content difference. This module
checks that requirement mechanically: any quoted span in the rationale
must actually appear in the real content of the node it's describing.
A paraphrase can't be verified this way (that's the point -- it forces
the panel toward checkable claims instead of confident-sounding ones).
"""
import re


def extract_quoted_claims(rationale: str, min_length: int = 10, max_length: int = 300) -> list[str]:
    """Finds quoted spans using straight or curly double quotes.
    min_length filters out trivial short quotes (a single word in
    quotes for emphasis, not an evidentiary citation).

    max_length + the turn-boundary rejection below exist because of a
    real bug found in a real run: a malformed match bridged across
    "[amended by panelist_x]" -- a structural marker between separate
    panelist turns in a multi-turn transcript -- capturing unrelated
    text from two different turns as if it were one continuous quote.
    No legitimate single quote should ever contain a turn-boundary
    marker or run this long; reject any match that does rather than
    report it as a real (fabricated-looking) claim."""
    patterns = [
        r'"([^"]{%d,%d})"' % (min_length, max_length),       # straight double quotes
        r'\u201c([^\u201d]{%d,%d})\u201d' % (min_length, max_length),  # curly “ ”
    ]
    quotes = []
    for pat in patterns:
        quotes.extend(re.findall(pat, rationale))
    return [q for q in quotes if "[amended by" not in q and "[amended" not in q]


def _normalize(text: str) -> str:
    """Collapse whitespace for comparison -- real text may wrap
    differently than how it's quoted in prose, but the actual words
    must still match."""
    return re.sub(r"\s+", " ", text).strip().lower()


def check_quotes_grounded(rationale: str, node_contents: dict[str, str]) -> list[dict]:
    """
    node_contents: {node_id: real_text} for every node this rationale
    could plausibly be citing. Callers should include BOTH a node's
    name AND its content properties concatenated together -- a real
    gap found while testing this: a rationale legitimately quoting a
    node's NAME (not just its content body) was false-flagged as
    ungrounded, because only content text was being checked against.

    Returns one entry per extracted quote: whether it was found
    verbatim (whitespace-normalized) in ANY of the provided node
    texts, and which one if so.
    """
    quotes = extract_quoted_claims(rationale)
    normalized_contents = {nid: _normalize(text) for nid, text in node_contents.items()}

    results = []
    for quote in quotes:
        norm_quote = _normalize(quote)
        matched_node = None
        for nid, norm_content in normalized_contents.items():
            if norm_quote in norm_content:
                matched_node = nid
                break
        results.append({"quote": quote, "found": matched_node is not None, "matched_node": matched_node})
    return results


def summarize_check(results: list[dict]) -> str:
    if not results:
        return ("No quoted claims found in this rationale. If the reasoning depends on a "
                "content difference between nodes, the prompt requires a verbatim quote -- "
                "its absence here means that requirement wasn't followed, not that the "
                "reasoning is necessarily wrong.")
    ungrounded = [r for r in results if not r["found"]]
    if not ungrounded:
        return f"All {len(results)} quoted claim(s) verified present in real node content."
    lines = [f"{len(ungrounded)}/{len(results)} quoted claim(s) NOT found in any real node content:"]
    for r in ungrounded:
        lines.append(f"  FABRICATED OR MISQUOTED: {r['quote']!r}")
    return "\n".join(lines)
