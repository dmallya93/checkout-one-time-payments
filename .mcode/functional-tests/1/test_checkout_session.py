"""GET /checkout-session (spec §5)."""

import re

import pytest
import requests
import stripe

from conftest import STRIPE_SECRET_KEY, record

SESSION_RE = re.compile(r"(cs_test_[A-Za-z0-9]+)")


@pytest.fixture(scope="module")
def created_session_id():
    r = requests.post(
        "http://localhost:4242/create-checkout-session",
        data={"quantity": "1"},
        allow_redirects=False,
        timeout=30,
    )
    assert r.status_code == 303, r.text
    return SESSION_RE.search(r.headers["Location"]).group(1)


class TestCheckoutSession:
    def test_get_session_real_id(self, main_server, created_session_id):
        r = requests.get(f"{main_server}/checkout-session", params={"sessionId": created_session_id}, timeout=30)
        record("test_get_session_real_id", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text)
        assert r.status_code == 200
        assert r.headers["Content-Type"].startswith("application/json")
        body = r.json()
        # Compare with the raw Stripe API representation: same keys and nesting.
        client = stripe.StripeClient(STRIPE_SECRET_KEY)
        raw = client.v1.checkout.sessions.retrieve(created_session_id).to_dict(for_json=True)
        assert set(body.keys()) == set(raw.keys())
        assert body["id"] == created_session_id
        assert body["object"] == "checkout.session"
        assert body["payment_status"] == "unpaid"
        assert isinstance(body["automatic_tax"], dict) and "enabled" in body["automatic_tax"]
        assert isinstance(body["payment_method_types"], list)
        for k, v in raw.items():
            if isinstance(v, dict):
                assert isinstance(body[k], dict) and set(body[k].keys()) == set(v.keys()), k

    def test_get_session_bogus_id(self, main_server):
        r = requests.get(f"{main_server}/checkout-session", params={"sessionId": "cs_test_ftrun_bogus"}, timeout=30)
        record("test_get_session_bogus_id", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text.strip())
        assert r.status_code == 400
        assert r.json().get("error")

    def test_get_session_missing_id(self, main_server):
        r = requests.get(f"{main_server}/checkout-session", timeout=30)
        record("test_get_session_missing_id", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text.strip())
        assert r.status_code == 400
        assert r.json().get("error")

    def test_get_session_empty_id(self, main_server):
        r = requests.get(f"{main_server}/checkout-session?sessionId=", timeout=30)
        record("test_get_session_empty_id", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text.strip())
        assert r.status_code == 400
        assert r.json().get("error")
