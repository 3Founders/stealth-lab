"""
Iterate-until-success loop: model writes code, we run it for real, and
if it doesn't pass, we hand back the REAL execution output (stdout,
stderr, exit code, verifier diagnostic) and give it a bounded number of
revision turns -- same shape as real coding agents (Claude Code, Codex,
Cursor), not the fixed single-shot/2-turn versions used for edit-pdf.

Reuses call_slm/call_llm from experiment_4_common.py (real API calls,
real usage capture) and SubprocessSandboxExecutor (real sandboxed
execution) -- does not duplicate either.
"""
import ast
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # backend root, for app.config etc.
sys.path.insert(0, str(Path(__file__).parent.parent))          # scripts/, for experiment_4_common

from experiment_4_common import extract_final_code_block
from app.services.sandbox_executor import SubprocessSandboxExecutor

sys.path.insert(0, str(Path(__file__).parent))
from verify import verify

SYSTEM_PROMPT = (
    "You are a careful Python programmer solving a real task. Think through the "
    "problem as much as you need to. When you are ready, provide your complete, "
    "final Python solution in a single fenced code block, starting with ```python "
    "and ending with ```. Only the LAST fenced code block in your response will be "
    "extracted and run.\n\n"
    "You will get real feedback after each attempt (the actual output, errors, or "
    "verification result from running your code) and a chance to fix it if it "
    "didn't work -- this is a real iterative process, not a single blind guess. "
    "Use the feedback carefully: it tells you exactly what went wrong."
)


@dataclass
class IterationResult:
    passed: bool
    turns_used: int
    total_prompt_tokens: int
    total_completion_tokens: int
    final_code: str
    turn_log: list[dict] = field(default_factory=list)  # per-turn detail, for inspection

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens


async def run_until_success(
    task_dir: Path,
    instruction: str,
    input_filename: str,
    call_model_fn,
    max_turns: int = 5,
    timeout_seconds: float = 30,
) -> IterationResult:
    """
    task_dir: contains the real input file (input_filename) and
    expected_output.json -- the synthetic task's real, local files, no
    HF fetch needed.
    call_model_fn: call_slm or call_llm from experiment_4_common.py.
    """
    input_path = task_dir / input_filename
    expected_path = task_dir / "expected_output.json"
    input_bytes = input_path.read_bytes()

    messages = [{"role": "user", "content": instruction}]
    executor = SubprocessSandboxExecutor()
    total_prompt_tokens = 0
    total_completion_tokens = 0
    turn_log = []
    final_code = ""

    for turn in range(1, max_turns + 1):
        print(f"  --- turn {turn}/{max_turns} ---")
        start = time.monotonic()
        raw_code, prompt_tokens, completion_tokens, hit_token_limit = await call_model_fn(messages)
        call_seconds = time.monotonic() - start
        code = extract_final_code_block(raw_code)
        final_code = code
        total_prompt_tokens += prompt_tokens
        total_completion_tokens += completion_tokens
        print(f"  received {len(code)} chars, {prompt_tokens}+{completion_tokens} tokens, "
              f"{call_seconds:.1f}s")
        if hit_token_limit:
            print(f"  WARNING: response was truncated by max_tokens (finish_reason='length') "
                  f"-- the model did not finish this turn, likely broken/incomplete")

        # Store the EXTRACTED code, not the raw response, as history --
        # a real bug found here: storing raw_code let a single truncated
        # (near-max_tokens, reasoning-heavy) turn dominate the NEXT
        # request's prompt size, cascading into a hard TPM-limit
        # failure one turn later (12484 requested vs Groq's 8000 cap,
        # confirmed real). Extracted code is far shorter and is also
        # the more honest artifact to show the model back anyway --
        # it's iterating on "your code", not "your reasoning trace".
        messages.append({"role": "assistant", "content": code})

        # Syntax check first -- same "don't waste a sandbox run on
        # something that can't possibly work" principle as edit-pdf's
        # harness.
        try:
            ast.parse(code)
        except SyntaxError as exc:
            feedback = f"Your code has a syntax error: line {exc.lineno}: {exc.msg}"
            print(f"  SYNTAX ERROR: {feedback}")
            turn_log.append({"turn": turn, "outcome": "syntax_error", "detail": feedback})
            messages.append({"role": "user", "content": f"{feedback}\n\nPlease fix and provide "
                              f"your complete corrected solution."})
            continue

        result = await executor.run(
            code, input_files={input_filename: input_bytes}, timeout_seconds=timeout_seconds,
        )
        output_bytes = result.output_files.get("output.json")

        if output_bytes is None:
            feedback = (
                f"Your code ran but did not produce output.json.\n"
                f"exit_code={result.exit_code}\n"
                f"stdout: {result.stdout[:1000]}\n"
                f"stderr: {result.stderr[:1000]}"
            )
            print(f"  NO OUTPUT: exit_code={result.exit_code}")
            turn_log.append({"turn": turn, "outcome": "no_output", "detail": feedback})
            messages.append({"role": "user", "content": f"{feedback}\n\nPlease fix and provide "
                              f"your complete corrected solution."})
            continue

        # Write output.json to a temp location just for verify() to read
        tmp_output = task_dir / "_tmp_output_for_verify.json"
        tmp_output.write_bytes(output_bytes)
        passed, verify_msg = verify(tmp_output, expected_path)
        tmp_output.unlink(missing_ok=True)

        if passed:
            print(f"  PASSED on turn {turn}: {verify_msg}")
            turn_log.append({"turn": turn, "outcome": "passed", "detail": verify_msg})
            return IterationResult(
                passed=True, turns_used=turn,
                total_prompt_tokens=total_prompt_tokens,
                total_completion_tokens=total_completion_tokens,
                final_code=code, turn_log=turn_log,
            )

        print(f"  VERIFICATION FAILED: {verify_msg[:200]}")
        turn_log.append({"turn": turn, "outcome": "verify_failed", "detail": verify_msg})
        messages.append({"role": "user", "content": f"Your code ran and produced output.json, "
                          f"but it's not correct:\n{verify_msg}\n\nPlease fix and provide your "
                          f"complete corrected solution."})

    print(f"  did not pass within {max_turns} turns")
    return IterationResult(
        passed=False, turns_used=max_turns,
        total_prompt_tokens=total_prompt_tokens,
        total_completion_tokens=total_completion_tokens,
        final_code=final_code, turn_log=turn_log,
    )
