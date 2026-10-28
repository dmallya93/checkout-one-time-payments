"""POST /create-checkout-session against real Stripe test mode (spec §5)."""

import re
from urllib.parse import urlparse

import requests
import stripe

from conftest import PORT, STRIPE_SECRET_KEY, record

SESSION_RE = re.compile(r"(cs_test_[A-Za-z0-9]+)")


def _post(base, **kw):
    return requests.post(f"{base}/create-checkout-session", allow_redirects=False, timeout=30, **kw)


def _verify_session(base, location, quantity, domain=None):
    """Retrieve the created session via the app and Stripe, check its params."""
    m = SESSION_RE.search(location)
    assert m, f"no session id in {location}"
    sid = m.group(1)
    r = requests.get(f"{base}/checkout-session", params={"sessionId": sid}, timeout=30)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["id"] == sid
    assert s["object"] == "checkout.session"
    assert s["mode"] == "payment"
    assert s["currency"] == "usd"
    assert s["amount_total"] == 500 * quantity
    assert s["amount_subtotal"] == 500 * quantity
    exp_domain = domain or f"http://localhost:{PORT}"
    assert s["success_url"] == f"{exp_domain}/success.html?session_id={{CHECKOUT_SESSION_ID}}"
    client = stripe.StripeClient(STRIPE_SECRET_KEY)
    items = client.v1.checkout.sessions.line_items.list(sid, params={"expand": ["data.price.product"]})
    assert len(items.data) == 1
    li = items.data[0]
    assert li.quantity == quantity
    assert li.price.unit_amount == 500
    assert li.price.currency == "usd"
    assert li.price.product.name == "Stubborn Attachments (photo)"
    return s


class TestCreateCheckoutSession:
    def test_create_session_form_quantity(self, main_server):
        r = _post(main_server, data={"quantity": "2"})
        loc = r.headers.get("Location", "")
        record("test_create_session_form_quantity", status=r.status_code, location=loc, body=f"Location: {loc}")
        assert r.status_code == 303
        assert urlparse(loc).hostname == "checkout.stripe.com"
        _verify_session(main_server, loc, 2)

    def test_create_session_json_quantity(self, main_server):
        r = _post(main_server, json={"quantity": 3})
        loc = r.headers.get("Location", "")
        record("test_create_session_json_quantity", status=r.status_code, location=loc, body=f"Location: {loc}")
        assert r.status_code == 303
        assert urlparse(loc).hostname == "checkout.stripe.com"
        _verify_session(main_server, loc, 3)

    def test_create_session_missing_quantity(self, main_server):
        r = _post(main_server, data={})
        record("test_create_session_missing_quantity", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text.strip())
        assert r.status_code == 502
        assert r.headers["Content-Type"].startswith("application/json")
        assert isinstance(r.json().get("error"), str) and r.json()["error"]

    def test_create_session_missing_quantity_json(self, main_server):
        r = _post(main_server, json={})
        record("test_create_session_missing_quantity_json", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text.strip())
        assert r.status_code == 502
        assert r.json().get("error")

    def test_create_session_invalid_quantity_string(self, main_server):
        r = _post(main_server, data={"quantity": "abc"})
        record("test_create_session_invalid_quantity_string", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text.strip())
        assert r.status_code == 502
        assert r.json().get("error")

    def test_create_session_zero_quantity(self, main_server):
        r = _post(main_server, json={"quantity": 0})
        record("test_create_session_zero_quantity", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text.strip())
        assert r.status_code == 502
        assert r.json().get("error")

    def test_create_session_quantity_not_clamped(self, main_server):
        """No server-side clamping: quantity 25 (above UI max 10) is passed to Stripe."""
        r = _post(main_server, data={"quantity": "25"})
        loc = r.headers.get("Location", "")
        record("test_create_session_quantity_not_clamped", status=r.status_code, location=loc, body=f"Location: {loc}")
        assert r.status_code == 303
        _verify_session(main_server, loc, 25)

    def test_create_session_domain_override(self, spawn_server):
        domain = "http://ftrun.example.test:8080"
        srv = spawn_server(DOMAIN=domain)
        assert srv.wait_ready(), srv.output()
        r = _post(srv.base, data={"quantity": "1"})
        loc = r.headers.get("Location", "")
        s = None
        if r.status_code == 303:
            s = _verify_session(srv.base, loc, 1, domain=domain)
        record("test_create_session_domain_override", status=r.status_code, location=loc,
               body=f"Location: {loc} | success_url: {s and s['success_url']}")
        assert r.status_code == 303
