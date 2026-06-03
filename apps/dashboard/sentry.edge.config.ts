// Edge-runtime Sentry init for the dashboard (S1-20, Slice B).
//
// Disabled-by-default: when NEXT_PUBLIC_SENTRY_DSN is empty/undefined we skip
// Sentry.init entirely, so nothing is captured or sent.
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
