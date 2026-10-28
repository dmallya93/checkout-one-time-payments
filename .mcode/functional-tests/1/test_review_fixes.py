"""Behaviours added by the code review: 100 kB body limit (413) and signed non-object webhook JSON (400)."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest
import requests
from conftest import BASE_URL, TIMEOUT, wait_for_log

SECRET = "whsec_ftrun_secret_0123456789"
TEXT_CT = "text/plain; charset=utf-8"
SIG_FAIL = "⚠️  Webhook signature verification failed."
LIMIT = 100 * 1024


def sign(payload: bytes, secret: str = SECRET) -> str:
    ts = int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


@pytest.fixture
def signed(spawn_server):
    return spawn_server({"STRIPE_WEBHOOK_SECRET": SECRET})


class TestBodyLimit:
    @pytest.mark.parametrize("path", ["/webhook", "/create-checkout-session"])
    def test_oversized_body_413(self, evidence, path):
        body = b'{"pad":"' + b"a" * (LIMIT + 10) + b'"}'
        resp = requests.post(f"{BASE_URL}{path}", data=body,
                             headers={"Content-Type": "application/json"}, timeout=TIMEOUT)
        evidence(endpoint=path, method="POST",
                 request_body=f"<{len(body)}-byte JSON body>",
                 expected_status=413, actual_status=resp.status_code,
                 output=f"Content-Type: {resp.headers.get('Content-Type')}\n{resp.text}")
        assert resp.status_code == 413
        assert resp.headers["Content-Type"].startswith("text/html")
        assert "PayloadTooLargeError: request entity too large" in resp.text

    def test_body_under_limit_accepted(self, evidence):
        body = b'{"type":"charge.succeeded","pad":"' + b"a" * (LIMIT - 200) + b'"}'
        assert len(body) < LIMIT
        resp = requests.post(f"{BASE_URL}/webhook", data=body,
                             headers={"Content-Type": "application/json"}, timeout=TIMEOUT)
        evidence(endpoint="/webhook", method="POST",
                 request_body=f"<{len(body)}-byte JSON body>",
                 expected_status=200, actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 200
        assert resp.text == "OK"


class TestSignedNonObject:
    @pytest.mark.parametrize("payload", [b"42", b'"text"', b"null", b"[1]"],
                             ids=["number", "string", "null", "array"])
    def test_signed_non_object_json_400(self, evidence, signed, payload):
        resp = requests.post(f"{signed.url}/webhook", data=payload,
                             headers={"Content-Type": "application/json",
                                      "Stripe-Signature": sign(payload)}, timeout=TIMEOUT)
        logged = wait_for_log(signed, SIG_FAIL)
        evidence(endpoint="/webhook", method="POST", request_body=payload.decode(),
                 expected_status=400, actual_status=resp.status_code,
                 output=f"Content-Type: {resp.headers.get('Content-Type')}\n{resp.text}")
        assert resp.status_code == 400
        assert resp.text == "Bad Request"
        assert resp.headers["Content-Type"] == TEXT_CT
        assert logged
