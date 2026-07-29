/**
 * Validation for `hermes://` deep links.
 *
 * Kept free of any `electron` import on purpose: a deep link is the one input
 * to the desktop app that an arbitrary web page can trigger (navigating to
 * `hermes://...` hands the URL to the OS, which hands it to us), so this is a
 * security boundary and it has to be testable without booting Electron.
 *
 * The renderer turns the payload into a `/blueprint` slash command pre-filled
 * in the composer. It previously interpolated `name` and every param key raw,
 * and quoted a value only when that value already contained whitespace, so a
 * crafted link could forge additional command tokens. Everything is validated
 * here so a hostile payload never reaches the renderer; the quoting there is
 * the second layer rather than the only one.
 */

/** Identifiers become command tokens verbatim, so they are restricted to a slug. */
const DEEP_LINK_SLUG_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/

const DEEP_LINK_VALUE_MAX_CHARS = 2048

export interface DeepLinkPayload {
  kind: string
  name: string
  params: Record<string, string>
}

export function isDeepLinkSlug(value: unknown): value is string {
  return typeof value === 'string' && DEEP_LINK_SLUG_RE.test(value)
}

/**
 * Slot values are free text and cannot be slugs. What they must not carry is a
 * control character: a newline or carriage return would split the composed
 * command into extra lines, which is the forging primitive the slug check
 * closes for keys and names.
 */
export function isSafeDeepLinkValue(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    value.length <= DEEP_LINK_VALUE_MAX_CHARS &&
    // eslint-disable-next-line no-control-regex
    !/[\u0000-\u001F\u007F]/.test(value)
  )
}

/**
 * Parse and validate a `hermes://` URL.
 *
 * Returns null for anything malformed or hostile. The caller logs the
 * rejection without echoing the offending value, since it is attacker-supplied
 * and the desktop log feeds the diagnostics bundle.
 */
export function parseDeepLink(url: unknown): DeepLinkPayload | null {
  if (!url || typeof url !== 'string') return null

  let parsed: URL
  try {
    parsed = new URL(url)
  } catch {
    return null
  }

  const kind = parsed.hostname || ''

  let name: string
  try {
    name = decodeURIComponent((parsed.pathname || '').replace(/^\//, ''))
  } catch {
    // Malformed percent-encoding: decodeURIComponent throws URIError.
    return null
  }

  if (!isDeepLinkSlug(kind) || !isDeepLinkSlug(name)) return null

  const params: Record<string, string> = {}
  let rejected = false
  parsed.searchParams.forEach((value, key) => {
    if (rejected) return
    if (!isDeepLinkSlug(key) || !isSafeDeepLinkValue(value)) {
      rejected = true

      return
    }
    params[key] = value
  })

  if (rejected) return null

  return { kind, name, params }
}
