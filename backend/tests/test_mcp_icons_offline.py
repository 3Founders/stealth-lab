"""The hosted server shows kell's own mark, not the host's: serverInfo carries icons, and the icon routes answer."""
from starlette.testclient import TestClient

from app.mcp_server import server as srv


def test_server_info_names_kell_with_icons_that_point_at_its_own_routes():
    icons = srv.server.icons
    assert srv.server.title == "kell"
    assert srv.server.website_url == "https://www.kell.org.in"
    assert [i.sizes for i in icons] == [["256x256"], ["64x64"]]
    assert all(i.src.endswith(("/icon.png", "/icon-64.png")) for i in icons)
    assert all(i.mime_type == "image/png" for i in icons)


def test_icon_routes_answer_with_the_right_type_and_are_cacheable():
    app = srv.server.streamable_http_app()
    c = TestClient(app, base_url="http://127.0.0.1:8765")
    for path, media in (("/favicon.ico", "image/x-icon"), ("/icon.png", "image/png"), ("/icon-64.png", "image/png")):
        r = c.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith(media), path
        assert "max-age" in r.headers["cache-control"]
        assert len(r.content) > 100
    assert c.get("/icon.png").content[:8] == b"\x89PNG\r\n\x1a\n"
