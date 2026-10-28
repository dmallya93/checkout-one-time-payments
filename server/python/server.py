"""Stripe Checkout one-time payments sample server (Flask)."""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import stripe
import waitress
from dotenv import load_dotenv
from flask import Flask, Response, jsonify, redirect, request, send_from_directory

BASE_DIR = Path(__file__).resolve().parent
SECRET_KEY_PATTERN = re.compile(r"(sk|rk)_(test|live)_[A-Za-z0-9]{10,}")

# A product with a fixed price, so no Price ID has to be created first.
LINE_ITEM_PRICE = {
    "currency": "usd",
    "unit_amount": 500,
    "product_data": {"name": "Stubborn Attachments (photo)"},
}


@dataclass(frozen=True)
class Config:
    port: int
    domain: str
    static_dir: Path
    stripe_secret_key: str
    stripe_webhook_secret: str | None = None


def load_config() -> Config:
    # Settings come from server/python/.env or the environment (the environment wins).
    load_dotenv(BASE_DIR / ".env", override=False)

    port = int(os.environ.get("PORT") or 4242)
    domain = os.environ.get("DOMAIN") or f"http://localhost:{port}"
    static_dir = (BASE_DIR / (os.environ.get("STATIC_DIR") or "../../client/html")).resolve()
    secret_key = os.environ.get("STRIPE_SECRET_KEY") or ""

    if not SECRET_KEY_PATTERN.fullmatch(secret_key):
        print("STRIPE_SECRET_KEY is not set.", file=sys.stderr, flush=True)
        sys.exit(1)

    return Config(
        port=port,
        domain=domain,
        static_dir=static_dir,
        stripe_secret_key=secret_key,
        # Empty means "not configured", like the truthiness check in the Node server.
        stripe_webhook_secret=os.environ.get("STRIPE_WEBHOOK_SECRET") or None,
    )


def create_stripe_client(secret_key: str) -> stripe.StripeClient:
    stripe.set_app_info(
        "stripe-samples/checkout-one-time-payments",
        version="0.0.1",
        url="https://github.com/stripe-samples/checkout-one-time-payments",
    )
    return stripe.StripeClient(secret_key)


def error_message(err: Exception) -> str:
    # Stripe errors carry the API message in user_message; str() adds a request-id prefix.
    return getattr(err, "user_message", None) or str(err)


def create_app(config: Config, stripe_client: stripe.StripeClient) -> Flask:
    static_dir = str(config.static_dir)
    app = Flask(__name__, static_folder=static_dir, static_url_path="")
    # Keep Stripe's key order in JSON responses, as Express does.
    app.json.sort_keys = False

    @app.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @app.get("/health")
    def health():
        try:
            balance = stripe_client.v1.balance.retrieve()
            return jsonify(status="ok", livemode=balance.livemode)
        except Exception as err:
            return jsonify(status="error", error=error_message(err)), 503

    # Fetch the Checkout Session to display the JSON result on the success page
    @app.get("/checkout-session")
    def checkout_session():
        session_id = request.args.get("sessionId")
        try:
            # stripe-python would raise a TypeError for None and request the list URL for "",
            # so reject a missing id up front (Node's SDK rejects `undefined` the same way).
            if not session_id:
                raise ValueError('Argument "session" must be a non-empty string.')
            session = stripe_client.v1.checkout.sessions.retrieve(session_id)
            # Recursive dict with the same keys and nesting as the API response;
            # for_json turns Decimal fields back into the API's decimal strings.
            return jsonify(session.to_dict(for_json=True))
        except Exception as err:
            return jsonify(error=error_message(err)), 400

    @app.post("/create-checkout-session")
    def create_checkout_session():
        if request.is_json:
            body = request.get_json(silent=True)
            quantity = body.get("quantity") if isinstance(body, dict) else None
        else:
            quantity = request.form.get("quantity")

        # ?session_id={CHECKOUT_SESSION_ID} means the redirect will have the session ID
        # set as a query param
        success_url = f"{config.domain}/success.html?session_id={{CHECKOUT_SESSION_ID}}"

        # For full details see https://stripe.com/docs/api/checkout/sessions/create
        try:
            session = stripe_client.v1.checkout.sessions.create(
                params={
                    "mode": "payment",
                    # quantity is passed through as-is; a None value is dropped from the
                    # request (like `undefined` in Node) and Stripe rejects it.
                    "line_items": [{"price_data": LINE_ITEM_PRICE, "quantity": quantity}],
                    "success_url": success_url,
                }
            )
            return redirect(session.url, code=303)
        except Exception as err:
            return jsonify(error=error_message(err)), 502

    # Webhook handler for asynchronous events.
    @app.post("/webhook")
    def webhook():
        # We need the raw body to verify webhook signatures.
        payload = request.get_data()
        # Check if webhook signing is configured.
        if config.stripe_webhook_secret:
            try:
                event = stripe.Webhook.construct_event(
                    payload,
                    request.headers.get("Stripe-Signature"),
                    config.stripe_webhook_secret,
                )
            except Exception:
                # ValueError / SignatureVerificationError, including a missing header.
                print("⚠️  Webhook signature verification failed.", flush=True)
                return Response("Bad Request", 400, mimetype="text/plain")
            event_type = event.type
        else:
            try:
                body = json.loads(payload) if payload else {}
            except ValueError:
                body = {}
            event_type = body.get("type") if isinstance(body, dict) else None

        if event_type == "checkout.session.completed":
            print("🔔  Payment received!", flush=True)

        return Response("OK", 200, mimetype="text/plain")

    return app


def main() -> None:
    config = load_config()
    stripe_client = create_stripe_client(config.stripe_secret_key)
    debug = os.environ.get("FLASK_DEBUG") == "1"
    # With the debug reloader, the child process serves; the parent already checked Stripe.
    reloader_child = debug and os.environ.get("WERKZEUG_RUN_MAIN") == "true"

    if not reloader_child:
        try:
            stripe_client.v1.balance.retrieve()
        except Exception as err:
            print(f"Startup failed: {error_message(err)}", file=sys.stderr, flush=True)
            sys.exit(1)

    app = create_app(config, stripe_client)
    if not reloader_child:
        print(f"Python server listening on port {config.port}!", flush=True)

    if debug:
        app.run(host="0.0.0.0", port=config.port, debug=True)
    else:
        waitress.serve(app, host="0.0.0.0", port=config.port)


if __name__ == "__main__":
    main()
