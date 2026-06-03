import type { ErrorEvent, Event, EventHint } from '@sentry/nextjs'

/**
 * Sentry PII scrubbing for the dashboard (S1-20, Slice B).
 *
 * This MUST stay in step with the API slice's scrubber
 * (apps/api/src/bullet_api/observability/sentry.py): the sentinel, denylist,
 * free-text regexes, and the two-zone walk below are a SHARED CONTRACT. If
 * either side changes, change both. The walk redacts in two zones:
 *   1. Inside DATA sub-trees (request, extra, contexts, user, tags,
 *      stack-frame `vars`, breadcrumb `data`) a denylisted key
 *      (case-insensitive) has its whole value replaced with the sentinel,
 *      regardless of the value's type.
 *   2. In Sentry's STRUCTURAL envelope (exception messages, breadcrumb
 *      messages, the transaction name) the denylist is NOT applied - keys
 *      there collide with data-column names, notably `value`, which is both a
 *      denylisted column AND the key Sentry uses for the exception message.
 *      Wholesale-filtering it would make every issue title "[Filtered]".
 *      Structural strings are regex-redacted instead, so an embedded email
 *      becomes the sentinel while the surrounding message stays readable.
 * Every string value, in either zone, is additionally regex-redacted for
 * emails, phone numbers, and signed/bearer URLs.
 */

/** Replacement sentinel for any redacted value. */
export const FILTERED = '[Filtered]'

/**
 * Keys whose entire value is replaced with the sentinel, matched
 * case-insensitively. Mirrors the API denylist exactly.
 */
export const DENYLIST_KEYS: readonly string[] = [
  'email',
  'phone',
  'password',
  'password_hash',
  'full_name',
  'contact_first_name',
  'contact_last_name',
  'transcript',
  'transcript_text',
  'value_text',
  'value',
  'metadata',
  'external_url',
  'signed_url',
  'source_url',
  'r2_key',
  'pandadoc_document_id',
  'authorization',
  'cookie',
  'set-cookie',
  'session',
  'payload',
  'response',
  'before',
  'after',
]

const DENYLIST_SET = new Set(DENYLIST_KEYS.map((k) => k.toLowerCase()))

/**
 * Event sub-trees that hold arbitrary application/user data. The denylist is
 * applied ONLY inside these (and anything nested under them); Sentry's
 * structural envelope outside them is regex-redacted only. Mirrors the API
 * `_DATA_SUBTREE_KEYS`.
 */
const DATA_SUBTREE_KEYS = new Set([
  'request',
  'extra',
  'contexts',
  'user',
  'tags',
  'vars',
  'data',
])

/** Free-text email matcher. */
export const EMAIL_RE = /[\w.+-]+@[\w-]+\.[\w.-]+/g

/** Free-text phone matcher (loose international shape). */
export const PHONE_RE = /\+?\d[\d\s().-]{7,}\d/g

/**
 * Matches a single signed/bearer URL token: a URL containing `pandadoc.com`
 * or carrying a sensitive query param. Used to redact whole URLs found inside
 * free text. The global flag lets us scan a string for any such URL.
 */
const SIGNED_URL_RE =
  /\bhttps?:\/\/[^\s"'<>]*?(?:pandadoc\.com|[?&](?:token|signature|sig|expires|x-amz-)[^\s"'<>]*)[^\s"'<>]*/gi

/** True when a bare string value is itself a signed/bearer URL. */
function isSignedUrl(value: string): boolean {
  if (!/^https?:\/\//i.test(value.trim())) return false
  if (/pandadoc\.com/i.test(value)) return true
  return /[?&](?:token|signature|sig|expires|x-amz-)/i.test(value)
}

/** Apply the free-text regexes to a single string value. */
function redactString(value: string): string {
  // A standalone signed/bearer URL is redacted wholesale.
  if (isSignedUrl(value)) return FILTERED

  return value
    .replace(SIGNED_URL_RE, FILTERED)
    .replace(EMAIL_RE, FILTERED)
    .replace(PHONE_RE, FILTERED)
}

/**
 * Recursively walk and redact an arbitrary value (returns a new structure;
 * never mutates the input). `inData` tracks whether we are inside a data
 * sub-tree (see `DATA_SUBTREE_KEYS`): a denylisted key is replaced wholesale
 * only when `inData` is true, so the structural envelope keeps readable
 * messages while embedded PII is still regex-redacted. `unknown` everywhere,
 * narrowed explicitly so we stay free of `any`.
 */
function scrubValue(value: unknown, inData: boolean): unknown {
  if (typeof value === 'string') {
    return redactString(value)
  }

  if (Array.isArray(value)) {
    return value.map((item) => scrubValue(item, inData))
  }

  if (value !== null && typeof value === 'object') {
    const source = value as Record<string, unknown>
    const out: Record<string, unknown> = {}
    for (const key of Object.keys(source)) {
      const keyLower = key.toLowerCase()
      const childInData = inData || DATA_SUBTREE_KEYS.has(keyLower)
      if (childInData && DENYLIST_SET.has(keyLower)) {
        out[key] = FILTERED
      } else {
        out[key] = scrubValue(source[key], childInData)
      }
    }
    return out
  }

  // numbers, booleans, null, undefined, bigint, symbol, functions: leave as-is.
  return value
}

/**
 * Sentry `beforeSend` / `beforeSendTransaction` hook. Scrubs the full event
 * payload (message, exception values, request, extra, contexts, breadcrumbs,
 * etc.) and returns it, starting outside any data sub-tree. The `hint` is
 * accepted to satisfy the hook signature but is not used.
 */
export function scrubEvent<T extends Event | ErrorEvent>(
  event: T,
  _hint?: EventHint,
): T {
  return scrubValue(event, false) as T
}
