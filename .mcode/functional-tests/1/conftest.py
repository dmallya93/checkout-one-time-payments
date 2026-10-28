"""Shared helpers for functional tests of the Flask checkout server.

The main server is expected on http://localhost:$PORT (started by run-app).
Tests that need log capture or config overrides spawn their own server
subprocesses on free ports.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import pytest
import requests

REPO_DIR = Path(__file__).resolve().parents[3]
SERVER_DIR = REPO_DIR / "server" / "python"
VENV_PY = SERVER_DIR / ".venv" / "bin" / "python"
PORT = int(os.environ.get("PORT") or 4242)
BASE_URL = f"http://localhost:{PORT}"
EVIDENCE_DIR = Path(os.environ.get("FT_EVIDENCE_DIR", "/tmp/ft_evidence"))


def _lifecycle_secret(name: str) -> str | None:
    """Fallback: read a secret from the lifecycle env_vars.json if not in env."""
    if os.environ.get(name):
        return os.environ[name]
    mcode = os.environ.get("MCODE_DIR")
    if not mcode:
        return None
    path = Path(mcode) / "lifecycle" / "target" / "env_vars.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return data.get("secrets", {}).get(name) or data.get("environment_variables", {}).get(name)


STRIPE_SECRET_KEY = _lifecycle_secret("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = _lifecycle_secret("STRIPE_WEBHOOK_SECRET")


def record(name: str, **data) -> None:
    """Store evidence for result recording (status, body, etc.)."""
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, default=str))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_env(**overrides) -> dict:
    # Drop agent-detection vars (CLAUDECODE, AI_AGENT, ...): stripe-python prints a
    # "<claude-code-hint .../>" line to stderr when it sees them, which is a sandbox
    # artifact and not part of the app's output in a normal user environment.
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PORT", "DOMAIN", "STATIC_DIR", "FLASK_DEBUG", "AI_AGENT")
        and not k.startswith("CLAUDE")
    }
    if STRIPE_SECRET_KEY:
        env["STRIPE_SECRET_KEY"] = STRIPE_SECRET_KEY
    if STRIPE_WEBHOOK_SECRET:
        env["STRIPE_WEBHOOK_SECRET"] = STRIPE_WEBHOOK_SECRET
    env["PYTHONUNBUFFERED"] = "1"
    for k, v in overrides.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v
    return env


class ServerProc:
    def __init__(self, env: dict, port: int, log_path: Path):
        self.port = port
        self.base = f"http://localhost:{port}"
        self.log_path = log_path
        self._log = open(log_path, "w")
        self.proc = subprocess.Popen(
            [str(VENV_PY), "server.py"],
            cwd=SERVER_DIR,
            env=env,
            stdout=self._log,
            stderr=subprocess.STDOUT,
        )

    def wait_ready(self, timeout: float = 30) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                return False
            try:
                if requests.get(f"{self.base}/health", timeout=3).status_code == 200:
                    return True
            except requests.RequestException:
                pass
            time.sleep(0.3)
        return False

    def output(self) -> str:
        self._log.flush()
        return self.log_path.read_text(encoding="utf-8", errors="replace")

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self._log.close()


@pytest.fixture
def spawn_server(tmp_path):
    procs: list[ServerProc] = []

    def _spawn(**overrides) -> ServerProc:
        port = int(overrides.pop("port", None) or free_port())
        env = server_env(PORT=str(port), **overrides)
        sp = ServerProc(env, port, tmp_path / f"server_{port}.log")
        procs.append(sp)
        return sp

    yield _spawn
    for p in procs:
        p.stop()


@pytest.fixture
def main_server():
    """The run-app managed server must be healthy."""
    resp = requests.get(f"{BASE_URL}/health", timeout=15)
    assert resp.status_code == 200, f"main server unhealthy: {resp.status_code} {resp.text}"
    return BASE_URL
