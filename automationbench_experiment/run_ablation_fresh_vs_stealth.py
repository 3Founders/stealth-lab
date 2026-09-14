"""
Small, fast (~2-5 min) real ablation: does a REAL procedure extracted from
one agent's OWN trajectory help a later task in the same family?

Stage A (FRESH):    gemini-3.8-flash solves task_1 with no help. Real
                     AutomationBench grader decides pass/fail.
Stage B (EXTRACT):  a real LLM call (General Compute gemma-4-31B-it) reads
                     task_1's ACTUAL trajectory (tool calls + outputs) and
                     distills a short, reusable Procedure. Zero items is a
                     valid outcome (never fabricated if extraction fails).
                     The result is written to .stealth/procedures/P-0001.md
                     -- a real artifact, not a mock.
Stage C (STEALTH):  gemini-3.8-flash solves task_2 (a DIFFERENT, sibling
                     task in the same family) with that real Procedure
                     injected into its system prompt.

No benchmark grading logic is touched -- both stages run through the
official, unmodified AutomationBenchEnv/rubric via automationbench.scripts.
eval.run_evaluation(); this script only substitutes which Dataset row(s)
get fed in (system-prompt text), via a temporary monkeypatch of
automationbench.scripts.eval.get_combined_dataset restored in `finally`.

Usage (from AutomationBench/, with its venv active):
    python ..\\automationbench_experiment\\run_ablation_fresh_vs_stealth.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AutomationBench"))
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

from dotenv import load_dotenv

load_dotenv(r"C:\Users\chait\Prog\3Found\Stealth\StealthLab\backend\.env")

import automationbench.scripts.eval as ab_eval
from automationbench.domains import get_combined_dataset

RESULTS_DIR = Path(__file__).parent / "results"
STEALTH_DIR = Path(__file__).parent / ".stealth" / "procedures"
STEALTH_DIR.mkdir(parents=True, exist_ok=True)


# gemini-3.8-flash was tried first and dropped: chat_completions breaks on a
# missing thought_signature passthrough for multi-turn tool calls (real
# 400 from Google's API), and gemini_interactions mode hung indefinitely
# (15 min, near-zero CPU growth) in this environment. mercury-2 is the
# proven-reliable substitute (90% on this exact domain, verified earlier).
TEACHER_MODEL = "mercury-2"
TEACHER_API = "chat_completions"
TEACHER_BASE_URL = "https://api.inceptionlabs.ai/v1"
TEACHER_API_KEY_VAR = "INCEPTION_API_KEY"
EXTRACTOR_BASE_URL = "https://api.generalcompute.com/v1"
EXTRACTOR_MODEL = "gemma-4-31B-it"

TASK_1 = "simple.email_sf_contact_phone_update"   # teacher, fresh
TASK_2 = "simple.email_sf_contact_city_update"     # teacher, +stealth (sibling task)


def _find_task_index(ds, task_name: str) -> int:
    short_name = task_name.split(".", 1)[-1]
    for i, row in enumerate(ds):
        info = row["info"] if isinstance(row["info"], dict) else json.loads(row["info"])
        candidate = info.get("task_name") or ""
        if candidate in (task_name, short_name):
            return i
    raise ValueError(f"task {task_name!r} not found")


def _one_task_dataset(domain: str, task_name: str, system_prompt_suffix: str | None = None):
    from datasets import Dataset

    ds = get_combined_dataset([domain])
    idx = _find_task_index(ds, task_name)
    row = dict(ds[idx])
    if system_prompt_suffix:
        prompt = row["prompt"]
        prompt = json.loads(prompt) if isinstance(prompt, str) else prompt
        prompt = [dict(m) for m in prompt]
        assert prompt[0]["role"] == "system"
        prompt[0]["content"] = prompt[0]["content"] + "\n\n" + system_prompt_suffix
        row["prompt"] = prompt
    return Dataset.from_dict({k: [row[k]] for k in ds.column_names})


async def run_one(*, model: str, api: str, task_name: str, domain: str,
                   export_name: str, system_prompt_suffix: str | None = None,
                   base_url: str | None = None, api_key_var: str = "GEMINI_API_KEY") -> dict:
    ds = _one_task_dataset(domain, task_name, system_prompt_suffix)
    export_path = RESULTS_DIR / export_name

    orig = ab_eval.get_combined_dataset
    ab_eval.get_combined_dataset = lambda domains: ds
    try:
        await ab_eval.run_evaluation(
            model=model, domains=[domain], base_url=base_url, api_key_var=api_key_var,
            num_examples=-1, export_json=str(export_path), api=api, toolset="api",
            max_concurrent=1,
        )
    finally:
        ab_eval.get_combined_dataset = orig

    data = json.loads(export_path.read_text(encoding="utf-8"))
    t = data["tasks"][0]
    return {
        "task": t["name"], "passed": t["passed"], "score": t["score"],
        "input_tokens": t["input_tokens"], "output_tokens": t["output_tokens"],
        "num_tool_calls": t["num_tool_calls"], "messages": t["messages"],
    }


EXTRACTION_SYSTEM_PROMPT = """You extract ONE short, reusable procedure from a real agent \
trajectory that solved a business-workflow task. Read the tool calls and their real outputs. \
Produce a procedure that would help a DIFFERENT agent solve a SIMILAR task in the same family \
(same apps, same kind of lookup-then-update pattern), not this exact task's specific values.

