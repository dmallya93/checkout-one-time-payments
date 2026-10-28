"""Fixtures for the Playwright E2E suite (pytest-playwright).

The tests drive the static client in ``client/html`` through the Python server,
which talks to stripe-mock instead of the real Stripe API.

By default the session-scoped ``app`` fixture starts everything itself:

1. stripe-mock: ``STRIPE_MOCK_URL`` if set; otherwise the ``stripe-mock`` binary on
   ``PATH`` on free ports; otherwise one is assumed at ``http://localhost:12111``
   (e.g. a CI service container).
2. ``server/python/server.py`` on a free port, with a dummy ``sk_test_...`` key and
   ``STRIPE_API_BASE`` pointing at stripe-mock. It waits for ``/health`` and stops
   the server (and stripe-mock, if it started it) at the end of the session.

To test an app that is already running, set ``BASE_URL`` (or pass ``--base-url``);
``STRIPE_MOCK_URL`` must then name the stripe-mock that app uses.

Requests to other hosts (picsum.photos images, js.stripe.com, the Checkout page) are
stubbed or blocked with ``page.route`` so runs are deterministic and work offline.
Redirect follow-ups cannot be routed, so the browser is also launched with the
Checkout host mapped to "not found" (``CHECKOUT_HOST_RULES``).

Optional: ``E2E_CHROMIUM_EXECUTABLE`` launches a specific Chromium build instead of
the one installed by ``playwright install chromium``.
"""

from __future__ import annotations

import base64
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any
from urllib.parse import urlsplit

import pytest
import requests
from playwright.sync_api import Page, Route

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_DIR = REPO_ROOT / "server" / "python"
DEFAULT_STRIPE_MOCK_URL = "http://localhost:12111"
# stripe-mock accepts any well-formed test key; this is not a real secret.
STRIPE_MOCK_KEY = "sk_test_e2esuite000000"
STARTUP_TIMEOUT = 30.0
TIMEOUT = 10
# Redirect follow-ups bypass ``page.route`` (Playwright auto-continues them), so the
# Checkout host stripe-mock redirects to is made unresolvable in the test browser:
# following the server's 303 never reaches Stripe and fails deterministically.
CHECKOUT_HOST = "checkout.stripe.com"
CHECKOUT_HOST_RULES = f"--host-resolver-rules=MAP {CHECKOUT_HOST} ~NOTFOUND"

# 1x1 transparent PNG served in place of the picsum.photos product images.
STUB_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def _env(name: str) -> str | None:
    return os.environ.get(name, "").strip() or None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(
    url: str,
    process: subprocess.Popen[bytes] | None,
    log: Path | None,
    **kwargs: Any,
) -> None:
    """Poll ``url`` until it answers 2xx; fail early if ``process`` exits."""
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        try:
            if requests.get(url, timeout=2, **kwargs).ok:
                return
        except requests.RequestException:
            pass
        if process is not None and process.poll() is not None:
            break
        time.sleep(0.25)
    output = log.read_text(errors="replace") if log and log.exists() else ""
    pytest.fail(f"{url} did not become ready.\n{output}", pytrace=False)


def _stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@dataclass(frozen=True)
class App:
    """The server under test and the stripe-mock behind it."""

    base_url: str
    stripe_mock_url: str


