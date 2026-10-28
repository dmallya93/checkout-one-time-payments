"""Black-box parity tests against the running servers, in real Stripe test mode.

Every case is a plain HTTP request (``requests``, no redirects followed) that runs against
each server in ``PARITY_TARGETS``. The per-target tests check the HTTP contract; when both
``node`` and ``python`` are targets, ``test_node_and_python_agree`` also runs every case on
both and compares status codes, content-type families, redirect hosts and JSON key sets
(never message text, and never the startup log lines, which differ on purpose).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import pytest
import requests

from .conftest import PARITY_TARGETS, REQUEST_TIMEOUT, LiveServer, ServerPool

pytestmark = pytest.mark.live

CHECKOUT_HOST = "checkout.stripe.com"
SUCCESS_PATH = "/success.html?session_id={CHECKOUT_SESSION_ID}"
PAYMENT_LOG = "🔔  Payment received!"
BAD_SIGNATURE_LOG = "⚠️  Webhook signature verification failed."
SESSION_KEYS = {"id", "object", "url", "success_url", "amount_total", "currency", "mode"}

# Express (mime 1.x) and Flask (Python's mimetypes) name some types differently.
_CONTENT_TYPE_ALIASES = {
    "application/javascript": "javascript",
    "application/x-javascript": "javascript",
    "text/javascript": "javascript",
    "image/x-icon": "icon",
    "image/vnd.microsoft.icon": "icon",
}


def content_type_family(header: str | None) -> str:
    mime = (header or "").split(";", 1)[0].strip().lower()
    return _CONTENT_TYPE_ALIASES.get(mime, mime)


def completed_event(event_type: str = "checkout.session.completed") -> bytes:
    event = {
        "id": "evt_test_parity",
        "object": "event",
        "type": event_type,
        "data": {"object": {"id": "cs_test_parity", "object": "checkout.session"}},
    }
    return json.dumps(event).encode()


# --- the request cases ----------------------------------------------------------------


Sender = Callable[[LiveServer, str], requests.Response]


@dataclass(frozen=True)
class Case:
    # Which webhook configuration the case needs (see conftest).
    signed: bool
    send: Sender


def _get(path: str) -> Sender:
    def send(server: LiveServer, _session_id: str) -> requests.Response:
        return requests.get(server.url(path), allow_redirects=False, timeout=REQUEST_TIMEOUT)

    return send


def _post(path: str, **kwargs: object) -> Sender:
    def send(server: LiveServer, _session_id: str) -> requests.Response:
        return requests.post(
            server.url(path), allow_redirects=False, timeout=REQUEST_TIMEOUT, **kwargs
        )

    return send


def _checkout_session(fixed_id: str | None = None) -> Sender:
    """GET /checkout-session with the case's session id, or with ``fixed_id`` if given."""

    def send(server: LiveServer, session_id: str) -> requests.Response:
        return requests.get(
            server.url("/checkout-session"),
            params={"sessionId": fixed_id or session_id},
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        )

    return send


def _webhook(payload: bytes, signature: str | None) -> Sender:
    """``signature`` is "valid" (signed with the server's secret), a literal header, or None."""

    def send(server: LiveServer, _session_id: str) -> requests.Response:
        headers = {"Content-Type": "application/json"}
        if signature == "valid":
            headers["Stripe-Signature"] = server.sign_webhook(payload)
        elif signature is not None:
            headers["Stripe-Signature"] = signature
        return requests.post(
            server.url("/webhook"),
            data=payload,
            headers=headers,
            allow_redirects=False,
            timeout=REQUEST_TIMEOUT,
        )

    return send


BAD_SIGNATURE = "t=1700000000,v1=" + "0" * 64
BOGUS_SESSION_ID = "cs_test_doesnotexist0000"
OTHER_EVENT = completed_event("payment_intent.created")

