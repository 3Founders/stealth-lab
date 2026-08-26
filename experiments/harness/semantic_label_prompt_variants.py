"""
Candidate semantic_label prompt variants for live_extractor.py (board Lane
MEASURE, prepared 2026-08-27 while the OpenRouter account has NO spendable
balance - see the board's OpenRouter budget-wall entry). NOT YET EXECUTED:
this module makes zero network calls and is imported by offline tests only.

Why this exists: the shipped extraction prompt (live_extractor.SYSTEM_PROMPT,
here reproduced verbatim as PROMPT_VARIANTS["terse_v2"], the terse-label
ruling from CLAUDE.md Task 1) moved semantic_label recall 0/4 -> 2/4 and
precision 0/17 -> 2/13 over the 42-fixture error floor (board Log
2026-08-26, fourth wave). Two residual failure modes survived that fix and
are still visible in the SAME live run's raw predictions
(live_extractor_preds_v2.jsonl):

  1. VOCABULARY MISMATCH, not verbosity: ef-sem-003's gold "continuous
     integration pipeline configuration added" vs the terse prediction "CI
     workflow configuration updated" is Jaccard 1/8=0.125 - both sides are
     already short, they just don't share tokens. A word-count rule cannot
     fix a synonym/abbreviation choice.
  2. UNDER-FIRE, mirror of the original over-verbosity finding: ef-sem-002
     (`rm -rf dist/`) got no label at all under the terse prompt, the one
     place the NONE contract fired somewhere it shouldn't have.

Each variant below targets ONE hypothesis about what would move these
numbers further, so a comparison run can attribute an improvement (or lack
of one) to a specific mechanism rather than a blended prompt rewrite:

  - few_shot            : show the actual gold event->label pairs (incl.
                           the ls-src NONE-contract negative) instead of
                           only describing the target style in prose.
                           Hypothesis: demonstration beats description for
                           matching an unstated house vocabulary.
  - vocab_discipline     : explicit instruction to spell out abbreviations
                           and reuse the trace event's own domain nouns
                           rather than inventing a synonym. Hypothesis:
                           directly targets the ef-sem-003 failure mode.
  - strict_noun_phrase   : tighter mechanical format (2-5 words, banned
                           word classes, a self-count step) - a stricter
                           version of the terse rule itself. Hypothesis:
                           terseness alone has more room to run.
  - combined             : vocab_discipline's rule + few_shot's exemplars
                           layered on the terse base. Hypothesis: the
                           single most promising blend if the first two
                           each move the needle independently.

HONEST LIMIT disclosed up front (not fixed by any variant here): the
file_touched-family fixtures (fixtures/error_floor/files.json) carry NO
semantic_label gold at all by DESIGN SCOPE (that family's own _comment:
"file_touched family... testing ONLY the mechanical layer"), not because
those edits are semantically empty - ef-file-001 ("Modified src/auth/
login.py: replaced empty stub with a version taking a user parameter")
is arguably MORE meaningful than several semantic.json golds. A model
correctly recognizing meaning there still costs precision under the
current rubric scope, and no prompt wording can teach "this specific
family is out of scope" without leaking the fixture boundary into the
prompt (which would be extraction-format-shaped cheating, not a genuine
extractor improvement). None of the variants below attempt to suppress
labeling on file-family-shaped events; that residual is a rubric-scope
question for the integrator, per Task 1's own instruction not to guess at
rubric changes unilaterally - flagged again here rather than silently
prompt-patched around.

HOW TO RUN ONE VARIANT ONCE FUNDS EXIST (each variant gets its own output
pair so runs never collide; run_error_floor.py grades any predictions
file the same way regardless of which variant produced it):

    python live_extractor.py --prompt-variant few_shot \\
        --out live_extractor_preds_few_shot.jsonl \\
        --spend-log live_extractor_spend_few_shot.jsonl
    python run_error_floor.py --predictions live_extractor_preds_few_shot.jsonl \\
        --out error_floor_results_few_shot.jsonl

Repeat per variant name in PROMPT_VARIANTS (swap "few_shot" above), then
diff each variant's semantic_label P/R/F1 against v2's committed baseline
(error_floor_results_v2.jsonl) the same way the terse-label ruling itself
was reported. Full 42-excerpt run per variant costs about what the v2 pass
cost ($0.0601 / 84 attempts) - four variants is roughly a quarter, well
inside the informal ~$1 lane mark on its own, but see the board's budget-
wall note before spending anything.
"""
from __future__ import annotations

