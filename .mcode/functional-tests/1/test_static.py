"""Static client serving from the root (spec §5 Static files)."""

import requests

from conftest import REPO_DIR, record

CLIENT = REPO_DIR / "client" / "html"


class TestStatic:
    def test_root_serves_index_html(self, main_server):
        r = requests.get(f"{main_server}/", timeout=10)
        record("test_root_serves_index_html", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text[:600])
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/html")
        assert r.content == (CLIENT / "index.html").read_bytes()

    def test_static_index_js(self, main_server):
        r = requests.get(f"{main_server}/index.js", timeout=10)
        record("test_static_index_js", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text[:400])
        assert r.status_code == 200
        assert "javascript" in r.headers["Content-Type"]
        assert r.content == (CLIENT / "index.js").read_bytes()

    def test_static_css_global(self, main_server):
        r = requests.get(f"{main_server}/css/global.css", timeout=10)
        record("test_static_css_global", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text[:400])
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/css")
        assert r.content == (CLIENT / "css" / "global.css").read_bytes()

    def test_static_success_html(self, main_server):
        r = requests.get(f"{main_server}/success.html", timeout=10)
        record("test_static_success_html", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text[:600])
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("text/html")
        assert r.content == (CLIENT / "success.html").read_bytes()

    def test_static_favicon(self, main_server):
        r = requests.get(f"{main_server}/favicon.ico", timeout=10)
        record("test_static_favicon", status=r.status_code, ctype=r.headers.get("Content-Type"), body=f"<{len(r.content)} bytes binary>")
        assert r.status_code == 200
        assert r.content == (CLIENT / "favicon.ico").read_bytes()

    def test_static_missing_file_404(self, main_server):
        r = requests.get(f"{main_server}/does-not-exist-ftrun.js", timeout=10)
        record("test_static_missing_file_404", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text[:300])
        assert r.status_code == 404

    def test_explicit_route_beats_static_file(self, spawn_server, tmp_path):
        """A file named 'health' in STATIC_DIR must not shadow GET /health."""
        static = tmp_path / "static"
        (static).mkdir()
        (static / "index.html").write_text("<html>ftrun index</html>")
        (static / "health").write_text("SHADOWED-STATIC-FILE")
        (static / "checkout-session").write_text("SHADOWED-STATIC-FILE")
        srv = spawn_server(STATIC_DIR=str(static))
        assert srv.wait_ready(), srv.output()
        h = requests.get(f"{srv.base}/health", timeout=15)
        cs = requests.get(f"{srv.base}/checkout-session", timeout=15)
        idx = requests.get(f"{srv.base}/", timeout=10)
        record(
            "test_explicit_route_beats_static_file",
            status=h.status_code,
            body=f"/health -> {h.status_code} {h.text.strip()} | /checkout-session -> {cs.status_code} {cs.text.strip()} | / -> {idx.status_code} {idx.text.strip()}",
        )
        assert h.status_code == 200 and h.json() == {"status": "ok", "livemode": False}
        assert cs.status_code == 400 and "error" in cs.json()
        assert idx.text == "<html>ftrun index</html>"