@pytest.fixture(scope="session")
def app(pytestconfig: pytest.Config) -> Iterator[App]:
    external = _env("BASE_URL") or pytestconfig.getoption("base_url", None)
    mock_url = _env("STRIPE_MOCK_URL")

    if external:
        mock_url = (mock_url or DEFAULT_STRIPE_MOCK_URL).rstrip("/")
        base_url = external.rstrip("/")
        _wait_for(f"{base_url}/health", None, None)
        yield App(base_url=base_url, stripe_mock_url=mock_url)
        return

    processes: list[subprocess.Popen[bytes]] = []
    logs: list[IO[bytes]] = []
    with tempfile.TemporaryDirectory(prefix="e2e-") as tmp:
        log_dir = Path(tmp)
        try:
            if mock_url is None and shutil.which("stripe-mock"):
                http_port, https_port = _free_port(), _free_port()
                mock_url = f"http://localhost:{http_port}"
                mock_log_path = log_dir / "stripe-mock.log"
                mock_log = mock_log_path.open("wb")
                logs.append(mock_log)
                mock = subprocess.Popen(
                    ["stripe-mock", "-http-port", str(http_port), "-https-port", str(https_port)],
                    stdout=mock_log,
                    stderr=subprocess.STDOUT,
                )
                processes.append(mock)
                _wait_for(f"{mock_url}/v1/balance", mock, mock_log_path, auth=(STRIPE_MOCK_KEY, ""))
            mock_url = (mock_url or DEFAULT_STRIPE_MOCK_URL).rstrip("/")

            port = _free_port()
            base_url = f"http://localhost:{port}"
            # Empty values are set on purpose: dotenv never overrides variables that
            # already exist, so a developer's server/python/.env cannot leak in.
            env = {
                **os.environ,
                "STRIPE_SECRET_KEY": STRIPE_MOCK_KEY,
                "STRIPE_API_BASE": mock_url,
                "STRIPE_WEBHOOK_SECRET": "",
                "PORT": str(port),
                "DOMAIN": "",
                "STATIC_DIR": "",
                "PYTHONUNBUFFERED": "1",
            }
            server_log_path = log_dir / "server.log"
            server_log = server_log_path.open("wb")
            logs.append(server_log)
            server = subprocess.Popen(
                [sys.executable, "server.py"],
                cwd=SERVER_DIR,
                env=env,
                stdout=server_log,
                stderr=subprocess.STDOUT,
            )
            processes.append(server)
            _wait_for(f"{base_url}/health", server, server_log_path)

            yield App(base_url=base_url, stripe_mock_url=mock_url)
        finally:
            for process in reversed(processes):
                _stop(process)
            for log in logs:
                log.close()


@pytest.fixture(scope="session")
def base_url(app: App) -> str:
    """Overrides pytest-base-url so ``page.goto("/")`` targets the app under test."""
    return app.base_url


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args: dict[str, Any]) -> dict[str, Any]:
    args = {
        **browser_type_launch_args,
        "args": [*browser_type_launch_args.get("args", []), CHECKOUT_HOST_RULES],
    }
    executable = _env("E2E_CHROMIUM_EXECUTABLE")
    if executable:
        args["executable_path"] = executable
    return args


@pytest.fixture(scope="session")
def stripe_mock(app: App) -> StripeMock:
    return StripeMock(app.stripe_mock_url)


class StripeMock:
    """Direct calls to stripe-mock, used as the source of truth for expected data."""

    def __init__(self, url: str) -> None:
        self.url = url

    def post(self, path: str, data: dict[str, Any]) -> Any:
        response = requests.post(
            f"{self.url}{path}", data=data, auth=(STRIPE_MOCK_KEY, ""), timeout=TIMEOUT
        )
        response.raise_for_status()
        return response.json()


@pytest.fixture(autouse=True)
def stub_external_requests(page: Page, base_url: str) -> None:
    """Keep the browser on the app under test: stub or block every other host.

    Tests can register their own, more specific routes; Playwright tries the most
    recently added route first.
    """
    app_origin = urlsplit(base_url).netloc

    def handle(route: Route) -> None:
        url = urlsplit(route.request.url)
        if url.netloc == app_origin:
            route.continue_()
        elif url.hostname == "picsum.photos":
            route.fulfill(status=200, content_type="image/png", body=STUB_PNG)
        elif url.hostname == "js.stripe.com":
            route.fulfill(
                status=200,
                content_type="application/javascript",
                body="window.Stripe = function () { return {}; };",
            )
        else:
            route.abort("blockedbyclient")

    page.route("**/*", handle)
