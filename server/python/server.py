"""Stripe Checkout one-time payments sample: Python / Flask server.

A one-to-one translation of the original Express server (removed after the parity gate).
It serves the static client in ``client/html``, creates and retrieves Stripe-hosted
Checkout Sessions, reports health through the Balance API and receives webhooks.

Run it with ``python server.py`` or, optionally, ``gunicorn "server:create_app()"``.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from html import escape
from http import HTTPStatus
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import quote

import stripe
from dotenv import load_dotenv
from flask import Flask, Response, redirect, request, send_from_directory
from werkzeug.exceptions import HTTPException
from werkzeug.security import safe_join
from werkzeug.wrappers import Response as BaseResponse

SERVER_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 4242
DEFAULT_STATIC_DIR = "../../client/html"
# Express' ``app.listen(PORT)`` binds every interface (dual-stack ``::``). Werkzeug is
# bound to every IPv4 interface instead, because ``::`` fails on hosts without IPv6.
LISTEN_HOST = "0.0.0.0"
# Matches the default ``limit: '100kb'`` of ``express.json()`` / ``express.urlencoded()``.
BODY_LIMIT_BYTES = 100 * 1024

SECRET_KEY_PATTERN = re.compile(r"(sk|rk)_(test|live)_[A-Za-z0-9]{10,}")

APP_INFO: dict[str, str] = {
    "name": "stripe-samples/checkout-one-time-payments",
    "version": "0.0.1",
    "url": "https://github.com/stripe-samples/checkout-one-time-payments",
}

# A product with a fixed price, so no Price ID has to be created first.
LINE_ITEM_PRICE: dict[str, Any] = {
    "currency": "usd",
    "unit_amount": 500,
    "product_data": {"name": "Stubborn Attachments (photo)"},
}

# ``{CHECKOUT_SESSION_ID}`` is a literal placeholder that Stripe fills in; it is
# deliberately a plain string (not an f-string) so Python never formats it.
SUCCESS_PATH = "/success.html?session_id={CHECKOUT_SESSION_ID}"

JSON_CONTENT_TYPE = "application/json; charset=utf-8"
TEXT_CONTENT_TYPE = "text/plain; charset=utf-8"
HTML_CONTENT_TYPE = "text/html; charset=utf-8"
# Express' ``send`` labels text, JavaScript and JSON static files with an upper-case
# ``charset=UTF-8`` (``mime.charsets.lookup``).
STATIC_CHARSET_PATTERN = re.compile(r"text/.*|application/(javascript|json)")

# Media types that Express (``send``/``mime`` 1.x) uses where Python's
# ``mimetypes`` table differs.
STATIC_MIMETYPE_OVERRIDES: dict[str, str] = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".ico": "image/x-icon",
}


@dataclass(frozen=True)
class Config:
    """Server settings, read from ``server/python/.env`` or the environment."""

    port: int
    domain: str
    static_dir: str
    secret_key: str | None
    webhook_secret: str | None = None
    # Test-only: points the Stripe client at stripe-mock. Unset in production.
    api_base: str | None = None


def load_config(env_file: Path | None = None) -> Config:
    """Load ``.env`` (without overriding real env vars) and build a :class:`Config`.

    Empty values fall back to the defaults, like ``process.env.X || default`` in Node.
    """
    load_dotenv(env_file or SERVER_DIR / ".env", override=False)

    port = int(os.environ.get("PORT") or DEFAULT_PORT)
    domain = os.environ.get("DOMAIN") or f"http://localhost:{port}"
    static_dir = (SERVER_DIR / (os.environ.get("STATIC_DIR") or DEFAULT_STATIC_DIR)).resolve()
    return Config(
        port=port,
        domain=domain,
        static_dir=str(static_dir),
        secret_key=os.environ.get("STRIPE_SECRET_KEY") or None,
        webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET") or None,
        api_base=os.environ.get("STRIPE_API_BASE") or None,
    )


def is_valid_secret_key(secret_key: str | None) -> bool:
    """Return True for ``sk_``/``rk_`` test or live keys (same regex as the Node server)."""
    return secret_key is not None and SECRET_KEY_PATTERN.fullmatch(secret_key) is not None


def require_valid_secret_key(config: Config) -> str:
    """Return the secret key, or print the Node server's message and exit with code 1."""
    if not is_valid_secret_key(config.secret_key):
        _fail("STRIPE_SECRET_KEY is not set.")
    assert config.secret_key is not None
    return config.secret_key


