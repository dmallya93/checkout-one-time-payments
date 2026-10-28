"""Startup and configuration tests: key validation, Balance check, .env precedence."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import stripe
from conftest import SERVER_DIR, TEST_SECRET_KEY, ConfigFactory

import server

KEY_ERROR = "STRIPE_SECRET_KEY is not set."


# --- Key validation -----------------------------------------------------------------


@pytest.mark.parametrize(
    "key",
    [
        "sk_test_" + "a" * 10,
        "sk_live_" + "Z9" * 12,
        "rk_test_" + "0" * 24,
        "rk_live_" + "AbC123xYz0",
    ],
)
def test_valid_keys_accepted(key: str) -> None:
    assert server.is_valid_secret_key(key)


@pytest.mark.parametrize(
    "key",
    [
        None,
        "",
        "sk_test_",
        "sk_test_short",
        "pk_test_" + "a" * 24,
        "sk_prod_" + "a" * 24,
        "sk_test_" + "a" * 23 + "-",
        "sk_test_" + "a" * 24 + "\n",
        " sk_test_" + "a" * 24,
        "whsec_" + "a" * 24,
    ],
)
def test_invalid_keys_rejected(key: str | None) -> None:
    assert not server.is_valid_secret_key(key)


@pytest.mark.parametrize("key", [None, "not-a-key", "sk_test_123"])
def test_main_exits_1_on_missing_or_invalid_key(
    key: str | None,
    make_config: ConfigFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(server, "load_config", lambda: make_config(secret_key=key))
    build = MagicMock()
    monkeypatch.setattr(server, "build_stripe_client", build)

    with pytest.raises(SystemExit) as exc_info:
        server.main()

    assert exc_info.value.code == 1
    assert capsys.readouterr().err.strip() == KEY_ERROR
    build.assert_not_called()


def test_create_app_without_config_exits_1_on_invalid_key(
    make_config: ConfigFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(server, "load_config", lambda: make_config(secret_key="bad"))

    with pytest.raises(SystemExit) as exc_info:
        server.create_app()

    assert exc_info.value.code == 1
    assert capsys.readouterr().err.strip() == KEY_ERROR


# --- Balance startup check -----------------------------------------------------------


def failing_client(message: str = "Invalid API Key provided") -> MagicMock:
    mock = MagicMock()
    mock.v1.balance.retrieve.side_effect = stripe.AuthenticationError(message)
    return mock


def test_run_startup_check_passes(stripe_mock: MagicMock) -> None:
    server.run_startup_check(stripe_mock)
    stripe_mock.v1.balance.retrieve.assert_called_once_with()


def test_main_exits_1_when_balance_call_fails(
    make_config: ConfigFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(server, "load_config", lambda: make_config())
    monkeypatch.setattr(server, "build_stripe_client", lambda _cfg: failing_client())
    run = MagicMock()
    monkeypatch.setattr("flask.Flask.run", run)

    with pytest.raises(SystemExit) as exc_info:
        server.main()

    assert exc_info.value.code == 1
    assert capsys.readouterr().err.strip() == "Startup failed: Invalid API Key provided"
    run.assert_not_called()


def test_create_app_without_config_exits_1_when_balance_fails(
    make_config: ConfigFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(server, "load_config", lambda: make_config())

    with pytest.raises(SystemExit) as exc_info:
        server.create_app(stripe_client=failing_client("boom"))

    assert exc_info.value.code == 1
    assert capsys.readouterr().err.strip() == "Startup failed: boom"


def test_create_app_without_config_runs_startup_check(
    make_config: ConfigFactory, monkeypatch: pytest.MonkeyPatch, stripe_mock: MagicMock
) -> None:
    monkeypatch.setattr(server, "load_config", lambda: make_config())

    app = server.create_app(stripe_client=stripe_mock)

    stripe_mock.v1.balance.retrieve.assert_called_once_with()
    assert app.test_client().get("/health").status_code == 200


def test_main_success_logs_and_listens_on_all_interfaces(
    make_config: ConfigFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stripe_mock: MagicMock,
) -> None:
    monkeypatch.setattr(server, "load_config", lambda: make_config(port=5005))
    monkeypatch.setattr(server, "build_stripe_client", lambda _cfg: stripe_mock)
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr("flask.Flask.run", lambda _self, **kwargs: calls.append(kwargs))

    server.main()

    stripe_mock.v1.balance.retrieve.assert_called_once_with()
    assert "Python server listening on port 5005!" in capsys.readouterr().out
    assert calls == [{"host": "0.0.0.0", "port": 5005}]


# --- Stripe client construction -------------------------------------------------------


def test_build_stripe_client_sets_app_info(
    make_config: ConfigFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stripe, "app_info", None)

    client = server.build_stripe_client(make_config())

    assert isinstance(client, stripe.StripeClient)
    assert stripe.app_info == {
        "name": "stripe-samples/checkout-one-time-payments",
        "partner_id": None,
        "url": "https://github.com/stripe-samples/checkout-one-time-payments",
        "version": "0.0.1",
    }


def test_build_stripe_client_api_base_override(make_config: ConfigFactory) -> None:
    client = server.build_stripe_client(make_config(api_base="http://localhost:12111"))

    assert client._requestor.base_addresses["api"] == "http://localhost:12111"


def test_build_stripe_client_default_api_base(make_config: ConfigFactory) -> None:
    client = server.build_stripe_client(make_config())

    assert client._requestor.base_addresses["api"] == "https://api.stripe.com"


# --- Configuration ------------------------------------------------------------------


def test_load_config_defaults(tmp_path: Path) -> None:
    config = server.load_config(tmp_path / "missing.env")

    assert config.port == 4242
    assert config.domain == "http://localhost:4242"
    assert Path(config.static_dir) == (SERVER_DIR / "../../client/html").resolve()
    assert config.secret_key is None
    assert config.webhook_secret is None
    assert config.api_base is None


def test_load_config_reads_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PORT", "5001")
    monkeypatch.setenv("STATIC_DIR", "../../client")
    monkeypatch.setenv("STRIPE_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_abc")
    monkeypatch.setenv("STRIPE_API_BASE", "http://localhost:12111")

    config = server.load_config(tmp_path / "missing.env")

    assert config.port == 5001
    assert config.domain == "http://localhost:5001"
    assert Path(config.static_dir) == (SERVER_DIR / "../../client").resolve()
    assert config.secret_key == TEST_SECRET_KEY
    assert config.webhook_secret == "whsec_abc"
    assert config.api_base == "http://localhost:12111"


def test_load_config_empty_values_use_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("PORT", "DOMAIN", "STATIC_DIR"):
        monkeypatch.setenv(name, "")

    config = server.load_config(tmp_path / "missing.env")

    assert config.port == 4242
    assert config.domain == "http://localhost:4242"


def test_config_is_frozen(config: server.Config) -> None:
    with pytest.raises(AttributeError):
        config.port = 1  # type: ignore[misc]


def test_env_file_loaded_without_overriding_real_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "STRIPE_SECRET_KEY=sk_test_fromdotenvfile0000\n"
        "PORT=5555\n"
        "DOMAIN=https://dotenv.example.com\n"
    )
    monkeypatch.setenv("STRIPE_SECRET_KEY", TEST_SECRET_KEY)
    monkeypatch.setenv("DOMAIN", "https://real-env.example.com")

    config = server.load_config(env_file)

    assert config.secret_key == TEST_SECRET_KEY
    assert config.domain == "https://real-env.example.com"
    assert config.port == 5555  # only in .env, so it is loaded


def test_default_env_file_is_next_to_server_py(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text("PORT=6123\n")
    monkeypatch.setattr(server, "SERVER_DIR", tmp_path)

    assert server.load_config().port == 6123


# --- Real process (python server.py) ---------------------------------------------------


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_server_copy(
    tmp_path: Path, env: dict[str, str], dotenv: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a copy of server.py from tmp_path so its .env is fully controlled."""
    shutil.copy(SERVER_DIR / "server.py", tmp_path / "server.py")
    if dotenv is not None:
        (tmp_path / ".env").write_text(dotenv)
    base_env = {
        k: v for k, v in os.environ.items() if not k.startswith(("STRIPE_", "PORT", "DOMAIN"))
    }
    base_env.pop("STATIC_DIR", None)
    # stripe-python writes an extra hint line to stderr when run under these tools.
    for name in ("CLAUDECODE", "CLAUDE_CODE_CHILD_SESSION"):
        base_env.pop(name, None)
    base_env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "server.py"],
        cwd=tmp_path,
        env={**base_env, **env},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_process_exits_1_without_key(tmp_path: Path) -> None:
    result = run_server_copy(tmp_path, {})

    assert result.returncode == 1
    assert result.stderr.strip() == KEY_ERROR


