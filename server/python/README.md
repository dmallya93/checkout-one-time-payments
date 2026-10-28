# Checkout one-time payments: Python / Flask server

A Python 3.13 / Flask 3.1 server for the Stripe Checkout one-time payments sample.
It replaced the sample's original Node/Express server and keeps its HTTP contract,
environment variables and startup checks: it serves the static client in `client/html`, creates
Stripe-hosted Checkout Sessions, returns session details to the success page,
reports health through the Balance API and receives webhooks.

## Requirements

- Python 3.13 (3.12+ works)
- A Stripe account and its [test secret key](https://dashboard.stripe.com/test/apikeys)
- Optional: the [Stripe CLI](https://stripe.com/docs/stripe-cli) to forward webhooks

## Setup

```sh
cd server/python
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # add -r requirements-dev.txt for tests and lint
cp .env.example .env               # then set STRIPE_SECRET_KEY
```

| Variable | Required | Default | Notes |
|---|---|---|---|
| `STRIPE_SECRET_KEY` | yes | | `sk_`/`rk_` test or live key |
| `STRIPE_WEBHOOK_SECRET` | no | | `whsec_...`; without it `/webhook` accepts unsigned JSON. Read at request time. |
| `PORT` | no | `4242` | |
| `DOMAIN` | no | `http://localhost:<PORT>` | Base of the Checkout `success_url` |
| `STATIC_DIR` | no | `../../client/html` | Relative to `server/python/` |
| `STRIPE_API_BASE` | no | | **Test-only** (points the client at stripe-mock). Leave unset otherwise. |

`.env` never overrides variables already set in the real environment.

## Run

```sh
python server.py
```

Open http://localhost:4242 and pay with the test card `4242 4242 4242 4242`
(any future expiry date, any CVC).

The server checks your key and calls the Stripe Balance API before it starts listening.
If the key is missing or malformed it prints `STRIPE_SECRET_KEY is not set.` and exits
with code 1; if the Balance call fails it prints `Startup failed: <message>` and exits 1.

Optional production-style server (runs the same startup checks):

```sh
gunicorn --bind 0.0.0.0:4242 "server:create_app()"
```

### Webhooks

```sh
stripe listen --forward-to localhost:4242/webhook
```

Put the printed `whsec_...` secret in `STRIPE_WEBHOOK_SECRET`. A completed checkout logs
`🔔  Payment received!`.

## Endpoints

| Route | Success | Error |
|---|---|---|
| `GET /` and static files | file from `STATIC_DIR` | 404 |
| `GET /health` | 200 `{"status":"ok","livemode":bool}` | 503 `{"status":"error","error":msg}` |
| `GET /checkout-session?sessionId=` | 200 session JSON | 400 `{"error":msg}` |
| `POST /create-checkout-session` (form or JSON `quantity`) | 303 to the Checkout URL | 502 `{"error":msg}` |
| `POST /webhook` | 200 `OK` | 400 `Bad Request` on signature failure |

## Tests and lint

```sh
pip install -r requirements-dev.txt
ruff check . && ruff format --check .
pytest -q
```

The unit tests use the Flask test client with a mocked `StripeClient`; no network
access or real key is needed.

The HTTP parity suite in [`tests/parity`](../../tests/parity/README.md) runs black-box
tests of the HTTP contract against this server, pointed at stripe-mock:
`bash tests/parity/run_parity.sh` from the repository root.

The Playwright browser tests in [`tests/e2e`](../../tests/e2e/README.md) drive the
static client through this server against stripe-mock: `pytest tests/e2e` from the
repository root (after `playwright install chromium`).

## Differences from the Express server

This server was translated from the sample's former Node/Express reference server
(`server/node`, Express 4 with stripe-node). That server was removed only after the HTTP
parity gate passed: the same suite gave identical results against both servers (56
passed, 4 skipped). The 4 skipped tests covered the empty-`sessionId` and malformed-JSON
differences listed below; the suite now asserts the Flask behaviour for them. This
section records the contract history.

The Flask server reproduces the Express behaviour the client and Stripe depend on,
including these Express quirks:

- JSON responses are compact, keep key order and use `application/json; charset=utf-8`;
  `OK` / `Bad Request` bodies use `text/plain; charset=utf-8`. Static text, CSS and
  JavaScript files use `charset=UTF-8`, as Express' `send` does.
- The `303` redirect from `/create-checkout-session` negotiates its body on `Accept` like
  `res.redirect`: `See Other. Redirecting to <url>` as `text/plain` for `*/*` or no
  `Accept`, a `<p>` as `text/html` for browsers, an empty body otherwise (`Vary: Accept`).
- Static files win over routes (a `before_request` hook serves any existing file for
  `GET`/`HEAD` before routing, like `express.static` mounted first). Dotfiles are ignored,
  directories redirect (301) to a trailing slash and serve their `index.html`.
- `.js` is served as `application/javascript` and `.ico` as `image/x-icon`, as Express does.
- Unsupported methods (e.g. `POST /index.html`, `GET /webhook`) return 404 rather than
  Flask's 405, with an Express-style `Cannot <METHOD> <path>` HTML page.
- Trailing slashes are not strict (`/health/` works).
- The server listens on all IPv4 interfaces (`0.0.0.0`), like `app.listen(PORT)` apart
  from IPv6 (see below).

Accepted differences, none of which affect the client or Stripe:

- **Error message wording** comes from stripe-python instead of stripe-node. The JSON
  keys and status codes are the same.
- **`GET /checkout-session` with an empty `sessionId=`** returns 400. stripe-node sends
  `GET /v1/checkout/sessions/`, which is the *list* endpoint, so Node answered 200 with a
  list. A missing `sessionId` returns 400 in both.
- **Malformed JSON bodies**, and top-level JSON primitives such as `"text"` or `42`, sent
  with `Content-Type: application/json` are treated as an empty body (so `/webhook` answers
  200 without a secret, and `/create-checkout-session` calls Stripe without a quantity).
  Express' strict body parser rejected them with a 400 HTML error page before the route ran.
- **Signed webhooks** with a valid signature but a non-object JSON body (`42`, `"text"`,
  `null`, `[1]`) answer 400 `Bad Request`, matching Express. This contrasts with the
  unsigned path above, which answers 200.
- **Signed webhooks** are verified against the raw body whatever the `Content-Type`.
  Express only kept the raw body for `application/json` requests.
- **Repeated or nested form fields** (`quantity=1&quantity=2`, `quantity[a]=1`) use the first
  plain value; Express' `qs` parser built arrays/objects.
- **Route matching is case-sensitive** (`/HEALTH` is 404); Express routes are not.
- **`OPTIONS` requests** get Flask's automatic empty 200 with an `Allow` header for any
  matching route; Express answered `OPTIONS` on routes with a text body and 404 on static
  files.
- **Response headers**: no `X-Powered-By: Express`, no ETags on JSON responses, static files
  use `Cache-Control: no-cache` instead of `public, max-age=0`, and the static directory
  redirect (`/css` → `/css/`) body is Werkzeug's HTML.
- **Logging**: the listening message is printed just before the server starts, and the
  Werkzeug development server adds its own banner and access log on stderr.
- **An invalid `PORT`** (non-numeric) stops the server with a Python error; Node's
  `Number()` produced `NaN`.
- **IPv4-only listener**: the server binds `0.0.0.0`, while Node's `app.listen(PORT)` bound
  `::` (dual-stack). A client that resolves `localhost` to `::1` and does not fall back
  to IPv4 gets connection refused. Browsers, curl and the Stripe CLI fall back, so this
  does not affect the client or Stripe.