CASES: dict[str, Case] = {
    "index": Case(True, _get("/")),
    "static-index-js": Case(True, _get("/index.js")),
    "static-global-css": Case(True, _get("/css/global.css")),
    "static-success-html": Case(True, _get("/success.html")),
    "static-favicon": Case(True, _get("/favicon.ico")),
    "health": Case(True, _get("/health")),
    "create-form": Case(True, _post("/create-checkout-session", data={"quantity": "2"})),
    "create-json": Case(True, _post("/create-checkout-session", json={"quantity": 2})),
    "create-form-missing-quantity": Case(True, _post("/create-checkout-session", data={})),
    "create-json-missing-quantity": Case(True, _post("/create-checkout-session", json={})),
    "checkout-session": Case(True, _checkout_session()),
    "checkout-session-bogus-id": Case(True, _checkout_session(BOGUS_SESSION_ID)),
    "checkout-session-missing-id": Case(True, _get("/checkout-session")),
    "webhook-signed-completed": Case(True, _webhook(completed_event(), "valid")),
    "webhook-signed-other-event": Case(True, _webhook(OTHER_EVENT, "valid")),
    "webhook-bad-signature": Case(True, _webhook(completed_event(), BAD_SIGNATURE)),
    "webhook-missing-signature": Case(True, _webhook(completed_event(), None)),
    "webhook-unsigned-completed": Case(False, _webhook(completed_event(), None)),
    "webhook-unsigned-empty-body": Case(False, _post("/webhook")),
}


def send(name: str, server: LiveServer, session_id: str = "") -> requests.Response:
    case = CASES[name]
    config = "signed" if case.signed else "unsigned"
    assert case.signed == (server.webhook_secret is not None), f"{name} needs a {config} server"
    return case.send(server, session_id)


def assert_json_error(resp: requests.Response, status: int) -> None:
    assert resp.status_code == status, resp.text[:500]
    assert content_type_family(resp.headers.get("Content-Type")) == "application/json"
    body = resp.json()
    assert isinstance(body, dict)
    assert set(body) == {"error"}
    assert isinstance(body["error"], str)
    assert body["error"]


def assert_checkout_redirect(resp: requests.Response) -> None:
    assert resp.status_code == 303, resp.text[:500]
    location = resp.headers["Location"]
    parts = urlsplit(location)
    assert parts.scheme == "https"
    assert parts.hostname == CHECKOUT_HOST, location


# --- per-target contract tests --------------------------------------------------------


def test_index_serves_client(live_server: LiveServer) -> None:
    resp = send("index", live_server)
    assert resp.status_code == 200
    assert content_type_family(resp.headers.get("Content-Type")) == "text/html"
    assert "Single photo" in resp.text
    assert resp.content == (live_server.static_dir / "index.html").read_bytes()


@pytest.mark.parametrize(
    ("case", "path", "family"),
    [
        ("static-index-js", "index.js", "javascript"),
        ("static-global-css", "css/global.css", "text/css"),
        ("static-success-html", "success.html", "text/html"),
        ("static-favicon", "favicon.ico", "icon"),
    ],
)
def test_static_assets(live_server: LiveServer, case: str, path: str, family: str) -> None:
    resp = send(case, live_server)
    assert resp.status_code == 200
    assert content_type_family(resp.headers.get("Content-Type")) == family
    assert resp.content == (live_server.static_dir / path).read_bytes()


def test_health_reports_test_mode(live_server: LiveServer) -> None:
    resp = send("health", live_server)
    assert resp.status_code == 200
    assert content_type_family(resp.headers.get("Content-Type")) == "application/json"
    body = resp.json()
    assert set(body) == {"status", "livemode"}
    assert body["status"] == "ok"
    assert body["livemode"] is False


@pytest.mark.parametrize("case", ["create-form", "create-json"])
def test_create_checkout_session_redirects_to_checkout(live_server: LiveServer, case: str) -> None:
    assert_checkout_redirect(send(case, live_server))


@pytest.mark.parametrize("case", ["create-form-missing-quantity", "create-json-missing-quantity"])
def test_create_checkout_session_without_quantity_is_502(
    live_server: LiveServer, case: str
) -> None:
    assert_json_error(send(case, live_server), 502)


def test_checkout_session_returns_the_session(live_server: LiveServer) -> None:
    created, session_id = live_server.create_session(quantity=2)
    assert_checkout_redirect(created)

    resp = send("checkout-session", live_server, session_id)
    assert resp.status_code == 200, resp.text[:500]
    assert content_type_family(resp.headers.get("Content-Type")) == "application/json"
    session = resp.json()
    assert set(session) >= SESSION_KEYS
    assert session["id"] == session_id
    assert session["object"] == "checkout.session"
    assert urlsplit(session["url"]).hostname == CHECKOUT_HOST
    assert session["success_url"].endswith(SUCCESS_PATH)
    assert session["success_url"] == f"{live_server.domain}{SUCCESS_PATH}"
    assert session["amount_total"] == 1000
    assert session["currency"] == "usd"
    assert session["mode"] == "payment"


