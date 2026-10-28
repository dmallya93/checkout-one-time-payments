"""POST /webhook signature verification (spec §5)."""

import hashlib
import hmac
import json
import time

import pytest
import requests

from conftest import STRIPE_WEBHOOK_SECRET, record

COMPLETED = "🔔  Payment received!"
FAILED = "⚠️  Webhook signature verification failed."


def _event(etype="checkout.session.completed"):
    return json.dumps(
        {
            "id": "evt_ftrun_1",
            "object": "event",
            "api_version": "2024-06-20",
            "created": int(time.time()),
            "type": etype,
            "data": {"object": {"id": "cs_test_ftrun", "object": "checkout.session"}},
        }
    )


def _sign(payload: str, secret: str, ts: int | None = None) -> str:
    ts = ts or int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


@pytest.fixture(scope="module")
def wh_server(tmp_path_factory):
    assert STRIPE_WEBHOOK_SECRET, "STRIPE_WEBHOOK_SECRET must be configured"
    from conftest import ServerProc, free_port, server_env

    port = free_port()
    sp = ServerProc(server_env(PORT=str(port)), port, tmp_path_factory.mktemp("wh") / "server.log")
    assert sp.wait_ready(), sp.output()
    yield sp
    sp.stop()


def _wait_log(srv, needle, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if needle in srv.output():
            return True
        time.sleep(0.1)
    return needle in srv.output()


class TestWebhook:
    def test_webhook_valid_signature_completed(self, wh_server):
        payload = _event()
        before = wh_server.output().count(COMPLETED)
        r = requests.post(
            f"{wh_server.base}/webhook",
            data=payload.encode(),
            headers={"Content-Type": "application/json", "Stripe-Signature": _sign(payload, STRIPE_WEBHOOK_SECRET)},
            timeout=15,
        )
        time.sleep(0.5)
        logged = wh_server.output().count(COMPLETED) > before
        record("test_webhook_valid_signature_completed", status=r.status_code, ctype=r.headers.get("Content-Type"),
               body=r.text, log=wh_server.output()[-400:])
        assert r.status_code == 200
        assert r.text == "OK"
        assert r.headers["Content-Type"].startswith("text/plain")
        assert logged, wh_server.output()

    def test_webhook_valid_signature_other_event(self, wh_server):
        payload = _event("payment_intent.created")
        before = wh_server.output().count(COMPLETED)
        r = requests.post(
            f"{wh_server.base}/webhook",
            data=payload.encode(),
            headers={"Content-Type": "application/json", "Stripe-Signature": _sign(payload, STRIPE_WEBHOOK_SECRET)},
            timeout=15,
        )
        time.sleep(0.5)
        record("test_webhook_valid_signature_other_event", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text)
        assert r.status_code == 200 and r.text == "OK"
        assert wh_server.output().count(COMPLETED) == before

    def test_webhook_bad_signature(self, wh_server):
        payload = _event()
        before = wh_server.output().count(FAILED)
        r = requests.post(
            f"{wh_server.base}/webhook",
            data=payload.encode(),
            headers={"Content-Type": "application/json", "Stripe-Signature": _sign(payload, "wrong_secret_ftrun")},
            timeout=15,
        )
        time.sleep(0.5)
        logged = wh_server.output().count(FAILED) > before
        record("test_webhook_bad_signature", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text,
               log=wh_server.output()[-400:])
        assert r.status_code == 400
        assert r.text == "Bad Request"
        assert logged, wh_server.output()

    def test_webhook_tampered_body(self, wh_server):
        payload = _event()
        sig = _sign(payload, STRIPE_WEBHOOK_SECRET)
        tampered = payload.replace("evt_ftrun_1", "evt_ftrun_X")
        r = requests.post(f"{wh_server.base}/webhook", data=tampered.encode(),
                          headers={"Content-Type": "application/json", "Stripe-Signature": sig}, timeout=15)
        record("test_webhook_tampered_body", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text)
        assert r.status_code == 400 and r.text == "Bad Request"

    def test_webhook_missing_signature(self, wh_server):
        payload = _event()
        before = wh_server.output().count(FAILED)
        r = requests.post(f"{wh_server.base}/webhook", data=payload.encode(),
                          headers={"Content-Type": "application/json"}, timeout=15)
        time.sleep(0.5)
        logged = wh_server.output().count(FAILED) > before
        record("test_webhook_missing_signature", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text,
               log=wh_server.output()[-400:])
        assert r.status_code == 400 and r.text == "Bad Request"
        assert logged

    def test_webhook_main_server_valid_signature(self, main_server):
        """The run-app managed server also verifies with the configured secret."""
        payload = _event()
        r = requests.post(f"{main_server}/webhook", data=payload.encode(),
                          headers={"Content-Type": "application/json", "Stripe-Signature": _sign(payload, STRIPE_WEBHOOK_SECRET)},
                          timeout=15)
        record("test_webhook_main_server_valid_signature", status=r.status_code, ctype=r.headers.get("Content-Type"), body=r.text)
        assert r.status_code == 200 and r.text == "OK"
