# Accept payments with Stripe Checkout

A small Node/Express server and an HTML client that sell a photo through a
Stripe-hosted [Checkout](https://docs.stripe.com/checkout) page.

## How to run

Requires Node 18+.

```
cd server/node
npm install
npm start
```

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
