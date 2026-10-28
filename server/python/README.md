# Python server

A [Flask](https://flask.palletsprojects.com) server served by
[Waitress](https://docs.pylonsproject.org/projects/waitress). See the
[main README](../../README.md) for the endpoints.

## Requirements

- Python 3.11 or newer (`requires-python = ">=3.11"`). The spec's Tech Stack
  says "Python 3.12+", but `pyproject.toml` follows its literal
  `requires-python >=3.11`. Development and testing target 3.12; 3.11 and 3.13
  are also supported.
- A Stripe **test-mode** secret key (`sk_test_...` or `rk_test_...`). The server
  refuses to start without a valid key, and it calls the Stripe API
  (`balance.retrieve`) at startup, so it needs outbound HTTPS access to
  `api.stripe.com`.

## Setup

With [uv](https://docs.astral.sh/uv/):

```
cd server/python
uv sync
cp .env.example .env   # then fill in STRIPE_SECRET_KEY
uv run python server.py
```

Or with pip:

```
cd server/python
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then fill in STRIPE_SECRET_KEY
python server.py
```

The server prints `Python server listening on port 4242!` and serves the client
at http://localhost:4242. Set `FLASK_DEBUG=1` to use Flask's development server
with auto-reload instead of Waitress.

## Configuration

Settings are read from `server/python/.env` or the environment. Real
environment variables take precedence over `.env`.

| Variable | Default | Notes |
|---|---|---|
| `STRIPE_SECRET_KEY` | (required) | Must match `^(sk\|rk)_(test\|live)_[A-Za-z0-9]{10,}$`, otherwise the server prints `STRIPE_SECRET_KEY is not set.` and exits 1 |
| `STRIPE_WEBHOOK_SECRET` | unset | If set, `/webhook` verifies the `Stripe-Signature` header (for example with the secret from `stripe listen --forward-to localhost:4242/webhook`) |
| `PORT` | `4242` | Port to listen on |
| `DOMAIN` | `http://localhost:{PORT}` | Base URL for the Checkout `success_url` |
| `STATIC_DIR` | `../../client/html` | Client directory, resolved relative to `server/python` |

## Tests

```
uv sync                        # installs the dev group too
uv run playwright install chromium
uv run pytest
```

- The unit tests mock Stripe and need no key or network.
- Tests in `tests/live/` are marked `live` (black-box parity in
  `test_parity.py`, Playwright E2E in `test_e2e.py`, which is also marked
  `e2e`). They need `STRIPE_SECRET_KEY` in the environment (a test-mode key;
  `.env` is not read for it) and network access to `api.stripe.com`. Without
  a key they are skipped with the reason
  `STRIPE_SECRET_KEY not set: live Stripe tests skipped`, shown in the pytest
  summary. Run only the offline suite with `uv run pytest -m "not live"`, or
  only the live tests with `uv run pytest -m live`.
- The live tests start the servers themselves, each on a free port, and wait
  for `GET /health` to return 200 (30 s timeout). `PARITY_TARGETS` selects
  which servers the parity tests run against: `python` (the default), `node`,
  or `node,python`. With both, `test_node_and_python_agree` also compares the
  two servers' status codes, content types, redirect hosts and JSON key sets.
  The `node` target is the original Express server, which was removed from
  this repo once parity was verified; to compare against it again, restore
  `server/node` from the git history and run `npm install` there. Without it
  the Node target is skipped with a reason.
- Webhook tests start each server twice: once with `STRIPE_WEBHOOK_SECRET`
  set to the fixed test secret `whsec_paritytest123` (the tests sign events
  with it), and once with it empty (signing not configured). Both settings
  override any `STRIPE_WEBHOOK_SECRET` from the environment or `.env`.
- Playwright E2E runs headless Chromium against the Python server and is
  skipped with a reason if Chromium is not installed
  (`uv run playwright install chromium`). It reaches `checkout.stripe.com` but
  does not complete a payment.

For example:

```
STRIPE_SECRET_KEY=sk_test_... uv run pytest -m live
STRIPE_SECRET_KEY=sk_test_... PARITY_TARGETS=node,python uv run pytest tests/live/test_parity.py
```

Static checks:

```
uv run ruff check .
uv run ruff format --check .
```
