"""Generate the stealth_app password and the stealth_app versions of every database URL into backend/.stealth_app.env
(gitignored). Prints counts only -- never a URL or the password. Refuses to overwrite an existing file."""
import re
import secrets
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from dotenv import dotenv_values

B = Path(r"C:\Users\user\stealth-lab\backend")
out = B / ".stealth_app.env"
if out.exists():
    raise SystemExit("backend/.stealth_app.env exists already; not overwriting (delete it deliberately to regenerate)")

password = secrets.token_urlsafe(32)
urls = {}
control = dotenv_values(B / ".env").get("DATABASE_URL")
if control:
    urls["DATABASE_URL"] = control
for line in (B / ".neon_shards.env").read_text(encoding="utf-8").splitlines():
    m = re.match(r"^\s*([SK]\d{3}_DATABASE_URL)\s*=\s*(.+?)\s*$", line)
    if m:
        urls[m.group(1)] = m.group(2).strip().strip('"').strip("'")


def as_app(dsn: str) -> str:
    p = urlsplit(dsn)
    host = p.hostname or ""
    netloc = f"stealth_app:{quote(password, safe='')}@{host}" + (f":{p.port}" if p.port else "")
    return urlunsplit((p.scheme, netloc, p.path, p.query, p.fragment))


lines = ["# stealth_app credentials (scripts/provision_app_role.py). SECRET -- gitignored, never commit or paste in chat.",
         "# Paste the *_DATABASE_URL lines into the hosting service's environment (Railway) to switch the services to",
         "# the restricted role. Keep the owner URLs (backend/.env, .neon_shards.env) for scripts/migrate.py only.",
         f"STEALTH_APP_DB_PASSWORD={password}", ""]
lines += [f"{k}={as_app(v)}" for k, v in sorted(urls.items())]
out.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"wrote backend/.stealth_app.env: password + {len(urls)} URLs "
      f"({sum(k.startswith('S') for k in urls)} S, {sum(k.startswith('K') for k in urls)} K, control={'DATABASE_URL' in urls})")
