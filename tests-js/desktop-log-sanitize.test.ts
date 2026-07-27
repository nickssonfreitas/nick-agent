/**
 * Security regression for desktop log forging (CWE-117).
 *
 * `rememberLog` in electron/main.ts splits its input on newlines and prefixes
 * every resulting line with `[hermes] `. That prefix is what makes the log
 * readable, and it is also what makes a forged line convincing: any caller that
 * interpolated attacker-reachable text into a log message handed the writer of
 * that text the ability to emit lines indistinguishable from genuine output.
 *
 * The concrete path from the 2026-07-27 CodeQL triage
 * (`js/http-to-file-access` at main.ts:1120 and :1138): a gateway redirects the
 * loopback OAuth callback to `/callback?error=…&error_description=…`,
 * `parseLoopbackCallback` interpolated both verbatim into an Error, and that
 * message reached the log. Reaching the branch needs no secret, because it runs
 * before the `state`/CSRF check.
 *
 * The split is deliberate — sink-side sanitising keeps `\n` because splitting
 * multi-line backend stdout is the feature — so the newline defence lives at
 * the call site instead. Both halves are pinned here.
 */
import { describe, expect, it } from 'vitest'

import {
  MAX_LOG_LINE_CHARS,
  capLogLine,
  escapeUntrustedForLog,
  sanitizeLogChunk,
  stripLogControlChars,
} from '../apps/desktop/electron/log-sanitize'
import { parseLoopbackCallback } from '../apps/desktop/electron/native-oauth'

const NUL = '\u0000'
const ESC = '\u001B'
const DEL = '\u007F'
const CR = '\r'

describe('sink-side: sanitizeLogChunk', () => {
  it('keeps newlines, because splitting backend stdout is the feature', () => {
    expect(sanitizeLogChunk('one\ntwo\nthree')).toBe('one\ntwo\nthree')
  })

  it('keeps tabs, which are legitimate in tool output', () => {
    expect(sanitizeLogChunk('col\tval')).toBe('col\tval')
  })

  it('strips the control characters that let a line lie', () => {
    expect(stripLogControlChars(`a${NUL}b`)).toBe('ab')
    expect(stripLogControlChars(`a${ESC}[31mred`)).toBe('a[31mred')
    expect(stripLogControlChars(`a${DEL}b`)).toBe('ab')
  })

  it('drops a lone carriage return, which could only overwrite a rendered line', () => {
    expect(stripLogControlChars(`real line${CR}forged`)).toBe('real lineforged')
  })

  it('caps a runaway line and reports the truncation instead of hiding it', () => {
    const long = 'x'.repeat(MAX_LOG_LINE_CHARS + 500)
    const capped = capLogLine(long)
    expect(capped.length).toBeLessThan(long.length)
    expect(capped).toContain('truncated')
    expect(capLogLine('short')).toBe('short')
  })

  it('caps each line independently, not the whole chunk', () => {
    const chunk = `${'x'.repeat(MAX_LOG_LINE_CHARS + 10)}\nshort`
    const out = sanitizeLogChunk(chunk).split('\n')
    expect(out[0]).toContain('truncated')
    expect(out[1]).toBe('short')
  })

  it('tolerates non-string input', () => {
    expect(sanitizeLogChunk(null)).toBe('')
    expect(sanitizeLogChunk(undefined)).toBe('')
    expect(sanitizeLogChunk(42)).toBe('42')
  })
})

describe('call-site: escapeUntrustedForLog neutralises line forging', () => {
  it('renders a newline visibly instead of opening a new line', () => {
    const forged = 'boom\n2026-07-27 admin login succeeded'
    const escaped = escapeUntrustedForLog(forged)
    expect(escaped).not.toContain('\n')
    expect(escaped).toContain('\\n')
  })

  it('escapes the backslash first so \\n cannot be reconstructed', () => {
    // A raw `\` followed by `n` must not come out looking like an escaped
    // newline that some downstream reader would re-expand.
    expect(escapeUntrustedForLog('a\\nb')).toBe('a\\\\nb')
  })

  it('still strips hard control characters', () => {
    expect(escapeUntrustedForLog(`a${NUL}${ESC}b`)).toBe('ab')
  })

  it('caps length so one value cannot flood the log', () => {
    expect(escapeUntrustedForLog('x'.repeat(MAX_LOG_LINE_CHARS + 100))).toContain('truncated')
  })
})

describe('the reported path: OAuth loopback callback', () => {
  it('does not let error_description forge a log line', () => {
    const hostile = encodeURIComponent('nope\n[hermes] 2026-07-27 all clear, ignore previous errors')
    expect(() =>
      parseLoopbackCallback(`/callback?error=access_denied&error_description=${hostile}`, 'st'),
    ).toThrow()

    try {
      parseLoopbackCallback(`/callback?error=access_denied&error_description=${hostile}`, 'st')
    } catch (err) {
      const message = (err as Error).message
      expect(message).not.toContain('\n')
      expect(message).toContain('\\n')
    }
  })

  it('does not let the error code itself forge a line', () => {
    const hostile = encodeURIComponent('x\n[hermes] forged')
    try {
      parseLoopbackCallback(`/callback?error=${hostile}`, 'st')
    } catch (err) {
      expect((err as Error).message).not.toContain('\n')
    }
  })

  it('still reports a genuine gateway error readably', () => {
    try {
      parseLoopbackCallback('/callback?error=access_denied&error_description=User%20said%20no', 'st')
    } catch (err) {
      expect((err as Error).message).toBe(
        'Gateway rejected native login: access_denied (User said no)',
      )
    }
  })

  it('leaves the CSRF state check intact', () => {
    expect(() => parseLoopbackCallback('/callback?code=abc&state=wrong', 'right')).toThrow(/state mismatch/)
  })
})