def build_stripe_client(config: Config) -> stripe.StripeClient:
    """Create the ``StripeClient`` with the sample's app info.

    stripe-python sends app info from the module-level setting, so it is set here
    right before the client is created.
    """
    stripe.set_app_info(APP_INFO["name"], version=APP_INFO["version"], url=APP_INFO["url"])
    if config.api_base:
        return stripe.StripeClient(
            config.secret_key or "",
            base_addresses={"api": config.api_base},
        )
    return stripe.StripeClient(config.secret_key or "")


def run_startup_check(stripe_client: stripe.StripeClient) -> None:
    """Call the Balance API before serving; on failure print the error and exit 1."""
    try:
        stripe_client.v1.balance.retrieve()
    except Exception as err:  # noqa: BLE001 - Node's startup ``.catch`` handles any error
        _fail(f"Startup failed: {error_message(err)}")


def bootstrap(
    stripe_client: stripe.StripeClient | None = None,
) -> tuple[Config, stripe.StripeClient]:
    """Load the config, validate the key, build the client and run the Balance check."""
    config = load_config()
    require_valid_secret_key(config)
    if stripe_client is None:
        stripe_client = build_stripe_client(config)
    run_startup_check(stripe_client)
    return config, stripe_client


def error_message(err: BaseException) -> str:
    """Return the error text Node would expose as ``err.message``.

    ``str(StripeError)`` prefixes the request ID, so the bare ``user_message`` is
    preferred when it is available.
    """
    if isinstance(err, stripe.StripeError) and err.user_message:
        return str(err.user_message)
    return str(err)


