# Accept payments with Stripe Checkout

A small Python/Flask server and an HTML client that sell a photo through a
Stripe-hosted [Checkout](https://docs.stripe.com/checkout) page.

## How to run

Requires Python 3.11+ (developed and tested on 3.12) and a Stripe test-mode
secret key. See [server/python/README.md](server/python/README.md) for all
settings and for running the tests.

```
cd server/python
uv sync
cp .env.example .env   # then set STRIPE_SECRET_KEY
uv run python server.py
```

Without uv: `python -m venv .venv`, activate it, `pip install -r requirements.txt`,
then `python server.py`.

Then open http://localhost:4242. Pay with the test card `4242 4242 4242 4242`,
any future expiry date and any CVC.

## Endpoints

| Route | What it does |
|---|---|
| `GET /` | The product page |
| `GET /health` | Health check |
| `POST /create-checkout-session` | Creates a Checkout Session and redirects to it |
| `GET /checkout-session?sessionId=...` | Returns the session, for the success page |
| `POST /webhook` | Receives Stripe events |
