"""
Gate 3 deterministic task graders (backend/gate3_graders.py).

Pure, offline, subprocess-based graders for the Gate 3 A/B experiment's
two scored coding tasks and one non-applicable abstention probe. A grader
inspects ONLY the resulting repository state by running the repo's real
code in a fresh subprocess -- it never sees, and is never influenced by,
the agent's self-report, the runner's stop reason, or the patch text.

Gate 3 discipline:
- A task success is decided here, by deterministic behavioral checks --
  never by stop_reason == finished, patch non-emptiness, or agent claims.
- Expected schemas/constants are pinned here (they define the task
  contract); the grader is strict and every check must pass.

This module is NOT collected by pytest (no test_ prefix) and imports
nothing from app/ -- it exists so the offline test suite
(tests/test_gate3_graders_offline.py) and the live experiment driver
(test_gate3_ab_experiment_live.py) share one grader definition.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from string import Template


# ---------------------------------------------------------------------------
# Task 1: multi-tool lazy-schema conversion
# ---------------------------------------------------------------------------

TASK1_TASK_TEXT = (
    "The developer dashboard keeps timing out while loading our internal "
    "tool server. In tool_server.py, the default listing payload returned "
    "by list_tools() currently embeds every tool's full JSON input schema, "
    "which has become far too large for clients that are only doing "
    "discovery. Refactor the server so that:\n"
    "1. list_tools() returns only each tool's name and description (no "
    "input schema data of any kind in the listing payload), and\n"
    "2. a new function get_tool_schema(name) returns the full, exact JSON "
    "input schema for the single tool being asked about.\n"
    "Do not change what any existing tool does when invoked, and do not "
    "change call_tool(name, args)'s interface or behavior."
)

TASK1_STARTER = '''"""Internal tool server (leaky-listing version -- pre-refactor)."""

TOOL_DESCRIPTIONS = {
    "echo": "Echo the given text back unchanged.",
    "add": "Add two numbers together and return the sum.",
    "upper": "Convert the given text to upper case.",
    "count_chars": "Count the characters in the given text.",
}

TOOL_SCHEMAS = {
    "echo": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    "add": {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    },
    "upper": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    "count_chars": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}


def list_tools():
    """Default discovery listing (currently leaks full schemas)."""
    return [
        {"name": name, "description": TOOL_DESCRIPTIONS[name],
         "input_schema": TOOL_SCHEMAS[name]}
        for name in TOOL_DESCRIPTIONS
    ]


def call_tool(name, args):
    if name == "echo":
        return args["text"]
    if name == "add":
        return args["a"] + args["b"]
    if name == "upper":
        return args["text"].upper()
    if name == "count_chars":
        return len(args["text"])
    raise ValueError(f"unknown tool: {name}")
'''

TASK1_BEHAVIORS = [
    ["echo", {"text": "hi"}, "hi"],
    ["add", {"a": 2, "b": 3}, 5],
    ["upper", {"text": "abc"}, "ABC"],
    ["count_chars", {"text": "hello"}, 5],
]

# ---------------------------------------------------------------------------
# Task 2: lazy-schema conversion + new-tool extension
# ---------------------------------------------------------------------------

TASK2_TASK_TEXT = (
    "Our tool server in tool_server.py has the same problem the rest of "
    "the platform just fixed: list_tools() embeds every tool's full JSON "
    "input schema, so the discovery payload is bloated for clients. "
    "Refactor it so list_tools() returns only each tool's name and "
    "description, and add a get_tool_schema(name) function that returns "
    "the full, exact JSON input schema for the one tool asked about. "
    "While you are in there, the server needs a new tool: `concat`, which "
    "joins its two string arguments `left` and `right` in order. Register "
    "concat like the other tools, including a proper JSON input schema (an "
    "object with string properties `left` and `right`, both required), "
    "reachable through get_tool_schema. Do not change what any existing "
    "tool does when invoked, and do not change call_tool(name, args)'s "
    "interface."
)

TASK2_STARTER = '''"""Internal tool server (leaky-listing version -- pre-refactor)."""

TOOL_DESCRIPTIONS = {
    "echo": "Echo the given text back unchanged.",
    "greet": "Return a friendly greeting for the given name.",
    "count_chars": "Count the characters in the given text.",
}

TOOL_SCHEMAS = {
    "echo": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    "greet": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
    "count_chars": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}


def list_tools():
    """Default discovery listing (currently leaks full schemas)."""
    return [
        {"name": name, "description": TOOL_DESCRIPTIONS[name],
         "input_schema": TOOL_SCHEMAS[name]}
        for name in TOOL_DESCRIPTIONS
    ]


def call_tool(name, args):
    if name == "echo":
        return args["text"]
    if name == "greet":
        return f"Hello, {args['name']}!"
    if name == "count_chars":
        return len(args["text"])
    raise ValueError(f"unknown tool: {name}")
'''

TASK2_BEHAVIORS = [
    ["echo", {"text": "hi"}, "hi"],
    ["greet", {"name": "Ada"}, "Hello, Ada!"],
    ["count_chars", {"text": "hello"}, 5],
    ["concat", {"left": "a", "right": "b"}, "ab"],
]

TASK1_EXPECTED_SCHEMAS = {
    "echo": {"type": "object", "properties": {"text": {"type": "string"}},
             "required": ["text"]},
    "add": {"type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"]},
    "upper": {"type": "object", "properties": {"text": {"type": "string"}},
              "required": ["text"]},
    "count_chars": {"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
}

TASK2_EXPECTED_SCHEMAS = {
    "echo": {"type": "object", "properties": {"text": {"type": "string"}},
             "required": ["text"]},
    "greet": {"type": "object", "properties": {"name": {"type": "string"}},
              "required": ["name"]},
    "count_chars": {"type": "object", "properties": {"text": {"type": "string"}},
                    "required": ["text"]},
    "concat": {"type": "object",
               "properties": {"left": {"type": "string"},
                              "right": {"type": "string"}},
               "required": ["left", "right"]},
}

# ---------------------------------------------------------------------------
# Non-applicable abstention probe (NOT part of the 12 scored trials)
# ---------------------------------------------------------------------------

NA_TASK_TEXT = (
    "A bug was reported against our tool server: invoking the echo tool "
    "in tool_server.py returns nothing. Find and fix the bug so that "
    "calling echo echoes its text argument back. Do not change the "
    "behavior of any other tool, and do not change the listing or schema "
    "lookup mechanisms."
)

NA_STARTER = '''"""Internal tool server (already lazy-listing; echo is broken)."""

TOOL_DESCRIPTIONS = {
    "echo": "Echo the given text back unchanged.",
    "add": "Add two numbers together and return the sum.",
}

TOOL_SCHEMAS = {
    "echo": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    "add": {
        "type": "object",
        "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
        "required": ["a", "b"],
    },
}


def list_tools():
    """Already lightweight -- name + description only."""
    return [
        {"name": name, "description": TOOL_DESCRIPTIONS[name]}
        for name in TOOL_DESCRIPTIONS
    ]


def get_tool_schema(name):
    if name in TOOL_SCHEMAS:
        return TOOL_SCHEMAS[name]
    return None


def call_tool(name, args):
    if name == "echo":
        return None  # BUG: echoes nothing
    if name == "add":
        return args["a"] + args["b"]
    raise ValueError(f"unknown tool: {name}")
'''

NA_BEHAVIORS = [["add", {"a": 2, "b": 3}, 5]]

NA_EXPECTED_SCHEMAS: dict = {}

# ---------------------------------------------------------------------------
# Subprocess check script (shared template) + grader entry points
# ---------------------------------------------------------------------------

_CHECK_SCRIPT_TEMPLATE = '''
import json, sys
sys.path.insert(0, sys.argv[1])
results = {"repo_path": sys.argv[1], "checks": {}}
try:
    import tool_server
    results["checks"]["import_ok"] = True
except Exception as exc:
    results["checks"]["import_ok"] = False
    results["error"] = repr(exc)
    print(json.dumps(results))
    sys.exit(0)

EXPECTED = json.loads(r\'\'\'$expected_schemas\'\'\')
BEHAVIORS = json.loads(r\'\'\'$behaviors\'\'\')
EXTRA_CHECKS = json.loads(r\'\'\'$extra_checks\'\'\')

# 1. default listing is lightweight metadata only -- NO schema data of any
#    plausible spelling may leak into the listing payload.
try:
    listing = tool_server.list_tools()
    light = True
    if not isinstance(listing, list) or len(listing) == 0:
        light = False
    else:
        for entry in listing:
            if not isinstance(entry, dict) or "name" not in entry:
                light = False
                break
            for leaky in ("input_schema", "inputSchema", "parameters",
                          "schema", "input_schema_json", "json_schema",
                          "inputSchemaJson"):
                if leaky in entry:
                    light = False
    results["checks"]["listing_lightweight"] = light
except Exception as exc:
    results["checks"]["listing_lightweight"] = False
    results["error"] = repr(exc)

# 2. targeted schema retrieval exists and is EXACTLY correct for every
#    declared tool (including any newly required one).
try:
    exact = all(tool_server.get_tool_schema(name) == expected
                for name, expected in EXPECTED.items())
    results["checks"]["targeted_schema_exact"] = exact
except Exception as exc:
    results["checks"]["targeted_schema_exact"] = False
    results["error"] = repr(exc)

# 3. targeted retrieval handles an unknown tool name safely (raising or
#    returning None are both acceptable; a leaked or wrong schema is not).
try:
    try:
        unknown = tool_server.get_tool_schema("definitely_not_a_tool")
        ok = unknown is None
    except Exception:
        ok = True
    results["checks"]["unknown_tool_safe"] = ok
except Exception as exc:
    results["checks"]["unknown_tool_safe"] = False
    results["error"] = repr(exc)

# 4. ordinary tool invocation must keep working (regression gate), plus
#    any newly required tool behavior.
try:
    results["checks"]["call_tool_regression"] = all(
        tool_server.call_tool(name, args) == expected
        for name, args, expected in BEHAVIORS)
except Exception as exc:
    results["checks"]["call_tool_regression"] = False
    results["error"] = repr(exc)

# 5. any task-specific extra checks (e.g. the new tool is discoverable).
for check_name, check_src in EXTRA_CHECKS.items():
    try:
        results["checks"][check_name] = bool(eval(check_src, {"ts": tool_server}))
    except Exception as exc:
        results["checks"][check_name] = False
        results["error"] = repr(exc)

print(json.dumps(results))
'''

_TASKS = {
    "task1_lazy_multi_tool": {
        "task_text": TASK1_TASK_TEXT,
        "starter": TASK1_STARTER,
        "behaviors": TASK1_BEHAVIORS,
        "expected_schemas": TASK1_EXPECTED_SCHEMAS,
        "extra_checks": {},
    },
    "task2_lazy_new_tool": {
        "task_text": TASK2_TASK_TEXT,
        "starter": TASK2_STARTER,
        "behaviors": TASK2_BEHAVIORS,
        "expected_schemas": TASK2_EXPECTED_SCHEMAS,
        "extra_checks": {"concat_registered":
                         '"concat" in {e.get("name") for e in ts.list_tools()}'},
    },
    "na_broken_echo": {
        "task_text": NA_TASK_TEXT,
        "starter": NA_STARTER,
        "behaviors": NA_BEHAVIORS,
        "expected_schemas": NA_EXPECTED_SCHEMAS,
        "extra_checks": {
            "echo_fixed": 'ts.call_tool("echo", {"text": "hi"}) == "hi"',
            "listing_still_lightweight":
                'all("name" in e and "input_schema" not in e for e in ts.list_tools())',
        },
    },
}


def get_task(task_id: str) -> dict:
    return _TASKS[task_id]


def list_task_ids():
    return sorted(_TASKS)


def write_starter_repo(task_id: str, repo_path: str) -> None:
    """Materialize the task's fresh disposable starting repository."""
    task = get_task(task_id)
    repo = Path(repo_path)
    repo.mkdir(parents=True, exist_ok=True)
    (repo / "tool_server.py").write_text(task["starter"], encoding="utf-8")
    (repo / "README.md").write_text(
        f"Disposable Gate 3 experiment repository for {task_id}.\n"
        "Entry points: tool_server.py (list_tools, call_tool).\n",
        encoding="utf-8")


