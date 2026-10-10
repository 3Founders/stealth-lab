"""Spin up a disposable local Postgres cluster for a local layout demo --
never touches production. Prints the DSN and the data dir (for teardown)."""
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _pg_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    for base in (r"C:\Program Files\PostgreSQL",):
        p = Path(base)
        if p.exists():
            hits = sorted(p.glob(f"*/bin/{name}")) + sorted(p.glob(f"*/bin/{name}.exe"))
            if hits:
                return str(hits[-1])
    return None


initdb, pg_ctl, pg_isready, psql = (_pg_bin(n) for n in ("initdb", "pg_ctl", "pg_isready", "psql"))
assert initdb and pg_ctl and pg_isready, "postgres toolchain not found"

tmp = Path(tempfile.mkdtemp(prefix="kel_demo_pg_"))
data = tmp / "data"
logfile = tmp / "pg.log"
port = _free_port()

r = subprocess.run([initdb, "-D", str(data), "-U", "postgres", "--auth=trust", "-E", "UTF8", "--no-locale"],
                    capture_output=True, text=True)
if r.returncode != 0:
    print("initdb failed:", r.stderr, file=sys.stderr)
    sys.exit(1)

subprocess.Popen([pg_ctl, "start", "-D", str(data), "-l", str(logfile),
                   "-o", f"-p {port} -c listen_addresses=127.0.0.1"],
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

deadline = time.time() + 30
while time.time() < deadline:
    if subprocess.run([pg_isready, "-h", "127.0.0.1", "-p", str(port)], capture_output=True).returncode == 0:
        break
    time.sleep(0.5)
else:
    print("postgres never became ready", file=sys.stderr)
    sys.exit(1)

admin = f"postgresql://postgres:postgres@127.0.0.1:{port}/postgres"
r = subprocess.run([psql, admin, "-v", "ON_ERROR_STOP=1", "-c", "CREATE DATABASE kel_demo"],
                    capture_output=True, text=True)
if r.returncode != 0:
    print("createdb failed:", r.stderr, file=sys.stderr)
    sys.exit(1)

dsn = f"postgresql://postgres:postgres@127.0.0.1:{port}/kel_demo"
(tmp / "dsn.txt").write_text(dsn)
print(dsn)
print(str(tmp))
