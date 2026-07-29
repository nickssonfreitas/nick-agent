/**
 * bridge_auth.js
 *
 * Access-control decisions for the bridge's HTTP surface, kept out of
 * bridge.js so they can be tested. bridge.js connects to WhatsApp at module
 * load, so nothing can import it; the sibling-module pattern here is the same
 * one allowlist.js and owner_message_gate.js already use.
 *
 * Node builtins only — no dependency on baileys or express — so these tests
 * run in CI without installing the bridge's runtime dependencies.
 *
 * Three separate controls live here, and they answer three different threats:
 *
 *   bridgeProof       Proves to the *caller* that we hold the shared token,
 *                     so a gateway can tell a real bridge from a process that
 *                     merely bound the port first. The caller sends a nonce
 *                     and no secret, so nothing leaks to an impostor.
 *   tokenIsValid      Proves to *us* that the caller holds the token. Only
 *                     load-bearing on loopback TCP (Windows); over a unix
 *                     socket the filesystem already decided who may connect.
 *   drainNeedsHeader  Keeps a browser from emptying the inbound queue with a
 *                     simple cross-origin GET. Not authentication at all.
 *
 * None of them touches prompt injection: an injected request arrives from the
 * legitimate gateway, which holds the token by construction.
 */

import { createHmac, createHash, timingSafeEqual } from 'crypto'

/** Header carrying the shared secret on authenticated requests. */
export const TOKEN_HEADER = 'x-hermes-bridge-token'

/**
 * Header that exists only to make a request non-simple, so a browser must
 * preflight it. Any value works; it is not a secret.
 */
export const POLL_HEADER = 'x-hermes-bridge'

/**
 * Routes reachable without the token. /health is the bootstrap: the gateway
 * has to be able to ask "is anyone there" before it knows whether to trust
 * the answer, and it proves our identity via bridgeProof instead.
 */
const UNAUTHENTICATED_PATHS = new Set(['/health'])

/** The proof a genuine bridge returns for a caller-supplied nonce. */
export function bridgeProof(token, nonce) {
  return createHmac('sha256', String(token)).update(String(nonce)).digest('hex')
}

/**
 * Constant-time comparison of a presented token against the configured one.
 *
 * Both sides are hashed first because timingSafeEqual throws on a length
 * mismatch, and letting that throw would itself disclose the token's length.
 * Hashing makes every comparison the same fixed width.
 */
export function tokenIsValid(presented, token) {
  const a = createHash('sha256').update(Buffer.from(String(presented ?? ''), 'utf8')).digest()
  const b = createHash('sha256').update(Buffer.from(String(token ?? ''), 'utf8')).digest()
  return timingSafeEqual(a, b)
}

/**
 * Whether a request should be rejected for a bad or missing token.
 *
 * With no token configured the bridge runs unauthenticated, which is the
 * pre-token behaviour and what `--pair-only` and ad-hoc local runs get. A
 * bridge that refuses to boot is indistinguishable, to a user, from a broken
 * install.
 */
export function requestIsUnauthorized(pathname, headers, token) {
  if (!token) return false
  if (UNAUTHENTICATED_PATHS.has(pathname)) return false
  return !tokenIsValid(headers?.[TOKEN_HEADER], token)
}

/**
 * Whether the destructive /messages drain should be refused.
 *
 * The drain splices the queue, so whoever calls it takes ownership and nobody
 * else ever sees those messages. The Host allowlist stops DNS rebinding but
 * not a page doing `<img src="http://127.0.0.1:3000/messages">`, which sends
 * an allowlisted Host. Requiring a custom header forces a preflight the bridge
 * never answers, so the browser never issues the real GET.
 */
export function drainNeedsHeader(headers) {
  return !headers?.[POLL_HEADER]
}

/** Build the /health body, adding a proof when the caller supplied a nonce. */
export function healthBody(base, token, nonce) {
  const body = { ...base }
  if (token && typeof nonce === 'string' && nonce) {
    body.proof = bridgeProof(token, nonce)
  }
  return body
}
