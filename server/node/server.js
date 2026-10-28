const express = require('express');
const { resolve } = require('path');

// Settings come from server/node/.env or the environment.
require('dotenv').config({ path: resolve(__dirname, '.env') });

const PORT = Number(process.env.PORT || 4242);
const DOMAIN = process.env.DOMAIN || `http://localhost:${PORT}`;
const STATIC_DIR = process.env.STATIC_DIR
  ? resolve(__dirname, process.env.STATIC_DIR)
  : resolve(__dirname, '../../client/html');
const SECRET_KEY = process.env.STRIPE_SECRET_KEY;

if (!SECRET_KEY || !/^(sk|rk)_(test|live)_[A-Za-z0-9]{10,}$/.test(SECRET_KEY)) {
  console.error('STRIPE_SECRET_KEY is not set.');
  process.exit(1);
}

const stripe = require('stripe')(SECRET_KEY, {
  appInfo: {
    name: 'stripe-samples/checkout-one-time-payments',
    version: '0.0.1',
    url: 'https://github.com/stripe-samples/checkout-one-time-payments',
  },
});

// A product with a fixed price, so no Price ID has to be created first.
const LINE_ITEM_PRICE = {
  currency: 'usd',
  unit_amount: 500,
  product_data: { name: 'Stubborn Attachments (photo)' },
};

const app = express();
app.use(express.static(STATIC_DIR));
app.use(express.urlencoded({ extended: true }));
app.use(
  express.json({
    // We need the raw body to verify webhook signatures.
    verify: function (req, res, buf) {
      if (req.originalUrl.startsWith('/webhook')) {
        req.rawBody = buf.toString();
      }
    },
  })
);

app.get('/', (req, res) => {
  res.sendFile(resolve(STATIC_DIR, 'index.html'));
});

app.get('/health', async (req, res) => {
  try {
    const balance = await stripe.balance.retrieve();
    res.send({ status: 'ok', livemode: balance.livemode });
  } catch (err) {
    res.status(503).send({ status: 'error', error: err.message });
  }
});

// Fetch the Checkout Session to display the JSON result on the success page
app.get('/checkout-session', async (req, res) => {
  const { sessionId } = req.query;
  try {
    const session = await stripe.checkout.sessions.retrieve(sessionId);
    res.send(session);
  } catch (err) {
    res.status(400).send({ error: err.message });
  }
});

app.post('/create-checkout-session', async (req, res) => {
  const { quantity } = req.body;

  // For full details see https://stripe.com/docs/api/checkout/sessions/create
  try {
    const session = await stripe.checkout.sessions.create({
      mode: 'payment',
      line_items: [{ price_data: LINE_ITEM_PRICE, quantity: quantity }],
      // ?session_id={CHECKOUT_SESSION_ID} means the redirect will have the session ID set as a query param
      success_url: `${DOMAIN}/success.html?session_id={CHECKOUT_SESSION_ID}`,
    });
    return res.redirect(303, session.url);
  } catch (err) {
    return res.status(502).send({ error: err.message });
  }
});

// Webhook handler for asynchronous events.
app.post('/webhook', async (req, res) => {
  let data;
  let eventType;
  // Check if webhook signing is configured.
  if (process.env.STRIPE_WEBHOOK_SECRET) {
    let event;
    let signature = req.headers['stripe-signature'];

    try {
      event = stripe.webhooks.constructEvent(
        req.rawBody,
        signature,
        process.env.STRIPE_WEBHOOK_SECRET
      );
    } catch (err) {
      console.log(`⚠️  Webhook signature verification failed.`);
      return res.sendStatus(400);
    }
    data = event.data;
    eventType = event.type;
  } else {
    data = req.body.data;
    eventType = req.body.type;
  }

  if (eventType === 'checkout.session.completed') {
    console.log(`🔔  Payment received!`);
  }

  res.sendStatus(200);
});

stripe.balance
  .retrieve()
  .then(() => {
    app.listen(PORT, () => console.log(`Node server listening on port ${PORT}!`));
  })
  .catch((err) => {
    console.error(`Startup failed: ${err.message}`);
    process.exit(1);
  });
