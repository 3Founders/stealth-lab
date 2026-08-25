"""
Offline proving tests for authn.py (Band 2.9 identity gate) and its three
propagation surfaces. No DB, no network: tokens are signed with a locally
generated RSA key against a static JWKS; write boundaries are proven with
fake pools that capture SQL parameters.

The load-bearing teeth:
  - posture: exposure flags without OIDC refuse to boot (no silent slip);
  - validation: alg confusion, wrong iss/aud, expiry, unknown kid, bad
    signature all reject -- a PRESENT-but-bad token can never degrade into
    anonymous;
  - propagation: a VALIDATED actor overrides self-asserted identities at
    every boundary (ingest payload actor_id, ChangeSet author omission,
    review-event actor omission), and explicit callers are unchanged.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.agent_review_state_machine import AgentReviewStateMachine
from app.services.authn import (
    EXEMPT_PATHS,
    Actor,
    OidcConfig,
    StaticJwks,
    TokenRejected,
    assert_boot_posture,
    current_actor,
    current_actor_id,
    extract_bearer,
    jwk_from_public_numbers,
    make_actor_middleware,
    oidc_configured,
    set_current_actor,
    validate_token,
)
from app.services.changeset_record import (
    ChangeOperation,
    ChangesetRecordError,
    record_change_set,
)

ISSUER = "https://idp.example"
AUDIENCE = "stealthlab-api"
KID = "test-key-1"


# ---------------------------------------------------------------------------
# Fixtures: one RSA keypair + static JWKS + config, generated offline.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def jwk(rsa_key):
    pub = rsa_key.public_key().public_numbers()
    size = (pub.n.bit_length() + 7) // 8
    return jwk_from_public_numbers(
        n_bytes=pub.n.to_bytes(size, "big"),
        e_bytes=pub.e.to_bytes((pub.e.bit_length() + 7) // 8, "big"),
        kid=KID,
    )


@pytest.fixture(scope="module")
def pem(rsa_key):
    return rsa_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@pytest.fixture()
def jwks(jwk):
    return StaticJwks({KID: jwk})


@pytest.fixture()
def config():
    return OidcConfig(issuer=ISSUER, audience=AUDIENCE, jwks_url="unused://")


def _token(pem, sub="user-123", **overrides):
    now = datetime.now(timezone.utc)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "exp": now + timedelta(minutes=10),
        "email": "u@x.io",
        "preferred_username": "uname",
    }
    headers = {"kid": KID}
    for k, v in overrides.pop("claims", {}).items():
        if v is None:
            claims.pop(k, None)
        else:
            claims[k] = v
    headers.update(overrides.pop("headers", {}))
    alg = overrides.pop("alg", "RS256")
    key = overrides.pop("key", None) or pem
    assert not overrides, f"unused overrides: {overrides}"
    kwargs = {"algorithm": alg, "headers": headers}
    if alg != "none":
        kwargs["key"] = key
    return pyjwt.encode(claims, kwargs.get("key"), algorithm=alg, headers=headers)


# ---------------------------------------------------------------------------
# Frozen posture.
# ---------------------------------------------------------------------------


def test_multi_user_exposure_without_oidc_refuses_to_boot():
    with pytest.raises(RuntimeError, match="multi_user_exposure_enabled"):
        assert_boot_posture(
            private_visibility_enabled=False,
            real_auth_enabled=False,
            oidc_configured_=False,
            multi_user_exposure_enabled=True,
        )


def test_real_auth_flag_without_oidc_refuses_to_boot():
    with pytest.raises(RuntimeError, match="OIDC is not configured"):
        assert_boot_posture(
            private_visibility_enabled=False,
            real_auth_enabled=True,
            oidc_configured_=False,
            multi_user_exposure_enabled=False,
        )


def test_default_public_posture_boots():
    assert_boot_posture(
        private_visibility_enabled=False,
        real_auth_enabled=False,
        oidc_configured_=False,
        multi_user_exposure_enabled=False,
    )


def test_fully_configured_oidc_allows_everything():
    assert_boot_posture(
        private_visibility_enabled=True,
        real_auth_enabled=True,
        oidc_configured_=True,
        multi_user_exposure_enabled=True,
    )


def test_oidc_configured_truth_table():
    class S:
        oidc_issuer = ISSUER
        oidc_audience = AUDIENCE

    class T:
        oidc_issuer = None
        oidc_audience = AUDIENCE

    assert oidc_configured(S) is True
    assert oidc_configured(T) is False


# ---------------------------------------------------------------------------
# Token validation.
# ---------------------------------------------------------------------------


def test_valid_token_yields_actor(config, jwks, pem):
    tok = _token(pem)
    actor = validate_token(tok, config=config, jwks_provider=jwks)
    assert isinstance(actor, Actor)
    assert actor.subject == "user-123"
    assert actor.issuer == ISSUER
    assert actor.email == "u@x.io"
    assert actor.name == "uname"


def test_wrong_audience_rejected(config, jwks, pem):
    tok = _token(pem, claims={"aud": "someone-else"})
    with pytest.raises(TokenRejected, match="audience"):
        validate_token(tok, config=config, jwks_provider=jwks)


def test_wrong_issuer_rejected(config, jwks, pem):
    tok = _token(pem, claims={"iss": "https://evil.example"})
    with pytest.raises(TokenRejected, match="issuer"):
        validate_token(tok, config=config, jwks_provider=jwks)


def test_expired_token_rejected(config, jwks, pem):
    tok = _token(pem, claims={"exp": datetime.now(timezone.utc) - timedelta(minutes=1)})
    with pytest.raises(TokenRejected):
        validate_token(tok, config=config, jwks_provider=jwks)


def test_missing_sub_rejected(config, jwks, pem):
    tok = _token(pem, claims={"sub": None})
    with pytest.raises(TokenRejected):
        validate_token(tok, config=config, jwks_provider=jwks)


def test_missing_exp_rejected(config, jwks, pem):
    tok = _token(pem, claims={"exp": None})
    with pytest.raises(TokenRejected):
        validate_token(tok, config=config, jwks_provider=jwks)


def test_alg_none_rejected_before_any_key_work(config, jwks, pem):
    """THE classic JWT break: unsigned token. The whitelist must kill it
    before any key material is consulted."""
    tok = _token(pem, alg="none")
    lookups_before = jwks.refresh_calls
    with pytest.raises(TokenRejected, match="not allowed"):
        validate_token(tok, config=config, jwks_provider=jwks)
    assert jwks.refresh_calls == lookups_before  # no key lookup even happened


def test_hs256_confusion_rejected_by_whitelist(config, pem):
    """HS256-signed with a known secret must die on the whitelist, never
    reach key selection."""
    tok = _token(pem, key="attacker-known-secret", alg="HS256")
    with pytest.raises(TokenRejected, match="not allowed"):
        validate_token(tok, config=config, jwks_provider=StaticJwks({}))


def test_unknown_kid_rejected(config, pem):
    tok = _token(pem, headers={"kid": "rotated-away"})
    with pytest.raises(TokenRejected, match="kid"):
        validate_token(tok, config=config, jwks_provider=StaticJwks({KID: {}}))


def test_tampered_signature_rejected(config, jwks, pem):
    tok = _token(pem)
    head, body, sig = tok.split(".")
    tampered = f"{head}.{body}.{sig[:-2]}{'aa'[: len(sig[-2:])]}"
    with pytest.raises(TokenRejected):
        validate_token(tampered, config=config, jwks_provider=jwks)


def test_sync_validator_refuses_async_provider(config, jwks, pem):
    """Contract pin: sync validate_token must never silently skip key
    fetching -- an async provider there is a caller bug. The token is
    otherwise perfectly signed so the failure lands exactly at the
    provider contract."""

    class AsyncOnly:
        async def get_key(self, kid):
            return {}

    with pytest.raises(TokenRejected, match="coroutine"):
        validate_token(_token(pem), config=config, jwks_provider=AsyncOnly())


def test_bearer_extraction_is_strict():
    assert extract_bearer("Bearer abc") == "abc"
    assert extract_bearer("bearer abc") == "abc"
    assert extract_bearer("Bearer ") is None
    assert extract_bearer("Basic dXNlcjpwYXNz") is None
    assert extract_bearer(None) is None


# ---------------------------------------------------------------------------
# Middleware (pure ASGI harness).
# ---------------------------------------------------------------------------


class Harness:
    def __init__(self, middleware_factory, private=False):
        self.seen_actor_ids = []
        self.called = False
        mw = middleware_factory

        async def downstream(scope, receive, send):
            self.called = True
            self.seen_actor_ids.append(current_actor_id())
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        async def wrapped(scope, receive, send):
            handler = mw(downstream)
            await handler(scope, receive, send)

        self.app = wrapped

    async def call(self, path="/v1/anything", authorization=None):
        scope = {
            "type": "http",
            "path": path,
            "headers": [],
        }
        if authorization:
            scope["headers"].append((b"authorization", authorization.encode()))
        responses = []

        async def receive():
            return {"type": "http.request"}

        async def send(message):
            responses.append(message)

        await self.app(scope, receive, send)
        return responses


def _mw(config, jwks_provider, private=False):
    return make_actor_middleware(config, jwks_provider, private_visibility_enabled=private)


def test_valid_bearer_publishes_actor_downstream(config, jwks, pem):
    h = Harness(_mw(config, jwks))
    res = asyncio.run(h.call(authorization=f"Bearer {_token(pem)}"))
    assert res[0]["status"] == 200 and h.called
    assert h.seen_actor_ids == ["user-123"]


def test_invalid_bearer_is_401_and_never_degrades_to_anonymous(config, jwks, pem):
    """A PRESENT-but-bad token must never become anonymous traffic."""
    h = Harness(_mw(config, jwks))
    res = asyncio.run(h.call(authorization="Bearer garbage.token.here"))
    assert res[0]["status"] == 401 and not h.called


def test_public_posture_missing_token_passes_anonymously(config, jwks):
    h = Harness(_mw(config, jwks))
    res = asyncio.run(h.call())
    assert res[0]["status"] == 200 and h.called
    assert h.seen_actor_ids == [None]


def test_private_posture_requires_token_except_exempt_paths(config, jwks):
    h = Harness(_mw(config, jwks, private=True))
    res = asyncio.run(h.call(path="/v1/graph"))
    assert res[0]["status"] == 401 and not h.called
    for exempt in sorted(EXEMPT_PATHS):
        h2 = Harness(_mw(config, jwks, private=True))
        res2 = asyncio.run(h2.call(path=exempt))
        assert res2[0]["status"] == 200 and h2.called, exempt


def test_contextvar_is_reset_after_each_request(config, jwks, pem):
    h = Harness(_mw(config, jwks))
    asyncio.run(h.call(authorization=f"Bearer {_token(pem)}"))
    assert current_actor() is None  # no leakage into whatever runs next
    anon = Harness(_mw(config, jwks))
    asyncio.run(anon.call())
    assert anon.seen_actor_ids == [None]  # prior request's actor gone


def test_unconfigured_middleware_is_exact_passthrough():
    h = Harness(_mw(None, None))
    res = asyncio.run(h.call(authorization="Bearer anything-at-all"))
    assert res[0]["status"] == 200 and h.called


# ---------------------------------------------------------------------------
# Propagation surface 1: Events (trace ingestion override).
# ---------------------------------------------------------------------------


class FakeConn:
    def __init__(self, pool):
        self.pool = pool

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, *params):
        self.pool.executes.append((" ".join(sql.split()), params))
        return "INSERT 0 1"


class IngestPool:
    def __init__(self):
        self.executes = []

    def acquire(self):
        return FakeConn(self)


def _ingest_payload(actor_id):
    return {
        "records": [
            {
                "trace_id": "t-1",
                "timestamp": "2026-08-25T00:00:00Z",
                "task_node_id": "00000000-0000-4000-8000-00000000aaaa",
                "action_type": "execute_tool",
                "outcome": "success",
                "actor_id": actor_id,
            }
        ]
    }


def test_authenticated_actor_overrides_spoofed_payload_actor_id():
    """The security point of the whole item: the client no longer gets to
    say who performed the event."""
    from app.api.ingest import ingest_traces

    pool = IngestPool()
    tok = set_current_actor(Actor(subject="real-sub"))
    try:
        res = asyncio.run(
            ingest_traces(payload=_ingest_payload("spoofed-identity"), pool=pool, scope_key="k")
        )
    finally:
        from app.services.authn import reset_current_actor

        reset_current_actor(tok)
    assert res.accepted == 1
    sql, params = pool.executes[0]
    assert "INSERT INTO traces" in sql
    assert params[4] == "real-sub"  # actor_id column: validated identity wins


def test_anonymous_ingest_keeps_payload_actor_id():
    from app.api.ingest import ingest_traces

    pool = IngestPool()
    res = asyncio.run(
        ingest_traces(payload=_ingest_payload("exporter-7"), pool=pool, scope_key="k")
    )
    assert res.accepted == 1
    assert pool.executes[0][1][4] == "exporter-7"  # public posture unchanged


# ---------------------------------------------------------------------------
# Propagation surface 2: ChangeSets (author resolution).
# ---------------------------------------------------------------------------


class CapturePool:
    def __init__(self):
        self.calls = []

    async def fetchval(self, sql, *params):
        self.calls.append((" ".join(sql.split()), params))
        return "cs-1"

    async def executemany(self, sql, rows):
        self.calls.append((" ".join(sql.split()), rows))


def _ops():
    return [ChangeOperation(operation="status_change", target_table="procedures", target_id="p1")]


def test_changeset_author_resolves_from_contextvar():
    pool = CapturePool()
    tok = set_current_actor(Actor(subject="reviewer-9"))
    try:
        asyncio.run(record_change_set(pool, reason="approve", operations=_ops()))
    finally:
        from app.services.authn import reset_current_actor

        reset_current_actor(tok)
    author_param = pool.calls[0][1][0]
    assert author_param == "reviewer-9"


def test_changeset_without_any_authority_raises():
    with pytest.raises(ChangesetRecordError, match="unattributed"):
        asyncio.run(record_change_set(CapturePool(), reason="x", operations=_ops()))


def test_explicit_author_still_wins_over_contextvar():
    pool = CapturePool()
    tok = set_current_actor(Actor(subject="context-sub"))
    try:
        asyncio.run(
            record_change_set(pool, author="system:quarantine_timer", reason="x", operations=_ops())
        )
    finally:
        from app.services.authn import reset_current_actor

        reset_current_actor(tok)
    assert pool.calls[0][1][0] == "system:quarantine_timer"


# ---------------------------------------------------------------------------
# Propagation surface 3: Reviews (review-event actor fallback).
# ---------------------------------------------------------------------------


class ReviewPool:
    def __init__(self, state="under_review"):
        self.state = state
        self.inserts = []

    def acquire(self):
        pool = self

        class Conn:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            def transaction(self):
                # plain def returning an async CM (self), matching asyncpg
                return self

            async def fetchrow(self, sql, *params):
                return {"state": pool.state}

            async def execute(self, sql, *params):
                pool.inserts.append((" ".join(sql.split()), params))

        return Conn()


def test_review_event_actor_falls_back_to_contextvar():
    pool = ReviewPool()
    sm = AgentReviewStateMachine(pool)
    tok = set_current_actor(Actor(subject="human-reviewer"))
    try:
        asyncio.run(sm.transition("00000000-0000-4000-8000-00000000bbbb", "pending_human_approval"))
    finally:
        from app.services.authn import reset_current_actor

        reset_current_actor(tok)
    event_inserts = [p for s, p in pool.inserts if "INSERT INTO agent_review_events" in s]
    assert event_inserts and event_inserts[0][4] == "human-reviewer"


def test_review_event_explicit_actor_unchanged():
    pool = ReviewPool()
    sm = AgentReviewStateMachine(pool)
    asyncio.run(
        sm.transition(
            "00000000-0000-4000-8000-00000000bbbb",
            "rejected",
            actor="scope:ip:1.2.3.4",
        )
    )
    event_inserts = [p for s, p in pool.inserts if "INSERT INTO agent_review_events" in s]
    assert event_inserts[0][4] == "scope:ip:1.2.3.4"
