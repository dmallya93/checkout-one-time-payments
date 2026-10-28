"""Startup behaviour of `python server.py` (the application's own entrypoint)."""

from __future__ import annotations

import pytest
import requests
from conftest import SERVER_DIR, TIMEOUT, free_port

KEY_MSG = "STRIPE_SECRET_KEY is not set."


class TestStartupKeyValidation:
    def test_missing_key_exits_1(self, evidence, spawn_server):
        srv = spawn_server({"STRIPE_SECRET_KEY": None}, wait=False)
        rc = srv.wait_exit()
        evidence(command="python server.py", args="(STRIPE_SECRET_KEY unset)", expected_exit_code=1,
                 actual_exit_code=rc, output=f"stdout={srv.stdout!r} stderr={srv.stderr!r}")
        assert rc == 1
        assert srv.stderr.strip() == KEY_MSG

    @pytest.mark.parametrize("key", ["sk_test_short", "pk_test_abcdefghijkl", "garbage", "sk_prod_abcdefghijkl"],
                             ids=["too_short", "publishable", "garbage", "bad_env"])
    def test_invalid_key_exits_1(self, evidence, spawn_server, key):
        srv = spawn_server({"STRIPE_SECRET_KEY": key}, wait=False)
        rc = srv.wait_exit()
        evidence(command="python server.py", args=f"(STRIPE_SECRET_KEY={key})", expected_exit_code=1,
                 actual_exit_code=rc, output=f"stdout={srv.stdout!r} stderr={srv.stderr!r}")
        assert rc == 1
        assert srv.stderr.strip() == KEY_MSG

    def test_restricted_key_accepted(self, evidence, spawn_server):
        srv = spawn_server({"STRIPE_SECRET_KEY": "rk_test_ftrun00000000"})
        resp = requests.get(f"{srv.url}/health", timeout=TIMEOUT)
        evidence(command="python server.py", args="(STRIPE_SECRET_KEY=rk_test_ftrun00000000)",
                 expected_exit_code=0, actual_exit_code=0 if srv.proc.poll() is None else srv.proc.returncode,
                 output=f"stdout={srv.stdout!r}; /health={resp.status_code} {resp.text}")
        assert resp.status_code == 200
        assert f"listening on port {srv.port}" in srv.stdout


class TestStartupStripeCheck:
    def test_stripe_unreachable_exits_1(self, evidence, spawn_server):
        dead = f"http://127.0.0.1:{free_port()}"
        srv = spawn_server({"STRIPE_API_BASE": dead}, wait=False)
        rc = srv.wait_exit(timeout=60)
        evidence(command="python server.py", args=f"(STRIPE_API_BASE={dead})", expected_exit_code=1,
                 actual_exit_code=rc, output=f"stdout={srv.stdout!r} stderr={srv.stderr!r}")
        assert rc == 1
        assert srv.stderr.startswith("Startup failed: ")
        assert len(srv.stderr.strip()) > len("Startup failed: ")
        assert "listening" not in srv.stdout

    def test_balance_error_exits_1(self, evidence, spawn_server, fake_stripe):
        fake_stripe.fail_balance = True
        try:
            srv = spawn_server({"STRIPE_API_BASE": fake_stripe.url}, wait=False)
            rc = srv.wait_exit(timeout=60)
        finally:
            fake_stripe.fail_balance = False
        evidence(command="python server.py", args="(Stripe balance.retrieve returns 500)",
                 expected_exit_code=1, actual_exit_code=rc,
                 output=f"stdout={srv.stdout!r} stderr={srv.stderr!r}")
        assert rc == 1
        assert srv.stderr.strip() == "Startup failed: ftrun injected balance failure"

    def test_successful_startup_logs_listening(self, evidence, spawn_server):
        srv = spawn_server({})
        evidence(command="python server.py", args="(valid key, stripe-mock)", expected_exit_code=0,
                 actual_exit_code=0 if srv.proc.poll() is None else srv.proc.returncode,
                 output=f"stdout={srv.stdout!r}")
        assert srv.proc.poll() is None
        assert f"listening on port {srv.port}" in srv.stdout


@pytest.fixture
def dotenv_file():
    path = SERVER_DIR / ".env"
    original = path.read_bytes() if path.exists() else None
    yield path
    if original is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(original)


class TestDotenv:
    def test_dotenv_does_not_override_real_env(self, evidence, spawn_server, dotenv_file, fake_stripe):
        env_port = free_port()
        dotenv_file.write_text(
            f"STRIPE_SECRET_KEY=invalid\nPORT={env_port}\nDOMAIN=http://from-dotenv.ftrun\n"
        )
        srv = spawn_server({"DOMAIN": "http://from-real-env.ftrun", "STRIPE_API_BASE": fake_stripe.url})
        before = len(fake_stripe.requests)
        resp = requests.post(f"{srv.url}/create-checkout-session", allow_redirects=False, timeout=TIMEOUT)
        creates = [r for r in fake_stripe.requests[before:] if r["path"] == "/v1/checkout/sessions"]
        success_url = creates[0]["form"].get("success_url") if creates else None
        evidence(command="python server.py", args="(.env: invalid key, other PORT/DOMAIN; real env set)",
                 expected_exit_code=0, actual_exit_code=0 if srv.proc.poll() is None else srv.proc.returncode,
                 output=f"stdout={srv.stdout!r}; listening port={srv.port}; success_url={success_url}")
        assert srv.proc.poll() is None
        assert f"listening on port {srv.port}" in srv.stdout
        assert resp.status_code == 303
        assert success_url == ["http://from-real-env.ftrun/success.html?session_id={CHECKOUT_SESSION_ID}"]

    def test_dotenv_used_when_env_absent(self, evidence, spawn_server, dotenv_file, fake_stripe):
        dotenv_file.write_text("DOMAIN=http://from-dotenv.ftrun\n")
        srv = spawn_server({"DOMAIN": None, "STRIPE_API_BASE": fake_stripe.url})
        before = len(fake_stripe.requests)
        resp = requests.post(f"{srv.url}/create-checkout-session", allow_redirects=False, timeout=TIMEOUT)
        creates = [r for r in fake_stripe.requests[before:] if r["path"] == "/v1/checkout/sessions"]
        success_url = creates[0]["form"].get("success_url") if creates else None
        evidence(command="python server.py", args="(.env: DOMAIN only; DOMAIN not in real env)",
                 expected_exit_code=0, actual_exit_code=0 if srv.proc.poll() is None else srv.proc.returncode,
                 output=f"stdout={srv.stdout!r}; success_url={success_url}")
        assert resp.status_code == 303
        assert success_url == ["http://from-dotenv.ftrun/success.html?session_id={CHECKOUT_SESSION_ID}"]

    def test_default_domain_from_port(self, evidence, spawn_server, fake_stripe):
        srv = spawn_server({"DOMAIN": None, "STRIPE_API_BASE": fake_stripe.url})
        before = len(fake_stripe.requests)
        resp = requests.post(f"{srv.url}/create-checkout-session", allow_redirects=False, timeout=TIMEOUT)
        creates = [r for r in fake_stripe.requests[before:] if r["path"] == "/v1/checkout/sessions"]
        success_url = creates[0]["form"].get("success_url") if creates else None
        evidence(command="python server.py", args="(DOMAIN unset)", expected_exit_code=0,
                 actual_exit_code=0 if srv.proc.poll() is None else srv.proc.returncode,
                 output=f"success_url={success_url}")
        assert resp.status_code == 303
        assert success_url == [
            f"http://localhost:{srv.port}/success.html?session_id={{CHECKOUT_SESSION_ID}}"
        ]
