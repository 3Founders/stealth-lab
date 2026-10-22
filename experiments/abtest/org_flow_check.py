"""Drives the whole organization flow against the local stack (run_org_stack.py) with the real CLI, and prints what happened.

    python experiments/abtest/org_flow_check.py

What it does, in order (statuses only; no key or token is printed):
  1. both test users sign in once (that creates their user rows);
  2. provision_org.py creates the organization and makes the `cto` user its owner (loopback database only);
  3. as the CTO, over the REST API: connect the provider key, apply the recommended policy, create an invite;
  4. as the developer, in a throwaway home directory: run  stealthlab-mcp join  with a Cursor model (a REAL call through the
     gateway to the provider: one 8-token request, a fraction of a cent);
  5. check the ledger row, the onboarding counts, and that the key is nowhere in plain text on the developer's machine or in the
     database.

The provider key is read from GENERAL_COMPUTE_API_KEY (environment, else backend/.env) and sent only to the local API.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
LOCAL = HERE / ".local"
API = os.environ.get("ORG_TEST_API", "http://127.0.0.1:8000")
DSN = os.environ.get("ORG_TEST_DATABASE_URL", "postgresql://kel_owner@127.0.0.1:55432/kel_orgtest")
PSQL = os.environ.get("PSQL", r"C:\Program Files\PostgreSQL\17\bin\psql.exe")
FAILED: list[str] = []


def key_from_env() -> str:
    v = os.environ.get("GENERAL_COMPUTE_API_KEY", "").strip()
    if not v:
        for line in (ROOT / "backend" / ".env").read_text(encoding="utf8").splitlines():
            if line.startswith("GENERAL_COMPUTE_API_KEY="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not v:
        sys.exit("set GENERAL_COMPUTE_API_KEY (or put it in backend/.env)")
    return v


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{(' -- ' + detail) if detail else ''}")
    if not ok:
        FAILED.append(name)
    return ok


def tok(name: str) -> str:
    return (LOCAL / f"token-{name}.txt").read_text().strip()


def api(method: str, path: str, who: str, **kw) -> httpx.Response:
    return httpx.request(method, f"{API}{path}", headers={"Authorization": f"Bearer {tok(who)}"}, timeout=60, **kw)


def psql(sql: str) -> str:
    r = subprocess.run([PSQL, "-X", "-A", "-t", DSN, "-c", sql], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def main() -> int:
    provider_key = key_from_env()
    print("1. sign-in creates the users")
    for who in ("cto", "dev"):
        check(f"{who} signs in", api("GET", "/v1/orgs/mine", who).status_code == 200)

    print("2. operator provisions the organization")
    r = subprocess.run([sys.executable, str(ROOT / "backend" / "scripts" / "provision_org.py"), "--name", "Example Company",
                        "--slug", "example", "--owner-email", "cto@stealthlab.invalid", "--dsn", DSN],
                       capture_output=True, text=True, timeout=60, cwd=ROOT / "backend")
    check("provision_org.py", r.returncode == 0, (r.stdout + r.stderr).strip()[-200:])
    mine = api("GET", "/v1/orgs/mine", "cto").json()
    org = next((o["organization_id"] for o in mine if o["name"] == "Example Company"), None)
    if not check("cto is the owner", bool(org) and mine[0]["role"] == "owner", json.dumps(mine)[:120]):
        return 1

    print("3. the CTO, over the API")
    units = [{"model": "gemma-4-31b-it", "provider_model": "gemma-4-31B-it", "input_per_mtok": 0.09, "output_per_mtok": 0.34,
              "no_system_role": True},
             {"model": "gpt-oss-120b", "input_per_mtok": 0.037, "output_per_mtok": 0.17}]
    body = {"provider": "general-compute", "base_url": "https://api.generalcompute.com/v1", "units": units, "api_key": provider_key}
    r = api("PUT", f"/v1/orgs/{org}/provider-keys/main", "cto", json=body)
    check("connect the key", r.status_code == 200, f"{r.status_code} {r.text[:160] if r.status_code != 200 else ''}")
    check("the response holds no key", provider_key not in r.text and "api_key" not in r.text)
    r = api("GET", f"/v1/orgs/{org}/provider-keys", "cto")
    check("list shows a hint only", r.status_code == 200 and provider_key not in r.text and r.json()[0]["key_hint"].startswith("..."))
    r = api("POST", f"/v1/orgs/{org}/policy/recommended", "cto", json={"monthly_budget_usd": "50", "per_user_daily_budget_usd": "5"})
    check("apply the recommended policy", r.status_code == 200, f"{r.status_code} {r.text[:200] if r.status_code != 200 else ''}")
    r = api("POST", f"/v1/orgs/{org}/invites", "cto", json={"label": "local test", "days": 1, "max_uses": 2})
    check("create an invite", r.status_code == 201, r.text[:160] if r.status_code != 201 else "")
    code = r.json()["code"]
    check("the invite looks right", re.fullmatch(r"sl1_[0-9a-f]{32}_[0-9a-f]{24}", code) is not None)
    check("status says invite developers", api("GET", f"/v1/orgs/{org}/onboarding", "cto").json()["next_step"] == "waiting_for_developers")

    print("4. the developer runs one command (real CLI, throwaway home)")
    home = tempfile.mkdtemp(prefix="orgtest-home-")
    sl = tempfile.mkdtemp(prefix="orgtest-sl-")
    env = {**os.environ, "STEALTHLAB_TEST_HOME": home, "STEALTHLAB_HOME": sl, "STEALTHLAB_TOKEN": tok("dev"),
           "STEALTHLAB_MCP_URL": "", "STEALTHLAB_CURSOR_MODEL": ""}
    cli = ["node", str(ROOT / "packaging" / "npm" / "bin" / "stealthlab-mcp.mjs"), "join", code, "--client", "cursor",
           "--url", "http://127.0.0.1:8765/mcp", "--api", API, "--cursor-model", "test-model"]
    r = subprocess.run(cli, capture_output=True, text=True, timeout=180, env=env)
    out = r.stdout + r.stderr
    print("      " + "\n      ".join(out.strip().splitlines()[-14:]))
    check("join exits 0 (ready)", r.returncode == 0, f"exit {r.returncode}")
    check("self-test line", "Done by" in out)

    print("5. what is on disk and in the database")
    exec_json = (Path(sl) / "exec.json").read_text() if (Path(sl) / "exec.json").exists() else ""
    cfg = (Path(sl) / "config.json").read_text() if (Path(sl) / "config.json").exists() else ""
    check("exec.json has gateway profiles and no key", "key_from" in exec_json and provider_key not in exec_json and "key_env" not in exec_json)
    check("the provider key is not on the developer machine", provider_key not in exec_json + cfg + out)
    check("the database holds ciphertext, not the key", provider_key not in psql("select secret_ciphertext from org_provider_keys") and
          psql("select left(secret_ciphertext,3) from org_provider_keys") == "v1.")
    ledger = psql("select status||' '||tool||' '||model from provider_call_ledger order by created_at desc limit 1")
    check("one settled gateway call is in the ledger", ledger.startswith("settled chat_completions"), ledger)
    st = api("GET", f"/v1/orgs/{org}/onboarding", "cto").json()
    check("onboarding: 2 joined, 1 ready, running", st["members"] == 2 and st["members_ready"] == 1 and st["next_step"] == "running", json.dumps(st))
    check("the developer cannot read the keys", api("GET", f"/v1/orgs/{org}/provider-keys", "dev").status_code == 403)
    check("the developer can read the client config", api("GET", f"/v1/orgs/{org}/client-config", "dev").status_code == 200)
    check("joining again uses no second invite", subprocess.run(cli, capture_output=True, text=True, timeout=180, env=env).returncode == 0 and
          psql("select uses from org_invites") == "1")

    print("6. refusals")
    r = api("POST", "/v1/join", "dev", json={"code": code[:-1] + ("0" if code[-1] != "0" else "1")})
    check("a wrong code is not found", r.status_code == 404)
    inv = api("GET", f"/v1/orgs/{org}/invites", "cto").json()[0]["id"]
    check("revoke the invite", api("DELETE", f"/v1/orgs/{org}/invites/{inv}", "cto").status_code == 204)
    r = httpx.post(f"{API}/v1/join", headers={"Authorization": f"Bearer {tok('cto')}"}, json={"code": code}, timeout=60)
    check("a revoked invite is refused", r.status_code == 404, str(r.status_code))
    k = api("POST", f"/v1/orgs/{org}/kill-switch", "cto", json={"on": True, "reason": "local test"})
    check("kill switch on", k.status_code == 200)
    g = httpx.post(f"{API}/v1/chat/completions", headers={"Authorization": f"Bearer {tok('dev')}", "X-Stealthlab-Org": org},
                   json={"model": "gpt-oss-120b", "max_tokens": 4, "messages": [{"role": "user", "content": "hi"}]}, timeout=60)
    check("the gateway refuses while stopped", g.status_code >= 400 and "stopped" in g.text, f"{g.status_code} {g.text[:120]}")
    check("kill switch off", api("POST", f"/v1/orgs/{org}/kill-switch", "cto", json={"on": False, "reason": "local test"}).status_code == 200)

    print(f"\n{'ALL OK' if not FAILED else 'FAILED: ' + ', '.join(FAILED)}")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
