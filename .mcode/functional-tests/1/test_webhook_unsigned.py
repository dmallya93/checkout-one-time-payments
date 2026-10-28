"""POST /webhook without STRIPE_WEBHOOK_SECRET: event type read only from JSON / urlencoded bodies."""

import json
import time

import pytest
import requests

from conftest import ServerProc, free_port, record, server_env

COMPLETED = "🔔  Payment received!"
EVENT = json.dumps({"id": "evt_ftrun_unsigned", "object": "event", "type": "checkout.session.completed"})


@pytest.fixture(scope="module")
def unsigned_server(tmp_path_factory):
    port = free_port()
    sp = ServerProc(server_env(PORT=str(port), STRIPE_WEBHOOK_SECRET=None), port,
                    tmp_path_factory.mktemp("whu") / "server.log")
    assert sp.wait_ready(), sp.output()
    yield sp
    sp.stop()


def _post(srv, data, ctype):
    headers = {"Content-Type": ctype} if ctype else {}
    before = srv.output().count(COMPLETED)
    r = requests.post(f"{srv.base}/webhook", data=data, headers=headers, timeout=15)
    time.sleep(0.5)
    return r, srv.output().count(COMPLETED) > before


def _assert_ok(r):
    assert r.status_code == 200
    assert r.text == "OK"
    assert r.headers["Content-Type"].startswith("text/plain")


class TestWebhookUnsigned:
    def test_webhook_unsigned_json_completed_logs(self, unsigned_server):
        r, logged = _post(unsigned_server, EVENT.encode(), "application/json")
        record("test_webhook_unsigned_json_completed_logs", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text, logged=logged)
        _assert_ok(r)
        assert logged, unsigned_server.output()

    def test_webhook_unsigned_form_completed_logs(self, unsigned_server):
        r, logged = _post(unsigned_server, b"type=checkout.session.completed",
                          "application/x-www-form-urlencoded")
        record("test_webhook_unsigned_form_completed_logs", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text, logged=logged)
        _assert_ok(r)
        assert logged, unsigned_server.output()

    def test_webhook_unsigned_text_plain_no_log(self, unsigned_server):
        r, logged = _post(unsigned_server, EVENT.encode(), "text/plain")
        record("test_webhook_unsigned_text_plain_no_log", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text, logged=logged)
        _assert_ok(r)
        assert not logged, unsigned_server.output()

    def test_webhook_unsigned_no_content_type_no_log(self, unsigned_server):
        # requests sends no Content-Type for raw bytes when none is given.
        r, logged = _post(unsigned_server, EVENT.encode(), None)
        record("test_webhook_unsigned_no_content_type_no_log", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text, logged=logged)
        _assert_ok(r)
        assert not logged, unsigned_server.output()

    def test_webhook_unsigned_malformed_json_ok(self, unsigned_server):
        r, logged = _post(unsigned_server, b'{"type": "checkout.session.completed"', "application/json")
        record("test_webhook_unsigned_malformed_json_ok", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text, logged=logged)
        _assert_ok(r)
        assert not logged
        assert unsigned_server.proc.poll() is None

    def test_webhook_unsigned_json_other_event_no_log(self, unsigned_server):
        body = json.dumps({"type": "payment_intent.created"}).encode()
        r, logged = _post(unsigned_server, body, "application/json")
        record("test_webhook_unsigned_json_other_event_no_log", status=r.status_code,
               ctype=r.headers.get("Content-Type"), body=r.text, logged=logged)
        _assert_ok(r)
        assert not logged