def create_app(
    config: Config | None = None,
    stripe_client: stripe.StripeClient | None = None,
) -> Flask:
    """Build the Flask app.

    With no ``config`` (e.g. ``gunicorn "server:create_app()"``) the configuration is
    loaded, the key validated and the Balance startup check run, as ``main()`` does.
    """
    if config is None:
        config, stripe_client = bootstrap(stripe_client)
    elif stripe_client is None:
        stripe_client = build_stripe_client(config)

    client: stripe.StripeClient = stripe_client
    app = Flask(__name__, static_folder=config.static_dir, static_url_path="")
    # Express routing is not strict about trailing slashes: ``/health/`` matches.
    app.url_map.strict_slashes = False
    # Reject bodies larger than Express' default 100kb (``express.json``/``urlencoded``).
    app.config["MAX_CONTENT_LENGTH"] = BODY_LIMIT_BYTES
    app.config["STRIPE_CLIENT"] = client
    app.config["APP_CONFIG"] = config

    @app.before_request
    def serve_static_first() -> BaseResponse | None:
        """Serve existing static files before any route, like ``express.static`` mounted first."""
        return _serve_static(config.static_dir)

    @app.get("/")
    def index() -> Response:
        return _send_static(config.static_dir, "index.html")

    @app.get("/health")
    def health() -> Response:
        try:
            balance = client.v1.balance.retrieve()
        except stripe.StripeError as err:
            return json_response({"status": "error", "error": error_message(err)}, 503)
        return json_response({"status": "ok", "livemode": balance.livemode})

    # Fetch the Checkout Session to display the JSON result on the success page
    @app.get("/checkout-session")
    def checkout_session() -> Response:
        session_id = request.args.get("sessionId")
        try:
            if not session_id:
                # stripe-node rejects a missing ID itself; stripe-python needs a guard.
                raise ValueError(
                    'Argument "session" must be a non-empty string, '
                    f"but got: {'undefined' if session_id is None else repr(session_id)}"
                )
            session = client.v1.checkout.sessions.retrieve(session_id)
        except (stripe.StripeError, ValueError) as err:
            return json_response({"error": error_message(err)}, 400)
        return json_response(session.to_dict())

    @app.post("/create-checkout-session")
    def create_checkout_session() -> BaseResponse:
        line_item: dict[str, Any] = {"price_data": LINE_ITEM_PRICE}
        body = _request_body()
        if "quantity" in body:
            # Passed through unchanged (a string from forms); Stripe validates it.
            line_item["quantity"] = body["quantity"]

        # For full details see https://stripe.com/docs/api/checkout/sessions/create
        params: dict[str, Any] = {
            "mode": "payment",
            "line_items": [line_item],
            # ?session_id={CHECKOUT_SESSION_ID} means the redirect will have the
            # session ID set as a query param
            "success_url": config.domain + SUCCESS_PATH,
        }
        try:
            session = client.v1.checkout.sessions.create(params=params)  # type: ignore[arg-type]
        except stripe.StripeError as err:
            return json_response({"error": error_message(err)}, 502)
        return express_redirect(session.url or "", 303)

    # Webhook handler for asynchronous events.
    @app.post("/webhook")
    def webhook() -> Response:
        # The raw bytes are read first: signatures are computed over the exact body.
        payload = request.get_data()
        webhook_secret = _current_webhook_secret(config)

        event_type: Any
        # Check if webhook signing is configured.
        if webhook_secret:
            signature = request.headers.get("Stripe-Signature")
            try:
                event = client.construct_event(payload, signature, webhook_secret)
            # A validly signed body that is not a JSON object makes stripe-python raise
            # AttributeError/TypeError while building the Event; Express returned 400 here.
            except (stripe.StripeError, ValueError, AttributeError, TypeError):
                _log("⚠️  Webhook signature verification failed.")
                return text_response("Bad Request", 400)
            event_type = event.type
        else:
            body = request.get_json(silent=True)
            event_type = body.get("type") if isinstance(body, dict) else None

        if event_type == "checkout.session.completed":
            _log("🔔  Payment received!")

        return text_response("OK", 200)

    @app.errorhandler(404)
    @app.errorhandler(405)
    def not_found(_err: HTTPException) -> Response:
        """Express answers unknown routes and unsupported methods with the same 404 page."""
        return express_not_found()

    @app.errorhandler(413)
    def payload_too_large(_err: HTTPException) -> Response:
        """Express' body-parser answered oversized bodies with its finalhandler 413 page."""
        return express_error_page(413, "PayloadTooLargeError: request entity too large")

    return app


def json_response(payload: Any, status: int = 200) -> Response:
    """Compact JSON with insertion-ordered keys, like Express ``res.send(obj)``."""
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=str)
    return Response(body, status=status, content_type=JSON_CONTENT_TYPE)


def text_response(body: str, status: int) -> Response:
    """Plain-text body, like Express ``res.sendStatus(status)``."""
    return Response(body, status=status, content_type=TEXT_CONTENT_TYPE)


def express_redirect(location: str, status: int) -> BaseResponse:
    """Redirect like Express ``res.redirect(status, url)``.

    Express negotiates the body on ``Accept``: plain text for ``*/*`` or no ``Accept``
    header, a ``<p>`` for HTML clients, and an empty body without a ``Content-Type``
    otherwise.
    """
    response = redirect(location, code=status)
    reason = HTTPStatus(status).phrase
    accept = request.accept_mimetypes
    best = accept.best_match(["text/plain", "text/html"]) if accept else "text/plain"
    if best == "text/plain":
        response.set_data(f"{reason}. Redirecting to {location}")
        response.content_type = TEXT_CONTENT_TYPE
    elif best == "text/html":
        response.set_data(f"<p>{reason}. Redirecting to {escape(location, quote=True)}</p>")
        response.content_type = HTML_CONTENT_TYPE
    else:
        response.set_data(b"")
        del response.headers["Content-Type"]
    response.headers["Vary"] = "Accept"
    return response


