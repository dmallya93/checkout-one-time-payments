"""Static client served from client/html at the site root; unknown routes / wrong methods 404."""

from __future__ import annotations

import pytest
import requests
from conftest import BASE_URL, REPO_ROOT, TIMEOUT

STATIC = REPO_ROOT / "client" / "html"


class TestStaticFiles:
    @pytest.mark.parametrize(
        "path,file,ctype_prefix",
        [
            ("/", "index.html", "text/html"),
            ("/index.html", "index.html", "text/html"),
            ("/success.html", "success.html", "text/html"),
            ("/index.js", "index.js", "application/javascript"),
            ("/success.js", "success.js", "application/javascript"),
            ("/css/global.css", "css/global.css", "text/css"),
            ("/css/normalize.css", "css/normalize.css", "text/css"),
            ("/favicon.ico", "favicon.ico", "image/x-icon"),
        ],
        ids=["root", "index_html", "success_html", "index_js", "success_js", "global_css", "normalize_css", "favicon"],
    )
    def test_static_file_served(self, evidence, path, file, ctype_prefix):
        resp = requests.get(f"{BASE_URL}{path}", timeout=TIMEOUT)
        expected = (STATIC / file).read_bytes()
        evidence(
            endpoint=path, method="GET", expected_status=200, actual_status=resp.status_code,
            output=f"Content-Type: {resp.headers.get('Content-Type')}; bytes={len(resp.content)}; "
            f"matches_file={resp.content == expected}; head={resp.content[:120]!r}",
        )
        assert resp.status_code == 200
        assert resp.headers["Content-Type"].startswith(ctype_prefix)
        assert resp.content == expected

    def test_index_contains_store_page(self, evidence):
        resp = requests.get(f"{BASE_URL}/", timeout=TIMEOUT)
        evidence(endpoint="/", method="GET", expected_status=200, actual_status=resp.status_code,
                 output=resp.text[:600])
        assert resp.status_code == 200
        assert "Stripe Checkout Sample" in resp.text
        assert 'action="/create-checkout-session"' in resp.text


class TestNotFound:
    def test_unknown_route_404(self, evidence):
        resp = requests.get(f"{BASE_URL}/does-not-exist-ftrun", timeout=TIMEOUT)
        evidence(endpoint="/does-not-exist-ftrun", method="GET", expected_status=404,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 404
        assert "Cannot GET /does-not-exist-ftrun" in resp.text

    def test_missing_static_file_404(self, evidence):
        resp = requests.get(f"{BASE_URL}/css/missing-ftrun.css", timeout=TIMEOUT)
        evidence(endpoint="/css/missing-ftrun.css", method="GET", expected_status=404,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 404

    def test_post_to_static_file_404(self, evidence):
        resp = requests.post(f"{BASE_URL}/index.html", timeout=TIMEOUT)
        evidence(endpoint="/index.html", method="POST", expected_status=404,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 404
        assert "Cannot POST /index.html" in resp.text

    def test_wrong_method_on_route_404(self, evidence):
        resp = requests.put(f"{BASE_URL}/health", timeout=TIMEOUT)
        evidence(endpoint="/health", method="PUT", expected_status=404,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 404

    def test_get_on_post_only_route_404(self, evidence):
        resp = requests.get(f"{BASE_URL}/create-checkout-session", timeout=TIMEOUT)
        evidence(endpoint="/create-checkout-session", method="GET", expected_status=404,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 404
        assert "Cannot GET /create-checkout-session" in resp.text

    def test_path_traversal_not_served(self, evidence):
        resp = requests.get(f"{BASE_URL}/..%2F..%2Fserver%2Fpython%2Fserver.py", timeout=TIMEOUT)
        evidence(endpoint="/..%2F..%2Fserver%2Fpython%2Fserver.py", method="GET", expected_status=404,
                 actual_status=resp.status_code, output=resp.text[:400])
        assert resp.status_code == 404
        assert "import stripe" not in resp.text