def test_process_exits_1_when_balance_call_fails(tmp_path: Path) -> None:
    result = run_server_copy(
        tmp_path,
        {
            "STRIPE_SECRET_KEY": TEST_SECRET_KEY,
            "STRIPE_API_BASE": f"http://127.0.0.1:{closed_port()}",
        },
    )

    assert result.returncode == 1
    assert result.stderr.startswith("Startup failed: ")
    assert "listening" not in result.stdout


def test_process_real_env_key_beats_invalid_dotenv_key(tmp_path: Path) -> None:
    # A valid real key wins over the invalid .env key, so the key check passes and
    # the process gets as far as the (failing) Balance call.
    result = run_server_copy(
        tmp_path,
        {
            "STRIPE_SECRET_KEY": TEST_SECRET_KEY,
            "STRIPE_API_BASE": f"http://127.0.0.1:{closed_port()}",
        },
        dotenv="STRIPE_SECRET_KEY=invalid\n",
    )

    assert result.returncode == 1
    assert result.stderr.startswith("Startup failed: ")


def test_process_invalid_real_env_key_beats_valid_dotenv_key(tmp_path: Path) -> None:
    result = run_server_copy(
        tmp_path,
        {"STRIPE_SECRET_KEY": "invalid"},
        dotenv=f"STRIPE_SECRET_KEY={TEST_SECRET_KEY}\n",
    )

    assert result.returncode == 1
    assert result.stderr.strip() == KEY_ERROR


def test_process_reads_key_from_dotenv(tmp_path: Path) -> None:
    result = run_server_copy(
        tmp_path,
        {},
        dotenv=(
            f"STRIPE_SECRET_KEY={TEST_SECRET_KEY}\n"
            f"STRIPE_API_BASE=http://127.0.0.1:{closed_port()}\n"
        ),
    )

    assert result.returncode == 1
    assert result.stderr.startswith("Startup failed: ")
