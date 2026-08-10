"""
Shared real pipeline for Experiment 4's cells: fetch real task files,
call the real SLM with real usage capture, execute generated code in
the real sandbox, check against the real verifier. Extracted from
run_experiment_4_cell1.py so Cell 2 (and later cells) reuse the same,
already-debugged logic rather than duplicate it -- this project has
already learned that lesson once (index_after_tasks.py existed before
a duplicate got written for the same purpose).

Every fix found running Cell 1 and Cell 2 for real lives here now:
  - max_tokens=6000: NOT the same as "just increase it." A real run hit
    Groq's real 8000 TPM cap for this model/tier -- a combined budget
    covering prompt tokens AND max_tokens together, not separately. An
    earlier max_tokens=8000 alone consumed the whole cap; 6000 leaves
    real headroom above observed actual completions (3495-4000 tokens)
    while comfortably fitting a several-hundred-token prompt underneath.
  - reasoning is now ALLOWED, not suppressed. An earlier version forced
    reasoning_effort="none"/"low" to stop thinking-mode prose from
    corrupting the code output on both providers. Real evidence showed
    that fix had a genuine cost: forced-low reasoning produced a bare
    stub on a multi-step task (a comment describing what a real
    solution would do, never doing it), not a smaller-but-genuine
    attempt. Fixed properly instead -- see extract_final_code_block()
    below, which finds the final fenced code block regardless of how
    much real reasoning precedes it, so suppression is no longer
    needed to keep the extracted code clean.
  - real usage.prompt_tokens/completion_tokens capture, not a text-length
    estimate (OpenAICompatAgent.respond() in panel.py discards usage
    entirely, and different tokenizers make an estimate biased here)
  - input files staged WITHOUT a "../" prefix (an earlier version staged
    at "../environment/...", which escaped the sandbox's own temp
    directory into a shared, never-cleaned-up location -- fixed to match
    what the model is actually told via instruction.md's own text)
  - a local ast.parse() check before the sandbox, so a broken response
    reports a clear line/message instead of a cryptic sandbox crash
  - call_llm uses General Compute (gpt-oss-120b), not Anthropic --
    switched because no Anthropic credits were available. reasoning_
    effort="none" applied here too, proactively, since gpt-oss-120b is
    also an explicit reasoning model and qwen3.6-27b already broke this
    exact way once for real.
"""
import ast
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from huggingface_hub import hf_hub_download

from app.config import settings
from app.db.session import create_pool
from app.services.sandbox_executor import SubprocessSandboxExecutor

REPO = "DavydenkoGr/AFTER"
TASK = "tasks/pm/edit-pdf"

SYSTEM_PROMPT = (
    "You are a careful Python programmer solving a real task. Think through the "
    "problem as much as you need to -- reasoning first is encouraged, especially "
    "for tasks with several distinct requirements. When you are ready, provide "
    "your complete, final Python solution in a single fenced code block, starting "
    "with ```python and ending with ```. Only the LAST fenced code block in your "
    "response will be extracted and run -- anything before or after it, including "
    "your reasoning, is discarded automatically, so do not put any part of your "
    "actual solution outside that final fenced block.\n\n"
    "Write defensive code. If your approach depends on an assumption about the "
    "input's structure (for example, whether a document has a particular internal "
    "feature), and that assumption might be wrong, include a working fallback path "
    "rather than exiting or doing nothing when the assumption doesn't hold. A "
    "partial, best-effort result that produces real output is better than an early "
    "return that produces none."
)


