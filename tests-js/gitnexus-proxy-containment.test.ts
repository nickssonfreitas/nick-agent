/**
 * Security regression for the gitnexus-explorer proxy
 * (``optional-skills/research/gitnexus-explorer/scripts/proxy.mjs``).
 *
 * CodeQL flagged ``js/path-injection`` there and the triage confirmed it by
 * exploitation: the server built its file path with
 * ``path.join(DIST_DIR, req.url)``, which is not a containment check — it
 * normalises ``..`` away *after* joining, so the result lands outside the dist
 * directory. Node's http server hands over the raw request target, so
 * ``GET /../../../../etc/passwd`` arrived untouched and was served with HTTP
 * 200. It compounded with ``server.listen(PORT, cb)``, which binds every
 * interface when no host is given, so the read was reachable from any host on
 * the network while the startup log still claimed ``localhost``.
 *
 * These tests spawn the real script and speak real HTTP to it. Mocking the
 * filesystem would defeat the purpose: what is under test is whether the
 * resolved path actually stays inside the root, which only the real
 * ``path.resolve`` + prefix comparison can answer.
 */
import { execFileSync, spawn, type ChildProcess } from 'node:child_process'
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import http from 'node:http'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'

const PROXY = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../optional-skills/research/gitnexus-explorer/scripts/proxy.mjs',
)

let root: string
let child: ChildProcess
let base: string

/** Raw request so the traversal reaches the server unnormalised — fetch() would collapse `..`. */
function rawGet(target: string): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const req = http.request(
      { hostname: '127.0.0.1', port: Number(new URL(base).port), path: target, method: 'GET' },
      (res) => {
        let body = ''
        res.on('data', (c: Buffer) => (body += c.toString()))
        res.on('end', () => resolve({ status: res.statusCode ?? 0, body }))
      },
    )
    req.on('error', reject)
    req.end()
  })
}

beforeAll(async () => {
  root = mkdtempSync(path.join(tmpdir(), 'gitnexus-proxy-test-'))
  mkdirSync(path.join(root, 'dist', 'assets'), { recursive: true })
  writeFileSync(path.join(root, 'dist', 'index.html'), '<html>INDEX</html>')
  writeFileSync(path.join(root, 'dist', 'assets', 'app.css'), 'body{color:red}')
  // Secret sits beside dist/, exactly where `..` would land.
  writeFileSync(path.join(root, 'SECRET.txt'), 'TOP_SECRET_VALUE')

  const port = varPort()
  base = `http://127.0.0.1:${port}`
  child = spawn(process.execPath, [PROXY, path.join(root, 'dist'), String(port)], {
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  await waitForListening()
}, 30_000)

afterAll(() => {
  child?.kill()
  if (root) rmSync(root, { recursive: true, force: true })
})

function varPort(): number {
  // High, per-run port. Collisions surface as a failed beforeAll, not a flaky assertion.
  return 20000 + Math.floor(Math.random() * 20000)
}

async function waitForListening(): Promise<void> {
  for (let i = 0; i < 100; i++) {
    try {
      await rawGet('/')
      return
    } catch {
      await new Promise((r) => setTimeout(r, 100))
    }
  }
  throw new Error('proxy did not start')
}

describe('gitnexus proxy path containment', () => {
  it('serves files inside the dist root', async () => {
    const index = await rawGet('/')
    expect(index.status).toBe(200)
    expect(index.body).toContain('INDEX')

    const css = await rawGet('/assets/app.css')
    expect(css.status).toBe(200)
    expect(css.body).toContain('color:red')
  })

  it('keeps the SPA fallback for extensionless routes', async () => {
    const route = await rawGet('/some/spa/route')
    expect(route.status).toBe(200)
    expect(route.body).toContain('INDEX')
  })

  it.each([
    ['../SECRET.txt', '/../SECRET.txt'],
    ['deep traversal to /etc/passwd', '/../../../../../../../../etc/passwd'],
    ['percent-encoded traversal', '/%2e%2e/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd'],
    ['traversal through a real subdir', '/assets/../../SECRET.txt'],
  ])('refuses to escape the root: %s', async (_label, target) => {
    const res = await rawGet(target)
    expect(res.status).toBe(403)
    expect(res.body).not.toContain('TOP_SECRET_VALUE')
    expect(res.body).not.toContain('root:')
  })

  it('does not leak file contents through the SPA fallback either', async () => {
    // Extensionless traversal must be rejected outright, not quietly rewritten
    // to index.html — a 200 here would hide the escape rather than block it.
    const res = await rawGet('/../../../../../../../../etc/hostname')
    expect(res.status).toBe(403)
  })
})

describe('gitnexus proxy network exposure', () => {
  it('binds loopback only, so the LAN cannot reach it', () => {
    const port = new URL(base).port
    const listening = execFileSync('sh', ['-c', 'ss -ltn 2>/dev/null || netstat -ltn 2>/dev/null || true'])
      .toString()
      .split('\n')
      .filter((l) => l.includes(`:${port}`))
    expect(listening.length).toBeGreaterThan(0)
    // Must be bound to 127.0.0.1, never 0.0.0.0/*/:::
    for (const line of listening) {
      expect(line).toMatch(/127\.0\.0\.1:/)
      expect(line).not.toMatch(/0\.0\.0\.0:\s*$|\*:/)
    }
  })
})
