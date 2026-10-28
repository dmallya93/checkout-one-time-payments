"""GET /health: 200 {"status":"ok","livemode":bool}; 503 {"status":"error","error":msg} on Stripe failure."""

from __future__ import annotations

import shutil
import subprocess

import requests
from conftest import BASE_URL, REPO_ROOT, TIMEOUT, free_port

JSON_CT = "application/json; charset=utf-8"


class TestHealth:
    def test_health_ok(self, evidence):
        resp = requests.get(f"{BASE_URL}/health", timeout=TIMEOUT)
        evidence(endpoint="/health", method="GET", expected_status=200,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 200
        assert resp.headers["Content-Type"] == JSON_CT
        assert resp.json() == {"status": "ok", "livemode": False}
        assert list(resp.json().keys()) == ["status", "livemode"]

    def test_health_503_on_stripe_api_error(self, evidence, fake_stripe, spawn_server):
        srv = spawn_server({"STRIPE_API_BASE": fake_stripe.url})
        fake_stripe.fail_balance = True
        try:
            resp = requests.get(f"{srv.url}/health", timeout=TIMEOUT)
        finally:
            fake_stripe.fail_balance = False
        evidence(endpoint="/health", method="GET", expected_status=503,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 503
        assert resp.headers["Content-Type"] == JSON_CT
        body = resp.json()
        assert list(body.keys()) == ["status", "error"]
        assert body["status"] == "error"
        assert "ftrun injected balance failure" in body["error"]

    def test_health_503_when_stripe_unreachable(self, evidence, spawn_server):
        mock_port = free_port()
        mock = subprocess.Popen(
            [shutil.which("stripe-mock") or "stripe-mock", "-http-port", str(mock_port),
             "-https-port", str(free_port())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            import time
            for _ in range(50):
                try:
                    requests.get(f"http://127.0.0.1:{mock_port}/v1/balance",
                                 auth=("sk_test_x", ""), timeout=1)
                    break
                except requests.RequestException:
                    time.sleep(0.2)
            srv = spawn_server({"STRIPE_API_BASE": f"http://127.0.0.1:{mock_port}"})
            ok = requests.get(f"{srv.url}/health", timeout=TIMEOUT)
            assert ok.status_code == 200
            mock.terminate()
            mock.wait(timeout=5)
            resp = requests.get(f"{srv.url}/health", timeout=60)
        finally:
            if mock.poll() is None:
                mock.kill()
        evidence(endpoint="/health", method="GET", expected_status=503,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "error"
        assert isinstance(body["error"], str) and body["error"]

    def test_static_file_takes_precedence_over_route(self, evidence, spawn_server, tmp_path):
        static = tmp_path / "static"
        shutil.copytree(REPO_ROOT / "client" / "html", static)
        (static / "health").write_text("ftrun static health file")
        srv = spawn_server({"STATIC_DIR": str(static)})
        resp = requests.get(f"{srv.url}/health", timeout=TIMEOUT)
        evidence(endpoint="/health", method="GET", expected_status=200,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 200
        assert resp.text == "ftrun static health file"
