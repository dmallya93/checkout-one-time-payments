"""Browser tests for the static client in ``client/html`` served by the Python server."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import Page, Request, Route, expect

if TYPE_CHECKING:
    from conftest import StripeMock

SESSION_ID = "cs_test_e2eSuccessPage0001"


@pytest.fixture
def store(page: Page) -> Page:
    page.goto("/")
    return page


# --- index.html -----------------------------------------------------------------


def test_page_loads(store: Page) -> None:
    expect(store).to_have_title("Stripe Checkout Sample")
    expect(store.get_by_role("heading", name="Single photo")).to_be_visible()
    expect(store.get_by_role("button", name="Buy")).to_be_visible()
    expect(store.locator("#quantity-input")).to_have_value("1")
    expect(store.locator("#subtract")).to_be_disabled()
    expect(store.locator("#add")).to_be_enabled()


def test_add_increments_and_disables_at_max(store: Page) -> None:
    quantity = store.locator("#quantity-input")
    add = store.locator("#add")
    subtract = store.locator("#subtract")

    add.click()
    expect(quantity).to_have_value("2")
    expect(subtract).to_be_enabled()
    expect(add).to_be_enabled()

    for expected in range(3, 11):
        add.click()
        expect(quantity).to_have_value(str(expected))
    expect(add).to_be_disabled()
    expect(subtract).to_be_enabled()


def test_subtract_decrements_and_disables_at_min(store: Page) -> None:
    quantity = store.locator("#quantity-input")
    add = store.locator("#add")
    subtract = store.locator("#subtract")

    for _ in range(9):
        add.click()
    expect(quantity).to_have_value("10")
    expect(add).to_be_disabled()

    subtract.click()
    expect(quantity).to_have_value("9")
    expect(add).to_be_enabled()
    expect(subtract).to_be_enabled()

    for expected in range(8, 0, -1):
        subtract.click()
        expect(quantity).to_have_value(str(expected))
    expect(subtract).to_be_disabled()
    expect(add).to_be_enabled()

    add.click()
    expect(quantity).to_have_value("2")
    expect(subtract).to_be_enabled()


@pytest.mark.parametrize(
    ("typed", "expected"),
    [("0", "1"), ("-3", "1"), ("25", "10"), ("11", "10"), ("1", "1"), ("7", "7"), ("10", "10")],
)
def test_typed_quantity_is_clamped_on_change(store: Page, typed: str, expected: str) -> None:
    quantity = store.locator("#quantity-input")
    quantity.fill(typed)
    quantity.blur()  # fires the input's "change" event
    expect(quantity).to_have_value(expected)


# --- checkout redirect ------------------------------------------------------------
#
# Playwright never routes the follow-up request of an HTTP redirect (its Chromium
# network layer auto-continues requests that have ``redirectedFrom``), so the
# server's 303 cannot be fulfilled by ``page.route`` on the way to Checkout. The
# flow is therefore covered in two steps:
#
# 1. The real redirect: the browser posts the form, gets the server's 303 and
#    follows it natively to the Checkout URL. The Checkout host is unresolvable in
#    the test browser (see ``conftest.CHECKOUT_HOST_RULES``), so that navigation fails
#    without ever reaching Stripe and the test asserts what the browser attempted.
# 2. The stubbed Checkout page: the POST is sent to the server without following
#    redirects (``route.fetch``), its 303 is checked, and the browser is handed the
#    ``Location`` as a plain navigation, which ``page.route`` can fulfil with a stub.

STUB_CHECKOUT_HTML = "<!DOCTYPE html><title>Stub Checkout</title><h1>Stub Checkout</h1>"


def _expected_checkout_url(stripe_mock: StripeMock) -> str:
    # stripe-mock answers every session with the same fixture URL; read it from
    # stripe-mock itself so the test does not depend on its version.
    session = stripe_mock.post("/v1/checkout/sessions", {"mode": "payment"})
    return str(session["url"])


def _is_create_session(request: Request) -> bool:
    return urlsplit(request.url).path == "/create-checkout-session"


def _assert_form_post(request: Request, quantity: str) -> None:
    assert request.method == "POST"
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    assert parse_qs(request.post_data or "") == {"quantity": [quantity]}


@pytest.mark.parametrize(("clicks", "quantity"), [(0, "1"), (2, "3"), (9, "10")])
def test_buy_posts_quantity_and_follows_303_to_checkout(
    store: Page, stripe_mock: StripeMock, clicks: int, quantity: str
) -> None:
    checkout_url = _expected_checkout_url(stripe_mock)
    for _ in range(clicks):
        store.locator("#add").click()
    expect(store.locator("#quantity-input")).to_have_value(quantity)

    with (
        store.expect_request(_is_create_session) as request_info,
        store.expect_event(
            "requestfailed", lambda r: r.url == checkout_url and r.is_navigation_request()
        ) as followed_info,
    ):
        store.get_by_role("button", name="Buy").click()

    request = request_info.value
    _assert_form_post(request, quantity)
    response = request.response()
    assert response is not None
    assert response.status == 303
    assert response.headers["location"] == checkout_url

    # The browser followed the 303 with a GET to stripe-mock's Checkout URL.
    followed = followed_info.value
    assert request.redirected_to is not None
    assert request.redirected_to.url == checkout_url
    assert followed.method == "GET"
    assert followed.redirected_from is not None
    assert urlsplit(followed.redirected_from.url).path == "/create-checkout-session"


@pytest.mark.parametrize(("clicks", "quantity"), [(0, "1"), (4, "5")])
def test_buy_lands_on_stubbed_checkout_page(
    store: Page, stripe_mock: StripeMock, clicks: int, quantity: str
) -> None:
    checkout_url = _expected_checkout_url(stripe_mock)
    checkout_requests: list[str] = []
    server_responses: list[tuple[int, str | None]] = []

    def fulfil_checkout(route: Route) -> None:
        checkout_requests.append(route.request.url)
        route.fulfill(status=200, content_type="text/html", body=STUB_CHECKOUT_HTML)

    def hand_off_redirect(route: Route) -> None:
        server_response = route.fetch(max_redirects=0)
        location = server_response.headers.get("location")
        server_responses.append((server_response.status, location))
        if server_response.status != 303 or not location:
            route.fulfill(response=server_response)
            return
        route.fulfill(
            status=200,
            content_type="text/html",
            body=f"<!DOCTYPE html><script>location.replace({json.dumps(location)})</script>",
        )

    checkout_origin = "{0.scheme}://{0.netloc}".format(urlsplit(checkout_url))
    store.route(f"{checkout_origin}/**", fulfil_checkout)
    store.route("**/create-checkout-session", hand_off_redirect)

    for _ in range(clicks):
        store.locator("#add").click()
    expect(store.locator("#quantity-input")).to_have_value(quantity)

    with store.expect_request(_is_create_session) as request_info:
        store.get_by_role("button", name="Buy").click()
    store.wait_for_url(checkout_url)

    _assert_form_post(request_info.value, quantity)
    assert server_responses == [(303, checkout_url)]
    assert checkout_requests == [checkout_url]
    assert store.url == checkout_url
    expect(store).to_have_title("Stub Checkout")
    expect(store.get_by_role("heading", name="Stub Checkout")).to_be_visible()


# --- success.html -----------------------------------------------------------------


def test_success_page_renders_session_json(page: Page) -> None:
    with page.expect_response(
        lambda r: urlsplit(r.url).path == "/checkout-session"
    ) as response_info:
        page.goto(f"/success.html?session_id={SESSION_ID}")
    response = response_info.value
    assert response.status == 200
    assert parse_qs(urlsplit(response.url).query) == {"sessionId": [SESSION_ID]}

    expect(page.get_by_role("heading", name="Your payment succeeded")).to_be_visible()
    pre = page.locator("pre")
    expect(pre).not_to_be_empty()
    session = json.loads(pre.text_content() or "")
    assert session["object"] == "checkout.session"
    assert session["id"] == SESSION_ID
    # success.js pretty-prints with two-space indentation.
    assert (pre.text_content() or "") == json.dumps(session, indent=2, ensure_ascii=False)


def test_success_page_without_session_id_leaves_pre_empty(page: Page) -> None:
    session_requests: list[str] = []
    page.on(
        "request",
        lambda r: (
            session_requests.append(r.url) if urlsplit(r.url).path == "/checkout-session" else None
        ),
    )
    page.goto("/success.html")
    page.wait_for_load_state("networkidle")

    expect(page.get_by_role("heading", name="Your payment succeeded")).to_be_visible()
    expect(page.locator("pre")).to_have_text("")
    assert session_requests == []


def test_restart_demo_returns_to_store(page: Page, base_url: str) -> None:
    page.goto(f"/success.html?session_id={SESSION_ID}")
    page.get_by_role("button", name="Restart demo").click()
    page.wait_for_url(f"{base_url}/")
    expect(page.get_by_role("heading", name="Single photo")).to_be_visible()
    expect(page.locator("#quantity-input")).to_have_value("1")
