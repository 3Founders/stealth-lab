"""The pipeline survey (pipeline_survey.py) on Modal: issues in parallel containers, the same image as the series runs.

  modal run experiments/abtest/modal_survey.py --stage 1 --n 48 --seed 1 --run survey-s1
Results: printed (the table) and on the volume at /mnt/series/survey/<run>/stage<k>.jsonl.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import modal  # noqa: E402
from modal_series import APP_DIR, CLAUDE_SECRET, KEYS_SECRET, VOL, VOLUME_NAME, build_image  # noqa: E402

app = modal.App("stealthlab-survey")
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


@app.function(image=build_image(), volumes={VOL: volume}, timeout=7200, cpu=2.0, memory=8192, secrets=[modal.Secret.from_name(KEYS_SECRET)])
def stage1_batch(iids: list[str]) -> list[dict]:
    os.environ.setdefault("HF_HOME", f"{VOL}/cache/hf")
    os.environ["STEALTHLAB_NPM_DIR"] = f"{APP_DIR}/packaging/npm"
    # the search, and the search narrowed by a cheap model (SURVEY_LOCATE_MODEL, an exec.json profile)
    import shutil, tempfile
    home = Path(tempfile.mkdtemp())
    shutil.copyfile(f"{APP_DIR}/experiments/abtest/exec.json", home / "exec.json")
    os.environ.update({"STEALTHLAB_HOME": str(home), "SURVEY_NARROW": "1",
                       "STEALTHLAB_LOCATE_MODEL": os.environ.get("SURVEY_LOCATE_MODEL", "glm-5.3-flash")})
    import pipeline_survey as P
    out = []
    for iid in iids:
        try:
            out.append(P.stage1(iid))
        except Exception as e:  # noqa: BLE001
            out.append({"instance_id": iid, "error": f"{type(e).__name__}: {str(e)[:200]}"})
    return out


@app.function(image=build_image(), volumes={VOL: volume}, timeout=7200, cpu=2.0, memory=8192,
              secrets=[modal.Secret.from_name(KEYS_SECRET), modal.Secret.from_name(CLAUDE_SECRET)])
def stage2_batch(iids: list[str]) -> list[dict]:
    """The test writer as dispatch runs it; the Claude token is for the Sonnet fallback (STEALTHLAB_ prefixed so the
    claude child gets it, as in the series runs)."""
    import tempfile
    os.environ.setdefault("HF_HOME", f"{VOL}/cache/hf")
    os.environ["STEALTHLAB_NPM_DIR"] = f"{APP_DIR}/packaging/npm"
    os.environ["STEALTHLAB_LOCATE_MODEL"] = os.environ.get("SURVEY_LOCATE_MODEL", "glm-5.3-flash")
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        os.environ["STEALTHLAB_CLAUDE_CODE_OAUTH_TOKEN"] = os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    import shutil
    home = Path(tempfile.mkdtemp())
    shutil.copyfile(f"{APP_DIR}/experiments/abtest/exec.json", home / "exec.json")
    import pipeline_survey as P
    out = []
    for iid in iids:
        work = Path(tempfile.mkdtemp())
        (work / "home").mkdir()
        shutil.copyfile(home / "exec.json", work / "home" / "exec.json")
        try:
            out.append(P.stage2(iid, work))
        except Exception as e:  # noqa: BLE001
            out.append({"instance_id": iid, "error": f"{type(e).__name__}: {str(e)[:200]}"})
    return out


@app.function(image=build_image(), volumes={VOL: volume}, timeout=1800)
def select_ids(n: int, seed: int) -> list[str]:
    os.environ.setdefault("HF_HOME", f"{VOL}/cache/hf")
    import pipeline_survey as P
    return P.select(n, seed)


@app.function(image=build_image(), volumes={VOL: volume}, timeout=600)
def save(run: str, stage: int, rows: list[dict]) -> str:
    d = Path(VOL) / "survey" / run
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"stage{stage}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    volume.commit()
    return str(p)


@app.local_entrypoint()
def main(stage: int = 1, n: int = 48, seed: int = 1, run: str = "survey", workers: int = 8):
    import pipeline_survey as P
    ids = select_ids.remote(n, seed)
    batches = [ids[i::workers] for i in range(workers)]
    rows = [r for b in (stage1_batch if stage == 1 else stage2_batch).map(batches) for r in b]
    path = save.remote(run, stage, rows)
    local = HERE / ".local" / "survey"
    local.mkdir(parents=True, exist_ok=True)
    (local / f"{run}-stage{stage}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print(P.report(rows) if stage == 1 else P.report2(rows))
    print(f"\nsaved: {path} (volume) and {local / f'{run}-stage{stage}.jsonl'}")
