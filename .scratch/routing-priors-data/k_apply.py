"""Apply pending migrations on knowledge shards K001..K084 (DSNs from .neon_shards.env, never printed).
Stops at the first failure. Prints one line per shard."""
import re
import subprocess
import sys
import time
from pathlib import Path

BACKEND = Path(r"C:\Users\user\stealth-lab\backend")
env = {}
for line in (BACKEND / ".neon_shards.env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^\s*(K\d{3}_DATABASE_URL)\s*=\s*(.+?)\s*$", line)
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
done = fail = 0
for name in sorted(env):
    t0 = time.time()
    out = subprocess.run([sys.executable, "scripts/migrate.py", "--dsn", env[name]], cwd=BACKEND,
                         capture_output=True, text=True)
    text = out.stdout + out.stderr
    applied = [ln.split()[-1] for ln in text.splitlines() if re.match(r"applied\s+1(3[4-9]|4\d|50)_", ln)]
    print(f"{name[:4]}: exit {out.returncode} in {time.time() - t0:.0f}s; newly applied {len(applied)}", flush=True)
    if out.returncode != 0:
        tail = [ln for ln in text.splitlines() if "postgres" not in ln.lower() and "neon" not in ln.lower()][-8:]
        print("   " + "\n   ".join(tail), flush=True)
        fail += 1
        break
    done += 1
print(f"DONE ok={done} failed={fail} of {len(env)}", flush=True)
