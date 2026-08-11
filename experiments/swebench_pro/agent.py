"""
The agent both arms run. Identical tools, identical budget, identical
decoding -- the *only* difference between arms is whether a block of
retrieved prior-fix context is present in the first user message.

WHY EDIT TOOLS RATHER THAN "EMIT A UNIFIED DIFF"

Asking a model to write a valid `git apply`-able diff by hand conflates two
skills: fixing the bug, and counting context lines. Open-weight models fail
the second far more often than the first, and a patch that fails to apply
grades identically to a wrong answer. Since both arms would suffer that
equally it would not flip the comparison, but it would push absolute
accuracy toward zero and leave nothing to compare. So the agent edits by
exact string replacement and the diff is generated mechanically here, where
it is always well-formed.

TOOL OUTPUT IS CAPPED (search hits, file lines). Uncapped, one `search` over
ansible can return thousands of lines and a single call dominates an
instance's token count -- the measurement would then be about who got
unlucky with a broad regex, not about who needed less exploration.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Optional

from app.services import code_index
from app.services.code_index import BINARY_EXT, SKIP_DIRS

# How many times an episode may recover from a provider error by dropping
# its last exchange. Bounded: if the conversation is unrecoverable, repeated
# trimming would silently eat the whole history rather than fail.
MAX_RECOVERIES = 5

# Per-request wall clock. Without it a non-responding provider freezes the
# whole sweep with no way to interrupt from above.
#
# THE CAPS BELOW EXIST FOR RATE LIMITS, NOT TIDINESS. The provider enforces a
# TOKENS-PER-MINUTE ceiling. An episode that sent 100-180K tokens breached it
# constantly, every breach became a 429, and the retries turned a 5-minute
# instance into an hour. Measured live: 4 of 5 probe calls fine, the 5th
# "429 Tokens per minute limit reached". Cutting tool output is what makes
# the run finish -- the caps are applied IDENTICALLY to every arm, so the
# comparison is unaffected, only the absolute cost.
REQUEST_TIMEOUT = 180.0
MAX_RETRIES = 4
MAX_BACKOFF = 20.0   # worst case 4+8+16 = 28s per call, not 124s

# Loosened from a 5-min-target to a 10-min one, and specifically NOW that
# list_symbols/read_symbol exist: the dumps these caps bound (raw search
# hits, raw read_file lines) are the expensive path a model should mostly be
# ABLE to avoid once it can ask for one function by name instead. Measured:
# median read_symbol response ~170 tokens vs a 2000-char/~500-token read_file
# page, so headroom here is being spent on the cases that still need a raw
# dump (config/data files, unsupported languages), not reintroducing the
# cost these caps existed to prevent.
MAX_SEARCH_HITS = 30
MAX_READ_LINES = 160
MAX_TOOL_CHARS = 2800
MAX_LIST_ENTRIES = 60
# Bounds the no-match case. A regex that hits returns early at
# MAX_SEARCH_HITS; a regex that misses otherwise walks the entire repo, and
# teleport-sized checkouts make that a multi-second tool call. Truncation is
# announced so the model knows to narrow with `path` rather than concluding
# the symbol does not exist.
MAX_SEARCH_FILES = 20_000
MAX_SEARCH_FILE_BYTES = 2_000_000


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, u) -> None:
        self.prompt_tokens += u.prompt_tokens
        self.completion_tokens += u.completion_tokens
        self.calls += 1


@dataclass
class AgentRun:
    instance_id: str
    arm: str
    patch: str
    usage: Usage
    steps: int
    tool_calls: list[str] = field(default_factory=list)
    files_edited: list[str] = field(default_factory=list)
    stop_reason: str = ""
    wall_seconds: float = 0.0
    retrieved: list[str] = field(default_factory=list)
    error: Optional[str] = None


class RepoSandbox:
    """A checkout on disk plus the four operations an agent needs on it.

    Read-only against everything except through `edit_file`, so the diff at
    the end is exactly the set of changes the model asked for -- there is no
    path by which a tool call mutates the tree without being recorded.
    """

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        # rel -> content before the agent touched it. None means the file did
        # not exist (created by the agent).
        self._original: dict[str, Optional[str]] = {}
        self._deleted: set[str] = set()
        # How many edits only landed via the whitespace-tolerant fallback.
        # Recorded rather than hidden: if this is high, the model is
        # systematically mis-reproducing indentation and that is worth knowing
        # independently of whether the run resolved.
        self.tolerant_edits = 0

    def _resolve(self, path: str) -> str:
        full = os.path.abspath(os.path.join(self.root, path.lstrip("/\\")))
        if not full.startswith(self.root):
            raise ValueError(f"path escapes repository: {path}")
        return full

    def list_dir(self, path: str = ".") -> str:
        full = self._resolve(path)
        if not os.path.isdir(full):
            return f"not a directory: {path}"
        entries = sorted(os.listdir(full))
        entries = [e for e in entries if e not in SKIP_DIRS]
        out = []
        for e in entries[:MAX_LIST_ENTRIES]:
            kind = "dir " if os.path.isdir(os.path.join(full, e)) else "file"
            out.append(f"{kind} {e}")
        if len(entries) > MAX_LIST_ENTRIES:
            out.append(f"... {len(entries) - MAX_LIST_ENTRIES} more")
        return "\n".join(out) or "(empty)"

    def search(self, pattern: str, path: str = ".") -> str:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return f"bad regex: {exc}"
        root = self._resolve(path)
        hits: list[str] = []
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for fn in filenames:
                if os.path.splitext(fn)[1].lower() in BINARY_EXT:
                    continue
                full = os.path.join(dirpath, fn)
                try:
                    if os.path.getsize(full) > MAX_SEARCH_FILE_BYTES:
                        continue  # minified bundle or lockfile; never a useful hit
                    with open(full, "rb") as f:
                        raw = f.read(MAX_SEARCH_FILE_BYTES)
                except OSError:
                    continue
                # Extension is a hint, not proof. A NUL in the head means
                # binary regardless of what it is called, and matching a
                # regex against mojibake produces noise the model then has
                # to spend steps discounting.
                if b"\x00" in raw[:4096]:
                    continue
                scanned += 1
                if scanned > MAX_SEARCH_FILES:
                    hits.append(f"... stopped after scanning {MAX_SEARCH_FILES} files; "
                                "narrow the search with the `path` argument")
                    return "\n".join(hits) if hits else "no matches (search truncated)"
                rel = os.path.relpath(full, self.root).replace("\\", "/")
                for i, line in enumerate(raw.decode("utf-8", errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{rel}:{i}: {line.rstrip()[:200]}")
                        if len(hits) >= MAX_SEARCH_HITS:
                            hits.append(f"... truncated at {MAX_SEARCH_HITS} hits")
                            return "\n".join(hits)
        return "\n".join(hits) if hits else "no matches"

    def read_file(self, path: str, start_line: int = 1, num_lines: int = MAX_READ_LINES) -> str:
        full = self._resolve(path)
        if not os.path.isfile(full):
            return f"not a file: {path}"
        num_lines = min(int(num_lines or MAX_READ_LINES), MAX_READ_LINES)
        start_line = max(1, int(start_line or 1))
        with open(full, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        chunk = lines[start_line - 1 : start_line - 1 + num_lines]
        if not chunk:
            return f"{path} has {len(lines)} lines; nothing at line {start_line}"
        body = "".join(f"{start_line + i:6d}| {ln}" for i, ln in enumerate(chunk))
        tail = ""
        if start_line - 1 + num_lines < len(lines):
            tail = f"\n... file continues to line {len(lines)}"
        return body[:MAX_TOOL_CHARS] + tail

    def list_symbols(self, path: str) -> str:
        """Outline of a file's functions/classes/methods, with line ranges --
        so the model can see what a file contains and jump straight to
        read_symbol instead of paging through read_file to find it."""
        full = self._resolve(path)
        if not os.path.isfile(full):
            return f"not a file: {path}"
        try:
            with open(full, "rb") as f:
                source = f.read()
        except OSError as exc:
            return f"could not read {path}: {exc}"
        syms = code_index.outline(source, path)
        if syms is None:
            return (f"{path}: no symbol index for this file type -- "
                    f"use read_file instead")
        if not syms:
            return f"{path}: no functions/classes found (use read_file)"
        return "\n".join(f"{s.kind:<10} {s.qualified_name:<40} lines {s.start_line}-{s.end_line}"
                         for s in syms)

    def read_symbol(self, path: str, name: str) -> str:
        """The exact source of ONE function/class/method, not the whole
        file. A byte-exact slice of the real file -- never summarized --
        so it can be wrong about which bytes (a parse gap) but can never
        hallucinate or drop a line the way an LLM-summarized version could."""
        full = self._resolve(path)
        if not os.path.isfile(full):
            return f"not a file: {path}"
        try:
            with open(full, "rb") as f:
                source = f.read()
        except OSError as exc:
            return f"could not read {path}: {exc}"
        matches = code_index.find_symbol(source, path, name)
        if matches is None:
            return (f"{path}: no symbol index for this file type -- "
                    f"use read_file instead")
        if not matches:
            return (f"no symbol named '{name}' in {path}. Use list_symbols "
                    f"to see what is actually there.")
        if len(matches) > 1:
            options = ", ".join(m.qualified_name for m in matches)
            return (f"'{name}' is ambiguous in {path} ({options}) -- "
                    f"use the qualified Class.method form")
        s = matches[0]
        body = source[s.start_byte:s.end_byte].decode("utf-8", errors="replace")
        return (f"{path}:{s.start_line}-{s.end_line}  {s.kind} {s.qualified_name}\n"
                + body[:MAX_TOOL_CHARS])

    @staticmethod
    def _width(lead: str) -> int:
        """Indent width with tabs normalised, so a ladder can be built across
        snippets that mix conventions."""
        return len(lead.replace("\t", "    "))

    @staticmethod
    def _file_indent_unit(text: str) -> str:
        """One indent LEVEL as this file writes it: a tab, or N spaces.

        The step BETWEEN distinct indent widths, not the smallest width. A
        file whose shallowest indented line sits at 8 spaces still indents in
        steps of 4, and reading 8 as the unit halves every computed depth.
        """
        lines = [l for l in text.splitlines() if l.strip() and l[:1] in (" ", "\t")]
        if any(l.startswith("\t") for l in lines):
            return "\t"
        widths = sorted({len(l) - len(l.lstrip(" ")) for l in lines} - {0})
        if len(widths) >= 2:
            from functools import reduce
            from math import gcd
            step = reduce(gcd, [b - a for a, b in zip(widths, widths[1:])])
            return " " * (step or widths[0])
        return " " * (widths[0] if widths else 4)

    @classmethod
    def _reindent(cls, text: str, old_lines: list[str], anchor: int,
                  file_indent: str, file_unit: str) -> str:
        """
        Re-anchor `text` to the file's indentation, translating DEPTH.

        Depth is read off a LADDER of the distinct indent widths present in
        the model's own snippet, not by dividing by a guessed "unit". The
        divide-by-unit version flattened every nested line whenever the
        snippet sat deeper than one level -- `depth = len(rel) // len(unit)`
        floors to 0 when the unit is mis-measured as the base indent -- which
        produced syntactically invalid Python that still reported "edited",
        then failed with IndentationError and 0 tests parsed. A ladder needs
        no unit at all on the model's side: position in the sorted list of
        widths IS the level.
        """
        widths = sorted({cls._width(l[: len(l) - len(l.lstrip())])
                         for l in text.splitlines() + old_lines if l.strip()})
        anchor_line = old_lines[anchor] if old_lines else ""
        anchor_w = cls._width(anchor_line[: len(anchor_line) - len(anchor_line.lstrip())])
        base = widths.index(anchor_w) if anchor_w in widths else 0

        out = []
        for line in text.splitlines():
            if not line.strip():
                out.append("")
                continue
            lead = line[: len(line) - len(line.lstrip())]
            depth = max(0, widths.index(cls._width(lead)) - base)
            out.append(file_indent + file_unit * depth + line[len(lead):])
        return "\n".join(out)

    def _tolerant_match(self, file_lines: list[str], old_lines: list[str]):
        """
        Locate `old_lines` ignoring leading/trailing whitespace.

        WHY THIS FALLBACK EXISTS -- it is not convenience, it was losing runs.
        Measured across three episodes: 16 edit_file calls, every one
        rejected, in the rhythm `edit -> read -> edit -> read`. The agent had
        located the right code and could not reproduce it byte-for-byte. Go is
        38% of this corpus and is TAB-indented; a model that emits four spaces
        where the file has a tab can never match, no matter how correct its
        fix is. Those runs graded as `no_patch` -- indistinguishable from "the
        model had no idea" -- so a formatting technicality was being recorded
        as a reasoning failure.

        Whitespace-insensitive, but NOT content-insensitive: every non-space
        character must still match exactly, and the match must still be
        unique. This never guesses at which code was meant.
        """
        key = [l.strip() for l in old_lines]
        if not any(key):
            return []  # all-blank anchor matches everywhere; refuse
        hits = []
        for i in range(len(file_lines) - len(old_lines) + 1):
            if [file_lines[i + j].strip() for j in range(len(old_lines))] == key:
                hits.append(i)
        return hits

    def edit_file(self, path: str, old_str: str, new_str: str) -> str:
        full = self._resolve(path)
        if not os.path.isfile(full):
            return (f"not a file: {path}. If this file should not exist yet, "
                    f"use create_file to author it.")
        with open(full, encoding="utf-8", errors="replace") as f:
            content = f.read()
        rel = os.path.relpath(full, self.root).replace("\\", "/")

        count = content.count(old_str)
        if count == 1:
            written = content.replace(old_str, new_str)
            self._original.setdefault(rel, content)
            with open(full, "w", encoding="utf-8", newline="") as f:
                f.write(written)
            return f"edited {rel}" + self._syntax_note(rel, written)
        if count > 1:
            return (f"old_str appears {count} times; include surrounding lines to "
                    "make it unique.")

        # Exact match failed. Retry ignoring indentation and trailing spaces.
        file_lines = content.splitlines(keepends=True)
        old_lines = old_str.splitlines()
        if not old_lines:
            return "old_str is empty -- use create_file to author a new file."
        hits = self._tolerant_match(file_lines, old_lines)
        if not hits:
            return ("old_str not found. Whitespace is ignored when matching, so "
                    "the difference is in the code itself -- read the region "
                    "again and copy it exactly.")
        if len(hits) > 1:
            return (f"old_str matches {len(hits)} places (ignoring whitespace); "
                    "include surrounding lines to make it unique.")

        i = hits[0]
        n = len(old_lines)
        anchor = next((j for j, l in enumerate(old_lines) if l.strip()), 0)
        old_indent = old_lines[anchor][: len(old_lines[anchor]) - len(old_lines[anchor].lstrip())]
        file_line = file_lines[i + anchor]
        file_indent = file_line[: len(file_line) - len(file_line.lstrip())]

        replacement = self._reindent(
            new_str, old_lines, anchor, file_indent, self._file_indent_unit(content))
        matched = "".join(file_lines[i:i + n])
        if matched.endswith("\n") and not replacement.endswith("\n"):
            replacement += "\n"

        written = "".join(file_lines[:i]) + replacement + "".join(file_lines[i + n:])
        self._original.setdefault(rel, content)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(written)
        self.tolerant_edits += 1
        return (f"edited {rel} (matched ignoring indentation; re-indented to the "
                f"file's own style)") + self._syntax_note(rel, written)

    @staticmethod
    def _syntax_note(rel: str, written: str) -> str:
        """
        Advisory, not a refusal -- appended to the tool RESULT so the model
        sees it on its own next turn and can decide whether to act, the same
        pattern already used for ambiguous-match and ORDER-BY-index style
        messages elsewhere in this file. Never blocks the write itself:
        rolling back would need to prove the error was CAUSED by this edit
        rather than pre-existing elsewhere in a large file, which a global
        error count cannot do reliably, and a false rollback would be worse
        than a false warning.

        WHY THIS MATTERS FOR RESOLUTION RATE, not just tidiness: a patch that
        does not parse is a GUARANTEED f2p_failed -- the grading harness
        cannot even import/build the module. Every run this session that hit
        that outcome only discovered it from the external Docker grading
        step, after `finish`/`subgoal_done` had already been called on the
        broken result, with no tool calls left to fix it. Surfacing it here,
        one parse (milliseconds, no Docker), gives the model the chance to
        self-correct inside the same episode instead.
        """
        errs = code_index.syntax_errors(written.encode("utf-8", errors="replace"), rel)
        if errs is None or errs[0] == 0:
            return ""
        count, line = errs
        return (f"\n[warning: {rel} now has {count} syntax error(s), first near "
                f"line {line} -- this will fail to run as-is]")

    def create_file(self, path: str, content: str) -> str:
        """
        Author a file that does not exist yet.

        Without this the agent could not solve 243 of the 731 corpus
        instances -- 33.2% -- because their gold patch adds a file and
        edit_file refuses any path that is not already a file. Those
        instances were not hard, they were IMPOSSIBLE: the hidden test
        imports a module the agent had no way to write, so every arm failed
        identically no matter how good retrieval was. They also poisoned the
        ablation, contributing guaranteed concordant failures and zero
        discordant pairs.

        Parent directories are created too: a new module frequently lands in
        a new package directory (src/hooks/, conf/mime/), and failing on the
        missing directory would reintroduce the same dead end one level up.
        """
        full = self._resolve(path)
        rel = os.path.relpath(full, self.root).replace("\\", "/")
        if os.path.isfile(full):
            return (f"{rel} already exists -- use edit_file to modify it, or "
                    f"delete_file first if it must be replaced wholesale.")
        os.makedirs(os.path.dirname(full) or self.root, exist_ok=True)
        # None (not "") marks "did not exist before", which is what diff()
        # needs to emit a `new file mode` header. An empty string would be
        # indistinguishable from an existing empty file.
        self._original.setdefault(rel, None)
        with open(full, "w", encoding="utf-8", newline="") as f:
            f.write(content)
        return f"created {rel} ({len(content)} bytes)" + self._syntax_note(rel, content)

    def delete_file(self, path: str) -> str:
        """Remove a file. 18 corpus instances (2.5%) delete one; combined with
        create_file this also covers the 10 that rename."""
        full = self._resolve(path)
        if not os.path.isfile(full):
            return f"not a file: {path}"
        rel = os.path.relpath(full, self.root).replace("\\", "/")
        with open(full, encoding="utf-8", errors="replace") as f:
            self._original.setdefault(rel, f.read())
        os.remove(full)
        self._deleted.add(rel)
        return f"deleted {rel}"

    def edited_files(self) -> list[str]:
        return sorted(self._original)

    def diff(self) -> str:
        """
        A `git apply`-compatible patch built mechanically from before/after
        content, so it is well-formed by construction.

        Creations and deletions need their own git headers. `git apply`
        rejects an add whose header claims a source file (`--- a/x`) when no
        such file exists, and rejects a delete that does not say so -- and a
        patch that fails to apply grades identically to a wrong answer, which
        would have silently undone the whole point of adding create_file.
        """
        parts = []
        for rel, before in sorted(self._original.items()):
            path = os.path.join(self.root, rel)
            deleted = rel in self._deleted
            created = before is None
            after = ""
            if not deleted and os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as f:
                    after = f.read()
            if (before or "") == after and not deleted and not created:
                continue

            hunks = difflib.unified_diff(
                ("" if created else (before or "")).splitlines(keepends=True),
                ("" if deleted else after).splitlines(keepends=True),
                fromfile="/dev/null" if created else f"a/{rel}",
                tofile="/dev/null" if deleted else f"b/{rel}",
                n=3,
            )
            # A source line with no trailing newline arrives from difflib as a
            # piece that does not end in "\n". Joining those directly fuses
            # the last '-' line onto the next '+' line ("-two+TWO") and git
            # rejects the whole patch as corrupt; appending a bare "\n" at the
            # end only papers over the final piece and still loses the marker
            # git needs. 127 of 4853 files in the ansible tree (2.6%) have no
            # trailing newline -- including changelog fragments, which its
            # gold patches always touch -- and `git apply` is all-or-nothing,
            # so one such file killed an entire multi-file patch. Deleting one
            # failed every time.
            pieces = []
            for piece in hunks:
                pieces.append(piece if piece.endswith("\n")
                              else piece + "\n\\ No newline at end of file\n")
            body = "".join(pieces)
            if not body:
                continue
            header = f"diff --git a/{rel} b/{rel}\n"
            if created:
                header += "new file mode 100644\n"
            elif deleted:
                header += "deleted file mode 100644\n"
            parts.append(header + body)
        return "".join(parts)


TOOLS = [
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List files and directories at a path in the repository.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Repo-relative directory, e.g. 'lib/ansible/cli'"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search",
        "description": "Regex search across repository source files. Returns path:line: text.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string", "description": "Python regex"},
            "path": {"type": "string", "description": "Optional subdirectory to limit the search"}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a slice of a file with line numbers.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "num_lines": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "list_symbols",
        "description": ("List the functions/classes/methods in a Python, Go, "
                        "JS or TS file with their line ranges, WITHOUT reading "
                        "the whole file. Use this before read_file on any file "
                        "over ~100 lines."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "read_symbol",
        "description": ("Read the exact source of ONE function, class or method "
                        "by name (or Class.method), instead of the whole file. "
                        "Prefer this over read_file when you already know which "
                        "symbol you need -- it costs a fraction of the tokens."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "name": {"type": "string", "description": "Symbol name, or Class.method"}},
            "required": ["path", "name"]}}},
    {"type": "function", "function": {
        "name": "edit_file",
        "description": ("Replace old_str with new_str in a file. old_str must appear "
                        "exactly once, byte for byte."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_str": {"type": "string"},
            "new_str": {"type": "string"}},
            "required": ["path", "old_str", "new_str"]}}},
    {"type": "function", "function": {
        "name": "create_file",
        "description": ("Create a NEW file that does not exist yet, with the given "
                        "content. Parent directories are created automatically. Use "
                        "this when the fix requires adding a module, not editing one."),
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Repo-relative path for the new file"},
            "content": {"type": "string", "description": "Full contents of the file"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "delete_file",
        "description": "Delete an existing file from the repository.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "finish",
        "description": "Call when the fix is complete. Ends the episode.",
        "parameters": {"type": "object", "properties": {
            "summary": {"type": "string"}}, "required": ["summary"]}}},
]

SYSTEM = """You are fixing a real bug in the {repo} repository, checked out at the \
commit where the issue was reported.

