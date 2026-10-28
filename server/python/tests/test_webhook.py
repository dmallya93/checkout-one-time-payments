"""Offline tests for POST /webhook with real Stripe-Signature headers."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest

import server
from tests.conftest import WEBHOOK_SECRET

PAYMENT_RECEIVED = "\U0001f514  Payment received!\n"
SIGNATURE_FAILED = "⚠️  Webhook signature verification failed.\n"


def make_event(event_type: str = "checkout.session.completed") -> str:
    return json.dumps(
        {
            "id": "evt_test_1",
            "object": "event",
            "api_version": "2025-01-27.acacia",
            "created": 1700000000,
            "livemode": False,
            "type": event_type,
            "data": {"object": {"id": "cs_test_a1b2c3", "object": "checkout.session"}},
        }
    )


def sign(payload: str, secret: str = WEBHOOK_SECRET, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header the same way Stripe does (HMAC-SHA256 of "t.payload")."""
    t = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{t}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={t},v1={digest}"


@pytest.fixture
def signed_client(make_config, stripe_client):
    app = server.create_app(make_config(stripe_webhook_secret=WEBHOOK_SECRET), stripe_client)
    return app.test_client()


def post_webhook(test_client, payload, signature: str | None = None):
    headers = {} if signature is None else {"Stripe-Signature": signature}
    return test_client.post(
        "/webhook", data=payload, headers=headers, content_type="application/json"
    )


def assert_ok(resp):
    assert resp.status_code == 200
    assert resp.mimetype == "text/plain"
    assert resp.get_data(as_text=True) == "OK"


def assert_bad_request(resp):
    assert resp.status_code == 400
    assert resp.mimetype == "text/plain"
    assert resp.get_data(as_text=True) == "Bad Request"


# --- signed mode --------------------------------------------------------------------------


def test_signed_completed_event_logs_payment(signed_client, capsys):
    payload = make_event()

    resp = post_webhook(signed_client, payload, sign(payload))

    assert_ok(resp)
    assert capsys.readouterr().out == PAYMENT_RECEIVED


def test_signed_event_actually_runs_construct_event(signed_client, mocker):
    spy = mocker.spy(server.stripe.Webhook, "construct_event")
    payload = make_event()
    signature = sign(payload)

    assert_ok(post_webhook(signed_client, payload, signature))

    spy.assert_called_once_with(payload.encode("utf-8"), signature, WEBHOOK_SECRET)
    assert spy.spy_return.type == "checkout.session.completed"


@pytest.mark.parametrize(
    "event_type",
    [
        "payment_intent.succeeded",
        "checkout.session.expired",
        "checkout.session.async_payment_failed",
    ],
)
def test_signed_other_event_type_does_not_log(signed_client, capsys, event_type):
    payload = make_event(event_type)

    resp = post_webhook(signed_client, payload, sign(payload))

    assert_ok(resp)
    assert capsys.readouterr().out == ""


def test_signed_with_multiple_signatures_accepts_any_valid_one(signed_client, capsys):
    payload = make_event()
    t = int(time.time())
    valid = sign(payload, timestamp=t).split(",")[1]
    header = f"t={t},v1={'0' * 64},{valid}"

    assert_ok(post_webhook(signed_client, payload, header))
    assert capsys.readouterr().out == PAYMENT_RECEIVED


def test_bad_signature_returns_400_and_logs(signed_client, capsys):
    payload = make_event()

    resp = post_webhook(signed_client, payload, sign(payload, secret="whsec_wrong_secret"))

    assert_bad_request(resp)
    assert capsys.readouterr().out == SIGNATURE_FAILED


def test_missing_signature_header_returns_400(signed_client, capsys):
    resp = post_webhook(signed_client, make_event())

    assert_bad_request(resp)
    assert capsys.readouterr().out == SIGNATURE_FAILED


