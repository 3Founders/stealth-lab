"""Stage S1 of the code cascade: hard file filters. Deterministic, free, applied to the git tree before any file is fetched
where the answer needs only the path, and to the bytes where it needs the content.

A file that fails here is never scored and never costs a model call. The list is deliberately conservative about what it
DROPS (vendored, generated, minified, lock, binary, tiny, huge): everything ambiguous survives to S2, where structure
decides. Test files survive too, but are labelled so S2 can weigh them down rather than lose them (how a project tests
its hard parts is exemplar material).
"""
from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from typing import Optional

MIN_FILE_BYTES = 300
MAX_FILE_BYTES = 200_000

LANGUAGE_BY_EXT: dict[str, str] = {
    ".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript",
    ".ts": "typescript", ".tsx": "typescript", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin",
    ".rb": "ruby", ".php": "php", ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".swift": "swift", ".scala": "scala", ".ex": "elixir", ".exs": "elixir",
}

_VENDORED = re.compile(
    r"(^|/)(node_modules|vendor|vendors|third_party|thirdparty|extern|external|dist|build|out|target|\.next|coverage|"
    r"__pycache__|\.tox|\.venv|venv|site-packages|migrations?|generated|gen|_generated|proto|protos)/"
    r"|\.min\.[a-z]+$|\.(lock|sum)$|(^|/)(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|poetry\.lock)$"
    r"|\.(pb|pb2|pb2_grpc|generated|gen)\.[a-z]+$|_pb2(_grpc)?\.py$|(^|/)setup\.py$|(^|/)conf\.py$|(^|/)__init__\.py$",
    re.IGNORECASE)
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec|specs|testing)/|(^|/)(test_[^/]+|[^/]+_test|[^/]+\.test|[^/]+\.spec)\.[a-z]+$|(^|/)conftest\.py$",
                        re.IGNORECASE)
_EXAMPLE_PATH = re.compile(r"(^|/)(examples?|samples?|demos?|docs?|documentation|benchmarks?|fixtures?|playground|tutorials?)/", re.IGNORECASE)
_GENERATED_MARKER = re.compile(r"@generated|DO NOT EDIT|Code generated .* DO NOT EDIT|auto-?generated|This file was (automatically )?generated",
                               re.IGNORECASE)
_HEAD_CHARS = 1_500


@dataclass(frozen=True)
class FileVerdict:
    path: str
    language: Optional[str]
    is_test: bool
    reason: Optional[str]          # None = survives S1
    is_example: bool = False       # examples/, docs/, benchmarks/ ...: kept, weighed down like tests

    @property
    def kept(self) -> bool:
        return self.reason is None


def language_of(path: str) -> Optional[str]:
    return LANGUAGE_BY_EXT.get(posixpath.splitext(path.lower())[1])


def is_test_path(path: str) -> bool:
    return bool(_TEST_PATH.search(path))


def is_example_path(path: str) -> bool:
    return bool(_EXAMPLE_PATH.search(path))


def role_of(path: str) -> str:
    """`test` | `example` | `source`. Tests and examples are kept (how a project tests or demonstrates its hard parts is worth
    having) but weighed down: the repository's own source should rank first."""
    return "test" if is_test_path(path) else "example" if is_example_path(path) else "source"


def path_verdict(path: str, size: Optional[int] = None) -> FileVerdict:
    """The decisions that need only the path (and, when the tree has it, the size)."""
    language = language_of(path)
    test = is_test_path(path)
    if language is None:
        return FileVerdict(path, None, test, "not_source_code", is_example_path(path))
    if _VENDORED.search(path):
        return FileVerdict(path, language, test, "vendored_generated_or_boilerplate_path", is_example_path(path))
    if size is not None and size > MAX_FILE_BYTES:
        return FileVerdict(path, language, test, "too_large", is_example_path(path))
    if size is not None and size < MIN_FILE_BYTES:
        return FileVerdict(path, language, test, "too_small", is_example_path(path))
    return FileVerdict(path, language, test, None, is_example_path(path))


def content_verdict(path: str, text: str) -> FileVerdict:
    """The decisions that need the bytes: binary, generated header, minified single line, size."""
    base = path_verdict(path, len(text.encode("utf-8", errors="ignore")))
    if not base.kept:
        return base
    if "\x00" in text[:4000]:
        return FileVerdict(path, base.language, base.is_test, "binary", base.is_example)
    if _GENERATED_MARKER.search(text[:_HEAD_CHARS]):
        return FileVerdict(path, base.language, base.is_test, "generated_marker", base.is_example)
    lines = text.count("\n") + 1
    if len(text) / max(1, lines) > 240:        # before the line count: a minified file IS one line
        return FileVerdict(path, base.language, base.is_test, "minified_or_data_blob", base.is_example)
    if lines < 8:
        return FileVerdict(path, base.language, base.is_test, "too_few_lines", base.is_example)
    return base
