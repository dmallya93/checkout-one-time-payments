"""Fixtures for the live test layers (parity and E2E).

These tests start the real servers as subprocesses and talk to the real Stripe API in
test mode, so they need ``STRIPE_SECRET_KEY`` in the environment and network access to
``api.stripe.com``. Without a usable key every test here is skipped with an explicit reason.

Servers are started on demand and shared for the whole session:

- ``PARITY_TARGETS`` (comma-separated, default ``python``; allowed ``node`` and ``python``)
  selects which servers the parametrized ``live_server`` fixtures run against.
- Each target is started in two webhook configurations. The "signed" configuration sets
  ``STRIPE_WEBHOOK_SECRET`` to the fixed test secret ``WEBHOOK_SECRET`` so signature tests
  are deterministic (it also overrides any value from the environment or a ``.env`` file).
  The "unsigned" configuration sets it to an empty string, which both servers treat as
  "not configured". The unsigned servers are only started if an unsigned test runs.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests

PYTHON_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = PYTHON_DIR.parents[1]
NODE_DIR = REPO_ROOT / "server" / "node"
CLIENT_DIR = REPO_ROOT / "client" / "html"

SECRET_KEY_PATTERN = re.compile(r"(sk|rk)_(test|live)_[A-Za-z0-9]{10,}")
SESSION_ID_PATTERN = re.compile(r"/(cs_test_[A-Za-z0-9]+)")
WEBHOOK_SECRET = "whsec_paritytest123"
ALLOWED_TARGETS = ("node", "python")
STARTUP_TIMEOUT = 30.0
REQUEST_TIMEOUT = 30.0


def _parse_parity_targets() -> list[str]:
    raw = os.environ.get("PARITY_TARGETS") or "python"
    targets = [part.strip().lower() for part in raw.split(",") if part.strip()]
    unknown = sorted(set(targets) - set(ALLOWED_TARGETS))
    if not targets or unknown:
        raise pytest.UsageError(
            f"PARITY_TARGETS={raw!r} is invalid: use a comma-separated subset of "
            f"{', '.join(ALLOWED_TARGETS)}"
        )
    # Keep the order given, drop duplicates.
    return list(dict.fromkeys(targets))


PARITY_TARGETS = _parse_parity_targets()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@dataclass
class LiveServer:
    """A server subprocess plus helpers for talking to it."""

    target: str
    port: int
    webhook_secret: str | None
    process: subprocess.Popen[str]
    domain: str
    static_dir: Path = CLIENT_DIR
    _lines: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _reader: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    # --- captured stdout/stderr -------------------------------------------------------

    def start_reader(self) -> None:
        def pump() -> None:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                with self._lock:
                    self._lines.append(line)

        self._reader = threading.Thread(target=pump, name=f"{self.target}-output", daemon=True)
        self._reader.start()

    def output(self, since: int = 0) -> str:
        with self._lock:
            return "".join(self._lines[since:])

    def mark(self) -> int:
        """Return a position in the captured output, for use with ``wait_for_output``."""
        with self._lock:
            return len(self._lines)

    def wait_for_output(self, text: str, since: int = 0, timeout: float = 10.0) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if text in self.output(since):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    # --- lifecycle ----------------------------------------------------------------------

    def wait_until_healthy(self, timeout: float = STARTUP_TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        last = "no response yet"
        while time.monotonic() < deadline:
            code = self.process.poll()
            if code is not None:
                self._join_reader()
                pytest.fail(
                    f"{self.target} server exited with code {code} before /health returned "
                    f"200.\n--- server output ---\n{self.output()}",
                    pytrace=False,
                )
            try:
                resp = requests.get(self.url("/health"), timeout=5)
            except requests.RequestException as err:
                last = f"{type(err).__name__}: {err}"
            else:
                if resp.status_code == 200:
                    return
                last = f"HTTP {resp.status_code}: {resp.text[:500]}"
            time.sleep(0.25)
        self.stop()
        pytest.fail(
            f"{self.target} server did not become healthy within {timeout:.0f}s "
            f"(last /health result: {last}).\n--- server output ---\n{self.output()}",
            pytrace=False,
        )

    def stop(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=10)
        self._join_reader()

    def _join_reader(self) -> None:
        if self._reader is not None:
            self._reader.join(timeout=5)

    # --- Stripe helpers -----------------------------------------------------------------

    def create_session(self, quantity: int = 2) -> tuple[requests.Response, str]:
        """POST /create-checkout-session and return the response and the session id."""
        resp = requests.post(
            self.url("/create-checkout-session"),
            data={"quantity": str(quantity)},
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 303:
            pytest.fail(
                f"{self.target}: expected 303 from /create-checkout-session, "
                f"got {resp.status_code}: {resp.text[:500]}"
            )
        return resp, extract_session_id(resp.headers["Location"])

    def sign_webhook(self, payload: bytes, secret: str | None = None) -> str:
        """Build a ``Stripe-Signature`` header for ``payload``."""
        key = secret if secret is not None else self.webhook_secret
        assert key, "sign_webhook needs a webhook secret"
        timestamp = int(time.time())
        signed = f"{timestamp}.".encode() + payload
        digest = hmac.new(key.encode(), signed, hashlib.sha256).hexdigest()
        return f"t={timestamp},v1={digest}"


def extract_session_id(checkout_url: str) -> str:
    """Pull the ``cs_test_...`` id out of a Stripe Checkout URL path."""
    match = SESSION_ID_PATTERN.search(urlsplit(checkout_url).path)
    if match is None:
        pytest.fail(f"no cs_test_ session id in Checkout URL path: {checkout_url}", pytrace=False)
    return match.group(1)


def _node_unavailable_reason() -> str | None:
    if not (NODE_DIR / "server.js").is_file():
        return "server/node not found (removed after cutover): node parity target skipped"
    if not (NODE_DIR / "node_modules").is_dir():
        return (
            "server/node/node_modules missing (run `npm install` in server/node): "
            "node parity target skipped"
        )
    if shutil.which("node") is None:
        return "node executable not found on PATH: node parity target skipped"
    return None


class ServerPool:
    """Starts each (target, webhook config) server once and stops them all at the end."""

    def __init__(self, secret_key: str) -> None:
        self._secret_key = secret_key
        self._servers: dict[tuple[str, bool], LiveServer] = {}
        # Skips and startup failures are remembered so the same server is not retried.
        self._outcomes: dict[tuple[str, bool], BaseException] = {}

    def get(self, target: str, *, signed: bool = True) -> LiveServer:
        key = (target, signed)
        if key in self._outcomes:
            raise self._outcomes[key]
        if key not in self._servers:
            try:
                self._servers[key] = self._start(target, signed)
            except BaseException as exc:
                self._outcomes[key] = exc
                raise
        return self._servers[key]

    def _start(self, target: str, signed: bool) -> LiveServer:
        if target == "node":
            reason = _node_unavailable_reason()
            if reason:
                pytest.skip(reason)
            command = ["node", "server.js"]
            cwd = NODE_DIR
        elif target == "python":
            command = [sys.executable, "server.py"]
            cwd = PYTHON_DIR
        else:
            raise ValueError(f"unknown target {target!r}")

        port = _free_port()
        domain = f"http://localhost:{port}"
        webhook_secret = WEBHOOK_SECRET if signed else None
        env = dict(os.environ)
        env.pop("FLASK_DEBUG", None)  # always serve with waitress, no reloader
        env.update(
            {
                "PORT": str(port),
                "DOMAIN": domain,
                "STATIC_DIR": "../../client/html",
                "STRIPE_SECRET_KEY": self._secret_key,
                # Empty means "not configured" for both servers, and a set variable also
                # beats any STRIPE_WEBHOOK_SECRET in a .env file.
                "STRIPE_WEBHOOK_SECRET": webhook_secret or "",
                "PYTHONUNBUFFERED": "1",
                "PYTHONIOENCODING": "utf-8",
            }
        )
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        server = LiveServer(
            target=target,
            port=port,
            webhook_secret=webhook_secret,
            process=process,
            domain=domain,
        )
        server.start_reader()
        server.wait_until_healthy()
        return server

    def stop_all(self) -> None:
        for server in self._servers.values():
            server.stop()
        self._servers.clear()


@pytest.fixture(scope="session")
def stripe_secret_key() -> str:
    key = os.environ.get("STRIPE_SECRET_KEY") or ""
    if not key:
        pytest.skip("STRIPE_SECRET_KEY not set: live Stripe tests skipped")
    if not SECRET_KEY_PATTERN.fullmatch(key):
        pytest.skip(
            "STRIPE_SECRET_KEY not set (value does not match ^(sk|rk)_(test|live)_...): "
            "live Stripe tests skipped"
        )
    if "_live_" in key:
        pytest.skip("STRIPE_SECRET_KEY is a live-mode key: live tests only run in test mode")
    return key


@pytest.fixture(scope="session")
def server_pool(stripe_secret_key: str) -> Iterator[ServerPool]:
    pool = ServerPool(stripe_secret_key)
    try:
        yield pool
    finally:
        pool.stop_all()


@pytest.fixture(scope="session", params=PARITY_TARGETS)
def live_server(request: pytest.FixtureRequest, server_pool: ServerPool) -> LiveServer:
    """Each PARITY_TARGETS server, with STRIPE_WEBHOOK_SECRET set to WEBHOOK_SECRET."""
    return server_pool.get(request.param, signed=True)


@pytest.fixture(scope="session", params=PARITY_TARGETS)
def unsigned_live_server(request: pytest.FixtureRequest, server_pool: ServerPool) -> LiveServer:
    """Each PARITY_TARGETS server with webhook signing not configured."""
    return server_pool.get(request.param, signed=False)


@pytest.fixture(scope="session")
def python_server(server_pool: ServerPool) -> LiveServer:
    """The Python server (signed webhook config), whatever PARITY_TARGETS says."""
    return server_pool.get("python", signed=True)
