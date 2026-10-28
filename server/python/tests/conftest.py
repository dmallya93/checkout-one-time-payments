"""Shared offline fixtures for the Flask server tests.

Nothing here talks to the network: the Stripe client is a pytest-mock MagicMock and
config-related fixtures keep the real environment and any real ``.env`` file out.
Nothing here is autouse, so the live tests in ``tests/live/`` are unaffected.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = SERVER_DIR.parent.parent
CLIENT_DIR = REPO_ROOT / "client" / "html"

# Make `import server` work regardless of the current working directory.
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import server  # noqa: E402

VALID_SECRET_KEY = "sk_test_" + "a1B2c3D4e5" * 3
WEBHOOK_SECRET = "whsec_test_secret"
SESSION_URL = "https://checkout.stripe.com/c/pay/cs_test_a1b2c3"

# Environment variables read by load_config()/main().
SERVER_ENV_VARS = (
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "PORT",
    "DOMAIN",
    "STATIC_DIR",
    "FLASK_DEBUG",
    "WERKZEUG_RUN_MAIN",
)


@pytest.fixture
def clean_env(monkeypatch):
    """Remove every server setting from os.environ for the duration of the test.

    Each variable is set and then deleted so monkeypatch records it either way; that also
    undoes anything load_dotenv() writes into os.environ during the test.
    """
    for name in SERVER_ENV_VARS:
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    return monkeypatch


@pytest.fixture
def no_dotenv(mocker):
    """Stop load_config() from reading a real server/python/.env file."""
    return mocker.patch("server.load_dotenv", return_value=False)


@pytest.fixture
def isolated_env(clean_env, no_dotenv):
    """Clean environment with .env loading disabled; returns monkeypatch for setenv()."""
    return clean_env


@pytest.fixture
def make_config():
    """Factory for server.Config with sensible offline defaults."""

    def _make(**overrides) -> server.Config:
        values = {
            "port": 4242,
            "domain": "http://localhost:4242",
            "static_dir": CLIENT_DIR,
            "stripe_secret_key": VALID_SECRET_KEY,
            "stripe_webhook_secret": None,
        }
        values.update(overrides)
        return server.Config(**values)

    return _make


@pytest.fixture
def make_session(mocker):
    """Factory for fake Checkout Session objects (url + to_dict(for_json=True))."""

    def _make(url: str = SESSION_URL, data: dict | None = None):
        session = mocker.MagicMock(name="Session")
        session.url = url
        session.to_dict.return_value = data if data is not None else {"id": "cs_test_a1b2c3"}
        return session

    return _make


@pytest.fixture
def stripe_client(mocker, make_session):
    """Mocked StripeClient exposing the v1 services the server uses."""
    client = mocker.MagicMock(name="StripeClient")
    client.v1.balance.retrieve.return_value = mocker.MagicMock(livemode=False)
    client.v1.checkout.sessions.retrieve.return_value = make_session()
    client.v1.checkout.sessions.create.return_value = make_session()
    return client


@pytest.fixture
def config(make_config):
    return make_config()


@pytest.fixture
def app(config, stripe_client):
    flask_app = server.create_app(config, stripe_client)
    flask_app.testing = True
    return flask_app


@pytest.fixture
def client(app):
    return app.test_client()
