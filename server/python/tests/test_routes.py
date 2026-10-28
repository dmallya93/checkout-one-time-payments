"""Offline tests for the HTTP routes (Flask test client + mocked Stripe client)."""

from __future__ import annotations

import json

import pytest
import stripe

import server
from tests.conftest import CLIENT_DIR, SESSION_URL, VALID_SECRET_KEY

SUCCESS_URL_SUFFIX = "/success.html?session_id={CHECKOUT_SESSION_ID}"


def expected_create_params(quantity, domain: str = "http://localhost:4242") -> dict:
    return {
        "mode": "payment",
        "line_items": [
            {
                "price_data": {
                    "currency": "usd",
                    "unit_amount": 500,
                    "product_data": {"name": "Stubborn Attachments (photo)"},
                },
                "quantity": quantity,
            }
        ],
        "success_url": domain + SUCCESS_URL_SUFFIX,
    }


# --- static files -------------------------------------------------------------------------


def test_index_serves_client_index_html(client):
    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.mimetype == "text/html"
    body = resp.get_data(as_text=True)
    assert "Single photo" in body
    assert body == (CLIENT_DIR / "index.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("path", "mimetype_check", "marker"),
    [
        ("/index.js", lambda m: "javascript" in m, None),
        ("/success.js", lambda m: "javascript" in m, None),
        ("/css/global.css", lambda m: m == "text/css", None),
        ("/success.html", lambda m: m == "text/html", "Restart demo"),
        ("/index.html", lambda m: m == "text/html", "Single photo"),
        ("/favicon.ico", lambda m: m.startswith("image/"), None),
    ],
)
def test_static_assets_are_served(client, path, mimetype_check, marker):
    resp = client.get(path)

    assert resp.status_code == 200
    assert mimetype_check(resp.mimetype), resp.mimetype
    assert resp.get_data() == (CLIENT_DIR / path.lstrip("/")).read_bytes()
    if marker is not None:
        assert marker in resp.get_data(as_text=True)


def test_explicit_route_wins_over_static(client, stripe_client):
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.is_json
    stripe_client.v1.balance.retrieve.assert_called_once_with()


@pytest.mark.parametrize("path", ["/does-not-exist", "/css/missing.css", "/nested/thing.js"])
def test_unknown_path_returns_404(client, path):
    assert client.get(path).status_code == 404


def test_static_dir_from_config_is_used(tmp_path, make_config, stripe_client):
    (tmp_path / "index.html").write_text("<h1>custom index</h1>", encoding="utf-8")
    (tmp_path / "extra.txt").write_text("hello", encoding="utf-8")
    app = server.create_app(make_config(static_dir=tmp_path), stripe_client)
    test_client = app.test_client()

    assert test_client.get("/").get_data(as_text=True) == "<h1>custom index</h1>"
    assert test_client.get("/extra.txt").get_data(as_text=True) == "hello"
    assert test_client.get("/success.html").status_code == 404


def test_wrong_method_on_route(client):
    assert client.post("/health").status_code == 405
    # GET falls through to the root static route, so it is a 404 (as in Express).
    assert client.get("/create-checkout-session").status_code == 404


# --- /health ------------------------------------------------------------------------------


@pytest.mark.parametrize("livemode", [False, True])
def test_health_ok(client, stripe_client, mocker, livemode):
    stripe_client.v1.balance.retrieve.return_value = mocker.MagicMock(livemode=livemode)

    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
    assert resp.get_json() == {"status": "ok", "livemode": livemode}
    assert list(resp.get_json()) == ["status", "livemode"]


@pytest.mark.parametrize(
    "error",
    [
        stripe.APIConnectionError("Network error communicating with Stripe."),
        stripe.AuthenticationError(
            "Invalid API Key provided: sk_test_****abcd", headers={"request-id": "req_123"}
        ),
    ],
    ids=["api-connection", "authentication"],
)
def test_health_stripe_error_returns_503(client, stripe_client, error):
    stripe_client.v1.balance.retrieve.side_effect = error

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.get_json() == {"status": "error", "error": error.user_message}
    assert list(resp.get_json()) == ["status", "error"]


