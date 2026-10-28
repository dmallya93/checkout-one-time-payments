"""Browser end-to-end tests (Playwright for Python) against the running Python server.

The client in ``client/html`` is used unchanged. Images (picsum.photos) are blocked so the
tests never depend on them; Stripe.js still loads from js.stripe.com. Chromium is launched
headless by the ``browser`` fixture below, so the pytest-playwright plugin is not needed.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import requests

from .conftest import REQUEST_TIMEOUT, LiveServer, extract_session_id

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Page, Route

pytestmark = [pytest.mark.live, pytest.mark.e2e]

CHECKOUT_URL = re.compile(r"^https://checkout\.stripe\.com/")
NAVIGATION_TIMEOUT_MS = 60_000
EXPECT_TIMEOUT_MS = 15_000
MIN_PHOTOS = 1
MAX_PHOTOS = 10


@pytest.fixture(scope="module")
def playwright_api(python_server: LiveServer) -> Any:
    # Depends on python_server first, so a missing key is the reported skip reason.
    return pytest.importorskip(
        "playwright.sync_api", reason="playwright not installed: E2E tests skipped"
    )


@pytest.fixture(scope="module")
def browser(playwright_api: Any) -> Iterator[Browser]:
    with playwright_api.sync_playwright() as playwright:
        executable = playwright.chromium.executable_path
        if not executable or not Path(executable).exists():
            pytest.skip(
                "Playwright Chromium not installed (run `playwright install chromium`): "
                "E2E tests skipped"
            )
        browser = playwright.chromium.launch(headless=True)
        try:
            yield browser
        finally:
            browser.close()


def _block_images(route: Route) -> None:
    if route.request.resource_type == "image":
        route.abort()
    else:
        route.continue_()


@pytest.fixture
def page(browser: Browser, playwright_api: Any) -> Iterator[Page]:
    playwright_api.expect.set_options(timeout=EXPECT_TIMEOUT_MS)
    context = browser.new_context()
    context.set_default_navigation_timeout(NAVIGATION_TIMEOUT_MS)
    context.route("**/*", _block_images)
    page = context.new_page()
    try:
        yield page
    finally:
        context.close()


@pytest.fixture
def expect(playwright_api: Any) -> Any:
    return playwright_api.expect


def test_quantity_buttons_respect_bounds(
    page: Page, expect: Any, python_server: LiveServer
) -> None:
    page.goto(python_server.url("/"), wait_until="domcontentloaded")
    quantity = page.locator("#quantity-input")
    add = page.locator("#add")
    subtract = page.locator("#subtract")

    expect(page.locator("h1")).to_have_text("Single photo")
    expect(quantity).to_have_value(str(MIN_PHOTOS))
    expect(subtract).to_be_disabled()
    expect(add).to_be_enabled()

    for value in range(MIN_PHOTOS + 1, MAX_PHOTOS + 1):
        add.click()
        expect(quantity).to_have_value(str(value))
        expect(subtract).to_be_enabled()
    expect(add).to_be_disabled()

    for value in range(MAX_PHOTOS - 1, MIN_PHOTOS - 1, -1):
        subtract.click()
        expect(quantity).to_have_value(str(value))
        expect(add).to_be_enabled()
    expect(subtract).to_be_disabled()


def test_buy_lands_on_stripe_checkout(page: Page, expect: Any, python_server: LiveServer) -> None:
    page.goto(python_server.url("/"), wait_until="domcontentloaded")
    quantity = page.locator("#quantity-input")
    page.locator("#add").click()
    page.locator("#add").click()
    expect(quantity).to_have_value("3")

    page.locator("#submit").click()
    page.wait_for_url(CHECKOUT_URL, timeout=NAVIGATION_TIMEOUT_MS, wait_until="commit")

    # The form posted quantity=3, so the session Stripe created is for 3 x $5.00.
    session_id = extract_session_id(page.url)
    resp = requests.get(
        python_server.url("/checkout-session"),
        params={"sessionId": session_id},
        timeout=REQUEST_TIMEOUT,
    )
    assert resp.status_code == 200, resp.text[:500]
    session = resp.json()
    assert session["id"] == session_id
    assert session["amount_total"] == 1500


def test_success_page_renders_session_and_restarts(
    page: Page, expect: Any, python_server: LiveServer
) -> None:
    _, session_id = python_server.create_session(quantity=2)

    page.goto(
        python_server.url(f"/success.html?session_id={session_id}"),
        wait_until="domcontentloaded",
    )
    pre = page.locator("pre")
    expect(pre).to_contain_text(session_id)
    session = json.loads(pre.text_content() or "")
    assert session["id"] == session_id
    assert session["object"] == "checkout.session"

    restart = page.get_by_role("button", name="Restart demo")
    expect(restart).to_be_visible()
    # The long session JSON widens the flex-centred layout of the (unchanged) client, which can
    # push the button to a negative x offset that scrolling cannot reach, so dispatch the click.
    restart.dispatch_event("click")
    page.wait_for_url(python_server.url("/"), timeout=NAVIGATION_TIMEOUT_MS)
    expect(page.locator("h1")).to_have_text("Single photo")
