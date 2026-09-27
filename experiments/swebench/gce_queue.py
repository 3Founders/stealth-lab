"""Streaming grading on Compute Engine: the OFFICIAL harness, unmodified, with native Docker, fed through a GCS
queue, so patches are graded minutes after their episode ends instead of in one batch at the end of a stage.

    python gce_queue.py workers up|status|down          # create/start, inspect, stop the worker VMs
    python gce_queue.py submit  --tag train_A0          # enqueue every new, non-empty, not-yet-graded patch
    python gce_queue.py collect --tag train_A0          # pull finished reports into runs/logs/run_evaluation/<tag>
    python gce_queue.py stream  --tag train_A0          # submit + collect every 60 s until the tag is fully graded
    python gce_queue.py gold    --parts test+calibration  # enqueue the gold patches of design parts
    (grade.py --tag X uses this module when experiment.json grading.backend == "gce")

Layout under gs://<bucket>/<prefix>/ (experiment.json grading.gce):
    inputs/                      grading_dataset.json (local-dataset benchmarks) + prepull_<worker>.txt
    queue/<worker>/<run_id>__<n>.jsonl   pending predictions (official format), one batch per submit
    done/<worker>/...            processed batches
    results/<run_id>/logs/run_evaluation/<run_id>/<model>/<instance_id>/report.json

* Sharding: worker = sha1(instance_id) mod N, so every arm's patch for one instance is graded on the SAME
  VM: its image is pulled once and cached for all arms and retries (the group-by-image lever).
* Each patch is submitted once per (run id, instance). A tag's reports all land under run id <tag>; a
  harness error is re-submitted under <tag>__r<k> and collected into the same local tag directory.
* Workers merge all pending batches of a run id into one harness call (--max_workers from config), upload
  reports as each call ends, and power off after `idle_minutes` with an empty queue (cost guard).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import swe_env

GCLOUD = "gcloud.cmd" if sys.platform == "win32" else "gcloud"
STATE = swe_env.RUNS / "gce_submitted.json"


def cfg() -> dict:
    g = swe_env.CONFIG["grading"].get("gce")
    if not g:
        raise SystemExit("experiment.json grading.gce is not configured")
    return g


def base() -> str:
    g = cfg()
    return f"{g['bucket'].rstrip('/')}/{g['prefix']}"


def _gc(*args: str, check: bool = True, capture: bool = True) -> str:
    r = subprocess.run([GCLOUD, *args], capture_output=capture, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"gcloud {' '.join(args[:3])} failed: {(r.stderr or '')[-400:]}")
    return r.stdout or ""


def shard(instance_id: str, n: int) -> int:
    return int(hashlib.sha1(instance_id.encode()).hexdigest(), 16) % n


def worker_names() -> list[str]:
    return [w["name"] for w in cfg()["workers"]]


def _state() -> dict:
    return json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}


def _save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s, indent=1), encoding="utf-8")


def plan_batches(run_id: str, rows: list[dict], submitted: dict, workers: list[str]) -> dict[str, list[dict]]:
    """Pure: which prediction rows go to which worker. Skips empty patches and anything already submitted
    under this run id. Deterministic, so a crash between upload and state save only re-sends identical rows."""
    out: dict[str, list[dict]] = {}
    done = submitted.get(run_id, {})
    for r in rows:
        iid, patch = r["instance_id"], (r.get("model_patch") or "")
        if not patch.strip() or iid in done:
            continue
        out.setdefault(workers[shard(iid, len(workers))], []).append(r)
    return out


def submit_rows(run_id: str, rows: list[dict]) -> int:
    s = _state()
    batches = plan_batches(run_id, rows, s, worker_names())
    tmp = swe_env.RUNS / "_gce_batch.jsonl"
    n = 0
    for worker, batch in batches.items():
        seq = int(time.time() * 1000)
        tmp.write_text("".join(json.dumps(r) + "\n" for r in batch), encoding="utf-8")
        _gc("storage", "cp", str(tmp), f"{base()}/queue/{worker}/{run_id}__{seq}.jsonl")
        for r in batch:
            s.setdefault(run_id, {})[r["instance_id"]] = hashlib.sha256((r.get("model_patch") or "").encode()).hexdigest()[:16]
        _save_state(s)
        n += len(batch)
    tmp.unlink(missing_ok=True)
    return n


def _predictions(tag: str) -> list[dict]:
    return rows_from(swe_env.RUNS / f"predictions_{tag}.jsonl")


def rows_from(path: Path) -> list[dict]:
    """Official-format prediction rows, last one per instance (generate.py appends)."""
    rows: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["instance_id"]] = r
    return list(rows.values())


def submit(tag: str, only: set[str] | None = None, run_id: str | None = None) -> int:
    rows = [r for r in _predictions(tag) if only is None or r["instance_id"] in only]
    return submit_rows(run_id or tag, rows)


def collect(tag: str) -> Path:
    """Download reports of <tag> and its retry run ids into runs/logs/run_evaluation/<tag>/ (grade.collect reads it)."""
    local = swe_env.RUNS / "logs" / "run_evaluation" / tag
    local.mkdir(parents=True, exist_ok=True)
    listing = _gc("storage", "ls", f"{base()}/results/", check=False)
    for line in listing.splitlines():
        rid = line.rstrip("/").rsplit("/", 1)[-1]
        if rid == tag or rid.startswith(f"{tag}__r"):
            src = f"{base()}/results/{rid}/logs/run_evaluation/{rid}"
            _gc("storage", "rsync", "-r", src, str(local), check=False)
    return local


def reported(tag: str) -> set[str]:
    local = swe_env.RUNS / "logs" / "run_evaluation" / tag
    return {p.parent.name for p in local.rglob("report.json")} if local.exists() else set()


def _grades(tag: str, ids: list[str], preds: dict[str, str]) -> dict:
    import grade
    return grade.collect(tag, ids, preds)


def wait(tag: str, ids: list[str], timeout_s: int = 6 * 3600) -> None:
    """grade.py's gce backend: wait until every id has a report (or the timeout), collecting as it goes."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        collect(tag)
        missing = set(ids) - reported(tag)
        print(f"{tag}: {len(ids) - len(missing)}/{len(ids)} graded", flush=True)
        if not missing:
            return
        time.sleep(60)
    print(f"{tag}: timed out with {len(set(ids) - reported(tag))} ungraded (re-run to resume)")


