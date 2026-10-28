"""GET /health against real Stripe (spec §5)."""

import requests

from conftest import record


class TestHealth:
    def test_health_ok(self, main_server):
        r = requests.get(f"{main_server}/health", timeout=15)
        record("test_health_ok", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text.strip())
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("application/json")
        assert r.json() == {"status": "ok", "livemode": False}

    def test_health_post_not_allowed(self, main_server):
        r = requests.post(f"{main_server}/health", timeout=10)
        record("test_health_post_not_allowed", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text[:300])
        assert r.status_code in (404, 405)
