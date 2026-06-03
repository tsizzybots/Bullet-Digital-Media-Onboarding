// Next.js instrumentation hook for the dashboard (S1-20, Slice B).
//
// `register()` is called once per runtime. We lazily import the matching
// Sentry runtime config (server or edge) so neither pulls the other's deps.
// The config files live at the dashboard root, hence the `../` relative path
// from this `src/instrumentation.ts`.
//
// `onRequestError` captures errors thrown in Server Components, route
// handlers, and middleware. With no active Sentry client (DSN empty) it is a
// no-op, so this is safe in the disabled-by-default state.
import * as Sentry from '@sentry/nextjs'

export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    await import('../sentry.server.config')
  }

  if (process.env.NEXT_RUNTIME === 'edge') {
    await import('../sentry.edge.config')
  }
}

export const onRequestError = Sentry.captureRequestError
