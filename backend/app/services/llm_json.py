"""Parse a JSON object out of an LLM reply that may wrap it.

Providers often return the object inside a ```json fence, after a line of prose ("Here is the JSON:"), or
followed by a remark. A bare `json.loads` rejects all of these, and an extractor that treats that as "did not
parse" silently loses the item (observed in the step-3 ingestion pilot: BLOCKERS.md I4; the claim extractor's
own fix is `claim_extraction._parse_json_object`, cc455d8).

`parse_json_object` tries, in order: the whole reply; a fenced block anywhere in it (the first one that parses
to an object); the outermost `{...}` span. It returns the dict, or None when nothing parses to a JSON object.
It never repairs or guesses at a truncated object.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

_FENCE = re.compile(r"```[ \t]*(?:[A-Za-z0-9_-]+)?[ \t]*\r?\n?(.*?)```", re.S)


def _loads_object(text: str) -> Optional[dict[str, Any]]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def parse_json_object(text: Optional[str]) -> Optional[dict[str, Any]]:
    body = (text or "").strip()
    if not body:
        return None
    whole = _loads_object(body)
    if whole is not None:
        return whole
    for m in _FENCE.finditer(body):
        inner = _loads_object(m.group(1).strip())
        if inner is not None:
            return inner
    if body.startswith("```"):          # an opening fence with no closing one: drop the fence line
        rest = body.split("\n", 1)[1] if "\n" in body else ""
        inner = _loads_object(rest.strip())
        if inner is not None:
            return inner
    start, end = body.find("{"), body.rfind("}")
    if start != -1 and end > start:
        return _loads_object(body[start:end + 1])
    return None
