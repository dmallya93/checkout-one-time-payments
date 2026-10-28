"""HTTP parity: static files, health, Checkout Sessions and unknown routes.

Compares status codes, redirects, content types and JSON key sets, never messages.
Expected data comes from stripe-mock itself, so the suite does not depend on the
stripe-mock version.
"""

from __future__ import annotations

import pytest
import requests
from conftest import STATIC_DIR, StripeMock
from helpers import TIMEOUT, assert_content_type, assert_json, shape

SESSION_ID = "cs_test_a1ParitySuite"
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
# Keys the success page and any Checkout integration rely on.
CORE_SESSION_KEYS = {
    "id",
    "object",
    "url",
    "mode",
    "status",
    "payment_status",
    "amount_total",
    "amount_subtotal",
    "currency",
    "customer",
    "customer_details",
    "livemode",
    "metadata",
    "payment_intent",
    "success_url",
    "cancel_url",
    "created",
    "expires_at",
}


# --- Static files ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "filename", "media_type", "charset"),
    [
        ("/", "index.html", "text/html", "UTF-8"),
        ("/index.html", "index.html", "text/html", "UTF-8"),
        ("/success.html", "success.html", "text/html", "UTF-8"),
        ("/index.js", "index.js", "application/javascript", "UTF-8"),
        ("/success.js", "success.js", "application/javascript", "UTF-8"),
        ("/css/global.css", "css/global.css", "text/css", "UTF-8"),
        ("/css/normalize.css", "css/normalize.css", "text/css", "UTF-8"),
        ("/favicon.ico", "favicon.ico", "image/x-icon", None),
    ],
)
def test_static_file(
    http: requests.Session,
    base_url: str,
    path: str,
    filename: str,
    media_type: str,
    charset: str | None,
) -> None:
    response = http.get(f"{base_url}{path}", timeout=TIMEOUT)

    assert response.status_code == 200
    assert_content_type(response, media_type, charset)
    assert response.content == (STATIC_DIR / filename).read_bytes()


def test_static_query_string_is_ignored(http: requests.Session, base_url: str) -> None:
    response = http.get(f"{base_url}/success.html?session_id={SESSION_ID}", timeout=TIMEOUT)

    assert response.status_code == 200
    assert_content_type(response, "text/html", "UTF-8")


def test_head_static_file(http: requests.Session, base_url: str) -> None:
    response = http.head(f"{base_url}/index.js", timeout=TIMEOUT)

    assert response.status_code == 200
    assert_content_type(response, "application/javascript", "UTF-8")
    assert response.content == b""


def test_static_directory_redirects_to_trailing_slash(
    http: requests.Session, base_url: str
) -> None:
    response = http.get(f"{base_url}/css", timeout=TIMEOUT, allow_redirects=False)

    assert response.status_code == 301
    assert response.headers["Location"] == "/css/"


@pytest.mark.parametrize("path", ["/css/", "/.env", "/css/.hidden"])
def test_static_directory_without_index_and_dotfiles_are_404(
    http: requests.Session, base_url: str, path: str
) -> None:
    response = http.get(f"{base_url}{path}", timeout=TIMEOUT, allow_redirects=False)

    assert response.status_code == 404
    assert_content_type(response, "text/html", "utf-8")


# --- GET /health -------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/health", "/health/"])
def test_health(http: requests.Session, base_url: str, path: str) -> None:
    response = http.get(f"{base_url}{path}", timeout=TIMEOUT)

    assert response.status_code == 200
    body = assert_json(response)
    assert set(body) == {"status", "livemode"}
    assert body["status"] == "ok"
    assert body["livemode"] is False


# --- GET /checkout-session -----------------------------------------------------------


def test_checkout_session(http: requests.Session, base_url: str) -> None:
    response = http.get(
        f"{base_url}/checkout-session", params={"sessionId": SESSION_ID}, timeout=TIMEOUT
    )

    assert response.status_code == 200
    body = assert_json(response)
    assert set(body) >= CORE_SESSION_KEYS
    assert body["id"] == SESSION_ID
    assert body["object"] == "checkout.session"


def test_checkout_session_matches_stripe_shape(
    http: requests.Session, base_url: str, stripe_mock: StripeMock
) -> None:
    """The server returns the session as Stripe sent it: same keys, nested too."""
    expected = stripe_mock.get(f"/v1/checkout/sessions/{SESSION_ID}")

    response = http.get(
        f"{base_url}/checkout-session", params={"sessionId": SESSION_ID}, timeout=TIMEOUT
    )

    body = assert_json(response)
    assert set(body) == set(expected)
    assert shape(body) == shape(expected)