Work by locating the relevant source, reading enough of it to understand the \
current behaviour, then making the change that fixes the issue. Hidden \
tests will be run against your change: they were written from the issue \
description, so implement what the issue actually asks for, not a narrower \
special case.

Rules:
- If the issue calls for a module, hook, helper or config file that does not \
exist yet, CREATE it with create_file. Roughly a third of issues like this are \
fixed by adding a file, not by editing one -- if you cannot find the thing the \
issue describes, consider that it may need to be written rather than found.
- Edit source files only. Do not edit anything under test/ -- test changes are \
discarded before grading and waste your budget.
- PREFER list_symbols + read_symbol OVER read_file for any Python, Go, JS or \
TS file over ~100 lines. read_symbol returns one function's exact source for \
a fraction of the tokens read_file would spend on the surrounding file. Use \
read_file only for config/data files or when you need to see a file's overall \
shape rather than one named symbol.
- You have at most {max_steps} tool calls. Spend them on locating the right code, \
not on reading whole directories.
- Call finish when done.
"""

USER = """Issue to fix:

{problem_statement}
{memory_block}
Begin."""


class Agent:
    def __init__(self, client, model: str, max_steps: int = 25, temperature: float = 0.0):
        self._client = client
        self._model = model
        self._max_steps = max_steps
        self._temperature = temperature

    def run(self, instance: dict, sandbox: RepoSandbox, arm: str,
            memory_block: str = "", retrieved: Optional[list[str]] = None) -> AgentRun:
        t0 = time.time()
        usage = Usage()
        messages = [
            {"role": "system", "content": SYSTEM.format(
                repo=instance["repo"], max_steps=self._max_steps)},
            {"role": "user", "content": USER.format(
                problem_statement=instance["problem_statement"],
                memory_block=f"\n{memory_block}" if memory_block else "")},
        ]
        tool_log: list[str] = []
        stop_reason = "step_budget"
        error = None

        recoveries = 0
        for step in range(self._max_steps):
            try:
                resp = self._complete(messages)
            except Exception as exc:  # noqa: BLE001
                # Persist the exact request that failed. A provider 400 that
                # cannot be reproduced synthetically can only be diagnosed
                # from the real payload, and re-running to catch it costs a
                # container pull plus both arms.
                self._dump_failed_request(instance, arm, step, messages, exc)

                # RECOVERY: drop the last tool exchange and keep going.
                #
                # Measured, not guessed. Replaying a captured failing payload:
                # 68 messages OK, 69 OK, 70 -> 400; and appending a SYNTHETIC
                # tool pair to the same 68 also OK. So the conversation minus
                # its final exchange is accepted, and the episode can continue
                # from there. Without this the run died at step 34 of 40 in
                # both arms with `finish` never reached, which is not a task
                # failure but was being recorded as one.
                #
                # The dropped exchange is a record of an action ALREADY TAKEN
                # -- the sandbox edit persists -- so the cost is that the model
                # may not recall doing it and could repeat it. edit_file is
                # idempotent against an already-applied change (old_str stops
                # matching), so a repeat fails loudly instead of corrupting.
                if recoveries < MAX_RECOVERIES and len(messages) > 4:
                    dropped = self._drop_last_exchange(messages)
                    recoveries += 1
                    log_note = (f"[a provider error occurred; {dropped} message(s) "
                                f"of history were dropped to recover. Any edit you "
                                f"already made is still applied.]")
                    messages.append({"role": "user", "content": log_note})
                    tool_log.append("__recovered__")
                    continue
                error, stop_reason = f"{type(exc).__name__}: {exc}", "api_error"
                break

            if resp.usage:
                usage.add(resp.usage)
            msg = resp.choices[0].message
            calls = msg.tool_calls or []

            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": c.id, "type": "function",
                     "function": {"name": c.function.name, "arguments": c.function.arguments}}
                    for c in calls
                ] or None,
            })
            if not calls:
                # No tool call and no finish: the model has stopped acting.
                # Nudging once would change the token accounting between arms
                # depending on who stalls more, so the episode just ends.
                stop_reason = "no_tool_call"
                break

            done = False
            for call in calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                tool_log.append(name)
                result, done_now = self._dispatch(name, args, sandbox)
                done = done or done_now
                messages.append({
                    "role": "tool", "tool_call_id": call.id,
                    "content": result[:MAX_TOOL_CHARS] + self._budget_note(
                        step, sandbox),
                })
            if done:
                stop_reason = "finished"
                break

        return AgentRun(
            instance_id=instance["instance_id"], arm=arm, patch=sandbox.diff(),
            usage=usage, steps=usage.calls, tool_calls=tool_log,
            files_edited=sandbox.edited_files(), stop_reason=stop_reason,
            wall_seconds=time.time() - t0, retrieved=retrieved or [], error=error,
        )

    @staticmethod
    def _drop_last_exchange(messages: list[dict]) -> int:
        """
        Remove the trailing assistant-with-tool_calls and its tool results.

        Must remove them TOGETHER. A tool result whose assistant message is
        gone, or an assistant tool_call with no matching result, is a
        malformed conversation that every provider rejects -- so a partial
        drop would turn one recoverable error into a permanent one.
        """
        removed = 0
        while messages and messages[-1].get("role") == "tool":
            messages.pop()
            removed += 1
        if messages and messages[-1].get("role") == "assistant":
            messages.pop()
            removed += 1
        return removed

    @staticmethod
    def _dump_failed_request(instance: dict, arm: str, step: int,
                             messages: list[dict], exc: Exception) -> None:
        """Best-effort: never let diagnostics kill the run they diagnose."""
        try:
            path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                f"failed_request_{instance['instance_id'][:40]}_{arm}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "instance_id": instance["instance_id"], "arm": arm,
                    "step": step, "error": f"{type(exc).__name__}: {exc}",
                    "n_messages": len(messages),
                    "approx_chars": sum(len(str(m)) for m in messages),
                    "roles": [m.get("role") for m in messages],
                    "messages": messages,
                }, f, indent=2, default=str)
        except Exception:  # noqa: BLE001, S110
            pass

    def _complete(self, messages: list[dict]):
        """
        Retry transient upstream failures instead of ending the episode.

        A 429 from the provider mid-episode is not a property of the arm, but
        without a retry it truncates that arm's run and its token count --
        which is exactly the quantity under measurement. In the pilot this
        killed a baseline run at step 4 and made it look 20x cheaper than the
        memory arm. Retrying is what keeps the comparison about memory.
        """
        last: Optional[Exception] = None
        for attempt in range(MAX_RETRIES):
            try:
                return self._client.chat.completions.create(
                    model=self._model, messages=messages, tools=TOOLS,
                    temperature=self._temperature, max_tokens=2000,
                    # A request with no timeout blocks the entire experiment
                    # indefinitely if the provider stops responding -- there is
                    # nothing above this that can interrupt it. Measured: one
                    # instance sat 77 minutes with the python process alive,
                    # ~4% CPU, no container, and the whole 20-instance sweep
                    # frozen behind it.
                    timeout=REQUEST_TIMEOUT,
                )
            except Exception as exc:  # noqa: BLE001
                last = exc
                # "provider_error" is the gateway reporting that ITS upstream
                # failed -- not a complaint about our request. It arrived as a
                # 400, which was not in this list, so the episode died at step
                # 34 of 40 with one edit made and `finish` never reached, in
                # BOTH arms. The failing payload could not be reproduced
                # synthetically (100 turns and 80K tokens pass; every message
                # shape passes), which is itself evidence it is upstream flake
                # rather than our shape. A genuinely malformed request still
                # fails all five attempts and surfaces normally.
                transient = any(s in str(exc).lower() for s in
                                ("429", "rate limit", "timeout", "502", "503",
                                 "504", "overloaded", "connection",
                                 "provider_error", "provider request failed"))
                if not transient or attempt == MAX_RETRIES - 1:
                    raise
                # Capped. Uncapped exponential backoff (4,8,16,32,64) costs
                # 124s per failed call, and at 40 steps that is 83 minutes of
                # sleeping in a single episode -- indistinguishable from a
                # hang, and it silently multiplies the cost of a whole sweep.
                # A 429 is a TOKENS-PER-MINUTE window, not congestion --
                # retrying in 4s just burns another attempt inside the same
                # window. Wait most of a window instead; anything else gets
                # the short backoff.
                rate_limited = "429" in str(exc) or "rate limit" in str(exc).lower()
                time.sleep(25.0 if rate_limited else min(4 * (2 ** attempt), MAX_BACKOFF))
        raise last  # type: ignore[misc]

    def _budget_note(self, step: int, sandbox: RepoSandbox) -> str:
        """
        Remaining-budget pressure, appended to every tool result.

        The pilot's failure mode was not wrong edits, it was no edits: one
        instance spent all 25 steps on `search` and finished with an empty
        patch, burning 148K tokens for nothing. A model that cannot see its
        budget cannot ration it. This is appended identically in both arms,
        so it changes the absolute numbers without favouring either.

        Proportional (last third), not the fixed "8" this used to be: a
        fixed threshold silently stops scaling whenever --max-steps changes
        -- it was calibrated against a 25-40 step budget and would have
        fired from literally the first step once the budget was cut to 18.
        """
        left = self._max_steps - step - 1
        if left > max(1, self._max_steps // 3):
            return ""
        note = f"\n\n[{left} tool calls remaining]"
        if not sandbox.edited_files():
            note += (" You have not edited any file yet. Stop searching and make "
                     "your best edit now with edit_file, then call finish.")
        return note

    @staticmethod
    def _dispatch(name: str, args: dict, sandbox: RepoSandbox) -> tuple[str, bool]:
        try:
            if name == "list_dir":
                return sandbox.list_dir(args.get("path", ".")), False
            if name == "search":
                return sandbox.search(args.get("pattern", ""), args.get("path", ".")), False
            if name == "read_file":
                return sandbox.read_file(args.get("path", ""),
                                         args.get("start_line", 1),
                                         args.get("num_lines", MAX_READ_LINES)), False
            if name == "list_symbols":
                return sandbox.list_symbols(args.get("path", "")), False
            if name == "read_symbol":
                return sandbox.read_symbol(args.get("path", ""), args.get("name", "")), False
            if name == "edit_file":
                return sandbox.edit_file(args.get("path", ""), args.get("old_str", ""),
                                         args.get("new_str", "")), False
            if name == "create_file":
                return sandbox.create_file(args.get("path", ""),
                                           args.get("content", "")), False
            if name == "delete_file":
                return sandbox.delete_file(args.get("path", "")), False
            if name == "finish":
                return "done", True
            return f"unknown tool: {name}", False
        except Exception as exc:  # noqa: BLE001
            return f"tool error: {type(exc).__name__}: {exc}", False
