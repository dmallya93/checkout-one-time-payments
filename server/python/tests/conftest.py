"""Shared fixtures: a test Config, a mocked StripeClient and a Flask test client."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import stripe
from flask import Flask
from flask.testing import FlaskClient

import server

SERVER_DIR = Path(__file__).resolve().parent.parent

# Dummy credentials: shaped like real ones, never valid against Stripe.
TEST_SECRET_KEY = "sk_test_" + "A1b2C3d4E5f6G7h8I9j0K1l2"
TEST_WEBHOOK_SECRET = "whsec_" + "testsecret0123456789abcdef"
STATIC_DIR = (SERVER_DIR / "../../client/html").resolve()

ConfigFactory = Callable[..., server.Config]


@pytest.fixture(autouse=True)
def isolated_environ() -> Iterator[None]:
    """Restore os.environ after each test (load_dotenv writes into it)."""
    saved = dict(os.environ)
    for name in (
        "STRIPE_SECRET_KEY",
        "STRIPE_WEBHOOK_SECRET",
        "PORT",
        "DOMAIN",
        "STATIC_DIR",
        "STRIPE_API_BASE",
    ):
        os.environ.pop(name, None)
    yield
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def make_config() -> ConfigFactory:
    def factory(**overrides: object) -> server.Config:
        values: dict[str, object] = {
            "port": 4242,
            "domain": "http://localhost:4242",
            "static_dir": str(STATIC_DIR),
            "secret_key": TEST_SECRET_KEY,
            "webhook_secret": None,
            "api_base": None,
        }
        values.update(overrides)
        return server.Config(**values)  # type: ignore[arg-type]

    return factory


@pytest.fixture
def config(make_config: ConfigFactory) -> server.Config:
    return make_config()


@pytest.fixture
def stripe_mock() -> MagicMock:
    """A StripeClient stand-in.

    API calls are mocks; ``construct_event`` delegates to a real StripeClient so
    webhook signatures are verified exactly as in production (no network involved).
    """
    real_client = stripe.StripeClient(TEST_SECRET_KEY)
    mock = MagicMock(name="StripeClient")
    mock.v1.balance.retrieve.return_value = stripe.StripeObject.construct_from(
        {"object": "balance", "livemode": False, "available": [], "pending": []},
        TEST_SECRET_KEY,
    )
    mock.v1.checkout.sessions.create.return_value = stripe.StripeObject.construct_from(
        {
            "id": "cs_test_123",
            "object": "checkout.session",
            "url": "https://checkout.stripe.com/c/pay/cs_test_123",
        },
        TEST_SECRET_KEY,
    )
    mock.construct_event.side_effect = real_client.construct_event
    return mock


@pytest.fixture
def app(config: server.Config, stripe_mock: MagicMock) -> Flask:
    flask_app = server.create_app(config, stripe_mock)
    flask_app.testing = True
    return flask_app


@pytest.fixture
def client(app: Flask) -> FlaskClient:
    return app.test_client()


def sign_payload(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build a ``Stripe-Signature`` header: ``t=<ts>,v1=<HMAC-SHA256 of "<ts>.<payload>">``."""
    ts = int(time.time()) if timestamp is None else timestamp
    signed = f"{ts}.".encode() + payload
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"
