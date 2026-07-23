import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { join } from 'node:path'

import { afterEach, test } from 'vitest'

import {
  BOOT_INLINE_SCRIPT_HASH,
  buildCsp,
  getGatewayOrigins,
  installRendererCsp,
  setGatewayOrigins,
} from './csp'

afterEach(() => setGatewayOrigins(null))

test('boot inline-script hash matches the source index.html (drift guard)', () => {
  // If the pre-paint <script> in index.html is edited, its hash changes and the
  // enforced script-src would reject it — white-screening every window. This
  // fails loudly at that moment instead. NOTE: checks the SOURCE file; the
  // Vite-packaged index.html can differ and must be re-verified before enforce.
  const html = readFileSync(join(__dirname, '..', 'index.html'), 'utf-8')
  const match = html.match(/<script>([\s\S]*?)<\/script>/)
  assert.ok(match, 'index.html must contain an inline <script>')
  const digest = createHash('sha256').update(match![1], 'utf-8').digest('base64')
  assert.equal(BOOT_INLINE_SCRIPT_HASH, `'sha256-${digest}'`)
})

test('script-src is hash/nonce-locked and never falls back to unsafe-inline', () => {
  const csp = buildCsp()
  const scriptSrc = csp.split(';').map(s => s.trim()).find(d => d.startsWith('script-src'))!
  assert.ok(scriptSrc.includes(BOOT_INLINE_SCRIPT_HASH))
  // blob: is load-bearing for the runtime plugin loader's import() of a blob URL.
  assert.ok(scriptSrc.includes('blob:'))
  // The whole point: no unsafe-inline in script-src, where XSS lands.
  assert.ok(!scriptSrc.includes("'unsafe-inline'"))
  assert.ok(!scriptSrc.includes("'unsafe-eval'"))
})

test('default-src none, object-src none, frame-ancestors none', () => {
  const csp = buildCsp()
  assert.ok(csp.includes("default-src 'none'"))
  assert.ok(csp.includes("object-src 'none'"))
  assert.ok(csp.includes("frame-ancestors 'none'"))
})

test('connect-src reflects the paired gateway origin and its ws twin', () => {
  setGatewayOrigins('https://gw.example.com:8443/base')
  assert.deepEqual(getGatewayOrigins(), [
    'https://gw.example.com:8443',
    'wss://gw.example.com:8443',
  ])

  const connectSrc = buildCsp()
    .split(';')
    .map(s => s.trim())
    .find(d => d.startsWith('connect-src'))!

  assert.ok(connectSrc.includes('https://gw.example.com:8443'))
  assert.ok(connectSrc.includes('wss://gw.example.com:8443'))
})

test('a malformed baseUrl clears gateway origins rather than throwing', () => {
  setGatewayOrigins('https://ok.example')
  setGatewayOrigins('not a url')
  assert.deepEqual(getGatewayOrigins(), [])
})

test('installRendererCsp is a no-op when a dev server is set', () => {
  let registered = false

  const fakeSession = {
    webRequest: {
      onHeadersReceived() {
        registered = true
      },
    },
  } as any

  installRendererCsp(fakeSession, 'http://localhost:5173')
  assert.equal(registered, false, 'dev server must skip CSP installation')
})

test('installRendererCsp stamps CSP only on main-frame file:// documents', () => {
  let handler: ((d: any, cb: (r: any) => void) => void) | null = null

  const fakeSession = {
    webRequest: {
      onHeadersReceived(fn: any) {
        handler = fn
      },
    },
  } as any

  installRendererCsp(fakeSession, undefined)
  assert.ok(handler, 'handler must be registered when no dev server')

  // Main-frame file:// document → CSP stamped.
  let out: any
  handler!(
    { resourceType: 'mainFrame', url: 'file:///app/index.html', responseHeaders: {} },
    r => {
      out = r
    },
  )
  const cspKey = Object.keys(out.responseHeaders).find(k => /content-security-policy/i.test(k))
  assert.ok(cspKey, 'main-frame file document must receive a CSP header')

  // A sub-resource / third-party frame (e.g. an OAuth page) → untouched.
  let passthrough: any
  handler!(
    { resourceType: 'subFrame', url: 'https://idp.example/login', responseHeaders: { a: ['b'] } },
    r => {
      passthrough = r
    },
  )
  assert.deepEqual(
    passthrough.responseHeaders,
    { a: ['b'] },
    'third-party frames must not receive our CSP',
  )
})
