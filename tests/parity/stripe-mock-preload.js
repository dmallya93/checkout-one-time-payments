// TEST-ONLY preload for the HTTP parity suite.
//
//   cd server/node && node -r ../../tests/parity/stripe-mock-preload.js server.js
//
// The reference Express server creates its client with `require('stripe')(key, opts)`
// and has no API base option. This preload replaces the cached `stripe` module export
// with a wrapper that adds `host`, `port` and `protocol` pointing at stripe-mock, so
// `server/node/server.js` itself stays byte-for-byte unchanged.
//
// The target is read from STRIPE_API_BASE, falling back to STRIPE_MOCK_URL and then
// http://localhost:12111. Never use this outside tests.
'use strict';

const path = require('path');

const NODE_SERVER_DIR = path.resolve(__dirname, '..', '..', 'server', 'node');
const target = new URL(
  process.env.STRIPE_API_BASE || process.env.STRIPE_MOCK_URL || 'http://localhost:12111'
);
const protocol = target.protocol.replace(/:$/, '');
if (protocol !== 'http' && protocol !== 'https') {
  throw new Error(`stripe-mock-preload: unsupported protocol in ${target.href}`);
}
const overrides = {
  protocol,
  host: target.hostname,
  port: Number(target.port || (protocol === 'https' ? 443 : 80)),
};

// Resolve `stripe` exactly as server.js will, from server/node's node_modules.
const stripePath = require.resolve('stripe', { paths: [NODE_SERVER_DIR] });
const OriginalStripe = require(stripePath);

function StripeMockStripe(key, config) {
  return new OriginalStripe(key, Object.assign({}, config, overrides));
}
// Keep static members (errors, webhooks, ...) and `instanceof` working.
Object.setPrototypeOf(StripeMockStripe, OriginalStripe);
StripeMockStripe.prototype = OriginalStripe.prototype;
StripeMockStripe.Stripe = StripeMockStripe;
StripeMockStripe.default = StripeMockStripe;

require.cache[stripePath].exports = StripeMockStripe;

console.log(
  `[stripe-mock-preload] stripe API -> ${overrides.protocol}://${overrides.host}:${overrides.port}`
);
