// Browser-side Sentry init for the dashboard (S1-20, Slice B).
//
// In @sentry/nextjs v9+, this `instrumentation-client.ts` file replaces the
// old `sentry.client.config.ts`. Next.js runs it on the client before the app
// hydrates. The DSN is build-inlined from NEXT_PUBLIC_SENTRY_DSN; when it is
// empty/undefined we skip Sentry.init so nothing is captured or sent.
import * as Sentry from '@sentry/nextjs'

import { scrubEvent } from '@/lib/sentry-scrub'

const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN

if (dsn) {
  Sentry.init({
    dsn,
    environment: process.env.SENTRY_ENVIRONMENT,
    tracesSampleRate: 0,
    beforeSend: scrubEvent,
    beforeSendTransaction: scrubEvent,
  })
}

// Instruments App Router client-side navigations. Safe to export even when
// Sentry is not initialised: with no active client it is a no-op.
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart
