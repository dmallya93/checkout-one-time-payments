"""Startup and config behaviour of `python server.py` (spec §4, §5 Startup)."""

import subprocess

import requests

from conftest import SERVER_DIR, VENV_PY, free_port, record, server_env


def _run_to_exit(env, timeout=30):
    return subprocess.run([str(VENV_PY), "server.py"], cwd=SERVER_DIR, env=env,
                          capture_output=True, text=True, timeout=timeout)


class TestStartupConfig:
    def test_missing_secret_key_exits_1(self):
        p = _run_to_exit(server_env(PORT=str(free_port()), STRIPE_SECRET_KEY=None))
        record("test_missing_secret_key_exits_1", exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr)
        assert p.returncode == 1
        assert p.stderr.strip() == "STRIPE_SECRET_KEY is not set."
        assert "listening" not in p.stdout

    def test_invalid_secret_key_exits_1(self):
        p = _run_to_exit(server_env(PORT=str(free_port()), STRIPE_SECRET_KEY="pk_test_notasecretkey123"))
        record("test_invalid_secret_key_exits_1", exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr)
        assert p.returncode == 1
        assert p.stderr.strip() == "STRIPE_SECRET_KEY is not set."

    def test_short_secret_key_exits_1(self):
        p = _run_to_exit(server_env(PORT=str(free_port()), STRIPE_SECRET_KEY="sk_test_short"))
        record("test_short_secret_key_exits_1", exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr)
        assert p.returncode == 1
        assert p.stderr.strip() == "STRIPE_SECRET_KEY is not set."

    def test_wellformed_but_rejected_key_startup_failed(self):
        """Key passes the regex but Stripe rejects it -> 'Startup failed:' and exit 1, never listens."""
        p = _run_to_exit(server_env(PORT=str(free_port()), STRIPE_SECRET_KEY="sk_test_ftrunInvalidKey0123456789"))
        record("test_wellformed_but_rejected_key_startup_failed", exit_code=p.returncode, stdout=p.stdout, stderr=p.stderr)
        assert p.returncode == 1
        assert p.stderr.startswith("Startup failed: ")
        assert "listening" not in p.stdout

    def test_valid_start_custom_port(self, spawn_server):
        srv = spawn_server()
        ready = srv.wait_ready()
        r = requests.get(f"{srv.base}/health", timeout=15) if ready else None
        out = srv.output()
        record("test_valid_start_custom_port", exit_code=0 if ready else srv.proc.returncode, stdout=out,
               status=r.status_code if r is not None else None, body=r.text.strip() if r is not None else "")
        assert ready, out
        assert f"Python server listening on port {srv.port}!" in out
        assert r.json() == {"status": "ok", "livemode": False}
