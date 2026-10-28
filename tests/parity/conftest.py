"""Fixtures for the HTTP parity suite.

The suite talks to an already running server over HTTP, so the same tests run
against the reference Express server and the Flask server. ``run_parity.sh``
starts everything; to run by hand, export:

- ``BASE_URL``: server started *without* ``STRIPE_WEBHOOK_SECRET`` (required).
- ``SIGNED_BASE_URL``: the same server started with ``STRIPE_WEBHOOK_SECRET``.
- ``PARITY_WEBHOOK_SECRET``: the secret that server was started with.
- ``STRIPE_MOCK_URL``: the stripe-mock both servers use (default
  ``http://localhost:12111``).

Signed-webhook tests are skipped when ``SIGNED_BASE_URL`` is unset, unless
``PARITY_REQUIRE_SIGNED=1`` (set by the runner) turns that into a failure.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = REPO_ROOT / "client" / "html"
DEFAULT_STRIPE_MOCK_URL = "http://localhost:12111"
# Any well-formed test key works with stripe-mock; this is not a real secret.
STRIPE_MOCK_KEY = "sk_test_paritysuite0000"
TIMEOUT = 10


def _env(name: str) -> str | None:
    return os.environ.get(name, "").strip() or None


def _strip(url: str) -> str:
    return url.rstrip("/")


@pytest.fixture(scope="session")
def base_url() -> str:
    url = _env("BASE_URL")
    if url is None:
        pytest.fail("BASE_URL is not set; point it at a running server (see README.md).")
    return _strip(url)


@pytest.fixture(scope="session")
def signed_base_url() -> str:
    url = _env("SIGNED_BASE_URL")
    if url is None:
        message = "SIGNED_BASE_URL is not set (server started with STRIPE_WEBHOOK_SECRET)."
        if _env("PARITY_REQUIRE_SIGNED") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return _strip(url)


@pytest.fixture(scope="session")
def webhook_secret(signed_base_url: str) -> str:
    secret = _env("PARITY_WEBHOOK_SECRET")
    if secret is None:
        pytest.fail("PARITY_WEBHOOK_SECRET must be set together with SIGNED_BASE_URL.")
    return secret


@pytest.fixture(scope="session")
def stripe_mock_url() -> str:
    return _strip(_env("STRIPE_MOCK_URL") or DEFAULT_STRIPE_MOCK_URL)


class NoRedirectSession(requests.Session):
    """A session that never follows redirects, so 3xx responses can be inspected."""

    def request(self, method: Any, url: Any, *args: Any, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("allow_redirects", False)
        kwargs.setdefault("timeout", TIMEOUT)
        return super().request(method, url, *args, **kwargs)


@pytest.fixture(scope="session")
def http() -> Iterator[requests.Session]:
    with NoRedirectSession() as session:
        yield session


@pytest.fixture(scope="session")
def stripe_mock(stripe_mock_url: str) -> StripeMock:
    return StripeMock(stripe_mock_url)


class StripeMock:
    """Direct calls to stripe-mock, used as the source of truth for expected data."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.headers = {"Authorization": f"Bearer {STRIPE_MOCK_KEY}"}

    def get(self, path: str) -> Any:
        response = requests.get(f"{self.url}{path}", headers=self.headers, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, data: dict[str, Any]) -> Any:
        response = requests.post(
            f"{self.url}{path}", data=data, headers=self.headers, timeout=TIMEOUT
        )
        response.raise_for_status()
        return response.json()