def grade_repo(task_id: str, repo_path: str) -> dict:
    """Run the task's deterministic behavioral grader in a fresh subprocess
    against the resulting repository. Returns
    {"grader_success": bool, "checks": {...}, "error": ...}.

    Grader-infrastructure failure (subprocess crash, unparseable output)
    is reported with error="grader_infra: ..." -- the caller records such
    a trial as INVALID, never as a task failure."""
    task = get_task(task_id)
    script = Template(_CHECK_SCRIPT_TEMPLATE).substitute(
        expected_schemas=json.dumps(task["expected_schemas"]),
        behaviors=json.dumps(task["behaviors"]),
        extra_checks=json.dumps(task["extra_checks"]),
    )
    with tempfile.NamedTemporaryFile("w", suffix="_gate3_check.py",
                                     delete=False, encoding="utf-8") as fh:
        fh.write(script)
        script_path = fh.name
    try:
        proc = subprocess.run(
            [sys.executable, script_path, str(repo_path)],
            capture_output=True, text=True, timeout=60, cwd=str(repo_path),
        )
    except Exception as exc:  # noqa: BLE001 -- recorded verbatim
        return {"grader_success": False, "checks": {},
                "error": f"grader_infra: {exc!r}"}
    finally:
        Path(script_path).unlink(missing_ok=True)
    if proc.returncode != 0:
        return {"grader_success": False, "checks": {},
                "error": f"grader_infra: rc={proc.returncode} "
                         f"stderr={proc.stderr[-2000:]}"}
    try:
        parsed = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {"grader_success": False, "checks": {},
                "error": "grader_infra: unparseable grader output "
                         f"{proc.stdout[-500:]!r}"}
    checks = parsed.get("checks", {})
    # EVERY check must pass -- one deterministic failure is a failure. A
    # declared agent success, a finished stop reason, and a non-empty patch
    # are NEVER consulted here.
    grader_success = bool(checks) and all(checks.values()) and not parsed.get("error")
    return {"grader_success": grader_success, "checks": checks,
            "error": parsed.get("error")}



