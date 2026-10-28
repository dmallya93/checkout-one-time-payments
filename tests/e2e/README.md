# Browser E2E tests

[pytest-playwright](https://playwright.dev/python/docs/test-runners) tests that drive
the unchanged static client in `client/html` through the Python/Flask server
(`server/python`), with [stripe-mock](https://github.com/stripe/stripe-mock) standing in
for the Stripe API.

## Coverage

| Page | Checked |
|---|---|
| `/` | title, heading, Buy button, quantity `1`, `-` disabled initially |
| `/` quantity buttons | `+` increments and disables at 10; `-` decrements and disables at 1; both re-enable when moving away from a limit |
| `/` typed quantity | values outside 1–10 are clamped on `change` (`0`, `-3` → `1`; `25`, `11` → `10`); in-range values are kept |
| `/` Buy | the form `POST`s `quantity=<n>` form-encoded to `/create-checkout-session`, the server answers `303` with `Location` = stripe-mock's Checkout URL, and the browser follows it with a `GET`; a second test lands on a stubbed Checkout page at exactly that URL |
| `/success.html?session_id=cs_test_...` | `/checkout-session?sessionId=...` answers 200 and the `<pre>` holds the session as pretty-printed JSON (`object == "checkout.session"`, `id` = the requested ID) |
| `/success.html` (no `session_id`) | no `/checkout-session` request, `<pre>` stays empty |
| Restart demo | navigates back to `/` |

## Run

Requirements: the dev dependencies (`pip install -r server/python/requirements-dev.txt`,
which includes `pytest-playwright`), a Playwright Chromium build
(`playwright install chromium`, or `playwright install --with-deps chromium` on a fresh
Linux machine), and stripe-mock: either the `stripe-mock` binary on `PATH` or an instance
already running (for example
`docker run --rm -p 12111-12112:12111-12112 stripe/stripe-mock`).

From the repository root:

```sh
server/python/.venv/bin/pytest tests/e2e
```

The session-scoped `app` fixture in `conftest.py`:

1. Uses `STRIPE_MOCK_URL` if set. Otherwise it starts `stripe-mock` from `PATH` on free
   ports, or assumes one listens on `http://localhost:12111`.
2. Starts `server/python/server.py` with the Python running pytest, on a free port, with a
   dummy `sk_test_...` key and `STRIPE_API_BASE=<stripe-mock>`. `DOMAIN`, `STATIC_DIR` and
   `STRIPE_WEBHOOK_SECRET` are set empty so a local `server/python/.env` cannot change the
   tested configuration.
3. Waits for `/health`, runs the tests, then stops the server (and stripe-mock if it
   started it).

### Against a server you started yourself

```sh
export BASE_URL=http://localhost:4242          # or pass --base-url
export STRIPE_MOCK_URL=http://localhost:12111  # the stripe-mock that server uses
pytest tests/e2e
```

The server must be backed by stripe-mock: the checkout tests compare the redirect target
with the Checkout URL stripe-mock returns.

### Options

- Standard pytest-playwright options work, e.g. `--headed`, `--slowmo 250`,
  `--tracing retain-on-failure`, `--video retain-on-failure` (artifacts go to
  `test-results/`, which is git-ignored).
- `E2E_CHROMIUM_EXECUTABLE=/path/to/chromium` launches a specific Chromium build instead
  of the one installed by `playwright install`.

## Offline determinism and the Checkout redirect

Every request to a host other than the app is handled with `page.route`: picsum.photos
images get a 1x1 PNG, `https://js.stripe.com/v3/` gets a stub script, and anything else is
aborted.

Playwright never routes the follow-up request of an HTTP redirect (its Chromium network
layer continues requests that have `redirectedFrom` without consulting routes), so the
server's 303 to `https://checkout.stripe.com/...` cannot be fulfilled by `page.route`.
The checkout flow is therefore covered in two steps:

1. **Real redirect**: the browser submits the form and follows the 303 natively. The test
   browser is launched with `--host-resolver-rules=MAP checkout.stripe.com ~NOTFOUND`, so
   that navigation fails with `ERR_NAME_NOT_RESOLVED` without contacting Stripe; the test
   asserts the POST, the 303 and its `Location`, and the followed `GET` to the Checkout URL.
2. **Stubbed Checkout page**: a route on `/create-checkout-session` sends the browser's
   POST to the server with `route.fetch(max_redirects=0)`, checks the server's 303 and
   `Location`, and hands the `Location` back to the browser as a plain navigation. A
   `page.route` on the Checkout origin fulfils it with a stub page, and the test asserts
   the final URL equals stripe-mock's Checkout URL.

## Lint

`ruff check tests && ruff format --check tests` from the repository root.
