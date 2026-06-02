/**
 * Auth HTTP wrappers for the dashboard (S1-18).
 *
 * Thin, fully-typed helpers over the generated `api` client that centralise
 * the HTTP-status -> user-facing-message mapping in one place, so the login,
 * confirmation, and resend UI surfaces stay free of status-code branching.
 *
 * With `openapi-fetch`, each call returns `{ data, error, response }`. We
 * branch on `response.status` rather than the typed `error` body, because the
 * API signals auth outcomes (401/403/429/400/410) purely through the status
 * code and the response shapes are otherwise interchangeable.
 */

import { api } from '@/lib/api'

/** Result of an auth call: success, or a failure with a display message. */
export type AuthResult = { ok: true } | { ok: false; status: number; message: string }

/**
 * Verify credentials and (on success) establish the session cookie.
 *
 * Maps the API's auth status codes to copy the login form can render directly.
 */
export async function login(email: string, password: string): Promise<AuthResult> {
  const { response } = await api.POST('/auth/login', {
    body: { email, password },
  })

  if (response.ok) {
    return { ok: true }
  }

  switch (response.status) {
    case 401:
      return { ok: false, status: 401, message: 'Invalid email or password.' }
    case 403:
      return {
        ok: false,
        status: 403,
        message:
          "Your email isn't confirmed yet. Check your inbox for the confirmation link.",
      }
    case 429:
      return {
        ok: false,
        status: 429,
        message: 'Too many attempts. Please wait a few minutes and try again.',
      }
    default:
      return {
        ok: false,
        status: response.status,
        message: 'Something went wrong. Please try again.',
      }
  }
}

/**
 * Invalidate the current session. Best-effort: a failed logout (already
 * expired session, network blip) must never throw, so callers can always
 * clear local state and redirect afterwards.
 */
export async function logout(): Promise<void> {
  try {
    await api.POST('/auth/logout')
  } catch {
    // Swallow: logout is best-effort.
  }
}

/** Result of confirming an email link. */
export type ConfirmResult =
  | { ok: true; status: 'confirmed' | 'already_confirmed' }
  | { ok: false; status: number; message: string }

/**
 * Confirm an email-confirmation token.
 *
 * On success the API returns a `{ status }` envelope; we narrow it to the two
 * meaningful states and treat any other 2xx string as a plain confirmation.
 */
export async function confirmEmail(token: string): Promise<ConfirmResult> {
  const { data, response } = await api.POST('/auth/confirm/{token}', {
    params: { path: { token } },
  })

  if (response.ok) {
    const status = data?.status === 'already_confirmed' ? 'already_confirmed' : 'confirmed'
    return { ok: true, status }
  }

  switch (response.status) {
    case 400:
      return { ok: false, status: 400, message: 'This confirmation link is invalid.' }
    case 410:
      return {
        ok: false,
        status: 410,
        message: 'This confirmation link has expired. Ask your admin to resend it.',
      }
    default:
      return {
        ok: false,
        status: response.status,
        message: 'We could not confirm your email. Please try again.',
      }
  }
}

/**
 * Re-send the confirmation email for an unconfirmed user.
 *
 * The endpoint always returns 200 (it never reveals whether the address maps
 * to a real account), so this always resolves; any transport error is
 * swallowed so the UI can show its neutral "if that account exists" copy.
 */
export async function resendConfirmation(email: string): Promise<void> {
  try {
    await api.POST('/auth/resend-confirmation', { body: { email } })
  } catch {
    // Swallow: the endpoint is intentionally non-revealing and best-effort.
  }
}
