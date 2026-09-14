import asyncio
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "AutomationBench"))
import os
os.environ.setdefault("PYTHONIOENCODING", "utf-8")

from automationbench.domains import get_combined_dataset
from automationbench.runner import strip_none_values
from automationbench.schema.world import WorldState

from local_dag_state import create_run
from dag_driver import run_dag

ROOT = Path(__file__).parent
RUN_MD = ROOT / "smoke_run.md"
IMPL_DIR = ROOT / "smoke_implementations"


def load_task(domain, task_name):
    ds = get_combined_dataset([domain])
    for row in ds:
        info = row["info"] if isinstance(row["info"], dict) else json.loads(row["info"])
        if info.get("task_name") == task_name:
            return info
    raise ValueError("not found")


async def main():
    shutil.rmtree(IMPL_DIR, ignore_errors=True)
    RUN_MD.unlink(missing_ok=True)

    info = load_task("simple", "simple.email_sf_contact_phone_update")
    goals = [
        ("Search for the email from the contact using a query filter to identify the relevant message.", []),
        ("Retrieve the full content of the identified email to extract the updated phone number.", [1]),
        ("Search for the corresponding contact record in Salesforce using the contact's name or email.", []),
        ("Update the phone field in the Salesforce contact record with the new value from the email.", [2, 3]),
    ]

    print("=== RUN 1 (cold, no cache) ===")
    create_run(RUN_MD, "smoke-1", goals)
    world1 = WorldState(**strip_none_values(info.get("initial_state", {})))
    result1 = await run_dag(
        model="mercury-2", base_url="https://api.inceptionlabs.ai/v1", api_key_env="INCEPTION_API_KEY",
        world=world1, run_md_path=RUN_MD, implementations_dir=IMPL_DIR, info=info,
    )
    print(json.dumps(result1, indent=2))

    print("\n=== RUN 2 (same goals, should replay from local cache, zero LLM calls) ===")
    create_run(RUN_MD, "smoke-2", goals)
    world2 = WorldState(**strip_none_values(info.get("initial_state", {})))
    result2 = await run_dag(
        model="mercury-2", base_url="https://api.inceptionlabs.ai/v1", api_key_env="INCEPTION_API_KEY",
        world=world2, run_md_path=RUN_MD, implementations_dir=IMPL_DIR, info=info,
    )
    print(json.dumps(result2, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
