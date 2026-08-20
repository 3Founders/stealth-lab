"""
Related-test-file discovery via naming convention, checked against a real
checkout on disk (ticket 14, memory-substrate map: "related tests" --
FILTER tier, "high precision for implementation tasks").

Same boundary discipline as call_graph.py and code_index.py: pure and
host-side, no DB, no LLM, no network. This module exists specifically so
local_retrieval.py's StructuralContext.related_tests field -- previously a
real, typed slot nothing populated -- has a real producer, the same way
get_current_working_set()/get_recent_commit_files() are the real producers
for open_files/recent_commit_files.

CONVENTION-BASED, NOT PROVEN. This is the same honest limitation
call_graph.py states for itself ("advisory signal, not a proof"): a repo
that doesn't follow one of the conventions below produces zero results
here, silently -- not a false positive, but a real recall gap. Every
candidate this module returns is checked against the actual filesystem
(os.path.exists), never a guessed path returned unconditionally -- so
precision stays high (ticket 14's own requirement for a FILTER-tier
signal) even though recall is bounded by convention-following.
"""
from __future__ import annotations

import os

from app.services.code_index import language_for

# Per-language naming conventions, most-common-first within each
# language. Python's variants reflect real, common layouts (pytest's
# own docs endorse both co-located and tests/-directory placement; the
# `_test.py` suffix is a real, if less common, alternative some projects
# use alongside `test_` prefix; `_e2e.py` is this very repo's own real
# convention for live-database integration tests, confirmed by checking
# real files -- test_procedures_e2e.py, test_applicability_e2e.py, etc.
# -- after an early version of this function missed them entirely).
def _candidate_test_paths(root: str, rel_path: str) -> list[str]:
    directory, filename = os.path.split(rel_path)
    stem, ext = os.path.splitext(filename)
    lang = language_for(rel_path)

    candidates: list[str] = []
    if lang == "python":
        candidates += [
            os.path.join(directory, f"test_{stem}{ext}"),
            os.path.join(directory, f"{stem}_test{ext}"),
            os.path.join(directory, f"test_{stem}_e2e{ext}"),
            os.path.join(directory, "tests", f"test_{stem}{ext}"),
            os.path.join(directory, "tests", f"test_{stem}_e2e{ext}"),
            os.path.join("tests", f"test_{stem}{ext}"),
            os.path.join("tests", f"test_{stem}_e2e{ext}"),
        ]
    elif lang == "go":
        candidates.append(os.path.join(directory, f"{stem}_test{ext}"))
    elif lang in ("javascript", "typescript", "tsx"):
        candidates += [
            os.path.join(directory, f"{stem}.test{ext}"),
            os.path.join(directory, f"{stem}.spec{ext}"),
            os.path.join(directory, "__tests__", f"{stem}.test{ext}"),
            os.path.join(directory, "__tests__", f"{filename}"),
            os.path.join(directory, "tests", f"{stem}.test{ext}"),
        ]
    # Unrecognized/unsupported language (language_for returns None, or a
    # language call_graph.py doesn't cover): no candidates, not an
    # error -- same "silently return nothing rather than guess" contract
    # every candidate below is already held to.

    return candidates


def related_test_files(root: str, rel_path: str) -> list[str]:
    """
    Real, filesystem-checked related test files for one source file.
    `root` is the repo checkout root; `rel_path` is a path relative to
    it (matching call_graph.py's own `seeds_in_file`/build_repo_symbol_index
    convention). Returns relative paths (forward-slash-normalized, same
    convention call_graph.py/local_retrieval.py already use), existing
    ones only -- never a guessed path the caller would have to verify
    itself.

    Deliberately does NOT search for tests referencing rel_path's actual
    SYMBOLS (e.g. grepping for `import stem` or `from stem import`) --
    that would catch more real cases but at real cost (a full-repo scan
    per call) and would blur into what call_graph.py's own reachability
    already partially covers for name-based association. Naming
    convention alone is what ticket 14 names as the FILTER-tier signal;
    a deeper, import-based version is real, separate future work, not
    silently substituted here.
    """
    found: list[str] = []
    for candidate in _candidate_test_paths(root, rel_path):
        if os.path.isfile(os.path.join(root, candidate)):
            found.append(candidate.replace(os.sep, "/"))
    return found


def related_test_files_for_many(root: str, rel_paths: list[str]) -> list[str]:
    """
    Convenience wrapper for local_retrieval.py's real use case: a caller
    typically has several files in scope (e.g. StructuralContext.open_files)
    and wants the union of their related tests, deduplicated, in a form
    directly assignable to StructuralContext.related_tests.
    """
    seen: set[str] = set()
    result: list[str] = []
    for rel_path in rel_paths:
        for test_path in related_test_files(root, rel_path):
            if test_path not in seen:
                seen.add(test_path)
                result.append(test_path)
    return result