def test_checkout_session_with_bogus_id_is_400(live_server: LiveServer) -> None:
    assert_json_error(send("checkout-session-bogus-id", live_server), 400)


def test_checkout_session_without_id_is_400(live_server: LiveServer) -> None:
    assert_json_error(send("checkout-session-missing-id", live_server), 400)


def test_webhook_signed_completed_event(live_server: LiveServer) -> None:
    mark = live_server.mark()
    resp = send("webhook-signed-completed", live_server)
    assert resp.status_code == 200
    assert content_type_family(resp.headers.get("Content-Type")) == "text/plain"
    assert resp.text == "OK"
    logged = live_server.wait_for_output(PAYMENT_LOG, since=mark)
    assert logged, live_server.output(mark)


def test_webhook_signed_other_event(live_server: LiveServer) -> None:
    resp = send("webhook-signed-other-event", live_server)
    assert resp.status_code == 200
    assert content_type_family(resp.headers.get("Content-Type")) == "text/plain"
    assert resp.text == "OK"


@pytest.mark.parametrize("case", ["webhook-bad-signature", "webhook-missing-signature"])
def test_webhook_rejects_bad_signature(live_server: LiveServer, case: str) -> None:
    mark = live_server.mark()
    resp = send(case, live_server)
    assert resp.status_code == 400
    assert content_type_family(resp.headers.get("Content-Type")) == "text/plain"
    assert resp.text == "Bad Request"
    logged = live_server.wait_for_output(BAD_SIGNATURE_LOG, since=mark)
    assert logged, live_server.output(mark)


def test_webhook_unsigned_completed_event(unsigned_live_server: LiveServer) -> None:
    mark = unsigned_live_server.mark()
    resp = send("webhook-unsigned-completed", unsigned_live_server)
    assert resp.status_code == 200
    assert content_type_family(resp.headers.get("Content-Type")) == "text/plain"
    assert resp.text == "OK"
    logged = unsigned_live_server.wait_for_output(PAYMENT_LOG, since=mark)
    assert logged, unsigned_live_server.output(mark)


def test_webhook_unsigned_empty_body(unsigned_live_server: LiveServer) -> None:
    resp = send("webhook-unsigned-empty-body", unsigned_live_server)
    assert resp.status_code == 200
    assert content_type_family(resp.headers.get("Content-Type")) == "text/plain"
    assert resp.text == "OK"


def test_python_startup_line(python_server: LiveServer) -> None:
    # Python-only: Node logs "Node server listening..." on purpose, so the lines are
    # never compared across targets.
    expected = f"Python server listening on port {python_server.port}!"
    logged = python_server.wait_for_output(expected)
    assert logged, python_server.output()


# --- cross-target comparison ----------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    status: int
    content_type: str | None
    location_host: str | None
    json_keys: frozenset[str] | None


def observe(resp: requests.Response) -> Observation:
    if resp.is_redirect:
        # Express and Flask send different redirect bodies; only the target matters.
        host = urlsplit(resp.headers.get("Location", "")).hostname
        return Observation(resp.status_code, None, host, None)
    family = content_type_family(resp.headers.get("Content-Type"))
    keys = None
    if family == "application/json":
        body = resp.json()
        keys = frozenset(body) if isinstance(body, dict) else None
    return Observation(resp.status_code, family, None, keys)


@pytest.mark.skipif(
    not {"node", "python"} <= set(PARITY_TARGETS),
    reason="cross-target parity needs PARITY_TARGETS to include both node and python",
)
def test_node_and_python_agree(server_pool: ServerPool) -> None:
    servers = {
        (target, signed): server_pool.get(target, signed=signed)
        for target in ("node", "python")
        for signed in (True, False)
    }
    # One session, read back through both servers.
    _, session_id = servers["python", True].create_session(quantity=2)

    mismatches = []
    for name, case in CASES.items():
        node = observe(case.send(servers["node", case.signed], session_id))
        python = observe(case.send(servers["python", case.signed], session_id))
        if node != python:
            mismatches.append(f"{name}:\n    node:   {node}\n    python: {python}")
        if name == "create-form":
            assert node.location_host == CHECKOUT_HOST

    assert not mismatches, "node and python differ:\n" + "\n".join(mismatches)
