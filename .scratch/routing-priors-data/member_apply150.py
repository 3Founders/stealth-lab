"""Apply pending migrations on search members S001..S004 (DSNs from .neon_shards.env, never printed).
Search members are migrated with the control set, as scripts/provision_neon_shards.py does."""
import os
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(r"C:\Users\user\stealth-lab\backend")
env = {}
for line in (BACKEND / ".neon_shards.env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^\s*(S\d{3}_DATABASE_URL)\s*=\s*(.+?)\s*$", line)
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
for name in sorted(env):
    out = subprocess.run([sys.executable, "scripts/migrate.py", "--dsn", env[name]], cwd=BACKEND,
                         capture_output=True, text=True)
    lines = [ln for ln in (out.stdout + out.stderr).splitlines() if ln.startswith("applied   1") and
             re.search(r"applied\s+150_", ln)]
    print(f"{name[:4]}: exit {out.returncode}; newly applied: {', '.join(ln.split()[-1] for ln in lines) or '-'}")
    if out.returncode != 0:
        tail = [ln for ln in (out.stdout + out.stderr).splitlines() if "postgres" not in ln.lower()][-6:]
        print("   " + "\n   ".join(tail))
        break

