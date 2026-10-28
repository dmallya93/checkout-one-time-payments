"""HTTP parity for ``POST /webhook``, unsigned and signed.

Unsigned tests use ``BASE_URL`` (no ``STRIPE_WEBHOOK_SECRET``); signed tests use
``SIGNED_BASE_URL``, a server started with ``PARITY_WEBHOOK_SECRET``.
"""

from __future__ import annotations

import json
import time

import pytest
import requests
from helpers import TIMEOUT, assert_content_type, stripe_signature

JSON_HEADERS = {"Content-Type": "application/json"}


def event(event_type: str = "checkout.session.completed") -> bytes:
    payload = {
        "id": "evt_test_parity",
        "object": "event",
        "api_version": "2024-06-20",
        "created": int(time.time()),
        "livemode": False,
        "type": event_type,
        "data": {"object": {"id": "cs_test_a1ParitySuite", "object": "checkout.session"}},
    }
    return json.dumps(payload).encode()


def assert_ok(response: requests.Response) -> None:
    assert response.status_code == 200
    assert_content_type(response, "text/plain", "utf-8")
    assert response.text == "OK"


def assert_bad_request(response: requests.Response) -> None:
    assert response.status_code == 400
    assert_content_type(response, "text/plain", "utf-8")
    assert response.text == "Bad Request"


# --- Unsigned (no STRIPE_WEBHOOK_SECRET) ---------------------------------------------


@pytest.mark.parametrize(
    ("data", "headers"),
    [
        pytest.param(event(), JSON_HEADERS, id="checkout.session.completed"),
        pytest.param(event("payment_intent.succeeded"), JSON_HEADERS, id="other-event"),
        pytest.param(b"", {}, id="empty-body"),
        pytest.param(b"", JSON_HEADERS, id="empty-json-body"),
        pytest.param(b"not json", {"Content-Type": "text/plain"}, id="text-body"),
        pytest.param(b"[1, 2]", JSON_HEADERS, id="json-array"),
        pytest.param(
            b"type=checkout.session.completed",
            {"Content-Type": "application/x-www-form-urlencoded"},
            id="form-body",
        ),
    ],
)
def test_unsigned_webhook_is_ok(
    http: requests.Session, base_url: str, data: bytes, headers: dict[str, str]
) -> None:
    response = http.post(f"{base_url}/webhook", data=data, headers=headers, timeout=TIMEOUT)

    assert_ok(response)


def test_unsigned_webhook_ignores_signature_header(http: requests.Session, base_url: str) -> None:
    headers = {**JSON_HEADERS, "Stripe-Signature": "t=1,v1=deadbeef"}

    response = http.post(f"{base_url}/webhook", data=event(), headers=headers, timeout=TIMEOUT)

    assert_ok(response)


@pytest.mark.parametrize("data", [b"{bad", b'"text"', b"42"], ids=["malformed", "string", "number"])
def test_unsigned_webhook_invalid_json_object_is_ok(
    http: requests.Session, base_url: str, data: bytes
) -> None:
    """Malformed JSON and top-level JSON primitives are treated as an empty body.

    Accepted difference from the former Express server, whose strict JSON parser
    answered 400 (see server/python/README.md).
    """
    response = http.post(f"{base_url}/webhook", data=data, headers=JSON_HEADERS, timeout=TIMEOUT)

    assert_ok(response)


# --- Signed (server started with STRIPE_WEBHOOK_SECRET) ------------------------------


@pytest.mark.signed
@pytest.mark.parametrize("event_type", ["checkout.session.completed", "payment_intent.succeeded"])
def test_signed_webhook_valid_signature_is_ok(
    http: requests.Session, signed_base_url: str, webhook_secret: str, event_type: str
) -> None:
    payload = event(event_type)
    headers = {**JSON_HEADERS, "Stripe-Signature": stripe_signature(payload, webhook_secret)}

    response = http.post(
        f"{signed_base_url}/webhook", data=payload, headers=headers, timeout=TIMEOUT
    )

    assert_ok(response)


@pytest.mark.signed
def test_signed_webhook_accepts_one_valid_signature_among_several(
    http: requests.Session, signed_base_url: str, webhook_secret: str
) -> None:
    payload = event()
    valid = stripe_signature(payload, webhook_secret)
    timestamp, v1 = valid.split(",")
    headers = {**JSON_HEADERS, "Stripe-Signature": f"{timestamp},v1={'0' * 64},{v1}"}

    response = http.post(
        f"{signed_base_url}/webhook", data=payload, headers=headers, timeout=TIMEOUT
    )

    assert_ok(response)


@pytest.mark.signed
@pytest.mark.parametrize(
    "case",
    ["wrong-secret", "tampered-body", "missing-header", "malformed-header", "expired"],
)
def test_signed_webhook_bad_signature_is_400(
    http: requests.Session, signed_base_url: str, webhook_secret: str, case: str
) -> None:
    payload = event()
    headers = dict(JSON_HEADERS)
    if case == "wrong-secret":
        headers["Stripe-Signature"] = stripe_signature(payload, webhook_secret + "_wrong")
    elif case == "tampered-body":
        headers["Stripe-Signature"] = stripe_signature(payload, webhook_secret)
        payload = payload.replace(b"checkout.session.completed", b"checkout.session.complete!")
    elif case == "malformed-header":
        headers["Stripe-Signature"] = "not-a-signature"
    elif case == "expired":
        # Stripe signature verification rejects timestamps older than 300 s by default.
        old = int(time.time()) - 3600
        headers["Stripe-Signature"] = stripe_signature(payload, webhook_secret, timestamp=old)

    response = http.post(
        f"{signed_base_url}/webhook", data=payload, headers=headers, timeout=TIMEOUT
    )

    assert_bad_request(response)
