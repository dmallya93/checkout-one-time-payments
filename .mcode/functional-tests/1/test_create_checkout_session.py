"""POST /create-checkout-session: 303 to session.url; exact Stripe params; 502 on Stripe error."""

from __future__ import annotations

import pytest
import requests
from conftest import BASE_URL, TIMEOUT

JSON_CT = "application/json; charset=utf-8"
FT_DOMAIN = "http://ftrun.example.test:9999"


@pytest.fixture
def proxied(fake_stripe, spawn_server):
    srv = spawn_server({"STRIPE_API_BASE": fake_stripe.url, "DOMAIN": FT_DOMAIN})
    return srv


def _last_create(fake_stripe, before):
    creates = [r for r in fake_stripe.requests[before:]
               if r["method"] == "POST" and r["path"] == "/v1/checkout/sessions"]
    assert len(creates) == 1, creates
    return creates[0]


class TestCreateCheckoutSessionRedirect:
    def test_form_quantity_redirects_303(self, evidence):
        resp = requests.post(f"{BASE_URL}/create-checkout-session", data={"quantity": "2"},
                             allow_redirects=False, timeout=TIMEOUT)
        evidence(endpoint="/create-checkout-session", method="POST", request_body="quantity=2",
                 expected_status=303, actual_status=resp.status_code,
                 output=f"Location: {resp.headers.get('Location')}\n{resp.text}")
        assert resp.status_code == 303
        assert resp.headers["Location"].startswith("https://checkout.stripe.com/")

    def test_json_quantity_redirects_303(self, evidence):
        resp = requests.post(f"{BASE_URL}/create-checkout-session", json={"quantity": 3},
                             allow_redirects=False, timeout=TIMEOUT)
        evidence(endpoint="/create-checkout-session", method="POST", request_body='{"quantity": 3}',
                 expected_status=303, actual_status=resp.status_code,
                 output=f"Location: {resp.headers.get('Location')}\n{resp.text}")
        assert resp.status_code == 303
        assert resp.headers["Location"].startswith("https://checkout.stripe.com/")

    def test_no_body_redirects_303(self, evidence):
        resp = requests.post(f"{BASE_URL}/create-checkout-session",
                             allow_redirects=False, timeout=TIMEOUT)
        evidence(endpoint="/create-checkout-session", method="POST",
                 expected_status=303, actual_status=resp.status_code,
                 output=f"Location: {resp.headers.get('Location')}\n{resp.text}")
        assert resp.status_code == 303
        assert resp.headers["Location"].startswith("https://checkout.stripe.com/")

    def test_location_is_session_url(self, evidence, fake_stripe, proxied):
        resp = requests.post(f"{proxied.url}/create-checkout-session", data={"quantity": "1"},
                             allow_redirects=False, timeout=TIMEOUT)
        # retrieve the stripe-mock session url for comparison
        sess = requests.get(f"{BASE_URL}/checkout-session", params={"sessionId": "cs_test_x"},
                            timeout=TIMEOUT).json()
        evidence(endpoint="/create-checkout-session", method="POST", request_body="quantity=1",
                 expected_status=303, actual_status=resp.status_code,
                 output=f"Location: {resp.headers.get('Location')}; stripe session url: {sess['url']}")
        assert resp.status_code == 303
        assert resp.headers["Location"] == sess["url"]


