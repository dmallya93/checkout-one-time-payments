# Accept payments with Stripe Checkout

A small Python/Flask server and an HTML client that sell a photo through a
Stripe-hosted [Checkout](https://docs.stripe.com/checkout) page.

## How to run

Requires Python 3.13 (3.12+ works) and a Stripe account's
[test secret key](https://dashboard.stripe.com/test/apikeys).

```
cd server/python
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # or requirements-dev.txt for tests and lint
cp .env.example .env               # then set STRIPE_SECRET_KEY
python server.py
```

Then open http://localhost:4242. Pay with the test card `4242 4242 4242 4242`,
any future expiry date and any CVC.

The server checks the key with the Stripe Balance API before it starts listening,
and exits with an error if the key is missing or rejected.

Optionally, run it under gunicorn instead (same startup checks):

```
gunicorn --bind 0.0.0.0:4242 "server:create_app()"
```

## Configuration

Set these in `server/python/.env` or the environment. Real environment variables
take precedence over `.env`.

| Variable | Required | Default | Notes |
|---|---|---|---|
| `STRIPE_SECRET_KEY` | yes | | `sk_`/`rk_` test or live key |
| `STRIPE_WEBHOOK_SECRET` | no | | `whsec_...`; without it `/webhook` accepts unsigned events |
| `PORT` | no | `4242` | |
| `DOMAIN` | no | `http://localhost:<PORT>` | Base URL of the Checkout `success_url` |
| `STATIC_DIR` | no | `../../client/html` | Relative to `server/python/` |

## Endpoints

| Route | What it does |
|---|---|
| `GET /` | The product page |
| `GET /health` | Health check |
| `POST /create-checkout-session` | Creates a Checkout Session and redirects to it |
| `GET /checkout-session?sessionId=...` | Returns the session, for the success page |
| `POST /webhook` | Receives Stripe events |

## Webhooks

With the [Stripe CLI](https://docs.stripe.com/stripe-cli):

```
stripe listen --forward-to localhost:4242/webhook
```

Put the printed `whsec_...` secret in `STRIPE_WEBHOOK_SECRET` and restart the server.
A completed payment logs `Payment received!`.

## Testing

Install the dev dependencies first (`pip install -r requirements-dev.txt` in
`server/python`). The parity and E2E tests also need
[stripe-mock](https://github.com/stripe/stripe-mock), either the `stripe-mock` binary on
`PATH` or a running instance
(`docker run --rm -p 12111-12112:12111-12112 stripe/stripe-mock`).

```
# Lint and unit tests (mocked Stripe client, no network), in server/python
ruff check . && ruff format --check .
pytest -q

# HTTP contract tests against the server and stripe-mock, from the repository root
bash tests/parity/run_parity.sh

# Browser tests of the HTML client (Playwright), from the repository root
playwright install chromium
pytest tests/e2e
```

See [`server/python/README.md`](server/python/README.md),
[`tests/parity/README.md`](tests/parity/README.md) and
[`tests/e2e/README.md`](tests/e2e/README.md) for details.
