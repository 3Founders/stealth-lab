"""Read-only: migrate.py --status on search members S001..S004, DSNs loaded from .neon_shards.env, never printed."""
import os
import re
import subprocess
import sys
from pathlib import Path

BACKEND = Path(r"C:\Users\user\stealth-lab\backend")
env = {}
for line in (BACKEND / ".neon_shards.env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^\s*(K001_DATABASE_URL)\s*=\s*(.+?)\s*$", line)
    if m:
        env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
for name in sorted(env)[: int(sys.argv[1]) if len(sys.argv) > 1 else 4]:
    out = subprocess.run([sys.executable, "scripts/migrate.py", "--status", "--dsn", env[name]], cwd=BACKEND,
                         capture_output=True, text=True)
    lines = (out.stdout + out.stderr).splitlines()
    pending = [ln for ln in lines if ln.startswith("pending")]
    applied = [ln for ln in lines if ln.startswith("applied")]
    print(f"{name[:4]}: applied {len(applied)}, last applied {applied[-1].split()[-1] if applied else '-'}; "
          f"pending {len(pending)}: {', '.join(p.split()[-1] for p in pending)}")
    if out.returncode != 0 and not lines:
        print(f"{name[:4]}: status failed (exit {out.returncode})")