class TestCreateCheckoutSessionParams:
    def test_session_params_and_literal_placeholder(self, evidence, fake_stripe, proxied):
        before = len(fake_stripe.requests)
        resp = requests.post(f"{proxied.url}/create-checkout-session", data={"quantity": "2"},
                             allow_redirects=False, timeout=TIMEOUT)
        req = _last_create(fake_stripe, before)
        form = req["form"]
        evidence(endpoint="/create-checkout-session", method="POST", request_body="quantity=2",
                 expected_status=303, actual_status=resp.status_code,
                 output=f"stripe request body: {req['body']}")
        assert resp.status_code == 303
        assert form["mode"] == ["payment"]
        assert form["line_items[0][price_data][currency]"] == ["usd"]
        assert form["line_items[0][price_data][unit_amount]"] == ["500"]
        assert form["line_items[0][price_data][product_data][name]"] == ["Stubborn Attachments (photo)"]
        assert form["line_items[0][quantity]"] == ["2"]
        assert form["success_url"] == [f"{FT_DOMAIN}/success.html?session_id={{CHECKOUT_SESSION_ID}}"]
        assert set(form) == {
            "mode", "success_url", "line_items[0][price_data][currency]",
            "line_items[0][price_data][unit_amount]",
            "line_items[0][price_data][product_data][name]", "line_items[0][quantity]",
        }

    def test_app_info_sent_to_stripe(self, evidence, fake_stripe, proxied):
        before = len(fake_stripe.requests)
        resp = requests.post(f"{proxied.url}/create-checkout-session", data={"quantity": "1"},
                             allow_redirects=False, timeout=TIMEOUT)
        req = _last_create(fake_stripe, before)
        headers = {k.lower(): v for k, v in req["headers"].items()}
        ua = headers.get("x-stripe-client-user-agent", "") + " | " + headers.get("user-agent", "")
        evidence(endpoint="/create-checkout-session", method="POST", request_body="quantity=1",
                 expected_status=303, actual_status=resp.status_code, output=ua)
        assert resp.status_code == 303
        assert "stripe-samples/checkout-one-time-payments/0.0.1" in headers.get("user-agent", "")
        assert "https://github.com/stripe-samples/checkout-one-time-payments" in ua

    def test_quantity_omitted_when_absent(self, evidence, fake_stripe, proxied):
        before = len(fake_stripe.requests)
        resp = requests.post(f"{proxied.url}/create-checkout-session",
                             allow_redirects=False, timeout=TIMEOUT)
        req = _last_create(fake_stripe, before)
        evidence(endpoint="/create-checkout-session", method="POST",
                 expected_status=303, actual_status=resp.status_code,
                 output=f"stripe request body: {req['body']}")
        assert resp.status_code == 303
        assert not any("quantity" in k for k in req["form"])

    def test_quantity_omitted_for_empty_json(self, evidence, fake_stripe, proxied):
        before = len(fake_stripe.requests)
        resp = requests.post(f"{proxied.url}/create-checkout-session", json={},
                             allow_redirects=False, timeout=TIMEOUT)
        req = _last_create(fake_stripe, before)
        evidence(endpoint="/create-checkout-session", method="POST", request_body="{}",
                 expected_status=303, actual_status=resp.status_code,
                 output=f"stripe request body: {req['body']}")
        assert resp.status_code == 303
        assert not any("quantity" in k for k in req["form"])

    def test_quantity_not_clamped_high(self, evidence, fake_stripe, proxied):
        before = len(fake_stripe.requests)
        resp = requests.post(f"{proxied.url}/create-checkout-session", data={"quantity": "100"},
                             allow_redirects=False, timeout=TIMEOUT)
        req = _last_create(fake_stripe, before)
        evidence(endpoint="/create-checkout-session", method="POST", request_body="quantity=100",
                 expected_status=303, actual_status=resp.status_code,
                 output=f"stripe request body: {req['body']}")
        assert resp.status_code == 303
        assert req["form"]["line_items[0][quantity]"] == ["100"]

    def test_quantity_not_clamped_zero(self, evidence, fake_stripe, proxied):
        before = len(fake_stripe.requests)
        resp = requests.post(f"{proxied.url}/create-checkout-session", json={"quantity": 0},
                             allow_redirects=False, timeout=TIMEOUT)
        req = _last_create(fake_stripe, before)
        evidence(endpoint="/create-checkout-session", method="POST", request_body='{"quantity": 0}',
                 expected_status=303, actual_status=resp.status_code,
                 output=f"stripe request body: {req['body']}")
        assert resp.status_code == 303
        assert req["form"]["line_items[0][quantity]"] == ["0"]

    def test_json_quantity_passed_through(self, evidence, fake_stripe, proxied):
        before = len(fake_stripe.requests)
        resp = requests.post(f"{proxied.url}/create-checkout-session", json={"quantity": 7},
                             allow_redirects=False, timeout=TIMEOUT)
        req = _last_create(fake_stripe, before)
        evidence(endpoint="/create-checkout-session", method="POST", request_body='{"quantity": 7}',
                 expected_status=303, actual_status=resp.status_code,
                 output=f"stripe request body: {req['body']}")
        assert resp.status_code == 303
        assert req["form"]["line_items[0][quantity]"] == ["7"]


class TestCreateCheckoutSessionErrors:
    def test_non_numeric_quantity_502(self, evidence):
        resp = requests.post(f"{BASE_URL}/create-checkout-session", data={"quantity": "abc"},
                             allow_redirects=False, timeout=TIMEOUT)
        evidence(endpoint="/create-checkout-session", method="POST", request_body="quantity=abc",
                 expected_status=502, actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 502
        assert resp.headers["Content-Type"] == JSON_CT
        body = resp.json()
        assert list(body.keys()) == ["error"]
        assert body["error"]

    def test_wrong_json_type_quantity_502(self, evidence):
        resp = requests.post(f"{BASE_URL}/create-checkout-session", json={"quantity": "lots"},
                             allow_redirects=False, timeout=TIMEOUT)
        evidence(endpoint="/create-checkout-session", method="POST", request_body='{"quantity": "lots"}',
                 expected_status=502, actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 502
        assert list(resp.json().keys()) == ["error"]