def test_health_error_uses_user_message_without_request_id(client, stripe_client):
    err = stripe.AuthenticationError("Invalid API Key", headers={"request-id": "req_abc"})
    assert str(err) == "Request req_abc: Invalid API Key"
    stripe_client.v1.balance.retrieve.side_effect = err

    resp = client.get("/health")

    assert resp.get_json()["error"] == "Invalid API Key"


def test_health_generic_exception_returns_503(client, stripe_client):
    stripe_client.v1.balance.retrieve.side_effect = RuntimeError("boom")

    resp = client.get("/health")

    assert resp.status_code == 503
    assert resp.get_json() == {"status": "error", "error": "boom"}


# --- /checkout-session --------------------------------------------------------------------

SESSION_DATA = {
    "id": "cs_test_a1b2c3",
    "object": "checkout.session",
    "amount_total": 1500,
    "currency": "usd",
    "livemode": False,
    "zeta_metadata": {"z": "1", "a": "2"},
    "customer_details": {"email": "jenny@example.com", "address": {"country": "US"}},
    "payment_method_types": ["card"],
    "line_items": {"object": "list", "data": [{"id": "li_1", "quantity": 3}]},
    "url": None,
    "alpha": "last",
}


def test_checkout_session_returns_full_session_json(client, stripe_client, make_session):
    session = make_session(data=SESSION_DATA)
    stripe_client.v1.checkout.sessions.retrieve.return_value = session

    resp = client.get("/checkout-session?sessionId=cs_test_a1b2c3")

    assert resp.status_code == 200
    assert resp.mimetype == "application/json"
    assert resp.get_json() == SESSION_DATA
    stripe_client.v1.checkout.sessions.retrieve.assert_called_once_with("cs_test_a1b2c3")
    session.to_dict.assert_called_once_with(for_json=True)


def test_checkout_session_preserves_key_order(client, stripe_client, make_session):
    stripe_client.v1.checkout.sessions.retrieve.return_value = make_session(data=SESSION_DATA)

    resp = client.get("/checkout-session?sessionId=cs_test_a1b2c3")

    body = json.loads(resp.get_data(as_text=True))
    assert list(body) == list(SESSION_DATA)
    assert list(body["zeta_metadata"]) == ["z", "a"]
    assert list(body["customer_details"]) == ["email", "address"]


def test_checkout_session_stripe_error_returns_400(client, stripe_client):
    stripe_client.v1.checkout.sessions.retrieve.side_effect = stripe.InvalidRequestError(
        "No such checkout.session: 'cs_test_bad'", "id", headers={"request-id": "req_9"}
    )

    resp = client.get("/checkout-session?sessionId=cs_test_bad")

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "No such checkout.session: 'cs_test_bad'"}


def test_checkout_session_generic_error_returns_400(client, stripe_client):
    stripe_client.v1.checkout.sessions.retrieve.side_effect = RuntimeError("kaput")

    resp = client.get("/checkout-session?sessionId=cs_test_x")

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "kaput"}


@pytest.mark.parametrize("query", ["", "?sessionId=", "?session_id=cs_test_a1b2c3"])
def test_checkout_session_missing_id_returns_400_without_calling_stripe(
    client, stripe_client, query
):
    resp = client.get("/checkout-session" + query)

    assert resp.status_code == 400
    body = resp.get_json()
    assert list(body) == ["error"]
    assert isinstance(body["error"], str)
    assert body["error"]
    stripe_client.v1.checkout.sessions.retrieve.assert_not_called()


# --- /create-checkout-session -------------------------------------------------------------


def test_create_session_form_quantity_passed_as_string(client, stripe_client):
    resp = client.post("/create-checkout-session", data={"quantity": "3"})

    assert resp.status_code == 303
    assert resp.headers["Location"] == SESSION_URL
    stripe_client.v1.checkout.sessions.create.assert_called_once_with(
        params=expected_create_params("3")
    )
    params = stripe_client.v1.checkout.sessions.create.call_args.kwargs["params"]
    assert params["line_items"][0]["quantity"] == "3"
    assert isinstance(params["line_items"][0]["quantity"], str)