import live_extractor

EXTRACT_SCHEMA = live_extractor.EXTRACT_SCHEMA

_MECHANICAL_BLOCK = (
    "You extract structured observations from ONE software-engineering "
    "trace event (a tool call). Emit every observation a careful engineer "
    "would agree the event warrants, of these types:\n"
    "- file_touched: the event created or modified a file. properties must "
    "carry file_path exactly as the event names it.\n"
    "- commit_made: the executed command created a git commit. properties "
    "must carry command = the full command line.\n"
    "- test_run: the executed command ran a test suite. properties.command "
    "= full command line.\n"
    "- command_executed: any other shell command that actually executed. "
    "properties.command = full command line.\n"
)

_TRAILING_RULES = (
    "Rules: one event can warrant several observations (a file edit can "
    "carry both its file_touched fact and a semantic label). Planning, "
    "search, lookup and delegation tools (todos, web search, subagent "
    "spawns, file reads/greps) warrant NOTHING. Do not invent facts the "
    "event does not show.\n"
    f"Reply with ONLY this JSON object, no other text:\n{EXTRACT_SCHEMA}"
)

# --------------------------------------------------------------------------
# terse_v2 - the SHIPPED baseline (CLAUDE.md Task 1), reproduced verbatim by
# reference (not copy-pasted) so this module can never silently drift from
# what live_extractor.py actually sends. Included so a variant sweep can
# print all four candidates next to the baseline they're measured against.
# --------------------------------------------------------------------------
TERSE_V2 = live_extractor.SYSTEM_PROMPT

# --------------------------------------------------------------------------
# few_shot
# --------------------------------------------------------------------------
_FEW_SHOT_SEMANTIC_BLOCK = (
    "- semantic_label: a TERSE label (aim for 3-6 words, never more than "
    "8) naming WHAT changed, in the gold house style: subject + "
    "past-tense verb, nothing else. Do NOT quote file paths, commands, "
    "commit hashes or exact strings from the event; do NOT add "
    "parentheticals, clauses, or explanations of why/how/for-what-purpose. "
    "If the event is too generic to say anything meaningful, emit NO "
    "label for it - silence beats filler.\n"
    "Examples (real event -> correct semantic_label, or NO LABEL):\n"
    '  Edit src/services/auth_service.py (verify() now validates the '
    'token signature instead of always returning True) -> '
    '"authentication implementation was modified"\n'
    '  Bash `rm -rf dist/` -> "build output directory was deleted"\n'
    '  Write .github/workflows/ci.yml (adds a test job on push) -> '
    '"continuous integration pipeline configuration added"\n'
    '  Bash `docker compose up -d db` -> "database container started"\n'
    '  Bash `ls src` -> NO LABEL (a directory listing says nothing '
    'semantic about the project - the NONE contract)\n'
)

FEW_SHOT = (_MECHANICAL_BLOCK + _FEW_SHOT_SEMANTIC_BLOCK + _TRAILING_RULES)

# --------------------------------------------------------------------------
# vocab_discipline
# --------------------------------------------------------------------------
_VOCAB_DISCIPLINE_SEMANTIC_BLOCK = (
    "- semantic_label: a TERSE label (aim for 3-6 words, never more than "
    "8) naming WHAT changed, in the gold house style: subject + "
    "past-tense verb, nothing else. Good: 'authentication implementation "
    "was modified', 'build output directory was deleted', 'continuous "
    "integration pipeline configuration added', 'database container "
    "started'. Do NOT quote file paths, commands, commit hashes or exact "
    "strings from the event; do NOT add parentheticals, clauses, or "
    "explanations of why/how/for-what-purpose - those pad the label "
    "without changing its meaning and are wrong even when true.\n"
    "VOCABULARY DISCIPLINE: spell out abbreviations a careful engineer "
    "would spell out in a status note - write 'continuous integration', "
    "never 'CI'; 'pull request', never 'PR'; 'dependency', never 'dep' - "
    "UNLESS the trace event's own path/command/content already uses the "
    "short form verbatim, in which case match the event's own spelling "
    "instead of expanding it. Do not invent a new synonym for a concept "
    "the event already names (a workflow config file is a 'pipeline' or "
    "a 'workflow', pick the term the event's own path/tool implies, not a "
    "different word for the same thing). If the event is too generic to "
    "say anything meaningful, emit NO label for it - silence beats "
    "filler.\n"
)

