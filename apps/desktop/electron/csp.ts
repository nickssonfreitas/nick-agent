// Content-Security-Policy for the Electron renderer.
//
// The renderer is otherwise well hardened (contextIsolation, sandbox,
// nodeIntegration:false), but the preload bridge exposes readFileText /
// readFileDataUrl / selectPaths and a generic api proxy, so any script
// execution in the renderer becomes arbitrary local file read plus
// authenticated backend calls. A CSP is the layer that contains that.
//
// Report-Only by default (HERMES_CSP_ENFORCE=1 to enforce). Report-Only lets a
// full release cycle collect violation reports from the renderer console before
// the policy can break anything — the app runs unchanged while it reports.
//
// IMPORTANT before enforcing (cannot be verified from source alone):
//   1. `npm --workspace apps/desktop run build`, then grep dist/assets for
//      `new Function(` / `eval(` — a dependency (mermaid/shiki/…) needing
//      'unsafe-eval' would only surface in the bundled output.
//   2. Confirm BOOT_INLINE_SCRIPT_HASH still matches the packaged index.html
//      (Vite may transform the inline pre-paint script). The vitest guard
//      checks the SOURCE index.html; the packaged file can differ.

import type { Session } from 'electron'

// sha256 of the inline pre-paint <script> in apps/desktop/index.html. A drift
// guard in csp.test.ts recomputes this from source and fails if it changes, so
// a future edit to that script cannot silently white-screen every window under
// the enforced policy.
export const BOOT_INLINE_SCRIPT_HASH =
  "'sha256-thP3Xi4D3xLhY5XYiK6vCmNBel4nusdd7+E9sSYH7cI='"

// Third-party hosts the social embeds load widget scripts from. These stay in
// script-src until the embeds move to a webview partition (a separate change);
// a consent gate already bounds their exposure.
const SOCIAL_SCRIPT_HOSTS = [
  'https://www.instagram.com',
  'https://www.tiktok.com',
  'https://platform.twitter.com',
]

const FRAME_HOSTS = [
  'https://www.youtube.com',
  'https://www.youtube-nocookie.com',
  'https://player.vimeo.com',
  'https://open.spotify.com',
  'https://www.instagram.com',
  'https://www.tiktok.com',
  'https://platform.twitter.com',
  'https://twitter.com',
  'https://x.com',
  'https://www.google.com',
  'https://maps.google.com',
  'https://www.openstreetmap.org',
  'https://www.pinterest.com',
  'https://assets.pinterest.com',
]

// connect-src cannot be static: the gateway origin is whatever remote the user
// paired with (connection.baseUrl). Rebuilt whenever the connection changes.
let gatewayOrigins: string[] = []

export function setGatewayOrigins(baseUrl?: string | null): void {
  if (!baseUrl) {
    gatewayOrigins = []

    return
  }

  try {
    const { origin } = new URL(baseUrl)
    gatewayOrigins = [origin, origin.replace(/^http/, 'ws')]
  } catch {
    gatewayOrigins = []
  }
}

export function getGatewayOrigins(): string[] {
  return [...gatewayOrigins]
}

export function buildCsp(): string {
  return [
    "default-src 'none'",
    // 'self' = the file:// bundle; BOOT hash = the inline pre-paint script;
    // blob: = runtime plugin loader (contrib/runtime-loader.ts does import() of
    // a blob URL, which is a SCRIPT fetch — 'unsafe-eval' does not substitute);
    // social hosts = embed widget scripts.
    `script-src 'self' ${BOOT_INLINE_SCRIPT_HASH} blob: ${SOCIAL_SCRIPT_HOSTS.join(' ')}`,
    // 'unsafe-inline' is unavoidable here: the SPA injects runtime <style> from
    // several sites and a stitches-based dep (leva), and under file:// there is
    // no server to mint a per-document nonce. Dropping it is a fake win; XSS
    // lands in script-src, which is nonce/hash-locked above.
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' data: https://fonts.gstatic.com",
    "img-src 'self' data: blob: file: hermes-media: https:",
    "media-src 'self' data: blob: file: hermes-media: https:",
    `connect-src 'self' data: blob: ${gatewayOrigins.join(' ')} ` +
      `${SOCIAL_SCRIPT_HOSTS.join(' ')} https://syndication.twitter.com`.trim(),
    `frame-src ${FRAME_HOSTS.join(' ')}`,
    "worker-src 'self' blob:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
  ].join('; ')
}

/**
 * Stamp the renderer CSP on defaultSession only, for main-frame file:// (and
 * devtools) documents. The OAuth / portal / link-title / embed / preview
 * partitions host third-party HTML we do not control — stamping our CSP there
 * would break IDP sign-in, so they are deliberately untouched.
 *
 * Skipped entirely when a dev server is set: Vite HMR needs 'unsafe-inline' /
 * 'unsafe-eval' and its own ws origin, and shipping a permissive dev CSP would
 * hide real violations behind allowances that never reach production.
 */
export function installRendererCsp(
  defaultSession: Pick<Session, 'webRequest'>,
  devServer?: string,
): void {
  if (devServer) {return}

  const header =
    process.env.HERMES_CSP_ENFORCE === '1'
      ? 'Content-Security-Policy'
      : 'Content-Security-Policy-Report-Only'

  defaultSession.webRequest.onHeadersReceived((details, callback) => {
    const isMainDoc =
      details.resourceType === 'mainFrame' && /^(file|devtools):/.test(details.url)

    if (!isMainDoc) {
      callback({ responseHeaders: details.responseHeaders })

      return
    }

    callback({
      responseHeaders: {
        ...details.responseHeaders,
        [header]: [buildCsp()],
        'X-Content-Type-Options': ['nosniff'],
        'Referrer-Policy': ['strict-origin-when-cross-origin'],
      },
    })
  })
}
