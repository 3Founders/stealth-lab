"""Boot posture, environment separation, secret hygiene and job authority.
Offline. Complements test_auth_enforcement_offline.py (request path) and
test_authorization_model_offline.py (policy)."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import auth_context as ac
from app.services.runtime_guard import RuntimeGuardViolation, assert_production_safe, is_production_safe

REPO = Path(__file__).resolve().parents[2]
GOOD_KEY = "k" * 48


def prod(**over):
    base = dict(
        environment="PRODUCTION", embedding_provider_chain="gemini,voyage", use_local_models=False,
        gemini_api_key="g", gemini_api_keys=None, voyage_api_key="v",
        database_url="postgresql://u:p@ep-x.neon.tech/db?sslmode=require",
        supabase_project_url="https://proj.supabase.co", supabase_jwt_audience="authenticated",
        oidc_issuer=None, oidc_audience=None, oidc_jwks_url=None,
        real_auth_enabled=True, multi_user_exposure_enabled=False, private_visibility_enabled=True, auth_provider=None,
        admin_api_key=GOOD_KEY, admin_api_key_legacy_enabled=True,
        service_token_issuer=None, service_token_audience=None, service_token_keys=None, service_token_alg="HS256",
        auth_environment="production",
    )
    base.update(over)
    return SimpleNamespace(**base)


def problems(**over):
    ok, reasons = is_production_safe(prod(**over))
    return reasons


def test_baseline_production_config_is_accepted():
    assert problems() == []
    assert assert_production_safe(prod()) is None


@pytest.mark.parametrize("env", ["PRODUCTION", "STAGING"])
def test_no_identity_provider_refuses_to_boot_outside_test(env):
    r = problems(environment=env, supabase_project_url=None, supabase_jwt_audience=None, real_auth_enabled=False,
                 private_visibility_enabled=False, auth_environment=env.lower())
    assert any("no identity provider" in x for x in r)
    with pytest.raises(RuntimeGuardViolation):
        assert_production_safe(prod(environment=env, supabase_project_url=None, supabase_jwt_audience=None,
                                    real_auth_enabled=False, private_visibility_enabled=False, auth_environment=env.lower()))


def test_test_environment_alone_may_run_without_identity():
    assert is_production_safe(prod(environment="TEST", supabase_project_url=None, supabase_jwt_audience=None,
                                   auth_provider="dev")) == (True, [])


@pytest.mark.parametrize("provider", ["dev", "fake", "mock", "noop", "none", "insecure", "permissive"])
def test_fake_auth_providers_refused_in_production(provider):
    assert any("permissive" in x or "no-op" in x for x in problems(auth_provider=provider))


def test_half_configured_supabase_preset_refused():
    assert problems(supabase_jwt_audience=None)


def test_non_https_issuer_refused():
    assert any("https" in x for x in problems(supabase_project_url="http://proj.supabase.co"))


@pytest.mark.parametrize("key", ["short", "admin", "change-me", "password", "x" * 31])
def test_weak_or_default_admin_key_refused(key):
    assert any("ADMIN_API_KEY" in x for x in problems(admin_api_key=key))


def test_weak_admin_key_is_fine_once_the_legacy_key_is_retired_or_unset():
    assert problems(admin_api_key="short", admin_api_key_legacy_enabled=False) == []
    assert problems(admin_api_key=None) == []


def test_service_token_config_must_be_coherent_and_distinct_from_human_identity():
    ok = dict(service_token_issuer="stealth-svc", service_token_audience="stealth-api", service_token_keys="k1:" + "s" * 48)
    assert problems(**ok) == []
    assert any("SERVICE_TOKEN" in x for x in problems(service_token_issuer="i"))                     # half configured
    assert any("shorter" in x for x in problems(**{**ok, "service_token_keys": "k1:tooshort"}))       # weak HS key
    assert any("crossover" in x for x in problems(**{**ok, "service_token_issuer": "https://proj.supabase.co/auth/v1"}))
    assert any("crossover" in x for x in problems(**{**ok, "service_token_audience": "authenticated"}))


def test_credentials_cannot_cross_environments():
    assert any("AUTH_ENVIRONMENT" in x for x in problems(auth_environment="staging"))
    assert any("AUTH_ENVIRONMENT" in x for x in problems(environment="STAGING", auth_environment="production"))


@pytest.mark.parametrize("url,bad", [
    ("postgresql://u:p@ep-1.neon.tech/db", True),
    ("postgresql://u:p@ep-1.neon.tech/db?sslmode=disable", True),
    ("postgresql://u:p@ep-1.neon.tech/db?sslmode=require", False),
    ("postgresql://u:p@ep-1.neon.tech/db?sslmode=verify-full", False),
    ("postgresql://u:p@localhost/db", False),
    ("postgresql://u:p@db/db", False),                               # compose service name
    ("postgresql://u:p@pg.railway.internal/db", False),
])
def test_database_tls_required_for_remote_hosts(url, bad):
    assert any("TLS" in x for x in problems(database_url=url)) is bad


def test_settings_production_config_check_is_no_longer_dead_code(monkeypatch):
    """Regression: it compared the upper-case environment to lower-case 'production'."""
    from app.config import Settings

    monkeypatch.setenv("STEALTHLAB_ENV", "PRODUCTION")
    with pytest.raises(RuntimeError, match="FRONTEND_ORIGIN"):
        Settings(frontend_origin="http://localhost:3000").assert_production_config()
    Settings(frontend_origin="https://app.example.com").assert_production_config()
    monkeypatch.setenv("STEALTHLAB_ENV", "TEST")
    Settings(frontend_origin="http://localhost:3000").assert_production_config()


def test_no_debug_admin_headers_or_auth_disabled_switch_exist_in_code():
    src = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in (REPO / "backend" / "app").rglob("*.py"))
    assert not re.search(r"AUTH_DISABLED|DISABLE_AUTH|SKIP_AUTH|x-debug-admin|x-admin-bypass|x-impersonate", src, re.I)
    # X-Viewer-Id is the only identity header, and only in the TEST-without-IdP posture
    assert src.count("x_viewer_id and not oidc_configured(settings) and settings.is_test") == 1


# ------------------------------------------------------------ secrets hygiene

def _tracked(*roots):
    out = subprocess.run(["git", "ls-files", *roots], cwd=REPO, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("git unavailable")
    return [REPO / f for f in out.stdout.splitlines() if f.endswith((".py", ".ts", ".tsx", ".js", ".json", ".sql", ".toml", ".yml", ".yaml", ".env", ".example"))]


_SECRET_PATTERNS = {
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "supabase_secret_key": re.compile(r"sb_secret_[A-Za-z0-9_-]{8,}"),
    "db_url_with_password": re.compile(r"postgres(?:ql)?://[^:/@\s\"']+:(?!pw\b|pass\b|password\b|secret\b|\*\*\*|\$|<|%s|\{)[^@\s\"'$<{]{4,}@(?!localhost|127\.0\.0\.1|db[:/])[\w.-]+"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def test_no_secrets_in_tracked_source():
    hits = []
    for f in _tracked("backend/app", "backend/db", "backend/scripts", "frontend/src", "frontendv1/src", "prod_frontend/app"):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for name, pat in _SECRET_PATTERNS.items():
            if pat.search(text):
                hits.append(f"{f.relative_to(REPO)}: {name}")
    assert not hits, hits


def test_supabase_service_role_key_is_never_referenced_by_the_backend_or_frontends():
    for f in _tracked("backend/app", "frontend/src", "frontendv1/src", "prod_frontend/app"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"SERVICE_ROLE|service_role", text):
            line = text[max(0, text.rfind("\n", 0, m.start())):text.find("\n", m.end())]
            # only prose/comments warning against it are allowed
            assert re.search(r"(#|//|\*|never|NEVER|must not)", line), f"{f.relative_to(REPO)}: {line.strip()[:100]}"


def test_frontends_never_receive_database_credentials():
    for f in _tracked("frontend/src", "frontendv1/src", "prod_frontend/app"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        assert not re.search(r"NEXT_PUBLIC_[A-Z_]*(DATABASE|NEON|POSTGRES|SERVICE_ROLE|SERVICE_TOKEN)", text), f


def test_migrations_do_not_depend_on_supabase_auth_schema():
    """Neon has no auth.uid()/auth.jwt()/service_role: no migration may rely on them."""
    offenders = []
    for f in (REPO / "backend" / "db").glob("*.sql"):
        code = "\n".join(l for l in f.read_text(encoding="utf-8", errors="ignore").splitlines() if not l.strip().startswith("--"))
        if re.search(r"\bauth\.(uid|jwt|role|users)\b|\bservice_role\b|\bsupabase_admin\b|\banon\b\s+(role|TO)", code, re.I):
            offenders.append(f.name)
    assert not offenders, offenders


def test_rls_policies_key_on_a_session_guc_not_supabase_functions():
    text = (REPO / "backend" / "db" / "29_rls_backstop.sql").read_text(encoding="utf-8")
    assert "current_setting('app.tenant_id'" in text.replace('"', "'") or "app.tenant_id" in text


# ------------------------------------------------------------ job authority

class _Row(dict):
    pass


def test_job_authority_derivation_is_conservative():
    from app.ingestion.queue import job_authority_from_row

    legacy_private = job_authority_from_row(_Row(owner_id="alice", visibility="private"))
    assert legacy_private.scope == "user_private" and legacy_private.submitted_by_user_id == "alice"
    assert legacy_private.publication_allowed is False, "publication is never inferred"
    legacy_public = job_authority_from_row(_Row(owner_id=None, visibility="public"))
    assert legacy_public.scope == "global_public" and legacy_public.submitted_by_user_id is None
    org = job_authority_from_row(_Row(owner_id="alice", visibility="org", auth_scope="tenant_private",
                                      auth_tenant_id="tttttttt-0000-0000-0000-000000000001", publication_allowed=False))
    assert org.scope == "tenant_private" and org.tenant_id.startswith("tttttttt")
    unknown = job_authority_from_row(_Row(visibility="weird"))
    assert unknown.scope == "system_internal"


def test_enqueue_stamps_authority_and_never_takes_it_from_the_payload():
    import asyncio

    from app.ingestion import queue as q

    captured = {}

    class Pool:
        async def fetchrow(self, sql, *args):
            captured["sql"], captured["args"] = sql, args
            return {"id": 1}

    auth = ac.JobAuthority(submitted_by_user_id="alice", tenant_id=None, scope="user_private", visibility="private",
                           publication_allowed=False)
    asyncio.run(q.enqueue(Pool(), "skill_package", {"submitted_by_user_id": "mallory", "publication_allowed": True},
                          idempotency_key="k", scope_type="user", owner_id="alice", visibility="private",
                          offload=False, authority=auth))
    args = captured["args"]
    assert "submitted_by_user_id" in captured["sql"]
    assert "alice" in args and "mallory" not in args[10:], "authority columns come from `authority`, not the payload"
    assert args[-1] is False and args[10] == "alice"


def test_worker_refuses_to_start_without_a_valid_credential(monkeypatch):
    import asyncio

    from app.config import settings
    from app.ingestion import worker

    monkeypatch.setenv("STEALTHLAB_ENV", "PRODUCTION")
    monkeypatch.delenv("INGEST_SERVICE_TOKEN", raising=False)
    monkeypatch.setattr(settings, "service_token_keys", None, raising=False)
    with pytest.raises(SystemExit):
        asyncio.run(worker.authenticate_worker(object()))
    monkeypatch.setenv("STEALTHLAB_ENV", "TEST")
    assert asyncio.run(worker.authenticate_worker(object())) is None


def test_worker_requires_the_ingestion_process_scope(monkeypatch):
    import asyncio

    from app.config import settings
    from app.ingestion import worker
    from app.services.service_identity import ServiceRecord, ServiceTokenConfig, StaticServiceRegistry, mint_service_token
    import app.services.service_identity as si

    monkeypatch.setenv("STEALTHLAB_ENV", "PRODUCTION")
    for k, v in {"service_token_issuer": "i", "service_token_audience": "a", "service_token_keys": "k1:" + "s" * 48,
                 "service_token_alg": "HS256", "auth_environment": "production"}.items():
        monkeypatch.setattr(settings, k, v, raising=False)
    cfg = ServiceTokenConfig.from_settings(settings)
    reg = StaticServiceRegistry([ServiceRecord("proj", frozenset({ac.PROJECTION_WRITE, ac.INGESTION_PROCESS}), frozenset(), "production")])
    monkeypatch.setattr(si, "PgServiceRegistry", lambda pool, ttl=0.0: reg)
    tok, _ = mint_service_token(cfg, service_id="proj", scopes=[ac.PROJECTION_WRITE])
    monkeypatch.setenv("INGEST_SERVICE_TOKEN", tok)
    with pytest.raises(SystemExit, match="ingestion:process"):
        asyncio.run(worker.authenticate_worker(object()))
    tok, _ = mint_service_token(cfg, service_id="proj", scopes=[ac.INGESTION_PROCESS])
    monkeypatch.setenv("INGEST_SERVICE_TOKEN", tok)
    ctx = asyncio.run(worker.authenticate_worker(object()))
    assert ctx.service_id == "proj" and ctx.has_scope(ac.INGESTION_PROCESS)
    monkeypatch.setenv("INGEST_SERVICE_TOKEN", tok + "x")
    with pytest.raises(SystemExit, match="rejected"):
        asyncio.run(worker.authenticate_worker(object()))
