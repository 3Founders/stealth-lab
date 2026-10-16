"""The pipeline survey (pipeline_survey.py) on Modal: issues in parallel containers, the same image as the series runs.

  modal run experiments/abtest/modal_survey.py --stage 1 --n 48 --seed 1 --run survey-s1
Results: printed (the table) and on the volume at /mnt/series/survey/<run>/stage<k>.jsonl.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import modal  # noqa: E402
from modal_series import APP_DIR, CLAUDE_SECRET, KEYS_SECRET, VOL, VOLUME_NAME, build_image  # noqa: E402

app = modal.App("stealthlab-survey")
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)


def _settings(job: dict) -> None:
    """The run's settings (--set K=V,...) into this worker's environment: only SURVEY_, STEALTHLAB_ and ABTEST_ names."""
    for k, v in (job.get("settings") or {}).items():
        if re.match(r"^(SURVEY|STEALTHLAB|ABTEST)_[A-Z0-9_]+$", k):
            os.environ[k] = str(v)


def _keep(job: dict, row: dict) -> dict:
    """Each issue's result is on the volume the moment it is made: a dropped connection or a stopped run loses nothing."""
    try:
        d = Path(VOL) / "survey" / job["run"] / f"stage{job['stage']}.parts"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{row.get('instance_id', 'x')}.json").write_text(json.dumps(row), encoding="utf-8")
        volume.commit()
    except Exception:  # noqa: BLE001 -- keeping is best-effort; the row is returned either way
        pass
    return row


@app.function(image=build_image(), volumes={VOL: volume}, timeout=7200, cpu=2.0, memory=8192, secrets=[modal.Secret.from_name(KEYS_SECRET)])
def stage1_batch(job: dict) -> list[dict]:
    iids = job["iids"]
    _settings(job)
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
            out.append(_keep(job, P.stage1(iid)))
        except Exception as e:  # noqa: BLE001
            out.append({"instance_id": iid, "error": f"{type(e).__name__}: {str(e)[:200]}"})
    return out


@app.function(image=build_image(), volumes={VOL: volume}, timeout=7200, cpu=2.0, memory=8192,
              secrets=[modal.Secret.from_name(KEYS_SECRET), modal.Secret.from_name(CLAUDE_SECRET)])
def stage2_batch(job: dict) -> list[dict]:
    iids = job["iids"]
    _settings(job)
    os.environ["SURVEY_WRITER_MODE"] = job.get("writer_mode", "call")
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
            out.append(_keep(job, P.stage2(iid, work)))
        except Exception as e:  # noqa: BLE001
            out.append({"instance_id": iid, "error": f"{type(e).__name__}: {str(e)[:200]}"})
    return out


@app.function(image=build_image(), volumes={VOL: volume}, timeout=28800, cpu=2.0, memory=8192,
              secrets=[modal.Secret.from_name(KEYS_SECRET), modal.Secret.from_name(CLAUDE_SECRET)])
def stage3_batch(job: dict) -> list[dict]:
    iids = job["iids"]
    _settings(job)
    """The cheap agent on each issue -- lean Claude Code and StealthLab's own loop, both on SURVEY_AGENT_MODEL -- judged by
    our tests and by the real hidden tests."""
    import shutil, tempfile
    os.environ.setdefault("HF_HOME", f"{VOL}/cache/hf")
    os.environ["STEALTHLAB_NPM_DIR"] = f"{APP_DIR}/packaging/npm"
    os.environ["STEALTHLAB_LOCATE_MODEL"] = os.environ.get("SURVEY_LOCATE_MODEL", "glm-5.3-flash")
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        os.environ["STEALTHLAB_CLAUDE_CODE_OAUTH_TOKEN"] = os.environ["CLAUDE_CODE_OAUTH_TOKEN"]
    model = os.environ.get("SURVEY_AGENT_MODEL", "glm-5.3-flash")
    cfg = json.loads(Path(f"{APP_DIR}/experiments/abtest/exec.json").read_text(encoding="utf-8"))
    cfg["executors"] = {"stealth": {"models": [model]}, "claude": {"models": [model]}}
    import pipeline_survey as P
    out = []
    for iid in iids:
        work = Path(tempfile.mkdtemp())
        (work / "home").mkdir()
        (work / "home" / "exec.json").write_text(json.dumps(cfg), encoding="utf-8")
        try:
            out.append(_keep(job, P.stage3(iid, work, model=model)))
        except Exception as e:  # noqa: BLE001
            out.append({"instance_id": iid, "error": f"{type(e).__name__}: {str(e)[:200]}"})
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return out


