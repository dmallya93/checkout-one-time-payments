# HTTP parity suite

Black-box pytest suite that checks the Python/Flask server (`server/python`) behaves
like the reference Express server (`server/node`) over HTTP. The same tests run against
each server, both pointed at [stripe-mock](https://github.com/stripe/stripe-mock) with a
dummy `sk_test_...` key. A green run against both servers is the gate for deleting
`server/node`.

The suite compares status codes, redirect behaviour (`303` + `Location`, not followed),
content types (media type, plus charset where Express sets one, compared
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
| `GET /checkout-session` (no `sessionId`) | 400, JSON keys exactly `{error}` |
| `POST /create-checkout-session` | form, form with charset, JSON number/string, no body, no quantity: 303 with `Location` = stripe-mock's Checkout URL; redirect body type for `*/*` and browser `Accept`; invalid quantity: 502 `{error}` |
| `POST /webhook`, no secret | `checkout.session.completed`, other events, empty body, empty JSON body, `text/plain`, JSON array, form body, ignored signature header: 200 `OK` `text/plain` |
| `POST /webhook`, signed | valid signature (and one valid among several): 200 `OK`; wrong secret, tampered body, missing/malformed header, expired timestamp: 400 `Bad Request` `text/plain` |
| Unknown routes and methods (`/nope`, `POST /index.html`, `GET /webhook`, `POST /health`, ...) | 404 `text/html` |

Skipped tests mark the known differences documented in
[`server/python/README.md`](../../server/python/README.md#differences-from-the-express-server):

- Malformed JSON and top-level JSON primitives on `/webhook`: Express answers 400 from its
  strict JSON parser, Flask answers 200.
- `GET /checkout-session?sessionId=` (empty): stripe-node calls the list endpoint (200 on
  real Stripe; 400 from stripe-mock); Flask answers 400.

## Run

Requirements: Python with the dev dependencies
(`pip install -r server/python/requirements-dev.txt`, which includes pytest and requests),
Node 18+ for the reference server, and stripe-mock: either the `stripe-mock` binary on
`PATH`, or an instance already running (for example
`docker run --rm -p 12111-12112:12111-12112 stripe/stripe-mock`, or a CI service container).

From the repository root:

```sh
bash tests/parity/run_parity.sh all       # Node, then Python
bash tests/parity/run_parity.sh node      # reference server only
bash tests/parity/run_parity.sh python    # Flask server only
```

The runner:

1. Uses `server/python/.venv/bin/python` if it exists, otherwise `python3`.
2. Uses `STRIPE_MOCK_URL` if set. Otherwise it starts `stripe-mock` from `PATH` on
   12111/12112 (or free ports if those are taken), or assumes one listens on
   `http://localhost:12111`.
3. Runs `npm install` in `server/node` if `node_modules` is missing. `node_modules` and
   `package-lock.json` are git-ignored.
4. Starts two instances of each server with a dummy key and
   `STRIPE_API_BASE=<stripe-mock>`: one without `STRIPE_WEBHOOK_SECRET` on port 4243
   (`BASE_URL`), and one with a fixed test secret on port 4244 (`SIGNED_BASE_URL`,
   `PARITY_WEBHOOK_SECRET`). If those ports are taken it picks free ones.
5. Waits for `/health`, runs `pytest tests/parity`, stops the servers, prints a summary and
   exits non-zero if any server failed.

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

## How the servers reach stripe-mock

- **Python**: the test-only `STRIPE_API_BASE` variable makes `server.py` pass
  `base_addresses={"api": ...}` to `StripeClient`. When it is unset, nothing changes.
- **Node**: `server.js` has no API base option and stays byte-for-byte unchanged. The
  runner starts it with `node -r ../../tests/parity/stripe-mock-preload.js server.js`. The
  preload resolves `stripe` from `server/node/node_modules` and replaces the cached module
  export with a wrapper that adds `host`, `port` and `protocol` from `STRIPE_API_BASE`
  (falling back to `STRIPE_MOCK_URL`, then `http://localhost:12111`).

## Lint

`ruff check tests && ruff format --check tests` from the repository root. The root
`ruff.toml` reuses the settings in `server/python/pyproject.toml`.
