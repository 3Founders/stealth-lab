"""Turn a GCE grading benchmark run (gce_grade_startup.sh oneshot with kel-prepull=1) into per-phase numbers.

    python bench_report.py bench30_a bench30_b      # downloads results/<run_id>/ from GCS, prints a comparison

Per run: VM setup time, pre-pull time (total, per image, MB/s, failures such as Docker Hub 429s), harness
time, per-instance test durations (from run_instance.log timestamps), peak/median memory and CPU per test
container, host load, resolved counts. These numbers size the harness concurrency and the disk choice.
"""
from __future__ import annotations

import json
import re
import statistics as st
import subprocess
import sys
from datetime import datetime
from pathlib import Path

GCLOUD = "gcloud.cmd" if sys.platform == "win32" else "gcloud"
BASE = "gs://kel-evals-239594026863/rebench/results"
LOCAL = Path(__file__).resolve().parent / "runs" / "bench"


def fetch(run_id: str) -> Path:
    dst = LOCAL / run_id
    dst.mkdir(parents=True, exist_ok=True)
    subprocess.run([GCLOUD, "storage", "rsync", "-r", f"{BASE}/{run_id}", str(dst)], capture_output=True)
    return dst


def _mb(s: str) -> float:
    m = re.match(r"([\d.]+)\s*([KMG]i?B)", s.strip())
    if not m:
        return 0.0
    v, u = float(m.group(1)), m.group(2)
    return v * {"KiB": 1 / 1024, "KB": 1 / 1000, "MiB": 1, "MB": 1, "GiB": 1024, "GB": 1000}[u]


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


def report(run_id: str) -> dict:
    d = fetch(run_id)
    log = (d / "grade.log").read_text(encoding="utf-8", errors="replace") if (d / "grade.log").exists() else ""
    phases = dict(re.findall(r"^PHASE (\w+)=(\S+)", log, re.M))
    out: dict = {"run": run_id, **{k: phases.get(k) for k in ("docker_storage", "setup_s", "prepull_s", "harness_s")}}
    pulls = []
    if (d / "bench" / "pulls.tsv").exists():
        for line in (d / "bench" / "pulls.tsv").read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                pulls.append({"image": parts[0], "s": float(parts[1] or 0), "bytes": int(parts[2] or 0), "status": parts[3]})
    ok = [p for p in pulls if p["status"] == "ok"]
    out["pulls"] = len(pulls)
    out["pull_failures"] = [p["status"][:100] for p in pulls if p["status"] != "ok"]
    if ok:
        out["image_gb_median"] = round(st.median(p["bytes"] for p in ok) / 1e9, 2)
        out["image_gb_total"] = round(sum(p["bytes"] for p in ok) / 1e9, 1)
        out["pull_s_median"] = round(st.median(p["s"] for p in ok), 1)
        out["pull_s_p90"] = round(pct([p["s"] for p in ok], 0.9), 1)
        out["pull_MBps_median_per_stream"] = round(st.median(p["bytes"] / 1e6 / max(p["s"], 0.1) for p in ok), 1)
    durs, resolved = [], 0
    for rl in d.rglob("run_instance.log"):
        ts = re.findall(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d),\d+", rl.read_text(encoding="utf-8", errors="replace"), re.M)
        if len(ts) >= 2:
            f = "%Y-%m-%d %H:%M:%S"
            durs.append((datetime.strptime(ts[-1], f) - datetime.strptime(ts[0], f)).total_seconds())
    for rep in d.rglob("report.json"):
        resolved += sum(1 for v in json.loads(rep.read_text(encoding="utf-8")).values() if v.get("resolved"))
    out["instances_graded"] = len(durs)
    out["resolved"] = resolved
    if durs:
        out["task_s_median"] = round(st.median(durs))
        out["task_s_p90"] = round(pct(durs, 0.9))
        out["task_s_max"] = round(max(durs))
        out["task_s_sum"] = round(sum(durs))
    mem, cpu = {}, {}
    if (d / "bench" / "stats.tsv").exists():
        for line in (d / "bench" / "stats.tsv").read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                name = parts[1]
                mem.setdefault(name, []).append(_mb(parts[3].split("/")[0]))
                cpu.setdefault(name, []).append(float(parts[2].rstrip("%") or 0))
    if mem:
        peaks = [max(v) for v in mem.values()]
        out["container_mem_mb_peak_median"] = round(st.median(peaks))
        out["container_mem_mb_peak_max"] = round(max(peaks))
        out["container_cpu_pct_median"] = round(st.median(x for v in cpu.values() for x in v), 1)
        out["container_cpu_pct_p90"] = round(pct([x for v in cpu.values() for x in v], 0.9), 1)
    loads = []
    if (d / "bench" / "host.tsv").exists():
        for line in (d / "bench" / "host.tsv").read_text(encoding="utf-8").splitlines():
            m = re.search(r"load1=([\d.]+)", line)
            if m:
                loads.append(float(m.group(1)))
    if loads:
        out["host_load1_median"] = round(st.median(loads), 1)
        out["host_load1_max"] = round(max(loads), 1)
    return out


if __name__ == "__main__":
    rows = [report(r) for r in sys.argv[1:]]
    keys = list(dict.fromkeys(k for r in rows for k in r))
    for k in keys:
        print(f"{k:32}" + "".join(f"{str(r.get(k)):>28}" for r in rows))
