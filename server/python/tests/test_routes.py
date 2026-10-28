"""Route tests for /health, /create-checkout-session, /checkout-session and static files."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import stripe
from conftest import STATIC_DIR, TEST_SECRET_KEY, ConfigFactory
from flask.testing import FlaskClient

import server

JSON_TYPE = "application/json; charset=utf-8"
EXPECTED_PRICE_DATA = {
    "currency": "usd",
    "unit_amount": 500,
    "product_data": {"name": "Stubborn Attachments (photo)"},
}
SUCCESS_URL = "http://localhost:4242/success.html?session_id={CHECKOUT_SESSION_ID}"


def sent_params(stripe_mock: MagicMock) -> dict[str, object]:
    stripe_mock.v1.checkout.sessions.create.assert_called_once()
    return stripe_mock.v1.checkout.sessions.create.call_args.kwargs["params"]


# --- /health -----------------------------------------------------------------


def test_health_ok(client: FlaskClient, stripe_mock: MagicMock) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.content_type == JSON_TYPE
    assert response.get_json() == {"status": "ok", "livemode": False}
    # Key order and compact form match Express' JSON.stringify output.
    assert response.data == b'{"status":"ok","livemode":false}'
    stripe_mock.v1.balance.retrieve.assert_called_once_with()


def test_health_reports_livemode_true(client: FlaskClient, stripe_mock: MagicMock) -> None:
    stripe_mock.v1.balance.retrieve.return_value = stripe.StripeObject.construct_from(
        {"object": "balance", "livemode": True}, TEST_SECRET_KEY
    )

    assert client.get("/health").get_json() == {"status": "ok", "livemode": True}


def test_health_stripe_error_returns_503(client: FlaskClient, stripe_mock: MagicMock) -> None:
    stripe_mock.v1.balance.retrieve.side_effect = stripe.AuthenticationError(
        "Invalid API Key provided: sk_test_***"
    )

    response = client.get("/health")

    assert response.status_code == 503
    assert response.content_type == JSON_TYPE
    assert response.get_json() == {
        "status": "error",
        "error": "Invalid API Key provided: sk_test_***",
    }


def test_health_trailing_slash_matches_like_express(client: FlaskClient) -> None:
    assert client.get("/health/").status_code == 200


# --- POST /create-checkout-session -----------------------------------------------


def test_create_checkout_session_form_body(client: FlaskClient, stripe_mock: MagicMock) -> None:
    response = client.post("/create-checkout-session", data={"quantity": "2"})

    assert response.status_code == 303
    assert response.headers["Location"] == "https://checkout.stripe.com/c/pay/cs_test_123"
    assert sent_params(stripe_mock) == {
        "mode": "payment",
        "line_items": [{"price_data": EXPECTED_PRICE_DATA, "quantity": "2"}],
        "success_url": SUCCESS_URL,
    }


def test_create_checkout_session_json_body(client: FlaskClient, stripe_mock: MagicMock) -> None:
    response = client.post("/create-checkout-session", json={"quantity": 3})

    assert response.status_code == 303
    assert response.headers["Location"] == "https://checkout.stripe.com/c/pay/cs_test_123"
    assert sent_params(stripe_mock) == {
        "mode": "payment",
        "line_items": [{"price_data": EXPECTED_PRICE_DATA, "quantity": 3}],
        "success_url": SUCCESS_URL,
    }


@pytest.mark.parametrize(
    ("accept", "content_type", "body"),
    [
        (None, "text/plain; charset=utf-8", b"See Other. Redirecting to {url}"),
        ("*/*", "text/plain; charset=utf-8", b"See Other. Redirecting to {url}"),
        (
            "text/html,application/xhtml+xml,*/*;q=0.8",
            "text/html; charset=utf-8",
            b"<p>See Other. Redirecting to {url}</p>",
        ),
        ("application/json", None, b""),
    ],
)
def test_create_checkout_session_redirect_body_negotiated_like_express(
    client: FlaskClient, accept: str | None, content_type: str | None, body: bytes
) -> None:
    headers = {"Accept": accept} if accept is not None else {}
    response = client.post("/create-checkout-session", data={"quantity": "1"}, headers=headers)

    assert response.status_code == 303
    assert response.headers.get("Content-Type") == content_type
    assert response.headers["Vary"] == "Accept"
    url = b"https://checkout.stripe.com/c/pay/cs_test_123"
    assert response.data == body.replace(b"{url}", url)


def test_success_url_keeps_literal_placeholder(client: FlaskClient, stripe_mock: MagicMock) -> None:
    client.post("/create-checkout-session", data={"quantity": "1"})

    success_url = sent_params(stripe_mock)["success_url"]
    assert isinstance(success_url, str)
    assert success_url.endswith("/success.html?session_id={CHECKOUT_SESSION_ID}")


def test_domain_override_used_in_success_url(
    make_config: ConfigFactory, stripe_mock: MagicMock
) -> None:
    app = server.create_app(make_config(domain="https://shop.example.com"), stripe_mock)

    app.test_client().post("/create-checkout-session", data={"quantity": "1"})

    assert sent_params(stripe_mock)["success_url"] == (
        "https://shop.example.com/success.html?session_id={CHECKOUT_SESSION_ID}"
    )


def test_load_config_domain_override_reaches_stripe(
    stripe_mock: MagicMock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOMAIN", "https://env.example.com")
    monkeypatch.setenv("STRIPE_SECRET_KEY", TEST_SECRET_KEY)
    app = server.create_app(server.load_config(tmp_path / "missing.env"), stripe_mock)

    app.test_client().post("/create-checkout-session", data={"quantity": "1"})

    assert sent_params(stripe_mock)["success_url"] == (
        "https://env.example.com/success.html?session_id={CHECKOUT_SESSION_ID}"
    )


@pytest.mark.parametrize(
    ("kwargs", "label"),
    [
        ({}, "no body"),
        ({"data": {}}, "empty form"),
        ({"json": {}}, "empty JSON object"),
        ({"data": {"other": "x"}}, "form without quantity"),
        ({"data": "quantity=2", "content_type": "text/plain"}, "unparsed content type"),
    ],
)
def test_missing_quantity_omits_key(
    client: FlaskClient, stripe_mock: MagicMock, kwargs: dict[str, object], label: str
) -> None:
    response = client.post("/create-checkout-session", **kwargs)  # type: ignore[arg-type]

    assert response.status_code == 303, label
    line_items = sent_params(stripe_mock)["line_items"]
    assert line_items == [{"price_data": EXPECTED_PRICE_DATA}], label


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"data": {"quantity": "abc"}}, "abc"),
        ({"data": {"quantity": "11"}}, "11"),
        ({"data": {"quantity": ""}}, ""),
        ({"json": {"quantity": "4"}}, "4"),
        ({"json": {"quantity": 0}}, 0),
        ({"json": {"quantity": None}}, None),
    ],
)
def test_quantity_passed_through_unchanged(
    client: FlaskClient,
    stripe_mock: MagicMock,
    kwargs: dict[str, object],
    expected: object,
) -> None:
    client.post("/create-checkout-session", **kwargs)  # type: ignore[arg-type]

    line_item = sent_params(stripe_mock)["line_items"][0]  # type: ignore[index]
    assert line_item["quantity"] == expected
    assert type(line_item["quantity"]) is type(expected)


def test_create_checkout_session_stripe_error_returns_502(
    client: FlaskClient, stripe_mock: MagicMock
) -> None:
    stripe_mock.v1.checkout.sessions.create.side_effect = stripe.InvalidRequestError(
        "Invalid integer: abc", "line_items[0][quantity]", headers={"request-id": "req_1"}
    )

    response = client.post("/create-checkout-session", data={"quantity": "abc"})

    assert response.status_code == 502
    assert response.content_type == JSON_TYPE
    # The bare message, like Node's err.message (no "Request req_1:" prefix).
    assert response.get_json() == {"error": "Invalid integer: abc"}


def test_create_checkout_session_get_is_404(client: FlaskClient) -> None:
    assert client.get("/create-checkout-session").status_code == 404


# --- GET /checkout-session --------------------------------------------------------


def test_checkout_session_returns_session_json(client: FlaskClient, stripe_mock: MagicMock) -> None:
    session_data = {
        "id": "cs_test_abc",
        "object": "checkout.session",
        "amount_total": 500,
        "currency": "usd",
        "customer_details": {"email": "jenny@example.com", "name": "Jenny"},
        "payment_status": "paid",
        "metadata": {},
    }
    stripe_mock.v1.checkout.sessions.retrieve.return_value = stripe.StripeObject.construct_from(
        session_data, TEST_SECRET_KEY
    )

    response = client.get("/checkout-session?sessionId=cs_test_abc")

    assert response.status_code == 200
    assert response.content_type == JSON_TYPE
    assert response.get_json() == session_data
    assert list(json.loads(response.data)) == list(session_data)
    stripe_mock.v1.checkout.sessions.retrieve.assert_called_once_with("cs_test_abc")


@pytest.mark.parametrize("query", ["", "?sessionId=", "?session_id=cs_test_abc"])
def test_checkout_session_missing_id_returns_400(
    client: FlaskClient, stripe_mock: MagicMock, query: str
) -> None:
    response = client.get(f"/checkout-session{query}")

    assert response.status_code == 400
    assert response.content_type == JSON_TYPE
    body = response.get_json()
    assert list(body) == ["error"]
    assert isinstance(body["error"], str) and body["error"]
    stripe_mock.v1.checkout.sessions.retrieve.assert_not_called()


def test_checkout_session_invalid_id_returns_400(
    client: FlaskClient, stripe_mock: MagicMock
) -> None:
    stripe_mock.v1.checkout.sessions.retrieve.side_effect = stripe.InvalidRequestError(
        "No such checkout.session: cs_bad", "id", headers={"request-id": "req_2"}
    )

    response = client.get("/checkout-session?sessionId=cs_bad")

    assert response.status_code == 400
    assert response.get_json() == {"error": "No such checkout.session: cs_bad"}


# --- Static files and unknown routes ------------------------------------------------


@pytest.mark.parametrize(
    ("path", "filename", "content_type"),
    [
        ("/", "index.html", "text/html; charset=UTF-8"),
        ("/index.html", "index.html", "text/html; charset=UTF-8"),
        ("/success.html", "success.html", "text/html; charset=UTF-8"),
        ("/css/global.css", "css/global.css", "text/css; charset=UTF-8"),
        ("/css/normalize.css", "css/normalize.css", "text/css; charset=UTF-8"),
        ("/index.js", "index.js", "application/javascript; charset=UTF-8"),
        ("/success.js", "success.js", "application/javascript; charset=UTF-8"),
        ("/favicon.ico", "favicon.ico", "image/x-icon"),
    ],
)
def test_static_files_served_from_client_html(
    client: FlaskClient, path: str, filename: str, content_type: str
) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.content_type == content_type
    assert response.data == (STATIC_DIR / filename).read_bytes()


def test_head_on_static_file(client: FlaskClient) -> None:
    response = client.head("/index.js")

    assert response.status_code == 200
    assert response.data == b""


@pytest.mark.parametrize("path", ["/nope", "/does/not/exist.html", "/css/missing.css"])
def test_unknown_path_returns_express_style_404(client: FlaskClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert response.content_type == "text/html; charset=utf-8"
    assert f"<pre>Cannot GET {path}</pre>".encode() in response.data


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/index.html"),
        ("PUT", "/css/global.css"),
        ("POST", "/"),
        ("GET", "/webhook"),
        ("POST", "/health"),
        ("DELETE", "/checkout-session"),
    ],
)
def test_unsupported_method_returns_404_not_405(
    client: FlaskClient, method: str, path: str
) -> None:
    response = client.open(path, method=method)

    assert response.status_code == 404
    assert f"Cannot {method} {path}".encode() in response.data


def test_directory_redirects_to_trailing_slash(client: FlaskClient) -> None:
    response = client.get("/css?x=1")

    assert response.status_code == 301
    assert response.headers["Location"] == "/css/?x=1"


def test_directory_without_index_is_404(client: FlaskClient) -> None:
    assert client.get("/css/").status_code == 404


def test_static_file_takes_precedence_over_route(
    make_config: ConfigFactory, stripe_mock: MagicMock, tmp_path: Path
) -> None:
    (tmp_path / "index.html").write_text("<p>home</p>")
    (tmp_path / "health").write_text("static health file")
    app = server.create_app(make_config(static_dir=str(tmp_path)), stripe_mock)

    response = app.test_client().get("/health")

    assert response.status_code == 200
    assert response.data == b"static health file"
    stripe_mock.v1.balance.retrieve.assert_not_called()


def test_dotfiles_are_ignored(
    make_config: ConfigFactory, stripe_mock: MagicMock, tmp_path: Path
) -> None:
    (tmp_path / ".secret").write_text("hidden")
    app = server.create_app(make_config(static_dir=str(tmp_path)), stripe_mock)

    assert app.test_client().get("/.secret").status_code == 404


def test_path_traversal_is_not_served(client: FlaskClient) -> None:
    assert client.get("/../python/server.py").status_code == 404
    assert client.get("/%2e%2e/python/server.py").status_code == 404
