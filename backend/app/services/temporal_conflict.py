"""
Date-range parsing and overlap detection, shared between two call sites:
  - knowledge_conflict.py, PRE-debate: compute the overlap fact in code
    and inject it directly into the trigger context, so the panel is
    TOLD the answer rather than expected to correctly perform date
    arithmetic itself while reasoning in prose.
  - verify_temporal_conflict_handling.py, POST-hoc: independent check
    that doesn't trust the panel complied with what it was told.

Both existing because of a confirmed real failure: a genuine, real
overlap between two promotion documents was misdiagnosed as an
unrelated false positive. The post-hoc check catches it after the
fact; this module's real contribution is catching it BEFORE the debate
even starts, by never asking the LLM to do the comparison at all.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

DATE_RANGE_RE = re.compile(
    r"ACTIVE FROM\s+(\d{1,2}/\d{1,2}/\d{4})\s+TO\s+(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE
)


def parse_date_range(text: str) -> Optional[tuple[datetime, datetime]]:
    m = DATE_RANGE_RE.search(text or "")
    if not m:
        return None
    try:
        return (datetime.strptime(m.group(1), "%m/%d/%Y"), datetime.strptime(m.group(2), "%m/%d/%Y"))
    except ValueError:
        return None


def compute_overlap(content_a: str, content_b: str) -> Optional[dict]:
    """
    Returns a structured overlap fact if both sides have a parseable
    date range AND they overlap, else None. The RETURNED VALUE is
    meant to be injected directly into the debate prompt as a stated
    fact -- the actual date comparison happens here, in real Python
    date math, once, not inside the LLM's own reasoning.
    """
    range_a = parse_date_range(content_a)
    range_b = parse_date_range(content_b)
    if range_a is None or range_b is None:
        return None

    overlap_start = max(range_a[0], range_b[0])
    overlap_end = min(range_a[1], range_b[1])
    if overlap_start > overlap_end:
        return None  # ranges genuinely don't overlap -- also a useful fact to know

    return {
        "range_a": f"{range_a[0].date()} to {range_a[1].date()}",
        "range_b": f"{range_b[0].date()} to {range_b[1].date()}",
        "overlap_start": str(overlap_start.date()),
        "overlap_end": str(overlap_end.date()),
        "overlap_days": (overlap_end - overlap_start).days + 1,
    }
