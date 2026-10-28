"""POST /webhook: unsigned JSON mode and HMAC-signed mode (raw-body verification)."""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest
import requests
from conftest import BASE_URL, TIMEOUT, wait_for_log

SECRET = "whsec_ftrun_secret_0123456789"
TEXT_CT = "text/plain; charset=utf-8"
PAID = "🔔  Payment received!"
SIG_FAIL = "⚠️  Webhook signature verification failed."
# Deliberately irregular whitespace/key order: re-serializing it would change the bytes.
RAW_EVENT = (
    b'{ "id":"evt_ftrun_1",  "object":"event", "type" : "checkout.session.completed",'
    b'\n "data": {"object": {"id": "cs_test_ftrun", "object":"checkout.session"}} }'
)


def sign(payload: bytes, secret: str = SECRET, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


@pytest.fixture
def unsigned(spawn_server):
    return spawn_server({"STRIPE_WEBHOOK_SECRET": None})


@pytest.fixture
def signed(spawn_server):
    return spawn_server({"STRIPE_WEBHOOK_SECRET": SECRET})


class TestWebhookUnsigned:
    def test_lifecycle_app_unsigned_event_ok(self, evidence):
        resp = requests.post(f"{BASE_URL}/webhook",
                             json={"type": "payment_intent.created", "data": {"object": {}}},
                             timeout=TIMEOUT)
        evidence(endpoint="/webhook", method="POST",
                 request_body='{"type": "payment_intent.created", "data": {"object": {}}}',
                 expected_status=200, actual_status=resp.status_code,
                 output=f"Content-Type: {resp.headers.get('Content-Type')}\n{resp.text}")
        assert resp.status_code == 200
        assert resp.text == "OK"
        assert resp.headers["Content-Type"] == TEXT_CT

    def test_completed_event_logs_payment_received(self, evidence, unsigned):
        resp = requests.post(f"{unsigned.url}/webhook", data=RAW_EVENT,
                             headers={"Content-Type": "application/json"}, timeout=TIMEOUT)
        logged = wait_for_log(unsigned, PAID)
        evidence(endpoint="/webhook", method="POST", request_body=RAW_EVENT.decode(),
                 expected_status=200, actual_status=resp.status_code,
                 output=f"body={resp.text}; server stdout={unsigned.stdout!r}")
        assert resp.status_code == 200
        assert resp.text == "OK"
        assert logged

    def test_other_event_does_not_log(self, evidence, unsigned):
        resp = requests.post(f"{unsigned.url}/webhook",
                             json={"type": "charge.succeeded", "data": {"object": {}}}, timeout=TIMEOUT)
        time.sleep(0.5)
        evidence(endpoint="/webhook", method="POST",
                 request_body='{"type": "charge.succeeded", "data": {"object": {}}}',
                 expected_status=200, actual_status=resp.status_code,
                 output=f"body={resp.text}; server stdout={unsigned.stdout!r}")
        assert resp.status_code == 200
        assert resp.text == "OK"
        assert PAID not in unsigned.stdout

    def test_empty_body_tolerated(self, evidence, unsigned):
        resp = requests.post(f"{unsigned.url}/webhook", timeout=TIMEOUT)
        evidence(endpoint="/webhook", method="POST", expected_status=200,
                 actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 200
        assert resp.text == "OK"

    def test_non_json_body_tolerated(self, evidence, unsigned):
        resp = requests.post(f"{unsigned.url}/webhook", data="not json {{",
                             headers={"Content-Type": "application/json"}, timeout=TIMEOUT)
        evidence(endpoint="/webhook", method="POST", request_body="not json {{",
                 expected_status=200, actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 200
        assert resp.text == "OK"


class TestWebhookSigned:
    def test_valid_signature_over_raw_bytes(self, evidence, signed):
        resp = requests.post(f"{signed.url}/webhook", data=RAW_EVENT,
                             headers={"Content-Type": "application/json",
                                      "Stripe-Signature": sign(RAW_EVENT)}, timeout=TIMEOUT)
        logged = wait_for_log(signed, PAID)
        evidence(endpoint="/webhook", method="POST", request_body=RAW_EVENT.decode(),
                 expected_status=200, actual_status=resp.status_code,
                 output=f"body={resp.text}; server stdout={signed.stdout!r}")
        assert resp.status_code == 200
        assert resp.text == "OK"
        assert resp.headers["Content-Type"] == TEXT_CT
        assert logged
        assert SIG_FAIL not in signed.stdout

    def test_tampered_byte_rejected(self, evidence, signed):
        sig = sign(RAW_EVENT)
        tampered = RAW_EVENT.replace(b"cs_test_ftrun", b"cs_test_ftruN")
        resp = requests.post(f"{signed.url}/webhook", data=tampered,
                             headers={"Content-Type": "application/json", "Stripe-Signature": sig},
                             timeout=TIMEOUT)
        logged = wait_for_log(signed, SIG_FAIL)
        evidence(endpoint="/webhook", method="POST", request_body=tampered.decode(),
                 expected_status=400, actual_status=resp.status_code,
                 output=f"body={resp.text}; server stdout={signed.stdout!r}")
        assert resp.status_code == 400
        assert resp.text == "Bad Request"
        assert resp.headers["Content-Type"] == TEXT_CT
        assert logged
        assert PAID not in signed.stdout

    def test_missing_signature_header_rejected(self, evidence, signed):
        resp = requests.post(f"{signed.url}/webhook", data=RAW_EVENT,
                             headers={"Content-Type": "application/json"}, timeout=TIMEOUT)
        logged = wait_for_log(signed, SIG_FAIL)
        evidence(endpoint="/webhook", method="POST", request_body=RAW_EVENT.decode(),
                 expected_status=400, actual_status=resp.status_code,
                 output=f"body={resp.text}; server stdout={signed.stdout!r}")
        assert resp.status_code == 400
        assert resp.text == "Bad Request"
        assert logged

    def test_wrong_secret_rejected(self, evidence, signed):
        resp = requests.post(f"{signed.url}/webhook", data=RAW_EVENT,
                             headers={"Content-Type": "application/json",
                                      "Stripe-Signature": sign(RAW_EVENT, "whsec_wrong")},
                             timeout=TIMEOUT)
        evidence(endpoint="/webhook", method="POST", request_body=RAW_EVENT.decode(),
                 expected_status=400, actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 400
        assert resp.text == "Bad Request"

    def test_stale_timestamp_rejected(self, evidence, signed):
        resp = requests.post(f"{signed.url}/webhook", data=RAW_EVENT,
                             headers={"Content-Type": "application/json",
                                      "Stripe-Signature": sign(RAW_EVENT, ts=int(time.time()) - 3600)},
                             timeout=TIMEOUT)
        evidence(endpoint="/webhook", method="POST", request_body=RAW_EVENT.decode(),
                 expected_status=400, actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 400
        assert resp.text == "Bad Request"

    def test_reserialized_json_signature_mismatch(self, evidence, signed):
        """Signature computed over compact JSON must not validate the irregular raw body."""
        import json
        compact = json.dumps(json.loads(RAW_EVENT), separators=(",", ":")).encode()
        resp = requests.post(f"{signed.url}/webhook", data=RAW_EVENT,
                             headers={"Content-Type": "application/json",
                                      "Stripe-Signature": sign(compact)}, timeout=TIMEOUT)
        evidence(endpoint="/webhook", method="POST", request_body=RAW_EVENT.decode(),
                 expected_status=400, actual_status=resp.status_code, output=resp.text)
        assert resp.status_code == 400
        assert resp.text == "Bad Request"