def test_checkout_session_missing_id_is_400(http: requests.Session, base_url: str) -> None:
    response = http.get(f"{base_url}/checkout-session", timeout=TIMEOUT)

    assert response.status_code == 400
    body = assert_json(response)
    assert set(body) == {"error"}
    assert isinstance(body["error"], str)


@pytest.mark.skip(
    reason="Documented difference (server/python/README.md): with an empty sessionId "
    "stripe-node calls the list endpoint (200 on real Stripe); Flask returns 400."
)
def test_checkout_session_empty_id(http: requests.Session, base_url: str) -> None:
    response = http.get(f"{base_url}/checkout-session?sessionId=", timeout=TIMEOUT)

    assert response.status_code == 400


# --- POST /create-checkout-session ----------------------------------------------------


@pytest.fixture(scope="module")
def checkout_url(stripe_mock: StripeMock) -> str:
    """The Checkout URL stripe-mock returns for a created session."""
    session = stripe_mock.post(
        "/v1/checkout/sessions",
        {
            "mode": "payment",
            "success_url": "http://localhost/success.html?session_id={CHECKOUT_SESSION_ID}",
            "line_items[0][price_data][currency]": "usd",
            "line_items[0][price_data][unit_amount]": "500",
            "line_items[0][price_data][product_data][name]": "Stubborn Attachments (photo)",
            "line_items[0][quantity]": "1",
        },
    )
    url = session["url"]
    assert isinstance(url, str)
    assert url
    return url


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"data": {"quantity": "2"}}, id="form"),
        pytest.param(
            {
                "data": "quantity=3",
                "headers": {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            },
            id="form-with-charset",
        ),
        pytest.param({"json": {"quantity": 2}}, id="json-number"),
        pytest.param({"json": {"quantity": "4"}}, id="json-string"),
        pytest.param({}, id="no-body"),
        pytest.param({"json": {}}, id="json-without-quantity"),
    ],
)
def test_create_checkout_session_redirects_303(
    http: requests.Session, base_url: str, checkout_url: str, kwargs: dict
) -> None:
    response = http.post(
        f"{base_url}/create-checkout-session",
        timeout=TIMEOUT,
        allow_redirects=False,
        **kwargs,
    )

    assert response.status_code == 303
    assert response.headers["Location"] == checkout_url


@pytest.mark.parametrize(
    ("accept", "media_type"),
    [
        pytest.param("*/*", "text/plain", id="any"),
        pytest.param(BROWSER_ACCEPT, "text/html", id="browser"),
    ],
)
def test_create_checkout_session_redirect_body_content_type(
    http: requests.Session, base_url: str, accept: str, media_type: str
) -> None:
    response = http.post(
        f"{base_url}/create-checkout-session",
        data={"quantity": "1"},
        headers={"Accept": accept},
        timeout=TIMEOUT,
        allow_redirects=False,
    )

    assert response.status_code == 303
    assert_content_type(response, media_type, "utf-8")


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"data": {"quantity": "abc"}}, id="form-not-a-number"),
        pytest.param({"json": {"quantity": "abc"}}, id="json-not-a-number"),
    ],
)
def test_create_checkout_session_stripe_error_is_502(
    http: requests.Session, base_url: str, kwargs: dict
) -> None:
    response = http.post(
        f"{base_url}/create-checkout-session",
        timeout=TIMEOUT,
        allow_redirects=False,
        **kwargs,
    )

    assert response.status_code == 502
    body = assert_json(response)
    assert set(body) == {"error"}
    assert isinstance(body["error"], str)


# --- Unknown routes and methods ----------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/nope"),
        ("GET", "/does/not/exist.html"),
        ("GET", "/css/missing.css"),
        ("POST", "/nope"),
        ("POST", "/index.html"),
        ("PUT", "/css/global.css"),
        ("POST", "/"),
        ("GET", "/webhook"),
        ("POST", "/health"),
        ("GET", "/create-checkout-session"),
        ("DELETE", "/checkout-session"),
    ],
)
def test_unknown_route_or_method_is_404(
    http: requests.Session, base_url: str, method: str, path: str
) -> None:
    response = http.request(method, f"{base_url}{path}", timeout=TIMEOUT, allow_redirects=False)

    assert response.status_code == 404
    assert_content_type(response, "text/html", "utf-8")