@app.function(image=build_image(), volumes={VOL: volume}, timeout=14400, cpu=2.0, memory=8192)
def stage4_batch(job: dict) -> list[dict]:
    """Stage 3's saved answers (run job["src_run"], on this volume) re-judged with no model call: the project's existing
    tests and the hidden tests on each saved patch."""
    import shutil, tempfile
    os.environ.setdefault("HF_HOME", f"{VOL}/cache/hf")
    volume.reload()
    src = Path(VOL) / "survey" / job["src_run"] / "stage3.parts"
    import pipeline_survey as P
    out = []
    for iid in job["iids"]:
        f = src / f"{iid}.json"
        if not f.exists():
            out.append({"instance_id": iid, "error": f"no saved answers in {job['src_run']}"})
            continue
        work = Path(tempfile.mkdtemp())
        try:
            out.append(_keep(job, P.stage4(iid, work, json.loads(f.read_text(encoding="utf-8")))))
        except Exception as e:  # noqa: BLE001
            out.append({"instance_id": iid, "error": f"{type(e).__name__}: {str(e)[:200]}"})
        finally:
            shutil.rmtree(work, ignore_errors=True)
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


@app.function(image=build_image(), volumes={VOL: volume}, timeout=86400)
def survey_run(stage: int, n: int, seed: int, run: str, workers: int, writer_mode: str = "call", ids: str = "", src_run: str = "",
               settings: dict | None = None) -> list[dict]:
    """The whole run, orchestrated inside Modal (launch with --detach: the laptop's connection can drop); issues already
    kept on the volume for this run are not run again."""
    os.environ.setdefault("HF_HOME", f"{VOL}/cache/hf")
    import pipeline_survey as P
    # ids: a probe of named issues (comma-separated) instead of the seeded sample
    ids = [i.strip() for i in ids.split(",") if i.strip()] or P.select(n, seed)
    parts = Path(VOL) / "survey" / run / f"stage{stage}.parts"
    done = {p.stem for p in parts.glob("*.json")} if parts.exists() else set()
    todo = [i for i in ids if i not in done]
    fn = {1: stage1_batch, 2: stage2_batch, 3: stage3_batch, 4: stage4_batch}[stage]
    jobs = [{"iids": todo[i::workers], "run": run, "stage": stage, "writer_mode": writer_mode, "src_run": src_run, "settings": settings or {}} for i in range(workers) if todo[i::workers]]
    for _ in fn.map(jobs):
        pass
    volume.reload()                    # the workers' commits: without a reload this container still sees the volume as it was
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(parts.glob("*.json"))] if parts.exists() else []
    save.local(run, stage, rows)
    return rows


@app.function(image=build_image(), volumes={VOL: volume}, timeout=600)
def gather(run: str, stage: int) -> list[dict]:
    volume.reload()
    parts = Path(VOL) / "survey" / run / f"stage{stage}.parts"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(parts.glob("*.json"))] if parts.exists() else []


@app.local_entrypoint()
def main(stage: int = 1, n: int = 48, seed: int = 1, run: str = "survey", workers: int = 8, collect: bool = False, writer_mode: str = "call", ids: str = "", src_run: str = "",
         wait: bool = False, set: str = ""):
    import pipeline_survey as P
    # --set "SURVEY_AGENTS=claude,SURVEY_AGENT_TIMEOUT_S=1200": settings for every worker of this run
    settings = dict(kv.split("=", 1) for kv in set.split(",") if "=" in kv) if set else {}
    if not collect and not wait:
        # started and left running: a call this entrypoint waits on is CANCELLED when the launching terminal disconnects
        # (a laptop asleep stopped survey-s3-ch2 at 8 of 15, 2026-10-16), a spawned call on a detached app is not.
        # Launch with `modal run --detach`; the results are kept per issue -> --collect when it is done.
        call = survey_run.spawn(stage, n, seed, run, workers, writer_mode, ids, src_run, settings)
        print(f"started {run} stage {stage} (call {call.object_id}); the terminal may now close. Progress: "
              f"modal volume ls {VOLUME_NAME} survey/{run}/stage{stage}.parts | then --collect")
        return
    rows = gather.remote(run, stage) if collect else survey_run.remote(stage, n, seed, run, workers, writer_mode, ids, src_run, settings)
    path = f"{VOL}/survey/{run}/stage{stage}.parts"
    local = HERE / ".local" / "survey"
    local.mkdir(parents=True, exist_ok=True)
    (local / f"{run}-stage{stage}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    print({1: P.report, 2: P.report2, 3: P.report3, 4: P.report4}[stage](rows))
    print(f"\nsaved: {path} (volume) and {local / f'{run}-stage{stage}.jsonl'}")
