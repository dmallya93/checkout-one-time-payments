"""Webhook tests: signed (raw-body HMAC) and unsigned JSON modes."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock

import pytest
from conftest import TEST_WEBHOOK_SECRET, ConfigFactory, sign_payload
from flask.testing import FlaskClient
from werkzeug.test import TestResponse

import server

PAYMENT_LOG = "🔔  Payment received!"
FAILURE_LOG = "⚠️  Webhook signature verification failed."
TEXT_TYPE = "text/plain; charset=utf-8"


def event_payload(event_type: str = "checkout.session.completed") -> bytes:
    # Deliberately unusual spacing, key order and non-ASCII text: re-serializing the
    # parsed JSON would change these bytes and break the signature.
    return (
        '{\n  "type": "' + event_type + '",  "id": "evt_test_1", "object": "event",\n'
        '  "data": {"object": {"id": "cs_test_1", "object": "checkout.session",'
        ' "customer_details": {"name": "Zoë Müller"}}}}'
    ).encode()


def post_signed(client: FlaskClient, payload: bytes, signature: str | None) -> TestResponse:
    headers = {"Stripe-Signature": signature} if signature is not None else {}
    return client.post("/webhook", data=payload, headers=headers, content_type="application/json")


@pytest.fixture
def signed_env(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    return TEST_WEBHOOK_SECRET


# --- Signed mode --------------------------------------------------------------------


def test_valid_signature_returns_ok_and_logs_payment(
    client: FlaskClient, signed_env: str, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = event_payload()

    response = post_signed(client, payload, sign_payload(payload, signed_env))

    assert response.status_code == 200
    assert response.content_type == TEXT_TYPE
    assert response.data == b"OK"
    assert PAYMENT_LOG in capsys.readouterr().out


def test_valid_signature_verified_against_raw_bytes(
    client: FlaskClient, stripe_mock: MagicMock, signed_env: str
) -> None:
    payload = event_payload()

    post_signed(client, payload, sign_payload(payload, signed_env))

    args = stripe_mock.construct_event.call_args.args
    assert args[0] == payload
    assert args[2] == signed_env


def test_valid_signature_other_event_does_not_log(
    client: FlaskClient, signed_env: str, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = event_payload("payment_intent.created")

    response = post_signed(client, payload, sign_payload(payload, signed_env))

    assert response.status_code == 200
    assert response.data == b"OK"
    assert PAYMENT_LOG not in capsys.readouterr().out


def test_tampered_body_returns_400(
    client: FlaskClient, signed_env: str, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = event_payload()
    signature = sign_payload(payload, signed_env)
    tampered = payload.replace(b"cs_test_1", b"cs_test_2", 1)
    assert len(tampered) == len(payload) and tampered != payload

    response = post_signed(client, tampered, signature)

    assert response.status_code == 400
    assert response.content_type == TEXT_TYPE
    assert response.data == b"Bad Request"
    out = capsys.readouterr().out
    assert FAILURE_LOG in out
    assert PAYMENT_LOG not in out


def test_tampered_signature_returns_400(
    client: FlaskClient, signed_env: str, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = event_payload()
    signature = sign_payload(payload, signed_env)
    last = signature[-1]
    tampered = signature[:-1] + ("0" if last != "0" else "1")

    response = post_signed(client, payload, tampered)

    assert response.status_code == 400
    assert response.data == b"Bad Request"
    assert FAILURE_LOG in capsys.readouterr().out


@pytest.mark.parametrize(
    "signature",
    [None, "", "garbage", "t=123", "v1=abc"],
)
def test_missing_or_malformed_signature_returns_400(
    client: FlaskClient, signed_env: str, signature: str | None
) -> None:
    response = post_signed(client, event_payload(), signature)

    assert response.status_code == 400
    assert response.data == b"Bad Request"


def test_signature_with_wrong_secret_returns_400(client: FlaskClient, signed_env: str) -> None:
    payload = event_payload()

    response = post_signed(client, payload, sign_payload(payload, "whsec_wrong"))

    assert response.status_code == 400


def test_expired_timestamp_returns_400(client: FlaskClient, signed_env: str) -> None:
    payload = event_payload()
    old = int(time.time()) - 3600

    response = post_signed(client, payload, sign_payload(payload, signed_env, old))

    assert response.status_code == 400


def test_signed_invalid_json_returns_400(client: FlaskClient, signed_env: str) -> None:
    payload = b"not json"

    response = post_signed(client, payload, sign_payload(payload, signed_env))

    assert response.status_code == 400
    assert response.data == b"Bad Request"


def test_secret_read_at_request_time(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    unsigned = json.dumps({"type": "checkout.session.completed"})
    assert client.post("/webhook", data=unsigned, content_type="application/json").data == b"OK"

    # Setting the variable after the app was created switches to signed mode.
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)

    response = client.post("/webhook", data=unsigned, content_type="application/json")
    assert response.status_code == 400


def test_config_secret_used_when_env_unset(
    make_config: ConfigFactory, stripe_mock: MagicMock
) -> None:
    app = server.create_app(make_config(webhook_secret=TEST_WEBHOOK_SECRET), stripe_mock)
    client = app.test_client()
    payload = event_payload()

    assert post_signed(client, payload, None).status_code == 400
    assert post_signed(client, payload, sign_payload(payload, TEST_WEBHOOK_SECRET)).data == b"OK"


def test_empty_env_secret_means_unsigned_mode(
    make_config: ConfigFactory, stripe_mock: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "")
    app = server.create_app(make_config(webhook_secret=TEST_WEBHOOK_SECRET), stripe_mock)

    response = app.test_client().post("/webhook", json={"type": "x"})

    assert response.status_code == 200


# --- Unsigned JSON mode -------------------------------------------------------------


def test_unsigned_completed_event_logs_payment(
    client: FlaskClient, stripe_mock: MagicMock, capsys: pytest.CaptureFixture[str]
) -> None:
    response = client.post(
        "/webhook", json={"type": "checkout.session.completed", "data": {"object": {}}}
    )

    assert response.status_code == 200
    assert response.content_type == TEXT_TYPE
    assert response.data == b"OK"
    assert PAYMENT_LOG in capsys.readouterr().out
    stripe_mock.construct_event.assert_not_called()


def test_unsigned_other_event_does_not_log(
    client: FlaskClient, capsys: pytest.CaptureFixture[str]
) -> None:
    response = client.post("/webhook", json={"type": "charge.succeeded", "data": {}})

    assert response.status_code == 200
    assert PAYMENT_LOG not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("data", "content_type"),
    [
        (b"", None),
        (b"", "application/json"),
        (b"not json at all", "application/json"),
        (b"{broken", "application/json"),
        (b"[1, 2, 3]", "application/json"),
        (b'"just a string"', "application/json"),
        (b"type=checkout.session.completed", "application/x-www-form-urlencoded"),
        (b'{"type": "checkout.session.completed"}', "text/plain"),
    ],
)
def test_unsigned_tolerates_empty_or_non_json_body(
    client: FlaskClient,
    capsys: pytest.CaptureFixture[str],
    data: bytes,
    content_type: str | None,
) -> None:
    response = client.post("/webhook", data=data, content_type=content_type)

    assert response.status_code == 200
    assert response.data == b"OK"
    # Like Express, only application/json bodies are parsed for the event type.
    assert PAYMENT_LOG not in capsys.readouterr().out
