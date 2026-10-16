"""Run the lomo folds as separate processes, 2 at a time; skip folds already written (resumable)."""
import os
import subprocess
import sys
import time

BACKEND = r"C:\Users\user\stealth-lab\backend"
OUT = r"C:\Users\user\stealth-lab\docs\routing_priors_eval"
DATA = r"C:\Users\user\stealth-lab\.scratch\routing-priors-data"
PY = os.path.join(BACKEND, ".venv", "Scripts", "python.exe")
FOLDS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
PARALLEL = int(sys.argv[2]) if len(sys.argv) > 2 else 2

env = {**os.environ, "PYTHONUTF8": "1"}
todo = [k for k in range(FOLDS) if not os.path.exists(os.path.join(OUT, f"lomo_fold{k}.json"))]
running = []
while todo or running:
    while todo and len(running) < PARALLEL:
        k = todo.pop(0)
        log = open(os.path.join(DATA, f"eval_lomo_fold{k}.log"), "w")
        running.append((k, subprocess.Popen([PY, "scripts/routing_priors_eval.py", "--data", DATA, "--out", OUT,
                                             "--exp", "lomo", "--fast", "--folds", str(FOLDS), "--fold", str(k)],
                                            cwd=BACKEND, stdout=log, stderr=subprocess.STDOUT, env=env)))
        print(time.strftime("%H:%M:%S"), "started fold", k, flush=True)
    for k, proc in list(running):
        if proc.poll() is not None:
            print(time.strftime("%H:%M:%S"), "fold", k, "exit", proc.returncode, flush=True)
            running.remove((k, proc))
    time.sleep(10)
subprocess.run([PY, "scripts/routing_priors_eval.py", "--data", DATA, "--out", OUT, "--exp", "lomo", "--fast",
                "--folds", str(FOLDS), "--merge"], cwd=BACKEND, env=env)
print(time.strftime("%H:%M:%S"), "ALLDONE", flush=True)