def test_create_session_json_quantity_passed_as_int(client, stripe_client):
    resp = client.post("/create-checkout-session", json={"quantity": 3})

    assert resp.status_code == 303
    assert resp.headers["Location"] == SESSION_URL
    stripe_client.v1.checkout.sessions.create.assert_called_once_with(
        params=expected_create_params(3)
    )
    params = stripe_client.v1.checkout.sessions.create.call_args.kwargs["params"]
    quantity = params["line_items"][0]["quantity"]
    assert quantity == 3
    assert type(quantity) is int


def test_create_session_success_url_has_literal_placeholder(client, stripe_client):
    client.post("/create-checkout-session", data={"quantity": "1"})

    params = stripe_client.v1.checkout.sessions.create.call_args.kwargs["params"]
    assert params["success_url"] == (
        "http://localhost:4242/success.html?session_id={CHECKOUT_SESSION_ID}"
    )


def test_create_session_redirect_uses_exact_session_url(client, stripe_client, make_session):
    url = "https://checkout.stripe.com/c/pay/cs_test_xyz#fidkdWxOYHwnPyd1blpxYHZxWjA0"
    stripe_client.v1.checkout.sessions.create.return_value = make_session(url=url)

    resp = client.post("/create-checkout-session", data={"quantity": "2"})

    assert resp.status_code == 303
    assert resp.headers["Location"] == url


def test_create_session_uses_custom_domain(make_config, stripe_client):
    app = server.create_app(make_config(domain="https://shop.example.com"), stripe_client)

    app.test_client().post("/create-checkout-session", data={"quantity": "1"})

    stripe_client.v1.checkout.sessions.create.assert_called_once_with(
        params=expected_create_params("1", domain="https://shop.example.com")
    )


def test_create_session_domain_defaults_from_port(isolated_env, stripe_client):
    isolated_env.setenv("STRIPE_SECRET_KEY", VALID_SECRET_KEY)
    isolated_env.setenv("PORT", "5000")

    config = server.load_config()
    assert config.domain == "http://localhost:5000"
    server.create_app(config, stripe_client).test_client().post(
        "/create-checkout-session", data={"quantity": "1"}
    )

    params = stripe_client.v1.checkout.sessions.create.call_args.kwargs["params"]
    assert params["success_url"] == (
        "http://localhost:5000/success.html?session_id={CHECKOUT_SESSION_ID}"
    )


@pytest.mark.parametrize(
    "post_kwargs",
    [
        {},
        {"data": {}},
        {"data": {"other": "x"}},
        {"json": {}},
        {"json": [1, 2, 3]},
        {"json": "3"},
        {"data": "{not json", "content_type": "application/json"},
    ],
    ids=[
        "no-body",
        "empty-form",
        "form-without-quantity",
        "empty-json",
        "json-list",
        "json-string",
        "invalid-json",
    ],
)
def test_create_session_without_quantity_passes_none_and_returns_502(
    client, stripe_client, post_kwargs
):
    stripe_client.v1.checkout.sessions.create.side_effect = stripe.InvalidRequestError(
        "Missing required param: line_items[0][quantity].", "line_items[0][quantity]"
    )

    resp = client.post("/create-checkout-session", **post_kwargs)

    assert resp.status_code == 502
    assert resp.get_json() == {"error": "Missing required param: line_items[0][quantity]."}
    stripe_client.v1.checkout.sessions.create.assert_called_once_with(
        params=expected_create_params(None)
    )


def test_create_session_generic_error_returns_502(client, stripe_client):
    stripe_client.v1.checkout.sessions.create.side_effect = RuntimeError("down")

    resp = client.post("/create-checkout-session", data={"quantity": "1"})

    assert resp.status_code == 502
    assert resp.get_json() == {"error": "down"}
    assert "Location" not in resp.headers