def fetch(remote_path: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    cached = hf_hub_download(REPO, filename=remote_path, repo_type="dataset")
    shutil.copy2(cached, local_path)


def fetch_task_files(task_root: Path) -> str:
    """Fetches everything needed for this task and returns the raw
    instruction.md text."""
    fetch(f"{TASK}/instruction.md", task_root / "instruction.md")
    fetch(f"{TASK}/tests/test_outputs.py", task_root / "tests" / "test_outputs.py")
    fetch(
        f"{TASK}/source_artifacts/source_task/inputs/input.pdf",
        task_root / "environment" / "data" / "input.pdf",
    )
    fetch(
        f"{TASK}/source_artifacts/source_task/inputs/input.txt",
        task_root / "environment" / "data" / "input.txt",
    )
    return (task_root / "instruction.md").read_text(encoding="utf-8")


async def call_slm(messages: list[dict]) -> tuple[str, int, int, bool]:
    """Real Groq call, real usage capture. Returns (code, prompt_tokens,
    completion_tokens, hit_token_limit).

    messages: the conversation so far, NOT including the system prompt
    (that's prepended here). A single-shot call passes
    [{"role": "user", "content": instruction}]; a multi-turn revision
    loop passes the growing history (user, assistant, user, ...).
    Changed from a single user_message: str to support the iterate-
    until-success loop (synthetic_tasks/), which needs to hand back the
    model's own prior code plus the real execution error for a genuine
    revision turn -- a single string can't represent that.

    max_tokens=6000, not 8000: a real run hit a hard wall here --
    Groq's on-demand tier caps this model at 8000 TPM TOTAL, covering
    prompt tokens AND the requested max_tokens budget together, not
    separately. max_tokens=8000 alone consumed the entire cap, leaving
    no room for any prompt at all; Cell 1's tiny 355-token prompt
    barely fit, Cell 2's trajectory-augmented prompt didn't. Real
    observed completions were 3495 (success) and 4000 (the old,
    smaller truncation limit) tokens -- 6000 leaves real headroom above
    actual observed need while comfortably fitting a several-hundred-
    token prompt under the 8000 total cap.

    NO reasoning_effort override anymore: an earlier version forced
    "none" here to stop thinking-mode prose from corrupting the code
    output. That fix had a real, demonstrated cost on General Compute's
    call_llm (see below) -- forcing low reasoning on a genuinely
    multi-step task produced a bare stub with a comment describing what
    a real solution would do, never actually doing it. Groq's own docs
    recommend thinking mode specifically for coding tasks. Now that
    extract_final_code_block() robustly finds the final fenced code
    block regardless of how much reasoning precedes it, suppressing
    reasoning is no longer needed to solve the original bug -- letting
    it run naturally is the more honest test of real capability.
    hit_token_limit (still checked below) is the safety net if this
    combination of real reasoning + max_tokens=6000 hits the TPM cap
    again on a longer response."""

    from openai import APIStatusError, AsyncOpenAI

    client = AsyncOpenAI(api_key=settings.require("groq_api_key"), base_url=settings.groq_base_url)
    try:
        resp = await client.chat.completions.create(
            model=settings.groq_model,
            max_tokens=6000,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
        )
    except APIStatusError as exc:
        if exc.status_code == 413 or "rate_limit_exceeded" in str(exc):
            print(f"  RATE LIMIT: this account's TPM cap was exceeded (prompt + max_tokens "
                  f"budget together exceeded the per-minute limit). Real error: {exc}")
            print("  Fix: lower max_tokens further in call_slm(), or wait a minute and retry "
                  "(TPM limits reset on a rolling window), or upgrade the Groq tier.")
        raise
    code = resp.choices[0].message.content or ""
    usage = resp.usage
    completion_tokens = usage.completion_tokens if usage else 0
    hit_token_limit = resp.choices[0].finish_reason == "length"
    return code, (usage.prompt_tokens if usage else 0), completion_tokens, hit_token_limit


async def fetch_pdf_skill_trajectory() -> str:
    """Real DB fetch of the 'pdf' skill task_node -- used by Cells 2
    and 4, the two cells that get a TaskNode trajectory hint. Extracted
    here rather than duplicated in both cell scripts, same reasoning as
    call_slm/run_cell above.

    HONEST SCOPE NOTE: the original plan described this hint as "masked
    io_schema, ordered REQUIRES chain, preconditions/postconditions."
    Real data doesn't support that -- ingest_after_skills.py never
    populates io_schema (confirmed empty '{}' for every skill) and
    creates no REQUIRES edges between task_nodes at all. What's
    actually real and available: name, description (dense, real text
    from AFTER's own SKILL.md), and postconditions (role tags). This
    is exactly what gets used -- not a fabricated schema or dependency
    chain."""
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL not found — set it in backend/.env (this cell needs the DB "
              "to fetch the real trajectory)")
        sys.exit(1)

    pool = await create_pool(os.environ["DATABASE_URL"])
    row = await pool.fetchrow(
        "SELECT name, description, success_criteria FROM task_nodes "
        "WHERE skill_ref = 'pdf' AND t_invalid IS NULL"
    )
    await pool.close()

    if not row:
        print("No task_node found with skill_ref = 'pdf' -- was ingest_after_skills.py run "
              "against this database?")
        sys.exit(1)

    roles = row["success_criteria"].get("postconditions", []) if row["success_criteria"] else []
    return (
        f"Reusable company skill guidance (retrieved for this task):\n"
        f"Skill: {row['name']}\n"
        f"Description: {row['description']}\n"
        f"Used by roles: {', '.join(roles) if roles else '(none recorded)'}\n"
    )


async def call_llm(messages: list[dict]) -> tuple[str, int, int, bool]:
    """Real General Compute call, real usage capture -- the frontier-
    adjacent arm (Cells 3/4). Switched from Anthropic (no credits
    available) to General Compute, which hosts large open-weight
    models with an OpenAI-compatible API -- confirmed real via direct
    search of their actual current docs/catalog, not assumed from
    memory.

    HONEST LIMIT: gpt-oss-120b is a real, large, capable model, but
    it's an open-weight model, not a closed-lab frontier system --
    the hypothesis ("does frontier already sit near-ceiling") is being
    tested against a frontier-ADJACENT model here, not literally the
    same category the original design envisioned.

    NO reasoning_effort override anymore: an earlier version forced
    "low" here (General Compute rejected "none" with a 400) to stop
    thinking-mode prose from corrupting the code output. Real evidence
    showed this had a genuine cost -- forced low reasoning on this
    multi-step task produced a bare stub (a comment describing what a
    real solution would do, never actually doing it), not a smaller
    but genuine attempt. Now that extract_final_code_block() robustly
    finds the final fenced code block regardless of how much reasoning
    precedes it, suppressing reasoning is no longer needed to solve
    the original bug -- letting it reason naturally is the more honest
    test of real capability.

    messages: see call_slm's docstring -- same change, same reason
    (the iterate-until-success loop needs real multi-turn history)."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.require("general_compute_api_key"),
        base_url=settings.general_compute_base_url,
    )
    resp = await client.chat.completions.create(
        model=settings.experiment_4_llm_model,
        max_tokens=6000,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
    )
    code = resp.choices[0].message.content or ""
    usage = resp.usage
    completion_tokens = usage.completion_tokens if usage else 0
    hit_token_limit = resp.choices[0].finish_reason == "length"
    return code, (usage.prompt_tokens if usage else 0), completion_tokens, hit_token_limit


def extract_final_code_block(text: str) -> str:
    """
    Finds the LAST ```...``` fenced code block anywhere in the
    response and returns its content. Replaces the old
    strip_markdown_fences, which assumed the entire response was code
    with at most a single wrapping fence -- that assumption broke for
    real once the system prompt started encouraging reasoning first
    (see module docstring): a genuine reasoning response has prose
    BEFORE the code, not just an optional fence AROUND it.

    Falls back to treating the whole (fence-stripped, if any) response
    as code if no fence is found at all -- keeps this tolerant of a
    model that ignores the fencing instruction entirely, same as
    before, rather than failing outright on that shape.
    """
    import re

    matches = re.findall(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
    if matches:
        return matches[-1].strip()

    # No fence found anywhere -- fall back to the old behavior (only
    # strips a single leading/trailing fence if present at the very
    # start/end; otherwise returns the text as-is).
    code = text.strip()
    if code.startswith("```"):
        lines = code.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        code = "\n".join(lines)
    return code.strip()


async def run_cell(task_root: Path, user_message: str, cell_label: str, call_model_fn) -> int:
    """Steps 2 through 6 of a cell: call the model, syntax-check, run in
    the sandbox, verify, report. task_root must already have the task
    files fetched (fetch_task_files) before calling this.

    call_model_fn: async callable taking user_message, returning
    (code, prompt_tokens, completion_tokens, hit_token_limit) -- e.g.
    call_slm (Groq, the SLM arm) or call_llm (Anthropic, the frontier
    arm). Not hardcoded to one provider, so Cells 1-4 all share this
    same execution/verification logic instead of duplicating it."""
    print(f"[2/6] calling the real model -- {cell_label}...")
    start = time.monotonic()
    raw_code, prompt_tokens, completion_tokens, hit_token_limit = await call_model_fn(
        [{"role": "user", "content": user_message}]
    )
    call_seconds = time.monotonic() - start
    code = extract_final_code_block(raw_code)
    print(f"  received {len(code)} chars of code, "
          f"{prompt_tokens} prompt + {completion_tokens} completion tokens, "
          f"{call_seconds:.1f}s")
    if hit_token_limit:
        print("  WARNING: response was truncated by max_tokens (finish_reason='length') -- "
              "the model did not finish; whatever follows is very likely incomplete/broken, "
              "not a fair test of the model's real capability")

    # Save immediately, unconditionally -- NOT only on a later success
    # path. Real gap found: a Cell 3 run errored out before producing
    # output.pdf, hit an early return, and generated_solution.py was
    # never written at all -- exactly the case where seeing the code
    # matters most for diagnosis. Also save the raw, pre-extraction
    # response, since when extraction itself is suspected of being
    # wrong, the extracted version alone doesn't tell you that.
    (task_root / "generated_solution.py").write_text(code, encoding="utf-8")
    (task_root / "raw_model_response.txt").write_text(raw_code, encoding="utf-8")

    print("\n[2.5/6] checking generated code parses as valid Python before running it...")
    try:
        ast.parse(code)
        print("  syntax OK")
    except SyntaxError as exc:
        print(f"  SYNTAX ERROR at line {exc.lineno}: {exc.msg}")
        print(f"  context: {exc.text!r}")
        print(f"  code was already saved to {task_root / 'generated_solution.py'} "
              f"and raw response to {task_root / 'raw_model_response.txt'}")
        print(f"\n[6/6] RESULT: FAIL (invalid Python, "
              f"{'likely truncated' if hit_token_limit else 'not truncated -- a real generation failure'})")
        print(f"tokens used: {prompt_tokens} prompt + {completion_tokens} completion "
              f"= {prompt_tokens + completion_tokens} total")
        return 1

    print("\n[3/6] executing the generated code in the sandbox...")
    input_files = {
        "environment/data/input.pdf": (task_root / "environment" / "data" / "input.pdf").read_bytes(),
        "environment/data/input.txt": (task_root / "environment" / "data" / "input.txt").read_bytes(),
    }
    executor = SubprocessSandboxExecutor()
    result = await executor.run(code, input_files=input_files, timeout_seconds=60)
    print(f"  exit_code={result.exit_code}  timed_out={result.timed_out}  "
          f"wall_time={result.wall_time_seconds:.1f}s")
    if result.stdout:
        print(f"  stdout: {result.stdout[:500]}")
    if result.stderr:
        print(f"  stderr: {result.stderr[:500]}")

    print(f"\n[4/6] output files produced: {list(result.output_files.keys())}")
    output_pdf_bytes = result.output_files.get("output.pdf")
    if not output_pdf_bytes:
        print("\n[5/6] SKIPPED -- no output.pdf was produced, cannot verify")
        print(f"  code was already saved to {task_root / 'generated_solution.py'} "
              f"and raw response to {task_root / 'raw_model_response.txt'}")
        print("\n[6/6] RESULT: FAIL (no output produced)")
        print(f"tokens used: {prompt_tokens} prompt + {completion_tokens} completion "
              f"= {prompt_tokens + completion_tokens} total")
        return 1

    print("\n[5/6] running the real verifier against the output...")
    output_dir = task_root / "output"
    output_dir.mkdir(exist_ok=True)
    (output_dir / "output.pdf").write_bytes(output_pdf_bytes)
    test_result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_outputs.py", "-v"],
        cwd=task_root, capture_output=True, text=True,
    )
    print(test_result.stdout[-2000:], test_result.stderr[-1000:])

    passed = test_result.returncode == 0
    print(f"\n[6/6] RESULT: {'PASS' if passed else 'FAIL'}")
    print(f"tokens used: {prompt_tokens} prompt + {completion_tokens} completion "
          f"= {prompt_tokens + completion_tokens} total")
    print(f"model call time: {call_seconds:.1f}s, sandbox execution time: "
          f"{result.wall_time_seconds:.1f}s")

    print(f"\ngenerated code was saved to {task_root / 'generated_solution.py'} for review")

    return 0 if passed else 1