def stream(tag: str, poll_s: int = 60) -> None:
    """Grade a tag while generate.py is still producing it. Ends when every design instance of the tag has a
    final attempt (non-environmental) and every non-empty, non-copied patch has a report."""
    import grade
    from generate import design, load_jsonl
    part = tag.split("_", 1)[0]
    want = set(design()[part])
    while True:
        rows = _predictions(tag)
        preds = {r["instance_id"]: r.get("model_patch") or "" for r in rows}
        copied = grade.reused_a0_grades(tag, preds)
        n = submit_rows(tag, [r for r in rows if r["instance_id"] not in copied])
        collect(tag)
        attempts = {r["instance_id"] for r in load_jsonl(swe_env.RUNS / f"attempts_{tag}.jsonl")
                    if not r.get("environmental_failure")}
        need = {i for i, p in preds.items() if p.strip() and i not in copied}
        pending = need - reported(tag)
        print(f"{time.strftime('%H:%M:%S')} {tag}: attempts {len(attempts & want)}/{len(want)} | submitted +{n} | "
              f"graded {len(need) - len(pending)}/{len(need)} | copied {len(copied)}", flush=True)
        if want <= attempts and not pending:
            grades = {**_grades(tag, sorted(set(preds) - set(copied)), preds), **copied}
            (swe_env.RUNS / f"grades_{tag}.json").write_text(json.dumps(grades, indent=1), encoding="utf-8")
            print(f"{tag}: complete -> grades_{tag}.json")
            return
        time.sleep(poll_s)


def gold(parts: str, run_id: str = "gold_check") -> int:
    """Enqueue the gold patches of the given design parts (a gold check through the same queue)."""
    ids = {i for p in parts.split("+") for i in json.loads((swe_env.RUNS / "design.json").read_text())[p]}
    return gold_ids(run_id, ids)


def gold_ids(run_id: str, ids) -> int:
    ids = set(ids)
    g = swe_env.CONFIG["grading"]
    name = g.get("dataset", swe_env.CONFIG["dataset"]["name"])
    if name.endswith(".json"):
        rows = json.loads((swe_env.CONFIG_PATH.parent / name).read_text(encoding="utf-8"))
    else:
        from datasets import load_dataset
        rows = load_dataset(name, split=swe_env.CONFIG["dataset"]["split"])
    preds = [{"instance_id": r["instance_id"], "model_name_or_path": "gold", "model_patch": r["patch"]}
             for r in rows if r["instance_id"] in ids]
    return submit_rows(run_id, preds)