def express_not_found() -> Response:
    """Reproduce Express' (finalhandler) default 404 page."""
    path = quote(request.path, safe="/!$&'()*+,;=:@~%")
    message = escape(f"Cannot {request.method} {path}", quote=True).replace("  ", " &nbsp;")
    return express_error_page(404, message, escaped=True)


def express_error_page(status: int, message: str, *, escaped: bool = False) -> Response:
    """Reproduce Express' (finalhandler) default HTML error page for ``status``."""
    text = message if escaped else escape(message, quote=True)
    body = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        "<title>Error</title>\n"
        "</head>\n"
        "<body>\n"
        f"<pre>{text}</pre>\n"
        "</body>\n"
        "</html>\n"
    )
    response = Response(body, status=status, content_type=HTML_CONTENT_TYPE)
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def _serve_static(static_dir: str) -> BaseResponse | None:
    """Return a response for an existing static file, or None to continue routing.

    Mirrors ``express.static`` defaults: only GET/HEAD, dotfiles ignored, directories
    redirected to a trailing slash and served via their ``index.html``.
    """
    if request.method not in ("GET", "HEAD"):
        return None
    rel_path = request.path.lstrip("/")
    parts = [part for part in rel_path.split("/") if part]
    if any(part.startswith(".") for part in parts):
        return express_not_found()
    full_path = safe_join(static_dir, rel_path) if rel_path else static_dir
    if full_path is None:
        return None
    if os.path.isdir(full_path):
        if not request.path.endswith("/"):
            query = request.query_string.decode("latin-1")
            target = request.path + "/" + (f"?{query}" if query else "")
            return redirect(target, code=301)
        index_rel = f"{rel_path}index.html" if rel_path else "index.html"
        if os.path.isfile(os.path.join(full_path, "index.html")):
            return _send_static(static_dir, index_rel)
        return None
    if os.path.isfile(full_path):
        return _send_static(static_dir, rel_path)
    return None


def _send_static(static_dir: str, rel_path: str) -> Response:
    mimetype = STATIC_MIMETYPE_OVERRIDES.get(Path(rel_path).suffix.lower())
    response = send_from_directory(static_dir, rel_path, mimetype=mimetype)
    if STATIC_CHARSET_PATTERN.fullmatch(response.mimetype or ""):
        response.content_type = f"{response.mimetype}; charset=UTF-8"
    return response


def _request_body() -> dict[str, Any]:
    """Return the parsed body like Express' json/urlencoded parsers (``{}`` otherwise)."""
    if request.is_json:
        body = request.get_json(silent=True)
        return body if isinstance(body, dict) else {}
    if request.mimetype == "application/x-www-form-urlencoded":
        return request.form.to_dict()
    return {}


def _current_webhook_secret(config: Config) -> str | None:
    """Read ``STRIPE_WEBHOOK_SECRET`` at request time, as Node reads ``process.env``.

    When the variable is not in the environment at all, the value from ``config``
    is used (useful for tests and embedding).
    """
    if "STRIPE_WEBHOOK_SECRET" in os.environ:
        return os.environ["STRIPE_WEBHOOK_SECRET"] or None
    return config.webhook_secret


def _log(message: str) -> None:
    """Write to stdout like ``console.log``."""
    print(message, flush=True)


def _fail(message: str) -> NoReturn:
    """Write to stderr like ``console.error`` and exit with code 1."""
    print(message, file=sys.stderr, flush=True)
    sys.exit(1)


def main() -> None:
    """Entry point for ``python server.py``."""
    config, stripe_client = bootstrap()
    app = create_app(config, stripe_client)
    _log(f"Python server listening on port {config.port}!")
    app.run(host=LISTEN_HOST, port=config.port)


if __name__ == "__main__":
    main()
