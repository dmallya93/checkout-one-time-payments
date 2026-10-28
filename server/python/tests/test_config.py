"""Offline tests for load_config(), helpers and main() startup."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import stripe
from flask import Flask

import server
from tests.conftest import CLIENT_DIR, SERVER_DIR, VALID_SECRET_KEY

KEY_NOT_SET = "STRIPE_SECRET_KEY is not set.\n"


# --- load_config(): STRIPE_SECRET_KEY ----------------------------------------------------


def test_missing_secret_key_exits_1(isolated_env, capsys):
    with pytest.raises(SystemExit) as exc_info:
        server.load_config()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == KEY_NOT_SET
    assert captured.out == ""


@pytest.mark.parametrize(
    "key",
    [
        "",
        "sk_test_short",
        "sk_test_123456789",  # 9 chars after the prefix
        "pk_test_" + "a" * 24,
        "sk_prod_" + "a" * 24,
        "whsec_" + "a" * 24,
        "sk_test_" + "a" * 10 + "-",
        "sk_test_" + "a" * 10 + "\n",
        " sk_test_" + "a" * 24,
        "SK_TEST_" + "a" * 24,
    ],
)
def test_invalid_secret_key_exits_1(isolated_env, capsys, key):
    isolated_env.setenv("STRIPE_SECRET_KEY", key)

    with pytest.raises(SystemExit) as exc_info:
        server.load_config()

    assert exc_info.value.code == 1
    assert capsys.readouterr().err == KEY_NOT_SET


@pytest.mark.parametrize(
    "key",
    [
        "sk_test_" + "a" * 10,
        "sk_test_" + "a1B2c3D4e5" * 10,
        "sk_live_" + "Z" * 24,
        "rk_test_" + "0" * 24,
        "rk_live_" + "a1B2c3D4e5f6",
    ],
)
def test_valid_secret_key_accepted(isolated_env, key):
    isolated_env.setenv("STRIPE_SECRET_KEY", key)

    assert server.load_config().stripe_secret_key == key


# --- load_config(): other settings -------------------------------------------------------


@pytest.fixture
def keyed_env(isolated_env):
    isolated_env.setenv("STRIPE_SECRET_KEY", VALID_SECRET_KEY)
    return isolated_env


def test_defaults(keyed_env):
    config = server.load_config()

    assert config == server.Config(
        port=4242,
        domain="http://localhost:4242",
        static_dir=CLIENT_DIR.resolve(),
        stripe_secret_key=VALID_SECRET_KEY,
        stripe_webhook_secret=None,
    )
    assert (config.static_dir / "index.html").is_file()


def test_port_parsed_as_int_and_drives_domain_default(keyed_env):
    keyed_env.setenv("PORT", "5000")

    config = server.load_config()

    assert config.port == 5000
    assert isinstance(config.port, int)
    assert config.domain == "http://localhost:5000"


def test_empty_port_falls_back_to_default(keyed_env):
    keyed_env.setenv("PORT", "")

    assert server.load_config().port == 4242


def test_custom_domain_used(keyed_env):
    keyed_env.setenv("PORT", "5000")
    keyed_env.setenv("DOMAIN", "https://shop.example.com")

    config = server.load_config()

    assert config.domain == "https://shop.example.com"
    assert config.port == 5000


def test_empty_domain_falls_back_to_default(keyed_env):
    keyed_env.setenv("DOMAIN", "")

    assert server.load_config().domain == "http://localhost:4242"


def test_static_dir_relative_to_server_dir_not_cwd(keyed_env, tmp_path):
    keyed_env.chdir(tmp_path)
    keyed_env.setenv("STATIC_DIR", "../../client/html")

    config = server.load_config()

    assert config.static_dir == CLIENT_DIR.resolve()
    assert config.static_dir.is_absolute()
    assert config.static_dir != (tmp_path / "../../client/html").resolve()


def test_static_dir_custom_relative_value(keyed_env, tmp_path):
    keyed_env.chdir(tmp_path)
    keyed_env.setenv("STATIC_DIR", "public/assets")

    assert server.load_config().static_dir == (SERVER_DIR / "public" / "assets").resolve()


def test_default_static_dir_independent_of_cwd(keyed_env, tmp_path):
    keyed_env.chdir(tmp_path)

    assert server.load_config().static_dir == CLIENT_DIR.resolve()


def test_static_dir_absolute_value(keyed_env, tmp_path):
    keyed_env.setenv("STATIC_DIR", str(tmp_path))

    assert server.load_config().static_dir == tmp_path.resolve()


def test_empty_static_dir_uses_default(keyed_env):
    keyed_env.setenv("STATIC_DIR", "")

    assert server.load_config().static_dir == CLIENT_DIR.resolve()


def test_webhook_secret_set(keyed_env):
    keyed_env.setenv("STRIPE_WEBHOOK_SECRET", "whsec_abc123")

    assert server.load_config().stripe_webhook_secret == "whsec_abc123"


def test_empty_webhook_secret_is_none(keyed_env):
    keyed_env.setenv("STRIPE_WEBHOOK_SECRET", "")

    assert server.load_config().stripe_webhook_secret is None


def test_load_dotenv_targets_server_dir_without_override(keyed_env, no_dotenv, tmp_path):
    keyed_env.chdir(tmp_path)

    server.load_config()

    no_dotenv.assert_called_once_with(SERVER_DIR / ".env", override=False)


# --- .env file handling (real python-dotenv, pointed at a temp dir) ----------------------


@pytest.fixture
def dotenv_dir(clean_env, mocker, tmp_path):
    """Point server.BASE_DIR at a temp dir so load_config() reads tmp_path/.env only."""
    base = tmp_path / "server"
    base.mkdir()
    mocker.patch.object(server, "BASE_DIR", base)
    clean_env.chdir(tmp_path)
    return base


def write_dotenv(base: Path, **values: str) -> None:
    text = "".join(f"{name}={value}\n" for name, value in values.items())
    (base / ".env").write_text(text, encoding="utf-8")


def test_dotenv_values_load_when_env_unset(dotenv_dir):
    dotenv_key = "sk_test_" + "D" * 24
    write_dotenv(
        dotenv_dir,
        STRIPE_SECRET_KEY=dotenv_key,
        PORT="6000",
        DOMAIN="https://dotenv.example.com",
        STATIC_DIR="static",
        STRIPE_WEBHOOK_SECRET="whsec_from_dotenv",
    )

    config = server.load_config()

    assert config == server.Config(
        port=6000,
        domain="https://dotenv.example.com",
        static_dir=(dotenv_dir / "static").resolve(),
        stripe_secret_key=dotenv_key,
        stripe_webhook_secret="whsec_from_dotenv",
    )


def test_real_env_takes_precedence_over_dotenv(dotenv_dir, clean_env):
    env_key = "sk_test_" + "E" * 24
    write_dotenv(
        dotenv_dir,
        STRIPE_SECRET_KEY="sk_test_" + "D" * 24,
        PORT="6000",
        STRIPE_WEBHOOK_SECRET="whsec_from_dotenv",
    )
    clean_env.setenv("STRIPE_SECRET_KEY", env_key)
    clean_env.setenv("PORT", "7000")

    config = server.load_config()

    assert config.stripe_secret_key == env_key
    assert config.port == 7000
    assert config.domain == "http://localhost:7000"
    # Not set in the real environment, so the .env value is used.
    assert config.stripe_webhook_secret == "whsec_from_dotenv"


def test_dotenv_with_invalid_key_exits_1(dotenv_dir, capsys):
    write_dotenv(dotenv_dir, STRIPE_SECRET_KEY="sk_test_...")

    with pytest.raises(SystemExit) as exc_info:
        server.load_config()

    assert exc_info.value.code == 1
    assert capsys.readouterr().err == KEY_NOT_SET


def test_missing_dotenv_file_is_fine(dotenv_dir, clean_env):
    clean_env.setenv("STRIPE_SECRET_KEY", VALID_SECRET_KEY)

    assert server.load_config().stripe_secret_key == VALID_SECRET_KEY


# --- helpers ------------------------------------------------------------------------------


def test_error_message_prefers_user_message():
    err = stripe.APIConnectionError("Network down", headers={"request-id": "req_1"})

    assert str(err) == "Request req_1: Network down"
    assert server.error_message(err) == "Network down"


def test_error_message_generic_exception():
    assert server.error_message(ValueError("bad value")) == "bad value"


def test_error_message_stripe_error_without_message():
    assert server.error_message(stripe.StripeError()) == "<empty message>"


def test_create_stripe_client_sets_app_info(mocker):
    set_app_info = mocker.patch.object(server.stripe, "set_app_info")

    client = server.create_stripe_client(VALID_SECRET_KEY)

    assert isinstance(client, stripe.StripeClient)
    set_app_info.assert_called_once_with(
        "stripe-samples/checkout-one-time-payments",
        version="0.0.1",
        url="https://github.com/stripe-samples/checkout-one-time-payments",
    )


# --- main() -------------------------------------------------------------------------------


@pytest.fixture
def startup(keyed_env, mocker, stripe_client):
    """Patch everything main() touches outside the process: Stripe, waitress, Flask.run."""
    calls: list[str] = []
    balance = mocker.MagicMock(livemode=False)

    def retrieve_balance():
        calls.append("balance.retrieve")
        return balance

    wsgi_server = mocker.MagicMock(name="WaitressServer")
    wsgi_server.run.side_effect = lambda: calls.append("server.run")

    def fake_create_server(*args, **kwargs):
        calls.append("create_server")
        return wsgi_server

    stripe_client.v1.balance.retrieve.side_effect = retrieve_balance
    return SimpleNamespace(
        calls=calls,
        env=keyed_env,
        stripe_client=stripe_client,
        wsgi_server=wsgi_server,
        create_stripe_client=mocker.patch.object(
            server, "create_stripe_client", return_value=stripe_client
        ),
        create_server=mocker.patch.object(
            server.waitress, "create_server", side_effect=fake_create_server
        ),
        flask_run=mocker.patch.object(Flask, "run"),
    )


def test_main_starts_waitress(startup, capsys):
    server.main()

    startup.create_stripe_client.assert_called_once_with(VALID_SECRET_KEY)
    startup.stripe_client.v1.balance.retrieve.assert_called_once_with()
    startup.create_server.assert_called_once()
    args, kwargs = startup.create_server.call_args
    assert isinstance(args[0], Flask)
    assert args[1:] == ()
    assert kwargs == {"host": "0.0.0.0", "port": 4242}
    startup.wsgi_server.run.assert_called_once_with()
    startup.flask_run.assert_not_called()
    captured = capsys.readouterr()
    assert captured.out == "Python server listening on port 4242!\n"
    assert captured.err == ""


def test_main_checks_balance_before_binding(startup):
    server.main()

    assert startup.calls == ["balance.retrieve", "create_server", "server.run"]


def test_main_logs_listening_after_bind_and_before_run(startup, capsys):
    stdout_seen: list[str] = []

    def create_server(*args, **kwargs):
        stdout_seen.append(capsys.readouterr().out)
        return startup.wsgi_server

    def run():
        stdout_seen.append(capsys.readouterr().out)

    startup.create_server.side_effect = create_server
    startup.wsgi_server.run.side_effect = run

    server.main()

    # Nothing printed before create_server; the listening line is printed before run().
    assert stdout_seen == ["", "Python server listening on port 4242!\n"]


def test_main_uses_configured_port(startup, capsys):
    startup.env.setenv("PORT", "5050")

    server.main()

    assert startup.create_server.call_args.kwargs == {"host": "0.0.0.0", "port": 5050}
    assert capsys.readouterr().out == "Python server listening on port 5050!\n"


def test_main_app_uses_loaded_config(startup):
    startup.env.setenv("DOMAIN", "https://shop.example.com")

    server.main()

    app = startup.create_server.call_args.args[0]
    resp = app.test_client().post("/create-checkout-session", data={"quantity": "1"})
    assert resp.status_code == 303
    params = startup.stripe_client.v1.checkout.sessions.create.call_args.kwargs["params"]
    assert params["success_url"] == (
        "https://shop.example.com/success.html?session_id={CHECKOUT_SESSION_ID}"
    )


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (
            stripe.AuthenticationError(
                "Invalid API Key provided: sk_test_****aaaa", headers={"request-id": "req_x"}
            ),
            "Invalid API Key provided: sk_test_****aaaa",
        ),
        (stripe.APIConnectionError("Could not connect to Stripe."), "Could not connect to Stripe."),
        (RuntimeError("boom"), "boom"),
    ],
    ids=["authentication", "api-connection", "generic"],
)
def test_main_balance_failure_exits_1(startup, capsys, error, message):
    startup.stripe_client.v1.balance.retrieve.side_effect = error

    with pytest.raises(SystemExit) as exc_info:
        server.main()

    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.err == f"Startup failed: {message}\n"
    assert captured.out == ""
    startup.create_server.assert_not_called()
    startup.flask_run.assert_not_called()


@pytest.mark.parametrize("key", [None, "sk_test_short"])
def test_main_invalid_key_exits_1_without_calling_stripe(startup, capsys, key):
    if key is None:
        startup.env.delenv("STRIPE_SECRET_KEY")
    else:
        startup.env.setenv("STRIPE_SECRET_KEY", key)

    with pytest.raises(SystemExit) as exc_info:
        server.main()

    assert exc_info.value.code == 1
    assert capsys.readouterr().err == KEY_NOT_SET
    startup.create_stripe_client.assert_not_called()
    startup.stripe_client.v1.balance.retrieve.assert_not_called()
    startup.create_server.assert_not_called()


def test_main_flask_debug_uses_app_run(startup, capsys):
    startup.env.setenv("FLASK_DEBUG", "1")

    server.main()

    startup.stripe_client.v1.balance.retrieve.assert_called_once_with()
    startup.flask_run.assert_called_once_with(host="0.0.0.0", port=4242, debug=True)
    startup.create_server.assert_not_called()
    assert capsys.readouterr().out == "Python server listening on port 4242!\n"


def test_main_flask_debug_reloader_child_skips_check_and_log(startup, capsys):
    startup.env.setenv("FLASK_DEBUG", "1")
    startup.env.setenv("WERKZEUG_RUN_MAIN", "true")

    server.main()

    startup.stripe_client.v1.balance.retrieve.assert_not_called()
    startup.flask_run.assert_called_once_with(host="0.0.0.0", port=4242, debug=True)
    startup.create_server.assert_not_called()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("value", ["0", "true", ""])
def test_main_flask_debug_other_values_use_waitress(startup, value):
    startup.env.setenv("FLASK_DEBUG", value)

    server.main()

    startup.create_server.assert_called_once()
    startup.flask_run.assert_not_called()
