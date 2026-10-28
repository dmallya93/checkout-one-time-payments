"""GET /checkout-session?sessionId=...: session JSON, or 400 {"error":msg}."""

from __future__ import annotations

import requests
from conftest import BASE_URL, TIMEOUT

JSON_CT = "application/json; charset=utf-8"


class TestCheckoutSession:
    def test_retrieve_session(self, evidence):
        resp = requests.get(f"{BASE_URL}/checkout-session",
                            params={"sessionId": "cs_test_ftrun_123"}, timeout=TIMEOUT)
        evidence(endpoint="/checkout-session?sessionId=cs_test_ftrun_123", method="GET",
                 expected_status=200, actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 200
        assert resp.headers["Content-Type"] == JSON_CT
        body = resp.json()
        assert body["object"] == "checkout.session"
        assert body["id"] == "cs_test_ftrun_123"
        for key in ("mode", "payment_status", "url", "livemode", "status"):
            assert key in body

    def test_retrieve_session_forwards_id_to_stripe(self, evidence, fake_stripe, spawn_server):
        srv = spawn_server({"STRIPE_API_BASE": fake_stripe.url})
        before = len(fake_stripe.requests)
        resp = requests.get(f"{srv.url}/checkout-session",
                            params={"sessionId": "cs_test_ftrun_fwd"}, timeout=TIMEOUT)
        calls = [r["method"] + " " + r["path"] for r in fake_stripe.requests[before:]]
        evidence(endpoint="/checkout-session?sessionId=cs_test_ftrun_fwd", method="GET",
                 expected_status=200, actual_status=resp.status_code,
                 output=f"stripe calls={calls}; body={resp.text[:300]}")
        assert resp.status_code == 200
        assert "GET /v1/checkout/sessions/cs_test_ftrun_fwd" in calls

    def test_missing_session_id_400(self, evidence):
        resp = requests.get(f"{BASE_URL}/checkout-session", timeout=TIMEOUT)
        evidence(endpoint="/checkout-session", method="GET", expected_status=400,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 400
        assert resp.headers["Content-Type"] == JSON_CT
        body = resp.json()
        assert list(body.keys()) == ["error"]
        assert isinstance(body["error"], str) and body["error"]

    def test_empty_session_id_400(self, evidence):
        resp = requests.get(f"{BASE_URL}/checkout-session?sessionId=", timeout=TIMEOUT)
        evidence(endpoint="/checkout-session?sessionId=", method="GET", expected_status=400,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 400
        assert list(resp.json().keys()) == ["error"]

    def test_unknown_session_id_400(self, evidence, fake_stripe, spawn_server):
        srv = spawn_server({"STRIPE_API_BASE": fake_stripe.url})
        resp = requests.get(f"{srv.url}/checkout-session",
                            params={"sessionId": "cs_ftrun_missing_1"}, timeout=TIMEOUT)
        evidence(endpoint="/checkout-session?sessionId=cs_ftrun_missing_1", method="GET",
                 expected_status=400, actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 400
        assert resp.headers["Content-Type"] == JSON_CT
        assert resp.json() == {"error": "No such checkout.session: 'cs_ftrun_missing_1'"}
