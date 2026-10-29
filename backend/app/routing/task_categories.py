"""Task categories: one ranking of models per kind of work, picked per request by the triage judge (JEV).

A model is not equally good at everything: one that fixes repository bugs well can be weak at competition puzzles
and strong at maths. The fit therefore puts every benchmark under the category of work it measures,

    category -> benchmark -> repo -> task,

so each category node learns where its tasks sit in the skill space (goal_x), and a model's chance on a category is
its ability plus its skills along that node. At decision time the triage verdict names the request's category
(`model_constraints.category`) and the plan's prior is drawn under that node, so the ladder is ranked for that kind
of work. Ranking still weighs cost (ladder.py: the cheapest sequence of tries that is as reliable as the session's
own model), so the strongest model wins only where nothing cheaper is reliable enough.

A category with no public benchmark yet (frontend, sql, data_science, terminal) has a node with no evidence: its
prior is the population's until probes or real results arrive. `code_question` has no benchmark at all (an answer has
no check), so dispatch leaves such requests to the session's model anyway.
"""
from __future__ import annotations

import re
from typing import Optional

from app.routing.evidence import node_id

# key -> (what the request asks for: the triage prompt's wording, benchmark name patterns that measure it)
CATEGORIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "repo_change": ("fix a bug, add a feature or refactor code inside an existing repository (several files, its tests)",
                    (r"^swe-bench", r"^swe-rebench", r"^nebius", r"^swe-agent", r"^llmrouterbench:swe-bench")),
    "script": ("write a self-contained function, script or algorithm (competition-style or utility code)",
               (r":livecodebench", r":humaneval", r":mbpp", r":bigcodebench", r":arenahard-coding",
                r"^routerbench:mbpp$")),
    "frontend": ("build or change a web page, component or UI from a description or a design", (r"design2code", r"websight")),
    "sql": ("write or fix SQL queries or database schemas", (r":bird", r":spider")),
    "data_science": ("data analysis with pandas, numpy, plotting or machine-learning code", (r":ds-?1000",)),
    "terminal": ("shell commands, environment setup, builds, devops, CI configuration", (r"terminal-bench",)),
    "agent_tools": ("a multi-step task calling tools or APIs to reach a goal", (r":tau2",)),
    "math": ("a maths problem or derivation", (r":aime", r":math500", r":livemathbench", r":mathbench", r":arenahard-math",
                                                r"^routerbench:grade-school-math$", r"^routerbench:mtbench-math$",
                                                r"^routerbench:mmlu-.*mathematics$", r"^routerbench:chinese-remainder")),
    "knowledge": ("a factual or domain-knowledge question (science, medicine, law, finance)",
                  (r":mmlu", r":gpqa", r":simpleqa", r":hle", r":medqa", r":finqa", r":arcc", r"^routerbench:arc-challenge$",
                   r"^routerbench:chinese", r"^routerbench:accounting")),
    "reasoning": ("a logic puzzle or reasoning question", (r":bbh", r":korbench", r":kandk", r":arc-agi", r":winogrande",
                                                          r"^routerbench:hellaswag$", r"riddles")),
    "writing": ("writing, summarising, translating or conversation", (r":arenahard-creative", r":arenahard-test", r":emorynlp",
                                                                     r":meld", r":dailydialog", r"^routerbench:mtbench",
                                                                     r"^routerbench:consensus_summary$",
                                                                     r"^routerbench:abstract2title$",
                                                                     r"^routerbench:bias_detection$", r"^routerbench:test-match$")),
    "code_question": ("a question about code or the project that asks for an explanation, not a change", ()),
}
_COMPILED = [(cat, [re.compile(p) for p in pats]) for cat, (_d, pats) in CATEGORIES.items()]


def category_of(benchmark: str) -> Optional[str]:
    """The category a benchmark measures (first match in CATEGORIES order), or None."""
    name = benchmark.lower()
    for cat, pats in _COMPILED:
        if any(p.search(name) for p in pats):
            return cat
    return None


def category_node(category: str) -> str:
    return node_id(f"category:{category}")


def valid(category: object) -> Optional[str]:
    return category if isinstance(category, str) and category in CATEGORIES else None