VOCAB_DISCIPLINE = (
    _MECHANICAL_BLOCK + _VOCAB_DISCIPLINE_SEMANTIC_BLOCK + _TRAILING_RULES)

# --------------------------------------------------------------------------
# strict_noun_phrase
# --------------------------------------------------------------------------
_STRICT_NOUN_PHRASE_SEMANTIC_BLOCK = (
    "- semantic_label: EXACTLY 2 to 5 words, no more, in the strict "
    "pattern '<subject noun phrase> <past-tense or past-participle verb>' "
    "(e.g. 'database container started', 'build output directory "
    "deleted'). Before answering, COUNT your words; if the count is above "
    "5, cut words until it is at most 5, keeping the subject and verb and "
    "dropping everything else first. Do NOT use adjectives ('proper', "
    "'new', 'correct'), adverbs, parentheticals, quoted strings, file "
    "paths, commands, or commit hashes. Do NOT explain why or how - name "
    "ONLY what changed. If the event is too generic to say anything "
    "meaningful, emit NO label for it - silence beats filler.\n"
)

STRICT_NOUN_PHRASE = (
    _MECHANICAL_BLOCK + _STRICT_NOUN_PHRASE_SEMANTIC_BLOCK + _TRAILING_RULES)

# --------------------------------------------------------------------------
# combined - vocab_discipline's rule layered onto few_shot's exemplars.
# --------------------------------------------------------------------------
_COMBINED_SEMANTIC_BLOCK = (
    "- semantic_label: a TERSE label (aim for 3-6 words, never more than "
    "8) naming WHAT changed, in the gold house style: subject + "
    "past-tense verb, nothing else. Do NOT quote file paths, commands, "
    "commit hashes or exact strings from the event; do NOT add "
    "parentheticals, clauses, or explanations of why/how/for-what-purpose. "
    "VOCABULARY DISCIPLINE: spell out abbreviations a careful engineer "
    "would spell out in a status note - write 'continuous integration', "
    "never 'CI'; 'pull request', never 'PR'; 'dependency', never 'dep' - "
    "UNLESS the trace event's own path/command/content already uses the "
    "short form verbatim. Do not invent a new synonym for a concept the "
    "event already names. If the event is too generic to say anything "
    "meaningful, emit NO label for it - silence beats filler.\n"
    "Examples (real event -> correct semantic_label, or NO LABEL):\n"
    '  Edit src/services/auth_service.py (verify() now validates the '
    'token signature instead of always returning True) -> '
    '"authentication implementation was modified"\n'
    '  Bash `rm -rf dist/` -> "build output directory was deleted"\n'
    '  Write .github/workflows/ci.yml (adds a test job on push) -> '
    '"continuous integration pipeline configuration added"\n'
    '  Bash `docker compose up -d db` -> "database container started"\n'
    '  Bash `ls src` -> NO LABEL (a directory listing says nothing '
    'semantic about the project - the NONE contract)\n'
)

COMBINED = (_MECHANICAL_BLOCK + _COMBINED_SEMANTIC_BLOCK + _TRAILING_RULES)

# name -> full system prompt text. "terse_v2" is the shipped baseline, kept
# here for side-by-side comparison; the other four are the untested
# candidates this module exists to prepare.
PROMPT_VARIANTS: dict[str, str] = {
    "terse_v2": TERSE_V2,
    "few_shot": FEW_SHOT,
    "vocab_discipline": VOCAB_DISCIPLINE,
    "strict_noun_phrase": STRICT_NOUN_PHRASE,
    "combined": COMBINED,
}

CANDIDATE_VARIANTS = tuple(
    name for name in PROMPT_VARIANTS if name != "terse_v2")
