/**
 * Sanitizers for the desktop log sink.
 *
 * Kept free of any `electron` import so the rules below can be exercised
 * without booting Electron.
 *
 * `rememberLog` splits its input on newlines and prefixes every resulting line
 * with `[hermes] `, which is what makes the log readable. It also means any
 * caller that interpolates untrusted text into a log message hands the writer
 * of that text the ability to emit lines indistinguishable from genuine ones
 * (CWE-117). The concrete path found in the 2026-07-27 CodeQL triage: a gateway
 * redirects the loopback OAuth callback to
 * `/callback?error=…&error_description=…`, `parseLoopbackCallback` puts both
 * verbatim into an Error, and that message reaches the log. The error branch
 * runs *before* the `state`/CSRF check, so no secret is needed to reach it.
 *
 * Two different jobs, deliberately split:
 *
 * - `sanitizeLogChunk` runs at the sink on everything. It cannot drop `\n`,
 *   because splitting multi-line backend stdout is the feature, so it removes
 *   the control characters that have no business in a log line and caps each
 *   line's length.
 * - `escapeUntrustedForLog` runs at the few call sites that interpolate
 *   attacker-reachable text. It is the one that neutralises `\n`, turning a
 *   forged line into visible `\n` inside a single line.
 */

/** Mirrors ui-tui's MAX_LOG_LINE_BYTES so both surfaces truncate alike. */
export const MAX_LOG_LINE_CHARS = 4096

/**
 * Remove control characters that let a log line lie, keeping `\n` (the split is
 * the feature) and `\t` (legitimate in tool output). `\r` is dropped outright:
 * `rememberLog` splits on `/\r?\n/`, so a lone `\r` never carries meaning and
 * would only serve to overwrite the start of a rendered line.
 */
export function stripLogControlChars(text: string): string {
  // eslint-disable-next-line no-control-regex
  return text.replace(/[\u0000-\u0008\u000B-\u001F\u007F]/g, '')
}

/** Truncate one line, reporting how much was dropped rather than hiding it. */
export function capLogLine(line: string): string {
  return line.length > MAX_LOG_LINE_CHARS
    ? `${line.slice(0, MAX_LOG_LINE_CHARS)}… [truncated ${line.length} chars]`
    : line
}

/**
 * Sink-side pass: strip control characters and cap every line. Returns the
 * chunk with `\n` preserved so the caller can still split it.
 */
export function sanitizeLogChunk(chunk: unknown): string {
  const text = stripLogControlChars(String(chunk ?? ''))

  return text.split('\n').map(capLogLine).join('\n')
}

/**
 * Call-site pass for attacker-reachable text: render newlines and tabs as
 * visible escapes so the value cannot open a new log line, then cap it.
 * Use this wherever a remote or user-supplied string is interpolated into a
 * log message.
 */
export function escapeUntrustedForLog(value: unknown): string {
  const text = stripLogControlChars(String(value ?? ''))
    .replace(/\\/g, '\\\\')
    .replace(/\n/g, '\\n')
    .replace(/\t/g, '\\t')

  return capLogLine(text)
}
