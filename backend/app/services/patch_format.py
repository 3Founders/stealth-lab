"""
Unified diff -> Aider-style SEARCH/REPLACE blocks.

WHY THIS CONVERSION EXISTS

The agent already EDITS by exact string replacement -- edit_file(path,
old_str, new_str) -- because asking a model to author a `git apply`-able
diff conflates fixing the bug with counting context lines, and small models
fail the second far more often than the first (agent.py's module docstring).

But the PRECEDENTS retrieved from the graph were still rendered as raw
unified diffs, hunk headers included. So the model read `@@ -42,7 +42,8 @@`
and then had to emit old_str/new_str: two formats, one of which carries
line-number arithmetic that is irrelevant to the edit and is exactly what
smaller models get wrong. Worse, an @@ header invites the model to reproduce
that shape in its own output, which the tool then rejects.

A SEARCH/REPLACE block is the same information with the arithmetic removed,
and it maps one-to-one onto the tool the agent actually has:

    SEARCH  -> old_str
    REPLACE -> new_str

Line numbers are dropped deliberately. They are not merely unnecessary --
they are wrong for the agent's file, whose contents differ from the
precedent's, so carrying them over is actively misleading.
"""
from __future__ import annotations

import re

_DIFF_FILE = re.compile(r"^diff --git a/(\S+)")
_HUNK = re.compile(r"^@@")


def diff_to_search_replace(patch: str, max_blocks: int | None = None,
                           max_chars: int | None = None) -> str:
    """
    Rebuild each hunk as the exact before/after text it represents.

    SEARCH is the hunk's context plus removed lines -- i.e. the file as it
    was. REPLACE is the context plus added lines -- the file as it became.
    Reconstructing both from one pass is what makes this lossless for the
    part that matters, without inventing line numbers.
    """
    blocks: list[str] = []
    cur_file: str | None = None
    search: list[str] = []
    replace: list[str] = []
    in_hunk = False

    def flush() -> None:
        nonlocal search, replace
        # A block with nothing on either side is a no-op hunk; a block with
        # no file is a malformed diff. Both are dropped rather than emitted
        # as an unusable stub the model would try to apply.
        if cur_file and (search or replace):
            blocks.append(
                f"{cur_file}\n<<<<<<< SEARCH\n"
                + "\n".join(search)
                + "\n=======\n"
                + "\n".join(replace)
                + "\n>>>>>>> REPLACE"
            )
        search, replace = [], []

    for line in (patch or "").splitlines():
        m = _DIFF_FILE.match(line)
        if m:
            flush()
            cur_file, in_hunk = m.group(1), False
            continue
        if _HUNK.match(line):
            flush()
            in_hunk = True
            continue
        if not in_hunk:
            continue  # index/---/+++ headers: '+'/'-' prefixed but not content
        if line.startswith("\\"):
            continue  # "\ No newline at end of file"
        if line.startswith("-"):
            search.append(line[1:])
        elif line.startswith("+"):
            replace.append(line[1:])
        elif line.startswith(" ") or line == "":
            search.append(line[1:] if line else "")
            replace.append(line[1:] if line else "")
        else:
            flush()
            in_hunk = False
    flush()

    if max_blocks is not None:
        dropped = len(blocks) - max_blocks
        blocks = blocks[:max_blocks]
        if dropped > 0:
            blocks.append(f"… [{dropped} further change block(s) omitted]")

    out = "\n\n".join(blocks)
    if max_chars is not None and len(out) > max_chars:
        out = out[:max_chars].rstrip() + "\n… [truncated]"
    return out