@pytest.mark.parametrize("header", ["", "garbage", "t=notanumber,v1=abc", "t=123", "v1=abc"])
def test_malformed_signature_header_returns_400(signed_client, capsys, header):
    resp = post_webhook(signed_client, make_event(), header)

    assert_bad_request(resp)
    assert capsys.readouterr().out == SIGNATURE_FAILED


def test_tampered_payload_returns_400(signed_client, capsys):
    original = make_event("payment_intent.succeeded")
    tampered = make_event("checkout.session.completed")

    resp = post_webhook(signed_client, tampered, sign(original))

    assert_bad_request(resp)
    assert capsys.readouterr().out == SIGNATURE_FAILED


def test_signature_outside_tolerance_returns_400(signed_client, capsys):
    payload = make_event()
    stale = int(time.time()) - 10 * 60  # stripe-python's default tolerance is 300 seconds

    resp = post_webhook(signed_client, payload, sign(payload, timestamp=stale))

    assert_bad_request(resp)
    assert capsys.readouterr().out == SIGNATURE_FAILED


def test_signed_but_invalid_json_returns_400(signed_client, capsys):
    payload = "{not json"

    resp = post_webhook(signed_client, payload, sign(payload))

    assert_bad_request(resp)
    assert capsys.readouterr().out == SIGNATURE_FAILED


def test_signed_mode_does_not_touch_stripe_api(signed_client, stripe_client):
    payload = make_event()

    post_webhook(signed_client, payload, sign(payload))

    stripe_client.v1.balance.retrieve.assert_not_called()
    stripe_client.v1.checkout.sessions.retrieve.assert_not_called()
    stripe_client.v1.checkout.sessions.create.assert_not_called()


# --- unsigned mode (no STRIPE_WEBHOOK_SECRET) ---------------------------------------------


def test_unsigned_completed_event_logs_payment(client, capsys):
    resp = post_webhook(client, make_event())

    assert_ok(resp)
    assert capsys.readouterr().out == PAYMENT_RECEIVED


def test_unsigned_ignores_signature_header(client, capsys):
    resp = post_webhook(client, make_event(), "t=1,v1=bogus")

    assert_ok(resp)
    assert capsys.readouterr().out == PAYMENT_RECEIVED


def test_unsigned_other_event_type_does_not_log(client, capsys):
    resp = post_webhook(client, make_event("payment_intent.succeeded"))

    assert_ok(resp)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "payload",
    ["", "{not json", "[1, 2, 3]", '"checkout.session.completed"', "42", "null", "{}"],
    ids=["empty", "invalid-json", "list", "string", "number", "null", "empty-object"],
)
def test_unsigned_without_usable_event_returns_ok(client, capsys, payload):
    resp = post_webhook(client, payload)

    assert_ok(resp)
    assert capsys.readouterr().out == ""


def test_unsigned_without_body_or_content_type_returns_ok(client, capsys):
    resp = client.post("/webhook")

    assert_ok(resp)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("content_type", ["text/plain", None])
def test_unsigned_ignores_json_without_json_content_type(client, capsys, content_type):
    kwargs = {} if content_type is None else {"content_type": content_type}
    resp = client.post("/webhook", data=make_event(), **kwargs)

    assert_ok(resp)
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    ("form", "expected"),
    [({"type": "checkout.session.completed"}, PAYMENT_RECEIVED), ({"type": "other"}, "")],
    ids=["completed", "other"],
)
def test_unsigned_reads_urlencoded_event_type(client, capsys, form, expected):
    resp = client.post("/webhook", data=form)

    assert_ok(resp)
    assert capsys.readouterr().out == expected


def test_empty_webhook_secret_config_means_unsigned(make_config, stripe_client, capsys):
    # load_config() maps an empty STRIPE_WEBHOOK_SECRET to None; an empty string behaves
    # the same way here (falsy), like the truthiness check in the Node server.
    app = server.create_app(make_config(stripe_webhook_secret=""), stripe_client)

    assert_ok(post_webhook(app.test_client(), make_event()))
    assert capsys.readouterr().out == PAYMENT_RECEIVED
