# HTTP parity suite

Black-box pytest suite for the HTTP contract of the Python/Flask server
(`server/python`), pointed at [stripe-mock](https://github.com/stripe/stripe-mock) with a
dummy `sk_test_...` key. It talks to the server only over HTTP and imports no server code,
so it can run against any server that implements the contract (`BASE_URL`).

The suite was the gate for replacing the sample's original Node/Express server: it passed
against both servers with identical results before `server/node` was removed (see
[`server/python/README.md`](../../server/python/README.md#differences-from-the-express-server)).
It now guards that contract against regressions.

The suite checks status codes, redirect behaviour (`303` + `Location`, not followed),
content types (media type, plus charset where the original server set one, compared
case-insensitively) and JSON key sets. It does not compare error messages. Expected
Stripe data, such as the session's full nested key set and the Checkout URL, is read
from stripe-mock directly, so the tests do not depend on the stripe-mock version.

## Coverage

| Route | Checked |
|---|---|
| `/`, `/index.html`, `/success.html`, `/index.js`, `/success.js`, `/css/*.css`, `/favicon.ico` | 200, content type, body equals the file in `client/html`; `HEAD`; query strings |
| `/css` / `/css/` / dotfiles | 301 to `/css/`; 404 |
| `GET /health` (and `/health/`) | 200, JSON keys exactly `{status, livemode}` |
| `GET /checkout-session?sessionId=cs_test_...` | 200, core session keys, full nested key set equals stripe-mock's |
| `GET /checkout-session` (no or empty `sessionId`) | 400, JSON keys exactly `{error}` |
| `POST /create-checkout-session` | form, form with charset, JSON number/string, no body, no quantity: 303 with `Location` = stripe-mock's Checkout URL; redirect body type for `*/*` and browser `Accept`; invalid quantity: 502 `{error}` |
| `POST /webhook`, no secret | `checkout.session.completed`, other events, empty body, empty JSON body, `text/plain`, JSON array, form body, malformed JSON and JSON primitives, ignored signature header: 200 `OK` `text/plain` |
| `POST /webhook`, signed | valid signature (and one valid among several): 200 `OK`; wrong secret, tampered body, missing/malformed header, expired timestamp: 400 `Bad Request` `text/plain` |
| Unknown routes and methods (`/nope`, `POST /index.html`, `GET /webhook`, `POST /health`, ...) | 404 `text/html` |

Two cases were skipped during the parity gate because the servers disagreed, and are now
asserted as the accepted Flask behaviour (documented in `server/python/README.md`):

- Malformed JSON and top-level JSON primitives on `/webhook` answer 200 `OK` (the Express
  server's strict JSON parser answered 400).
- `GET /checkout-session?sessionId=` (empty) answers 400 `{error}` (stripe-node called the
  list endpoint instead).

## Run

Requirements: Python with the dev dependencies
(`pip install -r server/python/requirements-dev.txt`, which includes pytest and requests)
and stripe-mock: either the `stripe-mock` binary on `PATH`, or an instance already running
(for example `docker run --rm -p 12111-12112:12111-12112 stripe/stripe-mock`, or a CI
service container).

From the repository root:

```sh
bash tests/parity/run_parity.sh           # same as: run_parity.sh python
```

The runner:

1. Uses `server/python/.venv/bin/python` if it exists, otherwise `python3`.
2. Uses `STRIPE_MOCK_URL` if set. Otherwise it starts `stripe-mock` from `PATH` on
   12111/12112 (or free ports if those are taken), or assumes one listens on
   `http://localhost:12111`.
3. Starts two instances of `server/python/server.py` with a dummy key and
   `STRIPE_API_BASE=<stripe-mock>`: one without `STRIPE_WEBHOOK_SECRET` on port 4243
   (`BASE_URL`), and one with a fixed test secret on port 4244 (`SIGNED_BASE_URL`,
   `PARITY_WEBHOOK_SECRET`). If those ports are taken it picks free ones.
4. Waits for `/health`, runs `pytest tests/parity`, stops the servers and exits with
   pytest's status (printing the server logs on failure).

Optional variables: `STRIPE_MOCK_URL`, `PARITY_PORT`, `PARITY_SIGNED_PORT`, and
`PARITY_PYTEST_ARGS` (for example `PARITY_PYTEST_ARGS="-k webhook -v"`).

### Against a server you started yourself

```sh
export BASE_URL=http://localhost:4242          # started without STRIPE_WEBHOOK_SECRET
export STRIPE_MOCK_URL=http://localhost:12111  # the stripe-mock that server uses
# Optional, for the signed-webhook tests (skipped otherwise):
export SIGNED_BASE_URL=http://localhost:4244 PARITY_WEBHOOK_SECRET=whsec_...
pytest tests/parity
```

## How the server reaches stripe-mock

The test-only `STRIPE_API_BASE` variable makes `server.py` pass
`base_addresses={"api": ...}` to `StripeClient`. When it is unset, nothing changes.

## Lint

`ruff check tests && ruff format --check tests` from the repository root. The root
`ruff.toml` reuses the settings in `server/python/pyproject.toml`.
