"""
HTN agent mechanics, exercised against REAL SWE-bench Verified diffs.

Every other HTN test uses hand-written toy content ("def f(): return 1").
That's fine for control-flow properties (retry counts, budget arithmetic,
thread attribution), but it never proves the agent's actual edit path --
read a real file, match a real SEARCH block, produce a real REPLACE, have
RepoSandbox assemble a real unified diff -- against text a small model
would really be asked to handle: real indentation, real quoting, a real
docstring or trailing blank line in the wrong place.

The three fixtures below are frozen, byte-real excerpts from
princeton-nlp/SWE-bench_Verified (`datasets.load_dataset(
"princeton-nlp/SWE-bench_Verified", split="test")`), chosen by filtering
for single-file, single-hunk, <700-char patches -- the smallest, cleanest
slice of a well-known, human-curated benchmark. `old_str`/`new_str` are the
gold patch's own hunk, parsed the same way `patch_format.diff_to_search_
replace` does (context + removed = SEARCH, context + added = REPLACE), so
they are exactly the SEARCH/REPLACE block a correct agent would need to
emit -- not a paraphrase. Frozen as literals (not fetched from HF at test
time) so this file has no network dependency and no docker dependency: it
runs in-process against a FakeClient and a tmp_path sandbox, the same way
every other HTN test does, in well under a second per case.

This deliberately does NOT run the real repo's test suite (FAIL_TO_PASS/
PASS_TO_PASS) -- that needs the real checkout and environment, which is
exactly the Docker-backed SWE-bench Pro harness's job, not a fast unit
test's. What this DOES prove: given the real problem statement and the
real target file, the HTN agent's plan -> execute -> edit_file -> patch
assembly pipeline reproduces the real gold change byte-for-byte.
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "experiments", "swebench_pro"))

from agent import RepoSandbox  # noqa: E402
from htn_agent import AugmentedHTNAgent  # noqa: E402


def _msg(content=None, tool_calls=None):
    tc = []
    for i, (name, args) in enumerate(tool_calls or []):
        tc.append(types.SimpleNamespace(
            id=f"c{i}", function=types.SimpleNamespace(
                name=name, arguments=json.dumps(args))))
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=content, tool_calls=tc or None))],
        usage=types.SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class FakeClient:
    """Replays a scripted list of responses. Only one node is ever ready
    at a time in these fixtures (single-file => one planned subgoal), so a
    plain sequential queue is sufficient -- no cross-node routing needed."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        import copy
        self.requests.append({**kw, "messages": copy.deepcopy(kw["messages"])})
        if not self.script:
            return _msg(tool_calls=[("subgoal_failed", {"reason": "script exhausted"})])
        return self.script.pop(0)


# Real, frozen SWE-bench Verified instances -- see module docstring for
# provenance and selection criteria. `old_str`/`new_str` are the gold
# patch's own hunk, unmodified.
REAL_INSTANCES = [
    {
        "instance_id": "psf__requests-1921",
        "repo": "psf/requests",
        "file_path": "requests/sessions.py",
        "problem_statement": (
            "Removing a default header of a session\n"
            "The docs say that you can prevent sending a session header by "
            "setting the headers value to None in the method's arguments. "
            "You would expect that this would work for session's default "
            "headers, too:\n\n"
            "    session = requests.Session()\n"
            "    session.headers['Accept-Encoding'] = None\n\n"
            "What happens is that \"None\" gets sent as the value of the "
            "header, instead of the header being omitted."
        ),
        "old_str": ("        if v is None:\n"
                    "            del merged_setting[k]\n"
                    "\n"
                    "    return merged_setting\n"
                    "\n"),
        "new_str": ("        if v is None:\n"
                    "            del merged_setting[k]\n"
                    "\n"
                    "    merged_setting = dict((k, v) for (k, v) in "
                    "merged_setting.items() if v is not None)\n"
                    "\n"
                    "    return merged_setting\n"
                    "\n"),
        # Substring that can ONLY appear if the real gold change landed --
        # not just any edit to the file.
        "gold_marker": "if v is not None)",
        # The hunk alone (8/12-space indents) is only valid nested inside
        # a function+for-loop, which the real file provides outside this
        # hunk's context window. Minimal real-shaped scaffolding so the
        # seed file is valid Python -- _verify_postcondition (htn_agent.py)
        # hard-gates subgoal_done on every edited file parsing cleanly, and
        # rightly refuses to accept an edit against an already-broken file.
        "file_prefix": ("def merge_setting(request_setting, session_setting, "
                        "dict_class=dict):\n"
                        "    merged_setting = dict_class()\n"
                        "    for (k, v) in merged_setting.items():\n"),
        "file_suffix": "",
    },
    {
        "instance_id": "sympy__sympy-22914",
        "repo": "sympy/sympy",
        "file_path": "sympy/printing/pycode.py",
        "problem_statement": (
            "PythonCodePrinter doesn't support Min and Max\n"
            "We can't generate python code for the sympy function Min and "
            "Max. For example:\n\n"
            "    from sympy import symbols, Min, pycode\n"
            "    a, b = symbols(\"a b\")\n"
            "    print(pycode(Min(a, b)))\n\n"
            "prints '# Not supported in Python: Min' instead of code. "
            "Similar to a prior fix for other functions, PythonCodePrinter "
            "needs _known_functions entries mapping Min -> min and "
            "Max -> max."
        ),
        "old_str": ("\n"
                    "_known_functions = {\n"
                    "    'Abs': 'abs',\n"
                    "}\n"
                    "_known_functions_math = {\n"
                    "    'acos': 'acos',"),
        "new_str": ("\n"
                    "_known_functions = {\n"
                    "    'Abs': 'abs',\n"
                    "    'Min': 'min',\n"
                    "    'Max': 'max',\n"
                    "}\n"
                    "_known_functions_math = {\n"
                    "    'acos': 'acos',"),
        "gold_marker": "'Min': 'min'",
        # The hunk's own context stops mid-dict (the real file closes
        # `_known_functions_math` outside this hunk's window) -- an
        # unclosed `{` is a genuine syntax error on its own, so a suffix
        # is needed to make the seed file valid Python. See the requests
        # fixture above for why this matters.
        "file_prefix": "",
        "file_suffix": "\n}\n",
    },
    {
        "instance_id": "django__django-14089",
        "repo": "django/django",
        "file_path": "django/utils/datastructures.py",
        "problem_statement": (
            "Allow calling reversed() on an OrderedSet\n"
            "Currently, OrderedSet isn't reversible (i.e. allowed to be "
            "passed as an argument to Python's reversed()). This would be "
            "natural to support given that OrderedSet is ordered. This "
            "should be straightforward to add by adding a __reversed__() "
            "method to OrderedSet."
        ),
        "old_str": ("    def __iter__(self):\n"
                    "        return iter(self.dict)\n"
                    "\n"
                    "    def __contains__(self, item):\n"
                    "        return item in self.dict\n"),
        "new_str": ("    def __iter__(self):\n"
                    "        return iter(self.dict)\n"
                    "\n"
                    "    def __reversed__(self):\n"
                    "        return reversed(self.dict)\n"
                    "\n"
                    "    def __contains__(self, item):\n"
                    "        return item in self.dict\n"),
        "gold_marker": "def __reversed__(self):",
        # 4-space indent is only valid inside a class body.
        "file_prefix": "class OrderedSet:\n    def __init__(self):\n        self.dict = {}\n\n",
        "file_suffix": "",
    },
]


