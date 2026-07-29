import test from 'node:test'
import assert from 'node:assert/strict'
import { createHmac } from 'node:crypto'

import {
  POLL_HEADER,
  TOKEN_HEADER,
  bridgeProof,
  drainNeedsHeader,
  healthBody,
  requestIsUnauthorized,
  tokenIsValid,
} from './bridge_auth.js'

const TOKEN = 'tok-ABC_123'

// ── The token gate ──────────────────────────────────────────────────────────

test('a request carrying the right token is allowed', () => {
  assert.equal(
    requestIsUnauthorized('/send', { [TOKEN_HEADER]: TOKEN }, TOKEN),
    false,
  )
})

test('a request with a wrong or missing token is rejected', () => {
  assert.equal(requestIsUnauthorized('/send', { [TOKEN_HEADER]: 'nope' }, TOKEN), true)
  assert.equal(requestIsUnauthorized('/send', {}, TOKEN), true)
})

test('a token that is merely a prefix of the real one is rejected', () => {
  // Guards against a comparison that stops at the shorter length.
  assert.equal(requestIsUnauthorized('/send', { [TOKEN_HEADER]: 'tok-' }, TOKEN), true)
  assert.equal(requestIsUnauthorized('/send', { [TOKEN_HEADER]: TOKEN + 'x' }, TOKEN), true)
})

test('every route except /health is gated', () => {
  // Enumerated from the bridge's own surface. A new route must not be able to
  // land outside the gate by accident.
  for (const route of ['/send', '/edit', '/send-media', '/send-poll',
                       '/send-location', '/typing', '/messages', '/chat/123']) {
    assert.equal(
      requestIsUnauthorized(route, {}, TOKEN), true,
      `${route} should require the token`,
    )
  }
})

test('/health stays reachable without the token', () => {
  // It is the bootstrap: the gateway calls it before it knows whether this
  // process is trustworthy. Identity is proven by the nonce challenge instead.
  assert.equal(requestIsUnauthorized('/health', {}, TOKEN), false)
})

test('with no token configured the bridge stays unauthenticated', () => {
  // Pre-token behaviour, and what --pair-only and ad-hoc local runs get. A
  // bridge that refuses to boot looks, to a user, exactly like a broken install.
  assert.equal(requestIsUnauthorized('/send', {}, ''), false)
})

test('an empty presented token does not authenticate against an empty one', () => {
  // Only reachable if a caller sends the header with no value while a token IS
  // configured; it must not be treated as a match by the "no token" shortcut.
  assert.equal(requestIsUnauthorized('/send', { [TOKEN_HEADER]: '' }, TOKEN), true)
})

test('tokenIsValid does not throw on a length mismatch', () => {
  // timingSafeEqual throws when the buffers differ in length, which is why
  // both sides are hashed to a fixed width first. A throw here would surface
  // as a 500 and leak that the length was wrong.
  assert.doesNotThrow(() => tokenIsValid('x', TOKEN))
  assert.equal(tokenIsValid('x', TOKEN), false)
})

// ── The /health identity proof ──────────────────────────────────────────────

test('the proof matches an independently computed HMAC', () => {
  // Computed here from the primitive rather than from bridgeProof, so this
  // pins the wire format the Python side reimplements
  // (_bridge_health_proof in plugins/platforms/whatsapp/adapter.py).
  const expected = createHmac('sha256', TOKEN).update('nonce-xyz').digest('hex')

  assert.equal(bridgeProof(TOKEN, 'nonce-xyz'), expected)
})

test('the proof is bound to the nonce', () => {
  // Otherwise one captured response could be replayed forever.
  assert.notEqual(bridgeProof(TOKEN, 'n-1'), bridgeProof(TOKEN, 'n-2'))
})

test('a different token yields a different proof', () => {
  assert.notEqual(bridgeProof(TOKEN, 'n-1'), bridgeProof('other-token', 'n-1'))
})

test('healthBody adds a proof only when asked with a nonce', () => {
  const base = { status: 'connected' }

  assert.equal(healthBody(base, TOKEN, 'n-1').proof, bridgeProof(TOKEN, 'n-1'))
  assert.equal('proof' in healthBody(base, TOKEN, undefined), false)
  assert.equal('proof' in healthBody(base, TOKEN, ''), false)
})

test('healthBody never claims a proof when no token is configured', () => {
  // A `proof` field computed from an empty token would be a forgeable constant
  // that still looks like an answer to the challenge.
  assert.equal('proof' in healthBody({ status: 'connected' }, '', 'n-1'), false)
})

test('healthBody does not mutate the body it is given', () => {
  const base = { status: 'connected' }

  healthBody(base, TOKEN, 'n-1')

  assert.equal('proof' in base, false)
})

// ── The destructive-drain guard ─────────────────────────────────────────────

test('the drain is refused without the preflight-forcing header', () => {
  // A browser can issue a simple cross-origin GET, and the splice would run
  // before CORS blocked it from reading the response — messages gone.
  assert.equal(drainNeedsHeader({}), true)
})

test('the drain is allowed with the header, whatever its value', () => {
  // It is not a secret. Its only job is to make the request non-simple so a
  // browser has to preflight.
  assert.equal(drainNeedsHeader({ [POLL_HEADER]: 'poll' }), false)
  assert.equal(drainNeedsHeader({ [POLL_HEADER]: 'anything' }), false)
})

test('an empty header value does not satisfy the drain guard', () => {
  // A browser cannot set the header at all without a preflight, so an empty
  // one means something else built the request.
  assert.equal(drainNeedsHeader({ [POLL_HEADER]: '' }), true)
})
