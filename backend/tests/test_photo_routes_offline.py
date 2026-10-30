"""Profile photo route: the signature check, the size limit and per-person scoping, with a hand-rolled fake pool."""
from datetime import datetime, timezone

from starlette.applications import Starlette
from starlette.testclient import TestClient

from app.mcp_server import photo_routes as pr

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 40
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 40
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 40


def test_sniff_accepts_only_the_three_types():
    assert pr.sniff_image_type(JPEG) == "image/jpeg"
    assert pr.sniff_image_type(PNG) == "image/png"
    assert pr.sniff_image_type(WEBP) == "image/webp"
    assert pr.sniff_image_type(b"GIF89a....") is None
    assert pr.sniff_image_type(b"<svg xmlns=...>") is None
    assert pr.sniff_image_type(b"") is None


class FakePool:
    def __init__(self):
        self.rows = {}
        self.shares = set()   # (viewer, owner) pairs that share a room

    async def fetchval(self, sql, me, other):
        return 1 if (me, other) in self.shares else None

    async def fetchrow(self, sql, sub):
        return self.rows.get(sub)

    async def execute(self, sql, *args):
        if sql.startswith("DELETE"):
            self.rows.pop(args[0], None)
        else:
            sub, kind, data = args
            self.rows[sub] = {"content_type": kind, "data": data, "updated_at": datetime.now(timezone.utc)}


class FakeGate:
    async def subject(self, request):
        who = request.headers.get("authorization")
        return (who, None) if who else (None, ("sign in first", 401))


class FakeServer:
    def __init__(self):
        self.app = Starlette()

    def custom_route(self, path, methods, include_in_schema=False):
        def deco(fn):
            self.app.add_route(path, fn, methods=methods)
            return fn
        return deco


def client(with_pool=False):
    server, pool = FakeServer(), FakePool()
    pr.register(server, FakeGate(), lambda: pool)
    return (TestClient(server.app), pool) if with_pool else TestClient(server.app)


def test_round_trip_is_per_person_and_needs_sign_in():
    c = client()
    assert c.get("/v1/me/photo").status_code == 401
    assert c.put("/v1/me/photo", content=JPEG).status_code == 401
    assert c.put("/v1/me/photo", content=JPEG, headers={"authorization": "alice"}).status_code == 204
    got = c.get("/v1/me/photo", headers={"authorization": "alice"})
    assert got.status_code == 200 and got.content == JPEG and got.headers["content-type"] == "image/jpeg"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert c.get("/v1/me/photo", headers={"authorization": "bob"}).status_code == 404
    assert c.delete("/v1/me/photo", headers={"authorization": "alice"}).status_code == 204
    assert c.get("/v1/me/photo", headers={"authorization": "alice"}).status_code == 404


def test_rejects_other_types_and_oversize():
    c = client()
    h = {"authorization": "alice"}
    assert c.put("/v1/me/photo", content=b"<svg/>", headers=h).status_code == 415
    assert c.put("/v1/me/photo", content=JPEG + b"0" * pr.MAX_BYTES, headers=h).status_code == 413


def test_someone_elses_photo_is_visible_only_to_people_who_share_a_room_with_them():
    c, pool = client(with_pool=True)
    assert c.put("/v1/me/photo", content=PNG, headers={"authorization": "alice"}).status_code == 204
    assert c.get("/v1/people/alice/photo").status_code == 401
    assert c.get("/v1/people/alice/photo", headers={"authorization": "bob"}).status_code == 404     # strangers
    pool.shares.add(("bob", "alice"))
    got = c.get("/v1/people/alice/photo", headers={"authorization": "bob"})
    assert got.status_code == 200 and got.content == PNG and got.headers["content-type"] == "image/png"
    assert got.headers["cache-control"].startswith("private")
    assert c.get("/v1/people/alice/photo", headers={"authorization": "alice"}).status_code == 200   # your own
    assert c.get("/v1/people/nobody/photo", headers={"authorization": "bob"}).status_code == 404