Rules:
- Generalize away task-specific values (names, ids, field values) -- keep the STRATEGY (which
  tools to call, in what order, how to resolve ambiguity), not the specific answer.
- If the trajectory has nothing generalizable, respond with exactly: NONE
- Respond with ONLY a JSON object: {"title": str, "steps": [str, ...], "applicability": str}
"""


async def extract_procedure_from_trajectory(messages: list) -> dict | None:
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ["GENERAL_COMPUTE_API_KEY"], base_url=EXTRACTOR_BASE_URL)
    trace_text = json.dumps(messages, default=str)[:8000]
    resp = await client.chat.completions.create(
        model=EXTRACTOR_MODEL,
        messages=[
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": trace_text},
        ],
        temperature=0.0, max_tokens=600,
    )
    text = (resp.choices[0].message.content or "").strip()
    if text == "NONE" or not text:
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


def write_stealth_procedure(proc: dict, path: Path) -> None:
    steps = "\n".join(f"{i+1}. {s}" for i, s in enumerate(proc.get("steps", [])))
    path.write_text(
        f"---\nid: P-0001\nscope: automationbench\nsource_model: {TEACHER_MODEL}\n"
        f"source_task: {TASK_1}\n---\n\n# {proc.get('title', 'Untitled procedure')}\n\n"
        f"## Applicability\n{proc.get('applicability', '')}\n\n## Method\n{steps}\n",
        encoding="utf-8",
    )


async def main():
    print(f"=== Stage A: FRESH -- {TEACHER_MODEL} on {TASK_1} ===")
    run_a = await run_one(
        model=TEACHER_MODEL, api=TEACHER_API, task_name=TASK_1, domain="simple",
        export_name="ablation_A_fresh.json", base_url=TEACHER_BASE_URL, api_key_var=TEACHER_API_KEY_VAR,
    )
    print(json.dumps({k: v for k, v in run_a.items() if k != "messages"}, indent=2))

    print("\n=== Stage B: EXTRACT real procedure from Stage A's trajectory ===")
    proc = await extract_procedure_from_trajectory(run_a["messages"])
    if proc is None:
        print("Extraction returned NONE -- nothing generalizable found. Stopping honestly.")
        return
    proc_path = STEALTH_DIR / "P-0001.md"
    write_stealth_procedure(proc, proc_path)
    print(f"Wrote real extracted procedure -> {proc_path}")
    print(json.dumps(proc, indent=2))

    stealth_context = (
        "RETRIEVED PROCEDURE (from a prior agent's real, verified run on a sibling task):\n"
        f"Title: {proc.get('title')}\nApplicability: {proc.get('applicability')}\n"
        "Steps:\n" + "\n".join(f"- {s}" for s in proc.get("steps", []))
    )

    print(f"\n=== Stage C: STEALTH -- {TEACHER_MODEL} on {TASK_2} (+ injected procedure) ===")
    run_c = await run_one(
        model=TEACHER_MODEL, api=TEACHER_API, task_name=TASK_2, domain="simple",
        export_name="ablation_C_stealth.json", system_prompt_suffix=stealth_context,
        base_url=TEACHER_BASE_URL, api_key_var=TEACHER_API_KEY_VAR,
    )
    print(json.dumps({k: v for k, v in run_c.items() if k != "messages"}, indent=2))

    print("\n=== Comparison ===")
    print(json.dumps({
        "fresh": {"task": run_a["task"], "passed": run_a["passed"], "score": run_a["score"],
                  "tokens": run_a["input_tokens"] + run_a["output_tokens"], "tool_calls": run_a["num_tool_calls"]},
        "stealth": {"task": run_c["task"], "passed": run_c["passed"], "score": run_c["score"],
                    "tokens": run_c["input_tokens"] + run_c["output_tokens"], "tool_calls": run_c["num_tool_calls"]},
    }, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