def _sandbox(tmp_path, fixture: dict) -> RepoSandbox:
    """Seeds a tmp checkout with exactly the real file excerpt the gold
    patch touches -- a real slice of the real file, not synthetic
    stand-in text, so edit_file has to match real code shape (real
    indentation, real quoting) to succeed."""
    path = tmp_path / fixture["file_path"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(fixture["file_prefix"] + fixture["old_str"] + fixture["file_suffix"],
                    encoding="utf-8")
    return RepoSandbox(str(tmp_path))


@pytest.mark.parametrize("fixture", REAL_INSTANCES, ids=lambda f: f["instance_id"])
class TestRealSingleFileInstances:
    """One planned subgoal, scripted to emit the real gold SEARCH/REPLACE
    and stop -- verifying the plan -> execute -> edit_file -> patch
    pipeline against real diff content end to end, with no LLM and no
    Docker. Each case runs in well under a second; the whole
    parametrized class runs in a small fraction of the 30s budget."""

    def test_real_gold_edit_applies_and_is_captured(self, tmp_path, fixture):
        sandbox = _sandbox(tmp_path, fixture)
        instance = {
            "instance_id": fixture["instance_id"], "repo": fixture["repo"],
            "problem_statement": fixture["problem_statement"],
        }
        client = FakeClient([
            _msg(content=json.dumps([
                {"id": 1, "goal": f"Fix the issue described in the problem "
                                  f"statement by editing {fixture['file_path']}",
                 "deps": [], "requires": []},
            ])),
            _msg(tool_calls=[("edit_file", {
                "path": fixture["file_path"],
                "old_str": fixture["old_str"],
                "new_str": fixture["new_str"],
            })]),
            _msg(tool_calls=[("subgoal_done", {"summary": "applied the gold fix"})]),
        ])

        run = AugmentedHTNAgent(client, "m").run(instance, sandbox, "htn_memory")

        # The plan actually ran to completion, not just "didn't crash".
        assert run.stop_reason == "finished"
        nodes = run.htn["nodes"]
        assert len(nodes) == 1
        assert nodes[0]["status"] == "done"
        assert nodes[0]["attempts"] == 1

        # The real problem statement reached the planner call verbatim --
        # if this instance's own text isn't in the first request, the
        # planner never actually saw the real bug report.
        first_request_content = client.requests[0]["messages"][1]["content"]
        assert fixture["problem_statement"].splitlines()[0] in first_request_content

        # The file on disk now holds the real REPLACE text exactly, and
        # the assembled unified diff captures a change that could only be
        # the real gold fix (not just AN edit to the file).
        expected = fixture["file_prefix"] + fixture["new_str"] + fixture["file_suffix"]
        assert (tmp_path / fixture["file_path"]).read_text(encoding="utf-8") == expected
        assert fixture["file_path"] in run.files_edited
        added_lines = [ln for ln in run.patch.splitlines() if ln.startswith("+")]
        assert any(fixture["gold_marker"] in ln for ln in added_lines), (
            f"gold marker {fixture['gold_marker']!r} not found on any added "
            f"line of the assembled patch:\n{run.patch}")