def write_prepull(parts: str) -> None:
    """inputs/prepull_<worker>.txt: the images each worker will need, pulled in the background at boot."""
    ids = sorted({i for p in parts.split("+") for i in json.loads((swe_env.RUNS / "design.json").read_text())[p]})
    workers = worker_names()
    name = swe_env.CONFIG["grading"].get("dataset", swe_env.CONFIG["dataset"]["name"])
    if name.endswith(".json"):
        image = {r["instance_id"]: r["image"] for r in json.loads((swe_env.CONFIG_PATH.parent / name).read_text(encoding="utf-8"))}
    else:
        from datasets import load_dataset
        image = {r["instance_id"]: r["image"] for r in load_dataset(name, split=swe_env.CONFIG["dataset"]["split"])}
    tmp = swe_env.RUNS / "_prepull.txt"
    for k, w in enumerate(workers):
        tmp.write_text("\n".join(tagged(image[i]) for i in ids if i in image and shard(i, len(workers)) == k) + "\n",
                       encoding="utf-8", newline="\n")
        _gc("storage", "cp", str(tmp), f"{base()}/inputs/prepull_{w}.txt")
    # keep_images.txt: images every arm grades again (test + calibration); workers never prune these
    design = json.loads((swe_env.RUNS / "design.json").read_text())
    keep = sorted({tagged(image[i]) for p in ("test", "calibration") for i in design[p] if i in image})
    tmp.write_text("\n".join(keep) + "\n", encoding="utf-8", newline="\n")
    _gc("storage", "cp", str(tmp), f"{base()}/inputs/keep_images.txt")
    tmp.unlink(missing_ok=True)


def tagged(image: str) -> str:
    """repo[:tag] as `docker images` prints it (an untagged reference means :latest)."""
    return image if ":" in image.rsplit("/", 1)[-1] else f"{image}:latest"


def workers(action: str) -> None:
    g = cfg()
    script = Path(__file__).resolve().parents[1] / "swebench_rebench" / "gce_grade_startup.sh"
    dataset = g.get("worker_dataset") or swe_env.CONFIG["grading"].get("dataset", swe_env.CONFIG["dataset"]["name"])
    for w in g["workers"]:
        name, zone = w["name"], w["zone"]
        status = _gc("compute", "instances", "describe", name, "--zone", zone, "--format=value(status)",
                     check=False).strip()
        if action == "status":
            print(name, status or "absent")
        elif action == "down" and status:
            _gc("compute", "instances", "stop", name, "--zone", zone, "--async")
            print(name, "stopping")
        elif action == "up":
            meta = ",".join(f"{k}={v}" for k, v in {
                "kel-mode": "daemon", "kel-bucket": g["bucket"], "kel-prefix": g["prefix"], "kel-worker": name,
                "kel-dataset": dataset, "kel-workers": w["harness_workers"], "kel-idle-min": g.get("idle_minutes", 20),
                "kel-max-hours": g.get("max_hours", 8), "kel-pull-par": w.get("pull_parallel", 6),
                "kel-local-ssd": 1 if w.get("local_ssds") else 0}.items())
            if not status:
                extra = [a for _ in range(int(w.get("local_ssds", 0))) for a in ("--local-ssd", "interface=NVME")]
                if w.get("spot"):   # interruptible: the queue resumes (unprocessed batches stay in queue/)
                    extra += ["--provisioning-model=SPOT", "--instance-termination-action=STOP"]
                _gc("compute", "instances", "create", name, "--zone", zone, "--machine-type", w["machine_type"],
                    "--image-family", "ubuntu-2204-lts", "--image-project", "ubuntu-os-cloud",
                    "--boot-disk-size", f"{w.get('disk_gb', 40)}GB", "--boot-disk-type", "pd-balanced", *extra,
                    "--scopes", "cloud-platform", f"--metadata-from-file=startup-script={script}", f"--metadata={meta}")
                print(name, "created")
            else:
                _gc("compute", "instances", "add-metadata", name, "--zone", zone,
                    f"--metadata-from-file=startup-script={script}", f"--metadata={meta}")
                if status != "RUNNING":
                    _gc("compute", "instances", "start", name, "--zone", zone, "--async")
                print(name, "started" if status != "RUNNING" else "running (metadata updated)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["workers", "submit", "collect", "stream", "gold", "prepull"])
    ap.add_argument("sub", nargs="?")
    ap.add_argument("--tag")
    ap.add_argument("--parts", default="test+calibration")
    ap.add_argument("--run-id")
    a = ap.parse_args()
    if a.action == "workers":
        workers(a.sub or "status")
    elif a.action == "submit":
        print(submit(a.tag, run_id=a.run_id), "patches enqueued")
    elif a.action == "collect":
        print(collect(a.tag), len(reported(a.tag)), "reports")
    elif a.action == "stream":
        stream(a.tag)
    elif a.action == "gold":
        print(gold(a.parts, a.run_id or "gold_check"), "gold patches enqueued")
    elif a.action == "prepull":
        write_prepull(a.parts)


if __name__ == "__main__":
    main()
