"""POST /v1/contact on the MCP host: the Contact form's route, exercised through a small Starlette app.

The pipeline itself (honeypot, limits, Turnstile, storage, the Resend email) is tested in its own file; these tests prove the
route around it: CORS for the site's origin only, the request shape the form sends, every refusal as {"detail": ...}, 202 on
success. Honest scope: `contact.submit` is faked here, and nothing was sent through a real mail provider."""
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from app.mcp_server import contact_routes
from app.services import contact

SITE = "https://www.kell.org.in"
GOOD = {"name": "Asha", "email": "asha@example.com", "company": None, "topic": "pilot", "message": "Hello, we would like a pilot.",
        "source_path": "/contact", "turnstile_token": "tok", "website": ""}


class _Server:
    def __init__(self):
        self.routes = []

    def custom_route(self, path, methods, include_in_schema=False):
        def deco(fn):
            self.routes.append(Route(path, fn, methods=methods))
            return fn
        return deco


def _client(pool=object()):
    server = _Server()
    contact_routes.register(server, lambda: pool)
    return TestClient(Starlette(routes=server.routes))


def test_preflight_from_the_site_is_allowed_and_from_elsewhere_is_not():
    c = _client()
    ok = c.options("/v1/contact", headers={"Origin": SITE, "Access-Control-Request-Method": "POST"})
    assert ok.status_code == 204 and ok.headers["access-control-allow-origin"] == SITE
    other = c.options("/v1/contact", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in other.headers


def test_a_good_message_is_handed_to_the_pipeline_and_answers_202(monkeypatch):
    seen = {}

    async def fake_submit(pool, **kw):
        seen.update(kw)
        return {"ok": True}
    monkeypatch.setattr(contact, "submit", fake_submit)
    r = _client().post("/v1/contact", json=GOOD, headers={"Origin": SITE})
    assert r.status_code == 202 and r.json() == {"ok": True}
    assert r.headers["access-control-allow-origin"] == SITE
    assert seen["name"] == "Asha" and seen["email"] == "asha@example.com" and seen["topic"] == "pilot"
    assert seen["turnstile_token"] == "tok" and seen["honeypot"] == ""


def test_a_refusal_comes_back_as_a_detail_sentence(monkeypatch):
    async def refuse(pool, **kw):
        raise contact.ContactRefused(403, "We couldn't confirm you're not a bot. Please try again.")
    monkeypatch.setattr(contact, "submit", refuse)
    r = _client().post("/v1/contact", json=GOOD, headers={"Origin": SITE})
    assert r.status_code == 403 and r.json() == {"detail": "We couldn't confirm you're not a bot. Please try again."}


def test_bad_input_is_422_and_not_json_is_400(monkeypatch):
    async def never(pool, **kw):
        raise AssertionError("a malformed request must not reach the pipeline")
    monkeypatch.setattr(contact, "submit", never)
    c = _client()
    assert c.post("/v1/contact", json={**GOOD, "name": None}).status_code == 422
    assert c.post("/v1/contact", json={**GOOD, "surprise": 1}).status_code == 422          # extra fields are refused
    assert c.post("/v1/contact", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    assert isinstance(c.post("/v1/contact", json={}).json()["detail"], str)


def test_no_database_is_a_503_the_form_can_show():
    r = _client(pool=None).post("/v1/contact", json=GOOD)
    assert r.status_code == 503 and "email us" in r.json()["detail"]
