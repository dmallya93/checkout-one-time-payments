"""Shared fixtures for the milestone-1 functional tests (Python / Flask server).

- ``BASE_URL`` (default http://localhost:4242) is the lifecycle-managed server, backed by
  stripe-mock at ``STRIPE_API_BASE`` (default http://localhost:12111).
- ``fake_stripe`` is a small recording HTTP proxy in front of stripe-mock. It records every
  request the server makes to "Stripe" (so the exact Checkout Session params can be checked)
  and can inject Stripe-style errors (balance failure, missing session).
- ``spawn_server`` starts extra ``python server.py`` instances on free ports with custom env
  (webhook secret, proxy API base, broken keys, ...), capturing stdout/stderr.
- Every test stores its evidence through the ``evidence`` fixture into
  ``ft_evidence.json`` so the results can be recorded verbatim afterwards.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVER_DIR = REPO_ROOT / "server" / "python"
PYTHON = str(SERVER_DIR / ".venv" / "bin" / "python")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:4242").rstrip("/")
STRIPE_MOCK_URL = os.environ.get("STRIPE_API_BASE", "http://localhost:12111").rstrip("/")
DUMMY_KEY = "sk_test_ftrun0000000000"
TIMEOUT = 10
EVIDENCE_FILE = Path(__file__).resolve().parent / "ft_evidence.json"
_evidence_lock = threading.Lock()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --------------------------------------------------------------------------- evidence
@pytest.fixture
def evidence(request: pytest.FixtureRequest):
    def _record(**data: Any) -> None:
        with _evidence_lock:
            current = json.loads(EVIDENCE_FILE.read_text()) if EVIDENCE_FILE.exists() else {}
            current[request.node.name] = data
            EVIDENCE_FILE.write_text(json.dumps(current, indent=2, ensure_ascii=False))

    return _record


# --------------------------------------------------------------------------- health gate
@pytest.fixture(autouse=True, scope="session")
def lifecycle_app_up() -> None:
    resp = requests.get(f"{BASE_URL}/health", timeout=5)
    assert resp.status_code == 200, f"lifecycle app not healthy: {resp.status_code} {resp.text}"


# --------------------------------------------------------------------------- fake Stripe
class FakeStripe:
    """Recording reverse proxy to stripe-mock with error injection."""

    def __init__(self) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        self.requests: list[dict[str, Any]] = []
        self.fail_balance = False
        self.missing_session_prefix = "cs_ftrun_missing"
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a: Any) -> None:  # silence
                pass

            def _handle(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "body": body.decode("utf-8", "replace"),
                        "form": parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True),
                        "headers": dict(self.headers),
                    }
                )
                if self.path.startswith("/v1/balance") and outer.fail_balance:
                    return self._error(
                        500, "api_error", "ftrun injected balance failure"
                    )
                if self.path.startswith(
                    f"/v1/checkout/sessions/{outer.missing_session_prefix}"
                ):
                    sid = self.path.split("/")[-1].split("?")[0]
                    return self._error(
                        404,
                        "invalid_request_error",
                        f"No such checkout.session: '{sid}'",
                        code="resource_missing",
                    )
                req = urllib.request.Request(
                    STRIPE_MOCK_URL + self.path,
                    data=body if self.command != "GET" else None,
                    method=self.command,
                    headers={
                        k: v
                        for k, v in self.headers.items()
                        if k.lower() not in ("host", "content-length", "connection")
                    },
                )
                try:
                    with urllib.request.urlopen(req, timeout=10) as r:
                        status, data, ctype = r.status, r.read(), r.headers.get("Content-Type")
                except urllib.error.HTTPError as e:
                    status, data, ctype = e.code, e.read(), e.headers.get("Content-Type")
                self.send_response(status)
                self.send_header("Content-Type", ctype or "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _error(self, status: int, etype: str, message: str, code: str | None = None) -> None:
                err: dict[str, Any] = {"type": etype, "message": message}
                if code:
                    err["code"] = code
                data = json.dumps({"error": err}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = _handle
            do_POST = _handle
            do_DELETE = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture(scope="session")
def fake_stripe():
    fs = FakeStripe()
    yield fs
    fs.close()


# --------------------------------------------------------------------------- spawned servers
class SpawnedServer:
    def __init__(self, env_overrides: dict[str, str | None], wait: bool = True) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("STRIPE_WEBHOOK_SECRET", "STRIPE_SECRET_KEY", "STRIPE_API_BASE", "DOMAIN")
            # stripe-python writes a harness-specific hint line to stderr when it detects
            # an agent session; strip those vars so stderr reflects a normal environment.
            and not k.startswith("CLAUDE")
        }
        env.update(
            {
                "PORT": str(self.port),
                "STRIPE_SECRET_KEY": DUMMY_KEY,
                "STRIPE_API_BASE": STRIPE_MOCK_URL,
                "PYTHONUNBUFFERED": "1",
                "STATIC_DIR": "../../client/html",
            }
        )
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        if "PORT" in env_overrides and env_overrides["PORT"]:
            self.port = int(env_overrides["PORT"])
            self.url = f"http://127.0.0.1:{self.port}"
        self.proc = subprocess.Popen(
            [PYTHON, "server.py"],
            cwd=SERVER_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self._out: list[str] = []
        self._err: list[str] = []
        threading.Thread(target=self._pump, args=(self.proc.stdout, self._out), daemon=True).start()
        threading.Thread(target=self._pump, args=(self.proc.stderr, self._err), daemon=True).start()
        if wait:
            self.wait_ready()

    @staticmethod
    def _pump(stream: Any, sink: list[str]) -> None:
        for line in stream:
            sink.append(line)

    @property
    def stdout(self) -> str:
        return "".join(self._out)

    @property
    def stderr(self) -> str:
        return "".join(self._err)

    def wait_ready(self, timeout: float = 20) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                time.sleep(0.2)
                raise RuntimeError(
                    f"server exited {self.proc.returncode}: {self.stdout} {self.stderr}"
                )
            try:
                requests.get(f"{self.url}/favicon.ico", timeout=1)
                return
            except requests.RequestException:
                time.sleep(0.2)
        raise RuntimeError(f"server not ready: {self.stdout} {self.stderr}")

    def wait_exit(self, timeout: float = 20) -> int:
        rc = self.proc.wait(timeout=timeout)
        time.sleep(0.2)  # let the pump threads drain
        return rc

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


@pytest.fixture
def spawn_server():
    servers: list[SpawnedServer] = []

    def _spawn(env: dict[str, str | None] | None = None, wait: bool = True) -> SpawnedServer:
        s = SpawnedServer(env or {}, wait=wait)
        servers.append(s)
        return s

    yield _spawn
    for s in servers:
        s.stop()


def wait_for_log(server: SpawnedServer, text: str, timeout: float = 5) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if text in server.stdout:
            return True
        time.sleep(0.1)
    return False
